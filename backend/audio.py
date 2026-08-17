# backend/audio.py — Audio Processing & ESP32 Opus Transcoder
import asyncio
import os
import shutil
import subprocess
from typing import Optional

def is_ffmpeg_available() -> bool:
    """Checks whether the ffmpeg binary is installed and executable on the host."""
    return bool(shutil.which("ffmpeg"))

def convert_to_esp32_opus(input_path: str) -> Optional[str]:
    """
    Converts any uploaded audio track (MP3, WAV, M4A, etc.) to a high-efficiency
    16kHz mono OGG Opus stream specifically optimized for XiaoZhi ESP32 hardware playback.
    """
    if not is_ffmpeg_available():
        print(f"[Audio Converter] ffmpeg is not installed on host. Skipping auto-transcode for '{os.path.basename(input_path)}'.")
        return None

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
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)
        if res.returncode == 0 and os.path.exists(opus_path):
            print(f"[Audio Converter] Converted '{os.path.basename(input_path)}' to ESP32 OGG Opus stream.")
            return opus_path
        else:
            print(f"[Audio Converter] ffmpeg exited with code {res.returncode}: {res.stderr.decode('utf-8', 'ignore')[:120]}")
    except Exception as e:
        print(f"[Audio Converter] Auto-conversion note: {e}")
    return None

async def convert_to_esp32_opus_async(input_path: str) -> Optional[str]:
    """Non-blocking async runner for convert_to_esp32_opus to avoid blocking the event loop."""
    return await asyncio.to_thread(convert_to_esp32_opus, input_path)
