import logging
import os
from mimetypes import guess_type

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.conf import settings
from django.contrib.contenttypes.models import ContentType
from django.core.files.storage import FileSystemStorage
from django.db import connection
from django.db.models import Count, OuterRef, Prefetch, Q, Subquery
from django.http import FileResponse, Http404, JsonResponse
from rest_framework import status, viewsets
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.media_files.models.models import DisplayPhoto, DisplayVideo, File

from ..accounts.forms import *
from .models import *
from .serializers import *
from .serializers import MessageSerializer
from .utils import *

protected_storage = FileSystemStorage(location=settings.PROTECTED_MEDIA_ROOT)


logger = logging.getLogger(__name__)


class GetChats(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        user = request.user

        last_message_subquery = Message.objects.filter(
            chat_id=OuterRef("pk"), is_deleted=False
        ).order_by("-date")

        chats_qs = (
            Chat.objects.filter(users=user)
            .annotate(
                users_count=Count("users", distinct=True),
                last_message_id=Subquery(last_message_subquery.values("id")[:1]),
            )
            .order_by("-created_at")
            .prefetch_related(
                Prefetch(
                    "chatmember_set",
                    queryset=ChatMember.objects.exclude(user=user).select_related(
                        "user__profile"
                    ),
                    to_attr="other_members",
                )
            )
        )

        dialog_interlocutor_ids = [
            member.user.id
            for chat in chats_qs
            if chat.is_dialog
            for member in getattr(chat, "other_members", [])
        ]

        contacts_qs = Contact.objects.filter(
            owner=user, user_id__in=dialog_interlocutor_ids
        ).select_related("user")
        contacts_map = {c.user_id: c for c in contacts_qs}

        last_message_ids = [
            chat.last_message_id for chat in chats_qs if chat.last_message_id
        ]
        messages_qs = (
            Message.objects.filter(id__in=last_message_ids)
            .select_related("user")
            .prefetch_related("file")
        )
        last_message_map = {m.chat_id: m for m in messages_qs}

        ct_chat = ContentType.objects.get_for_model(Chat).id
        ct_user = ContentType.objects.get_for_model(User).id
        ct_contact = ContentType.objects.get_for_model(Contact).id
        ct_profile = ContentType.objects.get_for_model(Profile).id

        object_tuples = []
        for chat in chats_qs:
            object_tuples.append((ct_chat, chat.id))
            if chat.is_dialog:
                for member in getattr(chat, "other_members", []):
                    interlocutor = member.user
                    object_tuples.append((ct_user, interlocutor.id))
                    profile = getattr(interlocutor, "profile", None)
                    if profile:
                        object_tuples.append((ct_profile, profile.id))
                    if interlocutor.id in contacts_map:
                        object_tuples.append(
                            (ct_contact, contacts_map[interlocutor.id].id)
                        )

        media_map = {}
        if object_tuples:
            ctype_ids, object_ids = zip(*object_tuples)
            media_qs = DisplayMedia.objects.filter(
                is_primary=True, content_type_id__in=ctype_ids, object_id__in=object_ids
            ).select_related("displayphoto", "displayvideo")
            media_map = {(dm.content_type_id, dm.object_id): dm for dm in media_qs}

        unread_map = dict(
            MessageRecipient.objects.filter(
                user=user, is_deleted=False, read_date__isnull=True
            )
            .exclude(message__user=user)
            .values("message__chat_id")
            .annotate(cnt=Count("id"))
            .values_list("message__chat_id", "cnt")
        )

        for chat in chats_qs:
            chat.last_message = last_message_map.get(chat.id)
            chat.unread_count = unread_map.get(chat.id, 0)

        serializer = ChatListSerializer(
            chats_qs,
            many=True,
            context={
                "request": request,
                "media_map": media_map,
                "contacts_map": contacts_map,
                "ct_contact_id": ct_contact,
                "ct_profile_id": ct_profile,
                "ct_chat_id": ct_chat,
                "interlocutors_map": {
                    chat.id: (
                        chat.other_members[0].user if chat.other_members else None
                    )
                    for chat in chats_qs
                    if chat.is_dialog
                },
            },
        )

        # print("--- SQL Queries ---")
        # for q in connection.queries:
        #     print(q["sql"])

        return Response({"chats": serializer.data}, status=200)


class GetChat(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, chat_id):
        try:
            chat = Chat.objects.get(id=chat_id)
            serializer = ChatSerializer(chat, context={"request": request})
            return Response({"chat": serializer.data}, status=200)
        except Chat.DoesNotExist:
            return Response({"error": "Chat not found"}, status=404)


class GetMessagesAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, *args, **kwargs):
        chat_id = kwargs.get("chat")
        cursor_id = request.GET.get("cursor_id")
        page_size = int(request.GET.get("page_size", 50))

        try:
            chat = Chat.objects.get(id=chat_id, users=request.user)
            recipients_prefetch = Prefetch(
                "recipients",
                queryset=MessageRecipient.objects.select_related("user").filter(
                    read_date__isnull=False
                ),
                to_attr="read_recipients",
            )

            messages_qs = (
                chat.messages.filter(is_deleted=False)
                .select_related("user", "user__profile", "reply_to")
                .prefetch_related("file", recipients_prefetch)
                .order_by("-date")
            )
            if cursor_id:
                messages_qs = messages_qs.filter(id__lt=cursor_id)

            messages = list(messages_qs[:page_size])
            messages.reverse()

            serializer = MessageSerializer(
                messages, many=True, context={"request": request}
            )

            next_cursor = messages[-1].id if messages else None
            return Response(
                {
                    "messages": serializer.data,
                    "next_cursor": next_cursor,
                }
            )

        except Chat.DoesNotExist:
            return Response({"error": "Chat not found"}, status=404)


