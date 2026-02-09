import logging
import os
import subprocess
import tempfile

from celery import shared_task
from django.core.files import File

from apps.media_files.models.models import DisplayVideo, VideoFile

logger = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=3, retry_backoff=True)
def compress_video_task(self, model_name: str, video_id: int):
    print(f"Compressing {model_name} id={video_id}")
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
            scale_option = ["-vf", "scale=800:-2"]
            audio_option = ["-c:a", "aac", "-b:a", "128k", "-profile:a", "aac_low"]

        with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tmp:
            temp_output = tmp.name

        cmd = [
            "ffmpeg",
            "-y",
            "-fflags",
            "+genpts",
            "-i",
            video_path,
            *duration_option,
            *scale_option,
            *audio_option,
            "-c:v",
            "libx264",
            "-profile:v",
            "baseline",
            "-level",
            "3.0",
            "-preset",
            "medium",
            "-b:v",
            "2M",
            "-maxrate",
            "2.5M",
            "-bufsize",
            "3M",
            "-g",
            "25",
            "-keyint_min",
            "25",
            "-sc_threshold",
            "0",
            "-movflags",
            "+faststart+frag_keyframe+empty_moov+default_base_moof",
            temp_output,
        ]

        subprocess.run(
            cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE
        )

        with open(temp_output, "rb") as f:
            video_field.save(os.path.basename(video_field.name), File(f), save=True)

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

        video_instance.has_audio = has_audio
        video_instance.save(update_fields=["has_audio", "status"])

        logger.info(f"Video {video_id} compressed successfully")
        return {"status": "done"}

    except Exception as e:
        logger.error(f"Failed to compress video {model_name} id={video_id}: {e}")
        if video_instance and hasattr(video_instance, "status"):
            video_instance.status = "failed"
            video_instance.save(update_fields=["status"])
        raise self.retry(countdown=5)

    finally:
        if temp_output and os.path.exists(temp_output):
            try:
                os.remove(temp_output)
            except Exception as e:
                logger.warning(f"Could not remove temp file: {e}")
