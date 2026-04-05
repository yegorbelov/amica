import logging
import os
import subprocess
import tempfile

from celery import shared_task
from django.core.files import File

from apps.media_files.models.models import DisplayVideo, VideoFile

logger = logging.getLogger(__name__)

# Speed-focused encoding profile with acceptable quality/size.
VIDEO_CRF = "27"
VIDEO_PRESET = "faster"
VIDEO_PROFILE = "main"
VIDEO_LEVEL = "4.0"
VIDEO_THREADS = "2"
VIDEO_MAXRATE = "1800k"
VIDEO_BUFSIZE = "3600k"


class FfmpegCompressionError(RuntimeError):
    pass


def compress_video_sync(model_name: str, video_id: int):
    logger.info(f"Compressing {model_name} id={video_id}")
    model_map = {
        "DisplayVideo": DisplayVideo,
        "VideoFile": VideoFile,
    }

    ModelClass = model_map.get(model_name)
    if not ModelClass:
        logger.error(f"Unknown model: {model_name}")
        return {"status": "error", "reason": "unknown model"}

    video_instance = None
    temp_output = None

    try:
        video_instance = ModelClass.objects.get(id=video_id)

        if model_name == "DisplayVideo":
            video_field = getattr(video_instance, "video", None)
        else:
            video_field = getattr(video_instance, "file", None)

        if not video_field or not getattr(video_field, "path", None):
            logger.error(f"Video file does not exist for {model_name} id={video_id}")
            return {"status": "error", "reason": "file missing"}

        video_path = video_field.path

        if model_name == "DisplayVideo":
            duration_option = ["-ss", "0", "-t", "10"]
            scale_option = ["-vf", "crop='min(iw,ih)':'min(iw,ih)',scale=800:800"]
            audio_option = ["-an"]
        else:
            duration_option = []
            # Do not upscale small sources to avoid unnecessary bitrate growth.
            scale_option = ["-vf", "scale='min(1280,iw)':-2"]
            audio_option = ["-c:a", "aac", "-b:a", "128k", "-profile:a", "aac_low"]

        # Chat / VideoFile: progressive MP4 (moov at start) — fewer Range round-trips in
        # <video> than fMP4 (frag_keyframe+empty_moov+default_base_moof). Profile clips
        # keep fragmented output for streaming-style use.
        if model_name == "DisplayVideo":
            movflags = "+faststart+frag_keyframe+empty_moov+default_base_moof"
        else:
            movflags = "+faststart"

        with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tmp:
            temp_output = tmp.name

        cmd = [
            "ffmpeg",
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-fflags",
            "+genpts",
            "-i",
            video_path,
            *duration_option,
            *scale_option,
            *audio_option,
            "-c:v",
            "libx264",
            "-threads",
            VIDEO_THREADS,
            "-profile:v",
            VIDEO_PROFILE,
            "-level",
            VIDEO_LEVEL,
            "-preset",
            VIDEO_PRESET,
            "-crf",
            VIDEO_CRF,
            "-maxrate",
            VIDEO_MAXRATE,
            "-bufsize",
            VIDEO_BUFSIZE,
            "-pix_fmt",
            "yuv420p",
            "-g",
            "25",
            "-keyint_min",
            "25",
            "-sc_threshold",
            "0",
            "-movflags",
            movflags,
            temp_output,
        ]

        try:
            subprocess.run(
                cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE
            )
        except subprocess.CalledProcessError as ffmpeg_err:
            stderr_text = (ffmpeg_err.stderr or b"").decode("utf-8", errors="replace")
            logger.error(
                "ffmpeg failed for %s id=%s (exit=%s) input=%s error=%s",
                model_name,
                video_id,
                ffmpeg_err.returncode,
                video_path,
                stderr_text.strip()[:4000],
            )
            raise FfmpegCompressionError(
                f"ffmpeg failed with exit code {ffmpeg_err.returncode}"
            ) from ffmpeg_err

        # Original is no longer needed; remove from storage before writing compressed
        # (avoids leaving a second copy if storage uses new names on save).
        save_name = os.path.basename(video_path)
        video_field.delete(save=False)

        with open(temp_output, "rb") as f:
            # FieldFile.save(save=True) persists only the file field, so metadata
            # (width/height/has_audio/file_size) may be computed but not written.
            # Save file name first, then persist model fields explicitly.
            video_field.save(save_name, File(f), save=False)

        if model_name == "VideoFile":
            video_instance.populate_video_metadata()
            video_instance.save(
                update_fields=["file", "file_size", "width", "height", "has_audio"]
            )
            return {"status": "done"}

        if not hasattr(video_instance, "status"):
            return {"status": "done"}

        video_instance.status = "done"

        cmd_probe = [
            "ffprobe",
            "-v",
            "quiet",
            "-print_format",
            "json",
            "-show_streams",
            video_field.path,
        ]
        result = subprocess.run(cmd_probe, capture_output=True, text=True)

        has_audio = False
        video_codec = "unknown"
        audio_codec = "none"

        if result.returncode != 0:
            logger.error(
                f"ffprobe failed (code {result.returncode}): {result.stderr.strip()}"
            )
        else:
            try:
                import json

                probe_data = json.loads(result.stdout)

                has_audio = any(
                    stream.get("codec_type") == "audio"
                    for stream in probe_data.get("streams", [])
                )

                for stream in probe_data.get("streams", []):
                    if stream.get("codec_type") == "video":
                        video_codec = stream.get("codec_tag_string") or "avc1"
                    if stream.get("codec_type") == "audio":
                        audio_codec = stream.get("codec_tag_string") or "mp4a"

                logger.info(
                    f"Compressed video codecs: video={video_codec}, audio={audio_codec}"
                )
                logger.info(f"Video {video_id} has_audio: {has_audio}")
            except json.JSONDecodeError as json_err:
                logger.error(f"ffprobe JSON parse error: {json_err}")
            except Exception as probe_err:
                logger.error(f"Unexpected error during probe parsing: {probe_err}")

        # DisplayVideo has status only (no has_audio on model). VideoFile returns earlier.
        video_instance.save(update_fields=["status"])

        logger.info(f"Video {video_id} compressed successfully")
        return {"status": "done"}
    finally:
        if temp_output and os.path.exists(temp_output):
            try:
                os.remove(temp_output)
            except Exception as e:
                logger.warning(f"Could not remove temp file: {e}")


