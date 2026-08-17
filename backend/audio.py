# backend/audio.py — Audio Processing & ESP32 Opus Transcoder
import os
import subprocess
from typing import Optional

def convert_to_esp32_opus(input_path: str) -> Optional[str]:
    """
    Converts any uploaded audio track (MP3, WAV, M4A, etc.) to a high-efficiency
    16kHz mono OGG Opus stream specifically optimized for XiaoZhi ESP32 hardware playback.
    """
    try:
        base_name = os.path.splitext(input_path)[0]
        opus_path = f"{base_name}.ogg"
        
        # If already .ogg, check if valid
        if input_path.lower().endswith(".ogg") and os.path.exists(opus_path):
            return opus_path

        # Run ffmpeg to convert to 16kHz mono OGG Opus (32k bitrate)
        cmd = [
            "ffmpeg", "-y", "-i", input_path,
            "-ac", "1",
            "-ar", "16000",
            "-c:a", "libopus",
            "-b:a", "32k",
            opus_path
        ]
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=40)
        if res.returncode == 0 and os.path.exists(opus_path):
            print(f"[Audio Converter] Converted '{os.path.basename(input_path)}' to ESP32 OGG Opus stream.")
            return opus_path
        else:
            print(f"[Audio Converter] ffmpeg not available or exited with code {res.returncode}")
    except Exception as e:
        print(f"[Audio Converter] Auto-conversion note: {e}")
    return None
