import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def make_film(path: Path):
    """40s test film: title card, quiet shot, busy 'action' shot, quiet shot."""
    graph = ("[0:v]fps=25,format=yuv420p,setsar=1[a];[1:v]fps=25,format=yuv420p,setsar=1[b];"
             "[2:v]fps=25,format=yuv420p,setsar=1[c];[3:v]fps=25,format=yuv420p,setsar=1[d];"
             "[a][b][c][d]concat=n=4:v=1:a=0[v]")
    subprocess.run([
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-f", "lavfi", "-i", "color=c=black:s=640x480:d=8,drawbox=x=150:y=200:w=340:h=60:color=white:t=fill",
        "-f", "lavfi", "-i", "color=c=gray:s=640x480:d=10,noise=alls=6:allf=t",
        "-f", "lavfi", "-i", "life=s=640x480:mold=10:r=25:ratio=0.3:death_color=#303030:life_color=#e0e0e0,trim=duration=14",
        "-f", "lavfi", "-i", "color=c=gray:s=640x480:d=8,noise=alls=6:allf=t",
        "-filter_complex", graph, "-map", "[v]", "-c:v", "libx264", "-preset", "ultrafast", str(path)],
        check=True)
    return path


@pytest.fixture(scope="session")
def film(tmp_path_factory) -> Path:
    return make_film(tmp_path_factory.mktemp("film") / "film.mp4")


@pytest.fixture()
def project(tmp_path, film):
    """A throwaway project dir with config, a local source folder (with licence) and a music track."""
    (tmp_path / "inbox").mkdir()
    shutil.copy(film, tmp_path / "inbox" / "old_comedy.mp4")
    (tmp_path / "inbox" / "licence.json").write_text(
        '{"licence": "Public domain (test)", "credit": "Test archive", "url": "https://example.org/film",'
        ' "title": "The Test Film", "year": 1921}')
    (tmp_path / "music").mkdir()
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                    "sine=f=262:d=30,volume=0.3", str(tmp_path / "music" / "calm_piano.mp3")], check=True)
    (tmp_path / "music" / "calm_piano.txt").write_text("Calm Piano by Test (Mixkit Free License)")
    cfg = (ROOT / "config.yaml").read_text()
    cfg = cfg.replace("provider: ollama", "provider: none")
    cfg = cfg.replace("""      - type: internet_archive
        query: 'collection:(silent_films) AND (subject:(comedy) OR subject:(slapstick) OR collection:(Comedy_Films))'
      - type: internet_archive
        query: 'subject:("Charlie Chaplin" OR "Buster Keaton" OR "Harold Lloyd" OR "Laurel and Hardy")'""",
                      """      - type: local
        folder: ./inbox""")
    cfg = cfg.replace("clip_seconds: [18, 40]", "clip_seconds: [8, 12]").replace("buffer: 4", "buffer: 2", 1)
    (tmp_path / "config.yaml").write_text(cfg)
    return tmp_path