@shared_task(bind=True, max_retries=3, retry_backoff=True)
def compress_video_task(self, model_name: str, video_id: int):
    try:
        return compress_video_sync(model_name, video_id)
    except FfmpegCompressionError as e:
        logger.error(
            "Compression skipped for %s id=%s: %s",
            model_name,
            video_id,
            e,
        )
        if model_name == "VideoFile":
            # Keep original file; make sure client still gets metadata.
            try:
                video_instance = VideoFile.objects.get(id=video_id)
                video_instance.populate_video_metadata()
                video_instance.save(
                    update_fields=["file_size", "width", "height", "has_audio"]
                )
            except Exception:
                logger.exception(
                    "Failed to populate metadata after compression skip for VideoFile id=%s",
                    video_id,
                )
        elif model_name == "DisplayVideo":
            try:
                video_instance = DisplayVideo.objects.get(id=video_id)
                video_instance.status = "failed"
                video_instance.save(update_fields=["status"])
            except Exception:
                logger.exception(
                    "Failed to mark DisplayVideo id=%s as failed after compression skip",
                    video_id,
                )
        return {"status": "skipped", "reason": str(e)}
    except Exception as e:
        logger.exception(f"Failed to compress video {model_name} id={video_id}: {e}")
        model_map = {
            "DisplayVideo": DisplayVideo,
            "VideoFile": VideoFile,
        }
        ModelClass = model_map.get(model_name)
        if ModelClass and hasattr(ModelClass, "status"):
            try:
                video_instance = ModelClass.objects.get(id=video_id)
                if hasattr(video_instance, "status"):
                    video_instance.status = "failed"
                    video_instance.save(update_fields=["status"])
            except Exception:
                pass
        # Avoid surfacing MaxRetriesExceededError as task crash after final attempt.
        if self.request.retries >= self.max_retries:
            logger.error(
                "Compression permanently failed for %s id=%s after %s retries",
                model_name,
                video_id,
                self.request.retries,
            )
            return {"status": "failed", "reason": str(e)}
        raise self.retry(countdown=5, exc=e)
