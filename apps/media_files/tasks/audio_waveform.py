# tasks/audio_waveform.py
from celery import shared_task
from pydub import AudioSegment
from pathlib import Path
from ..utils.waveform_utils import generate_waveform

@shared_task
def process_audio_task(audiofile_id):
    from apps.media_files.models import AudioFile
    try:
        audiofile = AudioFile.objects.get(id=audiofile_id)
        file_path = audiofile.file.storage.path(audiofile.file.name)

        audio = AudioSegment.from_file(file_path)
        duration = round(len(audio) / 1000, 2)
        waveform = generate_waveform(file_path, samples=60)

        audiofile.duration = duration
        audiofile.waveform = waveform
        audiofile.save(update_fields=["duration", "waveform"])

        return {"duration": duration, "waveform": waveform}
    except Exception as e:
        print(f"Audio processing failed for {audiofile_id}: {e}")
        return None
