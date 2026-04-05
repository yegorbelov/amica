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
from concurrent.futures import ThreadPoolExecutor, as_completed
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
# WebSocket uses binary frames; larger chunks reduce round-trip overhead.
WS_SERVER_CHUNK_SIZE = 2 * 1024 * 1024  # 2 MiB
MIN_CLIENT_CHUNK_SIZE = 64 * 1024  # 64 KiB
MAX_CLIENT_CHUNK_SIZE = SERVER_CHUNK_SIZE
MAX_FILE_SIZE = 1024 * 1024 * 1024  # 1 GiB, same as MessageViewSet.create
MERGE_COPY_BUFFER_BYTES = 1024 * 1024
MERGE_MAX_WORKERS = 4

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


def chunk_init_service(
    user,
    chat_id: int,
    filename: str,
    mime_type: str,
    media_kind: str,
    total_size: int,
    *,
    chunk_size: Optional[int] = None,
) -> dict:
    """
    Shared by HTTP and WebSocket. Returns ok + fields or ok=False + error + http_status.
    """
    use_chunk = chunk_size if chunk_size is not None else SERVER_CHUNK_SIZE
    if use_chunk < MIN_CLIENT_CHUNK_SIZE or use_chunk > MAX_CLIENT_CHUNK_SIZE:
        return {"ok": False, "error": "Invalid chunk_size", "http_status": 400}

    if not chat_id or total_size <= 0 or total_size > MAX_FILE_SIZE:
        return {"ok": False, "error": "Invalid chat_id or size", "http_status": 400}

    try:
        chat = Chat.objects.get(id=chat_id)
    except Chat.DoesNotExist:
        return {"ok": False, "error": "Chat not found", "http_status": 404}

    if not chat.users.filter(id=user.id).exists():
        return {"ok": False, "error": "User not in chat", "http_status": 403}

    upload_id = str(uuid.uuid4())
    chunk_count = max(1, math.ceil(total_size / use_chunk))

    os.makedirs(_session_dir(upload_id), exist_ok=True)
    meta = {
        "user_id": user.id,
        "chat_id": chat_id,
        "filename": filename,
        "mime_type": mime_type,
        "media_kind": media_kind,
        "total_size": total_size,
        "chunk_count": chunk_count,
        "chunk_size": use_chunk,
    }
    with open(_meta_path(upload_id), "w", encoding="utf-8") as f:
        json.dump(meta, f)

    return {
        "ok": True,
        "upload_id": upload_id,
        "chunk_count": chunk_count,
        "chunk_size": use_chunk,
    }


class MessageChunkInitView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        try:
            data = json.loads(request.body.decode("utf-8"))
        except json.JSONDecodeError:
            return JsonResponse({"error": "Invalid JSON"}, status=400)

        chat_id = int(data.get("chat_id") or 0)
        filename = (data.get("filename") or "file").replace("\\", "/").split("/")[-1]
        mime_type = (data.get("mime_type") or "").strip().lower()
        media_kind = (data.get("media_kind") or "").strip().lower()
        total_size = int(data.get("total_size") or 0)
        raw_chunk = data.get("chunk_size")
        try:
            requested_chunk = int(raw_chunk) if raw_chunk is not None else None
        except (TypeError, ValueError):
            return JsonResponse({"error": "Invalid chunk_size"}, status=400)

        result = chunk_init_service(
            request.user,
            chat_id,
            filename,
            mime_type,
            media_kind,
            total_size,
            chunk_size=requested_chunk,
        )
        if not result.get("ok"):
            return JsonResponse(
                {"error": result.get("error", "error")},
                status=int(result.get("http_status") or 400),
            )

        return JsonResponse(
            {
                "upload_id": result["upload_id"],
                "chunk_count": result["chunk_count"],
                "chunk_size": result["chunk_size"],
            }
        )


