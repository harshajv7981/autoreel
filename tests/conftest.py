import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def make_film(path: Path):
    """40s test film with a tone soundtrack: title card, quiet shot, busy 'action' shot, quiet shot."""
    graph = ("[0:v]fps=25,format=yuv420p,setsar=1[a];[1:v]fps=25,format=yuv420p,setsar=1[b];"
             "[2:v]fps=25,format=yuv420p,setsar=1[c];[3:v]fps=25,format=yuv420p,setsar=1[d];"
             "[a][b][c][d]concat=n=4:v=1:a=0[v]")
    subprocess.run([
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-f", "lavfi", "-i", "color=c=black:s=640x480:d=8,drawbox=x=150:y=200:w=340:h=60:color=white:t=fill",
        "-f", "lavfi", "-i", "color=c=gray:s=640x480:d=10,noise=alls=6:allf=t",
        "-f", "lavfi", "-i", "life=s=640x480:mold=10:r=25:ratio=0.3:death_color=#303030:life_color=#e0e0e0,trim=duration=14",
        "-f", "lavfi", "-i", "color=c=gray:s=640x480:d=8,noise=alls=6:allf=t",
        "-f", "lavfi", "-i", "sine=f=440:d=40,volume=0.5",
        "-filter_complex", graph, "-map", "[v]", "-map", "4:a", "-c:v", "libx264", "-preset", "ultrafast",
        "-c:a", "aac", "-shortest", str(path)], check=True)
    return path


@pytest.fixture(scope="session")
def film(tmp_path_factory) -> Path:
    return make_film(tmp_path_factory.mktemp("film") / "film.mp4")


def write_project(tmp_path: Path, film: Path, year: int = 1935) -> Path:
    (tmp_path / "inbox").mkdir(exist_ok=True)
    shutil.copy(film, tmp_path / "inbox" / "old_film.mp4")
    (tmp_path / "inbox" / "licence.json").write_text(
        '{"licence": "Public domain (test)", "credit": "Test archive", "url": "https://example.org/film",'
        f' "title": "The Test Film", "year": {year}}}')
    (tmp_path / "music").mkdir(exist_ok=True)
    cfg = yaml.safe_load((ROOT / "config.yaml").read_text())
    cfg["llm"]["provider"] = "none"
    cfg["longform"]["min_parts"] = 2
    cfg["longform"]["target_minutes"] = 1
    cfg["channels"] = [{
        "name": "test-channel", "enabled": True, "token": "./secrets/token_test.json", "playlist_id": None,
        "voice": "plain", "clip_seconds": [6, 9], "buffer": 2,
        "sources": [{"type": "local", "folder": "./inbox"}], "tags": ["history", "archive footage"]}]
    (tmp_path / "config.yaml").write_text(yaml.safe_dump(cfg, sort_keys=False))
    return tmp_path


@pytest.fixture()
def project(tmp_path, film):
    return write_project(tmp_path, film)
