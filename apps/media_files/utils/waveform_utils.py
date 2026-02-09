# utils/waveform_utils.py
from pydub import AudioSegment
import numpy as np


def generate_waveform(file_path, samples=120):
    audio = AudioSegment.from_file(file_path)
    data = np.array(audio.get_array_of_samples())
    if audio.channels == 2:
        data = data.reshape((-1, 2))
        data = data.mean(axis=1)

    block_size = max(1, len(data) // samples)
    waveform = [
        float(np.abs(data[i * block_size : (i + 1) * block_size]).max())
        for i in range(samples)
    ]

    max_val = max(waveform) or 1
    waveform = [v / max_val for v in waveform]

    return waveform