def chunk_part_service(
    user, upload_id: str, chunk_index: int, raw_bytes: bytes
) -> dict:
    if not upload_id:
        return {"ok": False, "error": "upload_id and chunk required", "http_status": 400}
    if raw_bytes is None:
        raw_bytes = b""

    meta_path = _meta_path(upload_id)
    if not os.path.isfile(meta_path):
        return {"ok": False, "error": "Unknown upload_id", "http_status": 404}

    with open(meta_path, encoding="utf-8") as f:
        meta = json.load(f)

    if meta["user_id"] != user.id:
        return {"ok": False, "error": "Forbidden", "http_status": 403}

    if chunk_index < 0 or chunk_index >= meta["chunk_count"]:
        return {"ok": False, "error": "chunk_index out of range", "http_status": 400}

    part_path = os.path.join(_session_dir(upload_id), f"{chunk_index}.part")
    with open(part_path, "wb") as out:
        out.write(raw_bytes)

    return {"ok": True, "chunk_index": chunk_index}


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

        raw = chunk_file.read()
        result = chunk_part_service(request.user, upload_id, chunk_index, raw)
        if not result.get("ok"):
            return JsonResponse(
                {"error": result.get("error", "error")},
                status=int(result.get("http_status") or 400),
            )

        return JsonResponse({"ok": True, "chunk_index": result["chunk_index"]})


def _merge_session_to_storage(
    upload_id: str, user_id: int, chat_id: int
) -> Optional[Tuple[str, str, str, str]]:
    """Returns (storage_path, original_filename, mime_type, media_kind) or None."""
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
    mime_type = (meta.get("mime_type") or "").strip().lower()
    media_kind = (meta.get("media_kind") or "").strip().lower()

    merged_tmp = os.path.join(base, "_merged.bin")
    written = 0
    with open(merged_tmp, "wb") as out:
        for i in range(chunk_count):
            part_path = os.path.join(base, f"{i}.part")
            if not os.path.isfile(part_path):
                return None
            part_size = os.path.getsize(part_path)
            with open(part_path, "rb") as inp:
                shutil.copyfileobj(inp, out, length=MERGE_COPY_BUFFER_BYTES)
                written += part_size

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

    return storage_name, original_name, mime_type, media_kind


