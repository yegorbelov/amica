"""
Chunked upload for large message attachments (videos).
Sessions are finalized in one bundle POST so a single Message can include multiple files.
"""
from __future__ import annotations

import json
import logging
import math
import os
import shutil
import uuid
from typing import Optional, Tuple

from django.conf import settings
from django.core.files import File as DjangoFile
from django.core.files.storage import FileSystemStorage
from django.http import JsonResponse
from mimetypes import guess_type
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView

from apps.media_files.models.models import File, ImageFile, VideoFile

from .models import Chat, Message

logger = logging.getLogger(__name__)

CHUNK_UPLOAD_SUBDIR = "chunk_uploads"
SERVER_CHUNK_SIZE = 4 * 1024 * 1024  # 4 MiB
MAX_FILE_SIZE = 1024 * 1024 * 1024  # 1 GiB, same as MessageViewSet.create

protected_storage = FileSystemStorage(location=settings.PROTECTED_MEDIA_ROOT)


def _session_dir(upload_id: str) -> str:
    return os.path.join(
        settings.FILE_UPLOAD_TEMP_DIR, CHUNK_UPLOAD_SUBDIR, upload_id
    )


def _meta_path(upload_id: str) -> str:
    return os.path.join(_session_dir(upload_id), "meta.json")


def _session_chunks_complete(upload_id: str, user_id: int, chat_id: int) -> bool:
    meta_path = _meta_path(upload_id)
    if not os.path.isfile(meta_path):
        return False
    with open(meta_path, encoding="utf-8") as f:
        meta = json.load(f)
    if meta.get("user_id") != user_id or int(meta.get("chat_id") or 0) != chat_id:
        return False
    base = _session_dir(upload_id)
    n = int(meta.get("chunk_count") or 0)
    for i in range(n):
        if not os.path.isfile(os.path.join(base, f"{i}.part")):
            return False
    return True


class MessageChunkInitView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        try:
            data = json.loads(request.body.decode("utf-8"))
        except json.JSONDecodeError:
            return JsonResponse({"error": "Invalid JSON"}, status=400)

        chat_id = int(data.get("chat_id") or 0)
        filename = (data.get("filename") or "file").replace("\\", "/").split("/")[-1]
        total_size = int(data.get("total_size") or 0)

        if not chat_id or total_size <= 0 or total_size > MAX_FILE_SIZE:
            return JsonResponse({"error": "Invalid chat_id or size"}, status=400)

        user = request.user
        try:
            chat = Chat.objects.get(id=chat_id)
        except Chat.DoesNotExist:
            return JsonResponse({"error": "Chat not found"}, status=404)

        if not chat.users.filter(id=user.id).exists():
            return JsonResponse({"error": "User not in chat"}, status=403)

        upload_id = str(uuid.uuid4())
        chunk_count = max(1, math.ceil(total_size / SERVER_CHUNK_SIZE))

        os.makedirs(_session_dir(upload_id), exist_ok=True)
        meta = {
            "user_id": user.id,
            "chat_id": chat_id,
            "filename": filename,
            "total_size": total_size,
            "chunk_count": chunk_count,
            "chunk_size": SERVER_CHUNK_SIZE,
        }
        with open(_meta_path(upload_id), "w", encoding="utf-8") as f:
            json.dump(meta, f)

        return JsonResponse(
            {
                "upload_id": upload_id,
                "chunk_count": chunk_count,
                "chunk_size": SERVER_CHUNK_SIZE,
            }
        )


class MessageChunkPartView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        upload_id = request.POST.get("upload_id")
        try:
            chunk_index = int(request.POST.get("chunk_index", "-1"))
        except ValueError:
            return JsonResponse({"error": "Invalid chunk_index"}, status=400)

        chunk_file = request.FILES.get("chunk") or request.FILES.get("file")
        if not upload_id or chunk_file is None:
            return JsonResponse({"error": "upload_id and chunk required"}, status=400)

        meta_path = _meta_path(upload_id)
        if not os.path.isfile(meta_path):
            return JsonResponse({"error": "Unknown upload_id"}, status=404)

        with open(meta_path, encoding="utf-8") as f:
            meta = json.load(f)

        if meta["user_id"] != request.user.id:
            return JsonResponse({"error": "Forbidden"}, status=403)

        if chunk_index < 0 or chunk_index >= meta["chunk_count"]:
            return JsonResponse({"error": "chunk_index out of range"}, status=400)

        part_path = os.path.join(_session_dir(upload_id), f"{chunk_index}.part")
        with open(part_path, "wb") as out:
            for chunk in chunk_file.chunks():
                out.write(chunk)

        return JsonResponse({"ok": True, "chunk_index": chunk_index})


