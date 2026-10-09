from pathlib import Path
import json
import shutil
import subprocess
from .model import Project

SUPPORTED = {".mp3", ".wav", ".m4a", ".flac", ".aac", ".mp4", ".ogg", ".opus", ".aiff", ".wma", ".webm"}


def import_audio(path: str) -> Project:
    source = Path(path).resolve()
    if not source.is_file() or source.suffix.lower() not in SUPPORTED:
        raise ValueError("Select a supported local audio or video file")
    project = Project(source.stem, str(source), file_size=source.stat().st_size)
    if shutil.which("ffprobe"):
        result = subprocess.run(["ffprobe", "-v", "error", "-show_format", "-show_streams",
                                 "-of", "json", str(source)], capture_output=True, text=True,
                                timeout=30, check=True)
        metadata = json.loads(result.stdout)
        audio = next((s for s in metadata.get("streams", []) if s.get("codec_type") == "audio"), None)
        if audio is None:
            raise ValueError("The file contains no audio stream")
        project.duration = float(metadata.get("format", {}).get("duration", 0))
        project.media = {k: audio.get(k) for k in ("codec_name", "sample_rate", "channels", "channel_layout")}
    else:
        project.media = {"note": "Install ffprobe for import-time media properties; duration is detected by playback"}
    return project