def _attach_storage_file_to_message(
    new_message,
    user,
    filename: str,
    original_name: str,
    mime_type: str = "",
    media_kind: str = "",
):
    """Attach file model quickly; heavy metadata/thumbnail work runs in background tasks."""
    resolved_mime = (mime_type or "").strip().lower()
    if not resolved_mime:
        guessed_mime, _ = guess_type(original_name)
        resolved_mime = (guessed_mime or "").strip().lower()
    # Some browsers/platforms may send empty/unknown mime for videos.
    # Keep classification stable by falling back to extension.
    ext = os.path.splitext((original_name or "").lower())[1]
    declared_kind = (media_kind or "").strip().lower()
    is_video = declared_kind == "video" or resolved_mime.startswith("video/") or ext in {
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
    is_image = declared_kind == "image" or resolved_mime.startswith("image/")
    is_audio = declared_kind == "audio" or resolved_mime.startswith("audio/") or ext in {
        ".mp3",
        ".wav",
        ".ogg",
        ".flac",
        ".m4a",
        ".aac",
        ".wma",
    }
    needs_processing = False

    if is_image:
        from apps.media_files.tasks.audio_waveform import process_image_task

        needs_processing = True
        new_file = ImageFile(file=filename)
        # Fast-path complete: defer metadata + thumbnails to background.
        new_file.save(process_media=False)
    elif is_video:
        from apps.media_files.tasks.audio_waveform import process_video_task

        needs_processing = True
        new_file = VideoFile(file=filename)
        # Fast-path complete: defer metadata extraction to background.
        new_file.save(process_media=False)
    elif is_audio:
        from apps.media_files.models import AudioFile
        from apps.media_files.tasks.audio_waveform import process_audio_task

        needs_processing = True
        new_file = AudioFile.objects.create(file=filename)
    else:
        new_file = File.objects.create(file=filename)

    new_message.file.add(new_file)
    # print(
    #     "CHUNK_ATTACH",
    #     {
    #         "message_id": new_message.id,
    #         "file_id": getattr(new_file, "id", None),
    #         "name": original_name,
    #         "mime": resolved_mime,
    #         "ext": ext,
    #         "video": is_video,
    #         "image": is_image,
    #         "audio": is_audio,
    #     },
    #     flush=True,
    # )
    if is_image:
        process_image_task.delay(
            imagefile_id=new_file.id,
            message_id=new_message.id,
            user_id=user.id,
        )
    elif is_video:
        process_video_task.delay(
            videofile_id=new_file.id,
            message_id=new_message.id,
            user_id=user.id,
        )
    elif is_audio:
        process_audio_task.delay(
            audiofile_id=new_file.id,
            message_id=new_message.id,
            user_id=user.id,
        )
    return needs_processing


def chunk_bundle_complete_service(
    user, chat_id: int, message_text: str, upload_ids: list
) -> dict:
    if not chat_id:
        return {"ok": False, "error": "chat_id required", "http_status": 400}

    if not isinstance(upload_ids, list) or not upload_ids:
        return {"ok": False, "error": "upload_ids required", "http_status": 400}

    try:
        chat = Chat.objects.get(id=chat_id)
    except Chat.DoesNotExist:
        return {"ok": False, "error": "Chat not found", "http_status": 404}

    if not chat.users.filter(id=user.id).exists():
        return {"ok": False, "error": "User not in chat", "http_status": 403}

    if not message_text and not upload_ids:
        return {"ok": False, "error": "Message or files required", "http_status": 400}

    try:
        normalized_ids = [u for u in upload_ids if isinstance(u, str)]
        # print(
        #     "CHUNK_COMPLETE",
        #     {
        #         "chat_id": chat_id,
        #         "raw_upload_ids": len(upload_ids)
        #         if isinstance(upload_ids, list)
        #         else "n/a",
        #         "normalized": len(normalized_ids),
        #     },
        #     flush=True,
        # )
        for uid in normalized_ids:
            if not _session_chunks_complete(uid, user.id, chat_id):
                return {
                    "ok": False,
                    "error": f"Incomplete or invalid upload session: {uid}",
                    "http_status": 400,
                }

        merged_by_uid: dict[str, Tuple[str, str, str, str]] = {}
        failed_uid: Optional[str] = None
        workers = min(MERGE_MAX_WORKERS, max(1, len(normalized_ids)))
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {
                executor.submit(_merge_session_to_storage, uid, user.id, chat_id): uid
                for uid in normalized_ids
            }
            for future in as_completed(futures):
                uid = futures[future]
                try:
                    merged = future.result()
                except Exception:
                    logger.exception("merge failed for upload_id=%s", uid)
                    failed_uid = uid
                    merged = None
                if not merged:
                    failed_uid = failed_uid or uid
                    continue
                merged_by_uid[uid] = merged

        if failed_uid:
            for path, _, _, _ in merged_by_uid.values():
                try:
                    protected_storage.delete(path)
                except Exception:
                    pass
            return {
                "ok": False,
                "error": f"Failed to assemble upload {failed_uid}",
                "http_status": 400,
            }

        merged_files = [merged_by_uid[uid] for uid in normalized_ids if uid in merged_by_uid]
        if len(merged_files) != len(normalized_ids):
            for path, _, _, _ in merged_files:
                try:
                    protected_storage.delete(path)
                except Exception:
                    pass
            return {
                "ok": False,
                "error": "Failed to assemble uploads",
                "http_status": 400,
            }

        new_message = Message.objects.create(value=message_text, user=user, chat=chat)
        for storage_path, orig_name, mime_type, media_kind in merged_files:
            _attach_storage_file_to_message(
                new_message, user, storage_path, orig_name, mime_type, media_kind
            )

        new_message.save()

        from apps.Site.services.ws_sender import send_ws_message

        send_ws_message(new_message, user.id)

        return {
            "ok": True,
            "status": "success",
            "message": "Message sent successfully",
            "message_id": new_message.id,
        }
    except Exception as e:
        logger.exception("chunk_bundle_complete_service: %s", e)
        return {"ok": False, "error": "Server error", "http_status": 500}


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

        result = chunk_bundle_complete_service(
            request.user, chat_id, message_text, upload_ids
        )
        if not result.get("ok"):
            return JsonResponse(
                {"error": result.get("error", "error")},
                status=int(result.get("http_status") or 400),
            )

        return JsonResponse(
            {
                "status": result["status"],
                "message": result["message"],
                "message_id": result["message_id"],
            }
        )