def _merge_session_to_storage(
    upload_id: str, user_id: int, chat_id: int
) -> Optional[Tuple[str, str]]:
    """Returns (storage_path, original_filename) or None."""
    meta_path = _meta_path(upload_id)
    if not os.path.isfile(meta_path):
        return None

    with open(meta_path, encoding="utf-8") as f:
        meta = json.load(f)

    if meta.get("user_id") != user_id or int(meta.get("chat_id") or 0) != chat_id:
        return None

    base = _session_dir(upload_id)
    chunk_count = meta["chunk_count"]
    total_size = meta["total_size"]
    original_name = meta["filename"]

    merged_tmp = os.path.join(base, "_merged.bin")
    written = 0
    with open(merged_tmp, "wb") as out:
        for i in range(chunk_count):
            part_path = os.path.join(base, f"{i}.part")
            if not os.path.isfile(part_path):
                return None
            with open(part_path, "rb") as inp:
                blob = inp.read()
                out.write(blob)
                written += len(blob)

    if written != total_size:
        logger.warning(
            "chunk merge size mismatch upload_id=%s expected=%s got=%s",
            upload_id,
            total_size,
            written,
        )
        try:
            os.remove(merged_tmp)
        except OSError:
            pass
        return None

    with open(merged_tmp, "rb") as fh:
        storage_name = protected_storage.save(original_name, DjangoFile(fh))

    try:
        os.remove(merged_tmp)
    except OSError:
        pass

    try:
        shutil.rmtree(base, ignore_errors=True)
    except Exception:
        pass

    return storage_name, original_name


def _attach_storage_file_to_message(new_message, user, filename: str, original_name: str):
    """Mirror MessageViewSet.create file branch (filename = path from protected_storage.save)."""
    mime_type, _ = guess_type(original_name)
    needs_processing = False

    if mime_type and mime_type.startswith("image/"):
        from apps.media_files.tasks.audio_waveform import process_image_task

        needs_processing = True
        new_file = ImageFile(file=filename)
        new_file.save(process_media=False)
        process_image_task.delay(
            imagefile_id=new_file.id,
            message_id=new_message.id,
            user_id=user.id,
        )
    elif mime_type and mime_type.startswith("video/"):
        from apps.media_files.tasks.audio_waveform import process_video_task

        needs_processing = True
        new_file = VideoFile(file=filename)
        new_file.save(process_media=False)
        process_video_task.delay(
            videofile_id=new_file.id,
            message_id=new_message.id,
            user_id=user.id,
        )
    elif mime_type and mime_type.startswith("audio/"):
        from apps.media_files.models import AudioFile
        from apps.media_files.tasks.audio_waveform import process_audio_task

        needs_processing = True
        new_file = AudioFile.objects.create(file=filename)
        process_audio_task.delay(
            audiofile_id=new_file.id,
            message_id=new_message.id,
            user_id=user.id,
        )
    else:
        new_file = File.objects.create(file=filename)

    new_message.file.add(new_file)
    return needs_processing


class MessageChunkBundleCompleteView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        try:
            data = json.loads(request.body.decode("utf-8"))
        except json.JSONDecodeError:
            return JsonResponse({"error": "Invalid JSON"}, status=400)

        chat_id = int(data.get("chat_id") or 0)
        message_text = data.get("message") or ""
        upload_ids = data.get("upload_ids") or []

        if not chat_id:
            return JsonResponse({"error": "chat_id required"}, status=400)

        if not isinstance(upload_ids, list) or not upload_ids:
            return JsonResponse({"error": "upload_ids required"}, status=400)

        user = request.user
        try:
            chat = Chat.objects.get(id=chat_id)
        except Chat.DoesNotExist:
            return JsonResponse({"error": "Chat not found"}, status=404)

        if not chat.users.filter(id=user.id).exists():
            return JsonResponse({"error": "User not in chat"}, status=403)

        if not message_text and not upload_ids:
            return JsonResponse({"error": "Message or files required"}, status=400)

        try:
            normalized_ids = [u for u in upload_ids if isinstance(u, str)]
            for uid in normalized_ids:
                if not _session_chunks_complete(uid, user.id, chat_id):
                    return JsonResponse(
                        {"error": f"Incomplete or invalid upload session: {uid}"},
                        status=400,
                    )

            merged_files: list[Tuple[str, str]] = []
            for uid in normalized_ids:
                merged = _merge_session_to_storage(uid, user.id, chat_id)
                if not merged:
                    for path, _ in merged_files:
                        try:
                            protected_storage.delete(path)
                        except Exception:
                            pass
                    return JsonResponse(
                        {"error": f"Failed to assemble upload {uid}"}, status=400
                    )
                merged_files.append(merged)

            new_message = Message.objects.create(
                value=message_text, user=user, chat=chat
            )
            any_processing = False
            for storage_path, orig_name in merged_files:
                any_processing = any_processing or _attach_storage_file_to_message(
                    new_message, user, storage_path, orig_name
                )

            new_message.save()

            if not any_processing:
                from apps.Site.services.ws_sender import send_ws_message

                send_ws_message(new_message, user.id)

            return JsonResponse(
                {
                    "status": "success",
                    "message": "Message sent successfully",
                    "message_id": new_message.id,
                }
            )
        except Exception as e:
            logger.exception("MessageChunkBundleCompleteView: %s", e)
            return JsonResponse({"error": "Server error"}, status=500)
