"""Thin ffmpeg/ffprobe helpers."""
import json
import subprocess
from pathlib import Path

from . import log


class FFmpegError(RuntimeError):
    pass


def run(cmd: list, cwd: Path | None = None, capture_bytes: bool = False):
    cmd = [str(c) for c in cmd]
    log.debug("$ %s", " ".join(cmd))
    proc = subprocess.run(cmd, capture_output=True, cwd=cwd, text=not capture_bytes)
    if proc.returncode != 0:
        err = proc.stderr if isinstance(proc.stderr, str) else proc.stderr.decode(errors="replace")
        raise FFmpegError(f"{cmd[0]} failed ({proc.returncode}):\n{err[-3000:]}")
    return proc


def probe(path: Path) -> dict:
    return json.loads(run(["ffprobe", "-v", "error", "-print_format", "json",
                           "-show_format", "-show_streams", path]).stdout)


def duration(path: Path) -> float:
    return float(probe(path)["format"]["duration"])


def video_size(path: Path) -> tuple[int, int]:
    v = next(s for s in probe(path)["streams"] if s["codec_type"] == "video")
    w, h = int(v["width"]), int(v["height"])
    rot = int(v.get("tags", {}).get("rotate", 0) or 0)
    for sd in v.get("side_data_list", []):
        rot = int(sd.get("rotation", rot))
    return (h, w) if abs(rot) % 180 == 90 else (w, h)


def has_audio(path: Path) -> bool:
    return any(s["codec_type"] == "audio" for s in probe(path)["streams"])


def grab_frames(video: Path, times: list[float], out_dir: Path, width: int = 512) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for i, t in enumerate(times):
        p = out_dir / f"f{i:02d}_{t:.1f}.jpg"
        run(["ffmpeg", "-y", "-ss", f"{t:.2f}", "-i", video, "-frames:v", "1",
             "-vf", f"scale={width}:-2", "-q:v", "4", p])
        paths.append(p)
    return paths
