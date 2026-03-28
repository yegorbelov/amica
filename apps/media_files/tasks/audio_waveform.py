# tasks/audio_waveform.py
from celery import shared_task
from pydub import AudioSegment
from django.core.files.base import ContentFile
from mutagen import File as MutagenFile
from ..utils.waveform_utils import generate_waveform
from django.core.files.temp import NamedTemporaryFile
import logging

logger = logging.getLogger(__name__)


@shared_task
def process_audio_task(audiofile_id, message_id, user_id):
    from apps.media_files.models import AudioFile
    from apps.Site.models import Message
    from apps.Site.services.ws_sender import send_ws_message

    try:
        audiofile = AudioFile.objects.get(id=audiofile_id)

        with NamedTemporaryFile(delete=True) as tmp:
            for chunk in audiofile.file.chunks():
                tmp.write(chunk)
            tmp.flush()

            audio = AudioSegment.from_file(tmp.name)
            duration = round(len(audio) / 1000, 2)

            waveform = generate_waveform(tmp.name, samples=60)

            mutagen_audio = MutagenFile(tmp.name)
            cover_data = None
            if mutagen_audio.tags:
                apic_keys = [
                    k for k in mutagen_audio.tags.keys() if k.startswith("APIC:")
                ]
                if apic_keys:
                    cover_data = mutagen_audio.tags[apic_keys[0]].data
                elif hasattr(mutagen_audio, "pictures") and mutagen_audio.pictures:
                    cover_data = mutagen_audio.pictures[0].data

            audiofile.duration = duration
            audiofile.waveform = waveform
            if cover_data:
                audiofile.cover.save("cover.jpg", ContentFile(cover_data), save=False)
                logger.info(
                    f"Audio {audiofile_id}: cover extracted, size={len(cover_data)} bytes"
                )
            else:
                logger.info(f"Audio {audiofile_id}: no cover found")

            audiofile.save(update_fields=["duration", "waveform", "cover"])

        logger.info(f"Processed audio {audiofile_id}: duration={duration}s")
        
        message = Message.objects.get(id=message_id)

        send_ws_message(message, user_id)

        return {"duration": duration, "waveform": waveform}

    except Exception as e:
        logger.exception(f"Audio processing failed for {audiofile_id}")
        return None


@shared_task
def process_image_task(imagefile_id, message_id, user_id):
    from apps.media_files.models import ImageFile
    from apps.Site.models import Message
    from apps.Site.services.ws_sender import send_ws_message

    try:
        imagefile = ImageFile.objects.get(id=imagefile_id)
        imagefile.process_image()
        imagefile.save(
            update_fields=[
                "width",
                "height",
                "dominant_color",
                "thumbnail_small",
                "thumbnail_medium",
            ],
            process_media=False,
        )

        message = Message.objects.get(id=message_id)
        send_ws_message(message, user_id)

        return {"imagefile_id": imagefile_id}
    except Exception:
        logger.exception(f"Image processing failed for {imagefile_id}")
        return None


@shared_task
def process_video_task(videofile_id, message_id, user_id):
    from apps.media_files.models import VideoFile
    from apps.Site.models import Message
    from apps.Site.services.ws_sender import send_ws_message

    try:
        videofile = VideoFile.objects.get(id=videofile_id)
        videofile.populate_video_metadata()
        videofile.save(
            update_fields=["width", "height", "has_audio"],
            process_media=False,
        )

        message = Message.objects.get(id=message_id)
        send_ws_message(message, user_id)

        return {"videofile_id": videofile_id}
    except Exception:
        logger.exception(f"Video processing failed for {videofile_id}")
        return None