class MessageReactionView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, message_id):
        try:
            message = Message.objects.get(id=message_id)
            reaction_type = request.data.get("reaction_type")

            valid_reactions = [choice[0] for choice in MessageReaction.REACTION_TYPES]
            if reaction_type not in valid_reactions and reaction_type is not None:
                return Response(
                    {"error": f"Invalid reaction type. Valid types: {valid_reactions}"},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            result = message.set_user_reaction(request.user, reaction_type)

            serializer = MessageSerializer(message, context={"request": request})
            return Response(
                {"success": True, "user_reaction": result, "message": serializer.data}
            )

        except Message.DoesNotExist:
            return Response(
                {"error": "Message not found"}, status=status.HTTP_404_NOT_FOUND
            )
        except Exception as e:
            logger.error(f"Error in MessageReactionView: {str(e)}")
            return Response(
                {"error": "Internal server error"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class ProtectedFileView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, file_id, version=None, format=None):
        try:
            file_obj = File.objects.get(id=file_id)
        except File.DoesNotExist:
            raise Http404("File not found")

        if not file_obj.messages.filter(chat__users=request.user).exists():
            return Response({"detail": "Forbidden"}, status=403)

        if isinstance(file_obj, ImageFile) and version in [
            "thumbnail_small",
            "thumbnail_medium",
        ]:
            file_field = getattr(file_obj, version)
            if not file_field:
                raise Http404("Thumbnail not found")
            file_path = os.path.join(settings.PROTECTED_MEDIA_ROOT, file_field.name)
        else:
            file_path = os.path.join(settings.PROTECTED_MEDIA_ROOT, file_obj.file.name)

        if not os.path.exists(file_path):
            raise Http404("File not found")

        return FileResponse(
            open(file_path, "rb"),
            as_attachment=False,
            filename=os.path.basename(file_path),
        )


class MessageViewSet(viewsets.ViewSet):
    permission_classes = [IsAuthenticated]

    def get_queryset(self, chat_id):
        return (
            Message.objects.filter(chat_id=chat_id, is_deleted=False)
            .select_related("user", "user__profile")
            .prefetch_related("file")
        )

    def retrieve(self, request, pk=None):
        try:
            chat_id = pk
            last_message = self.get_queryset(chat_id).order_by("-date").first()
            if not last_message:
                return Response({"message": None})
            serializer = MessageSerializer(last_message, context={"request": request})
            return Response({"message": serializer.data})
        except Exception as e:
            logger.error(f"MessageViewSet retrieve error: {str(e)}")
            return Response(
                {"error": "Internal server error"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    def create(self, request):
        try:
            files = request.FILES.getlist("file", [])
            message_text = request.POST.get("message", "")
            chat_id = int(request.POST.get("chat_id"))

            if not chat_id:
                return JsonResponse({"error": "Chat ID is required"}, status=400)

            user = request.user
            chat = Chat.objects.get(id=chat_id)

            if not chat.users.filter(id=user.id).exists():
                return JsonResponse({"error": "User not in chat"}, status=403)

            if not message_text and not files:
                return JsonResponse(
                    {"error": "Message or file is required"}, status=400
                )

            if message_text or files:
                new_message = Message.objects.create(
                    value=message_text, user=user, chat=chat
                )

                MAX_FILE_SIZE = 1024 * 1024 * 1024  # 1GB

                if files:
                    for uploaded_file in files:
                        if uploaded_file.size > MAX_FILE_SIZE:
                            continue

                        filename = protected_storage.save(
                            uploaded_file.name, uploaded_file
                        )

                        mime_type, _ = guess_type(uploaded_file.name)
                        if mime_type and mime_type.startswith("image/"):
                            new_file = ImageFile.objects.create(file=filename)
                        elif mime_type and mime_type.startswith("video/"):
                            new_file = VideoFile.objects.create(file=filename)
                        elif mime_type and mime_type.startswith('audio/'):
                            from apps.media_files.models import AudioFile
                            new_file = AudioFile.objects.create(file=filename)
                        else:
                            new_file = File.objects.create(file=filename)

                        new_message.file.add(new_file)

                new_message.save()

                channel_layer = get_channel_layer()

                serialized_message = MessageSerializer(
                    new_message, context={"request": request, "user_id": user.id}
                ).data

                user_ids = list(chat.users.values_list("id", flat=True))

                for user_id in user_ids:
                    async_to_sync(channel_layer.group_send)(
                        f"user_{user_id}",
                        {
                            "type": "chat_message",
                            "chat_id": chat_id,
                            "data": serialized_message,
                        },
                    )

                return JsonResponse(
                    {
                        "status": "success",
                        "message": "Message sent successfully",
                        "message_id": new_message.id,
                    }
                )

            return JsonResponse({"error": "No content to send"}, status=400)

        except Chat.DoesNotExist:
            return JsonResponse({"error": "Chat not found"}, status=404)
        except Exception as e:
            print(f"Error in send view: {e}")
            return JsonResponse({"error": "Server error"}, status=500)

    def update(self, request, pk=None):
        print("request")
        try:
            message = Message.objects.get(pk=pk, user=request.user)
            serializer = MessageSerializer(
                message, data=request.data, context={"request": request}
            )
            if serializer.is_valid():
                serializer.save()
                return Response(serializer.data)
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        except Message.DoesNotExist:
            return Response(
                {"error": "Message not found"}, status=status.HTTP_404_NOT_FOUND
            )

    def partial_update(self, request, pk=None):
        print("request")
        try:
            message = Message.objects.get(pk=pk, user=request.user)
            serializer = MessageSerializer(
                message, data=request.data, partial=True, context={"request": request}
            )
            if serializer.is_valid():
                serializer.save()
                return Response(serializer.data)
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        except Message.DoesNotExist:
            return Response(
                {"error": "Message not found"}, status=status.HTTP_404_NOT_FOUND
            )

    def destroy(self, request, pk=None):
        try:
            message = Message.objects.get(pk=pk, user=request.user)
            message.is_deleted = True
            message.save()
            return Response(status=status.HTTP_204_NO_CONTENT)
        except Message.DoesNotExist:
            return Response(
                {"error": "Message not found"}, status=status.HTTP_404_NOT_FOUND
            )


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def get_general_info(request):
    try:
        logger.info(f"Getting general info for user: {request.user.id}")

        user = (
            CustomUser.objects.select_related("profile")
            .prefetch_related(
                Prefetch(
                    "profile__profile_media",
                    queryset=DisplayPhoto.objects.all(),
                    to_attr="prefetched_photos",
                ),
                Prefetch(
                    "profile__profile_media",
                    queryset=DisplayVideo.objects.all(),
                    to_attr="prefetched_videos",
                ),
            )
            .filter(pk=request.user.pk)
            .first()
        )

        active_wallpaper = None
        if hasattr(request.user, "profile") and request.user.profile.active_wallpaper:
            active_wallpaper = WallpaperSerializer(
                request.user.profile.active_wallpaper, context={"request": request}
            ).data

        serializer = UserSerializer(user, context={"request": request})

        return Response(
            {
                "success": True,
                "user": serializer.data,
                "active_wallpaper": active_wallpaper,
            },
            status=status.HTTP_200_OK,
        )

    except Exception as e:
        logger.error(
            f"Error in get_general_info for user {request.user.id}: {str(e)}",
            exc_info=True,
        )

        return Response(
            {"success": False, "error": "Internal server error", "details": str(e)},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )


@api_view(["PUT", "PATCH"])
@permission_classes([IsAuthenticated])
def update_user_info(request):
    try:
        serializer = UserSerializer(request.user, data=request.data, partial=True)

        if serializer.is_valid():
            serializer.save()
            return Response({"success": True, "user": serializer.data})

        return Response(
            {"success": False, "errors": serializer.errors},
            status=status.HTTP_400_BAD_REQUEST,
        )

    except Exception as e:
        return Response(
            {"success": False, "error": str(e)},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )


@api_view(["GET"])
@permission_classes([IsAuthenticated])
class ChatListView(APIView):
    def get(self, request):
        chats = Chat.objects.annotate(
            last_message_content=Subquery(
                Message.objects.filter(chat=OuterRef("pk"))
                .order_by("-date")
                .values("value")[:1]
            ),
            last_message_date=Subquery(
                Message.objects.filter(chat=OuterRef("pk"))
                .order_by("-date")
                .values("date")[:1]
            ),
            unread_count=Count(
                "messages",
                filter=Q(messages__viewed=False) & ~Q(messages__user=request.user),
            ),
        ).values(
            "id", "name", "last_message_content", "last_message_date", "unread_count"
        )

        return Response(chats)


User = get_user_model()


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def GetContacts(request):
    try:
        contacts = (
            Contact.objects.filter(owner=request.user)
            .select_related("user", "user__profile")
            .prefetch_related(
                "display_media",
                "user__profile__profile_media",
            )
        )

        serializer = ContactSerializer(
            contacts, many=True, context={"request": request}
        )
        return Response({"contacts": serializer.data}, status=status.HTTP_200_OK)

    except Exception as e:
        logger.error(
            f"Error in GetContacts for user {request.user.id}: {str(e)}",
            exc_info=True,
        )
        return Response(
            {"error": "Internal server error"},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )


class UserEmailSearchView(APIView):
    def get(self, request):
        query = request.query_params.get("email", "").strip()

        if len(query) < 4:
            return Response(
                {"error": "Enter at least 4 characters"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        users = User.objects.filter(
            Q(email__istartswith=query) | Q(username__istartswith=query)
        )[:20]

        serializer = UserSerializer(users, many=True, context={"request": request})

        return Response(serializer.data, status=status.HTTP_200_OK)


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def protected_file(request, filename):
    filepath = os.path.join(settings.PROTECTED_MEDIA_ROOT, filename)
    if not os.path.exists(filepath):
        raise Http404("File not found")

    return FileResponse(open(filepath, "rb"))


class UserWallpapersAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        user_wallpapers = Wallpaper.objects.filter(
            userwallpaper__user=request.user
        ).distinct()

        serializer = WallpaperSerializer(
            user_wallpapers, many=True, context={"request": request}
        )
        return Response(serializer.data, status=status.HTTP_200_OK)

    def post(self, request):
        serializer = WallpaperSerializer(
            data=request.data, context={"request": request}
        )

        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        try:
            with transaction.atomic():
                wallpaper = serializer.save()
                UserWallpaper.objects.create(user=request.user, wallpaper=wallpaper)

            try:
                channel_layer = get_channel_layer()
                user_id = request.user.id

                file_url = wallpaper.file.url if wallpaper.file else None
                if file_url:
                    file_url = request.build_absolute_uri(file_url)

                async_to_sync(channel_layer.group_send)(
                    f"user_{user_id}",
                    {
                        "type": "user_wallpaper_added",
                        "data": {
                            "id": wallpaper.id,
                            "file_url": file_url,
                            "type": "photo",
                        },
                    },
                )
            except Exception as e:
                print(f"Channels error: {e}")

            return Response(
                {
                    "id": wallpaper.id,
                    "file_url": file_url,
                },
                status=status.HTTP_201_CREATED,
            )

        except Exception as e:
            return Response(
                {"detail": "Internal server error", "error": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
