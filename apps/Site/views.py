import json
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
from .services.get_chats_service import get_chats_list
from .services.get_chat_service import get_chat_for_user
from .services.get_general_info_service import get_general_info_for_user
from .services.create_group_service import create_group_and_serialize
from .services.search_groups_service import search_groups_globally_for_user
from .utils import *

protected_storage = FileSystemStorage(location=settings.PROTECTED_MEDIA_ROOT)


logger = logging.getLogger(__name__)


class GetChats(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        data = get_chats_list(request.user)
        return Response(data, status=200)


from rest_framework.response import Response
from rest_framework.views import APIView


class GetChat(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, chat_id):
        cursor = request.GET.get("cursor")
        if cursor is not None:
            try:
                cursor = int(cursor)
            except (TypeError, ValueError):
                cursor = None
        cursor_newer = request.GET.get("cursor_newer")
        if cursor_newer is not None:
            try:
                cursor_newer = int(cursor_newer)
            except (TypeError, ValueError):
                cursor_newer = None
        page_size = request.GET.get("page_size")
        if page_size is not None:
            try:
                page_size = int(page_size)
            except (TypeError, ValueError):
                page_size = 25
        else:
            page_size = 25
        try:
            response_data = get_chat_for_user(
                chat_id,
                request.user,
                cursor=cursor,
                cursor_newer=cursor_newer,
                page_size=page_size,
            )
            response = Response(response_data, status=200)
            response["Content-Security-Policy"] = (
                "default-src 'self'; "
                "script-src 'self'; "
                "style-src 'self' 'unsafe-inline'; "
                "img-src 'self' data: blob:; "
                "connect-src 'self' wss://* ws://*; "
                "object-src 'none'; "
                "frame-ancestors 'none'; "
                "base-uri 'self'; "
                "form-action 'self';"
            )
            return response
        except Chat.DoesNotExist:
            response = Response({"error": "Chat not found"}, status=404)
            response["Content-Security-Policy"] = (
                "default-src 'self'; script-src 'self'; object-src 'none';"
            )
            return response


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
                chat.messages.filter(deleted_at__isnull=True)
                .select_related("user", "user__profile", "reply_to")
                .prefetch_related("file", recipients_prefetch, "message_reactions")
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
            if reaction_type not in valid_reactions:
                return Response(
                    {"error": f"Invalid reaction type. Valid types: {valid_reactions}"},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            result = message.set_user_reaction(request.user, reaction_type)

            serializer = MessageSerializer(message, context={"request": request})
            return Response(
                {"success": True, "user_reactions": result, "message": serializer.data}
            )

        except Message.DoesNotExist:
            return Response(
                {"error": "Message not found"}, status=status.HTTP_404_NOT_FOUND
            )
        except ValueError as e:
            return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)
        except Exception as e:
            logger.error(f"Error in MessageReactionView: {str(e)}")
            return Response(
                {"error": "Internal server error"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


from django.http import StreamingHttpResponse, Http404, HttpResponse
import re

import logging
from rest_framework_simplejwt.authentication import JWTAuthentication


class ProtectedFileView(APIView):
    permission_classes = [IsAuthenticated]

    def get_file_path_and_type(self, file_obj, version=None):

        if isinstance(file_obj, DisplayPhoto):
            if version in ("thumbnail_small", "thumbnail_medium"):
                file_field = getattr(file_obj, version, None)
                if not file_field:
                    raise Http404("Thumbnail not found")

                return (
                    os.path.join(settings.PROTECTED_MEDIA_ROOT, file_field.name),
                    "image/webp",
                )

            if not file_obj.image:
                raise Http404("Image not found")

            return (
                os.path.join(settings.PROTECTED_MEDIA_ROOT, file_obj.image.name),
                "image/jpeg",
            )

        if isinstance(file_obj, DisplayVideo):
            if version == "preview" and getattr(file_obj, "preview", None):
                return (
                    os.path.join(settings.PROTECTED_MEDIA_ROOT, file_obj.preview.name),
                    "image/jpeg",
                )
            return (
                os.path.join(settings.PROTECTED_MEDIA_ROOT, file_obj.video.name),
                "video/mp4",
            )

        if isinstance(file_obj, ImageFile) and version in [
            "thumbnail_small",
            "thumbnail_medium",
        ]:
            file_field = getattr(file_obj, version)
            if not file_field:
                raise Http404("Thumbnail not found")
            return (
                os.path.join(settings.PROTECTED_MEDIA_ROOT, file_field.name),
                "image/webp",
            )
        elif isinstance(file_obj, AudioFile) and version == "cover":
            if not file_obj.cover:
                raise Http404("Cover not found")
            return (
                os.path.join(settings.PROTECTED_MEDIA_ROOT, file_obj.cover.name),
                "image/jpeg",
            )
        else:
            path = os.path.join(settings.PROTECTED_MEDIA_ROOT, file_obj.file.name)
            if isinstance(file_obj, AudioFile):
                return path, "audio/mpeg"
            elif isinstance(file_obj, VideoFile):
                return path, "video/mp4"
            return path, "application/octet-stream"

    def get(self, request, file_id, version=None, file_type=None, format=None):
        if file_type == "display_photo":
            try:
                file_obj = DisplayPhoto.objects.get(id=file_id)
            except DisplayPhoto.DoesNotExist:
                raise Http404("File not found")
        else:
            try:
                file_obj = File.objects.get(id=file_id)
            except File.DoesNotExist:
                try:
                    file_obj = DisplayPhoto.objects.get(id=file_id)
                except DisplayPhoto.DoesNotExist:
                    try:
                        file_obj = DisplayVideo.objects.get(id=file_id)
                    except DisplayVideo.DoesNotExist:
                        raise Http404("File not found")

        if hasattr(file_obj, "messages"):
            if not file_obj.messages.filter(chat__users=request.user).exists():
                return Response({"detail": "Forbidden"}, status=403)

        file_path, content_type = self.get_file_path_and_type(file_obj, version)

        if not os.path.exists(file_path):
            raise Http404("File not found")

        file_size = os.path.getsize(file_path)

        range_header = request.headers.get("Range", "").strip()
        range_match = re.match(r"bytes=(\d+)-(\d*)", range_header)

        start = 0
        end = file_size - 1

        if range_match:
            start = int(range_match.group(1))
            if range_match.group(2):
                end = int(range_match.group(2))

        if start >= file_size:
            response = HttpResponse(status=416)
            response["Content-Range"] = f"bytes */{file_size}"
            return response

        end = min(end, file_size - 1)
        length = end - start + 1
        chunk_size = 1024 * 512

        def file_iterator(path, offset=0, length=None, chunk_size=chunk_size):
            with open(path, "rb") as f:
                f.seek(offset)
                remaining = length
                while remaining > 0:
                    read_size = min(chunk_size, remaining)
                    data = f.read(read_size)
                    if not data:
                        break
                    remaining -= len(data)
                    yield data

        response = StreamingHttpResponse(
            file_iterator(file_path, offset=start, length=length),
            status=206 if range_match else 200,
            content_type=content_type,
        )
        response["Content-Length"] = str(length)
        response["Accept-Ranges"] = "bytes"
        response["Cache-Control"] = "private, max-age=3600"
        response["X-Content-Type-Options"] = "nosniff"

        if range_match:
            response["Content-Range"] = f"bytes {start}-{end}/{file_size}"

        return response


class MessageViewSet(viewsets.ViewSet):
    permission_classes = [IsAuthenticated]

    def get_queryset(self, chat_id):
        return (
            Message.objects.filter(chat_id=chat_id, deleted_at__isnull=True)
            .select_related("user", "user__profile")
            .prefetch_related("file", "message_reactions")
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
                
                needs_processing = False
                created_audio_file = None

                if files:
                    for uploaded_file in files:
                        if uploaded_file.size > MAX_FILE_SIZE:
                            continue

                        filename = protected_storage.save(
                            uploaded_file.name, uploaded_file
                        )

                        mime_type, _ = guess_type(uploaded_file.name)
                        resolved_mime = (mime_type or "").strip().lower()
                        ext = os.path.splitext((uploaded_file.name or "").lower())[1]
                        is_video = resolved_mime.startswith("video/") or ext in {
                            ".mp4",
                            ".mov",
                            ".avi",
                            ".webm",
                            ".mkv",
                            ".mpeg",
                            ".flv",
                            ".m4v",
                            ".ts",
                            ".vob",
                            ".3gp",
                            ".3g2",
                            ".m4p",
                            ".m4b",
                            ".m4r",
                        }
                        is_image = resolved_mime.startswith("image/")
                        is_audio = resolved_mime.startswith("audio/") or ext in {
                            ".mp3",
                            ".wav",
                            ".ogg",
                            ".flac",
                            ".m4a",
                            ".aac",
                            ".wma",
                        }

                        if is_image:
                            from apps.media_files.tasks.audio_waveform import (
                                process_image_task,
                            )

                            needs_processing = True
                            new_file = ImageFile(file=filename)
                            # Populate image metadata/thumbnails immediately so prod
                            # does not depend on Celery timing/worker file access.
                            new_file.save(process_media=True)
                        elif is_video:
                            from apps.media_files.tasks.audio_waveform import (
                                process_video_task,
                            )

                            needs_processing = True
                            new_file = VideoFile(file=filename)
                            # Populate width/height immediately so prod does not
                            # depend on Celery timing/worker file access.
                            new_file.save(process_media=True)
                        elif is_audio:
                            from apps.media_files.models import AudioFile
                            from apps.media_files.tasks.audio_waveform import process_audio_task
                            needs_processing = True
                            new_file = AudioFile.objects.create(file=filename)
                        else:
                            new_file = File.objects.create(file=filename)
                        new_message.file.add(new_file)
                        if is_video:
                            process_video_task.delay(
                                videofile_id=new_file.id,
                                message_id=new_message.id,
                                user_id=user.id,
                            )
                        elif is_audio:
                            process_audio_task.delay(
                                audiofile_id=new_file.id,
                                message_id=new_message.id,
                                user_id=user.id
                            )

                new_message.save()
                
                from apps.Site.services.ws_sender import send_ws_message
                send_ws_message(new_message, user.id)

                # channel_layer = get_channel_layer()

                # serialized_message = MessageSerializer(
                #     new_message, context={"request": request, "user_id": user.id}
                # ).data

                # user_ids = list(chat.users.values_list("id", flat=True))

                # for user_id in user_ids:
                #     async_to_sync(channel_layer.group_send)(
                #         f"user_{user_id}",
                #         {
                #             "type": "chat_message",
                #             "chat_id": chat_id,
                #             "data": serialized_message,
                #         },
                #     )

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
            logger.error(f"Error in send view: {e}")
            return JsonResponse({"error": "Server error"}, status=500)

    def update(self, request, pk=None):
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
            chat_id = message.chat_id
            message.deleted_at = timezone.now()
            message.save()
            from .services.ws_sender import send_ws_message_deleted
            send_ws_message_deleted(chat_id, message.id)
            return Response(status=status.HTTP_204_NO_CONTENT)
        except Message.DoesNotExist:
            return Response(
                {"error": "Message not found"}, status=status.HTTP_404_NOT_FOUND
            )


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def get_general_info(request):
    logger.info("Getting general info", extra={"user_id": request.user.id})
    try:
        data = get_general_info_for_user(request.user)
        if not data.get("success"):
            return Response(
                {"success": False, "error": data.get("error", "Unknown")},
                status=status.HTTP_404_NOT_FOUND,
            )
        return Response(data, status=status.HTTP_200_OK)
    except Exception:
        logger.exception(
            "Error in get_general_info",
            extra={"user_id": request.user.id},
        )
        return Response(
            {"success": False, "error": "Internal server error"},
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
                filter=Q(messages__viewed=False, messages__deleted_at__isnull=True) & ~Q(messages__user=request.user),
            ),
        ).values(
            "id", "name", "last_message_content", "last_message_date", "unread_count"
        )

        return Response(chats)


User = get_user_model()


class UserEmailSearchView(APIView):
    def get(self, request):
        query = request.query_params.get("email", "").strip()

        if len(query) < 4:
            return Response(
                {"error": "Enter at least 4 characters"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        users = list(
            User.objects.filter(
                Q(email__istartswith=query) | Q(username__istartswith=query)
            )[:20]
        )

        peer_ids = [u.id for u in users if u.id != request.user.id]
        dm_chat_by_peer_id = {}
        if peer_ids:
            my_chat_ids = ChatMember.objects.filter(user=request.user).values_list(
                "chat_id", flat=True
            )
            for cm in ChatMember.objects.filter(
                user_id__in=peer_ids, chat_id__in=my_chat_ids
            ).select_related("chat"):
                if cm.chat.chat_type == Chat.ChatType.DIALOG:
                    dm_chat_by_peer_id[cm.user_id] = cm.chat_id

        serializer = UserSerializer(
            users,
            many=True,
            context={
                "request": request,
                "dm_chat_by_peer_id": dm_chat_by_peer_id,
            },
        )

        return Response(serializer.data, status=status.HTTP_200_OK)


class GroupSearchView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        q = request.query_params.get("q", "").strip()
        if len(q) < 1:
            return Response(
                {"error": "Enter at least 1 character"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            limit = int(request.query_params.get("limit", 40))
        except (TypeError, ValueError):
            limit = 40
        data = search_groups_globally_for_user(request.user, q, limit=limit)
        return Response(data, status=status.HTTP_200_OK)


class CreateGroupView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        raw = request.data.get("name")
        if raw is None:
            return Response(
                {"error": "name is required"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        name = str(raw).strip()
        try:
            serialized = create_group_and_serialize(request.user, name)
        except ValueError as e:
            return Response(
                {"error": str(e)},
                status=status.HTTP_400_BAD_REQUEST,
            )
        except Exception:
            logger.exception("CreateGroupView failed")
            return Response(
                {"error": "Failed to create group"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        try:
            channel_layer = get_channel_layer()
            payload_safe = json.loads(json.dumps({"chat": serialized}, default=str))
            async_to_sync(channel_layer.group_send)(
                f"user_{request.user.id}",
                {"type": "chat_created", **payload_safe},
            )
        except Exception as e:
            logger.error("Channels error on create group: %s", e)

        return Response({"chat": serialized}, status=status.HTTP_201_CREATED)


class JoinGroupView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, chat_id):
        try:
            chat = Chat.objects.get(
                id=chat_id, chat_type=Chat.ChatType.GROUP
            )
        except Chat.DoesNotExist:
            return Response(
                {"error": "Group not found"},
                status=status.HTTP_404_NOT_FOUND,
            )
        ChatMember.objects.get_or_create(
            chat=chat,
            user=request.user,
            defaults={"role": ChatMember.Role.MEMBER},
        )
        return Response({"ok": True}, status=status.HTTP_200_OK)


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
                            "url": file_url,
                            "type": wallpaper.type,
                        },
                    },
                )
            except Exception as e:
                logger.error(f"Channels error: {e}")

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
            
from django.shortcuts import get_object_or_404

class ContactAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        owner = request.user
        user_id = request.data.get("user_id")

        if not user_id:
            return Response(
                {"detail": "user_id is required"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        user = get_object_or_404(CustomUser, id=user_id)

        if user == owner:
            return Response(
                {"detail": "You cannot add yourself to contacts"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            contact, created = Contact.objects.get_or_create(
                owner=owner,
                user=user,
            )
        except IntegrityError:
            return Response(
                {"detail": "Contact already exists"},
                status=status.HTTP_409_CONFLICT,
            )

        return Response(
            {
                "created": created,
                "contact_id": contact.id,
                "name": contact.name,
            },
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )

    def delete(self, request):
        contact_id = request.data.get("contact_id")

        if not contact_id:
            return Response(
                {"detail": "contact_id is required"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        contact = get_object_or_404(Contact, id=contact_id)
        contact.delete()

        return Response(status=status.HTTP_204_NO_CONTENT)

    def patch(self, request):
        contact_id = request.data.get("contact_id")
        new_name = request.data.get("name")

        if not contact_id or new_name is None:
            return Response(
                {"detail": "contact_id and name are required"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        contact = get_object_or_404(Contact, id=contact_id)

        if contact.owner != request.user:
            return Response(
                {"detail": "You do not have permission to edit this contact"},
                status=status.HTTP_403_FORBIDDEN,
            )

        contact.name = new_name
        contact.save()

        return Response(
            {
                "contact_id": contact.id,
                "name": contact.name,
            },
            status=status.HTTP_200_OK,
        )
