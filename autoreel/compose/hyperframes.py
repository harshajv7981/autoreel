"""Animated text overlay rendered with HyperFrames (HTML -> MP4, runs locally, no account)."""
import html
import shutil
import subprocess
from pathlib import Path

from .. import log
from ..ff import duration, run

TEMPLATE_DIR = Path(__file__).resolve().parents[2] / "templates" / "hyperframes"


def _line(line: str, keyword: str) -> str:
    safe = html.escape(line)
    if keyword and keyword.lower() in line.lower():
        i = line.lower().find(keyword.lower())
        j = i + len(keyword)
        safe = (html.escape(line[:i]) + f'<span class="kw" style="display:inline-block">'
                f"{html.escape(line[i:j])}</span>" + html.escape(line[j:]))
    return safe


def render_overlay(base: Path, out: Path, text: dict, style: dict, cfg) -> Path:
    proj = out.parent / f"{out.stem}_hf"
    if proj.exists():
        shutil.rmtree(proj)
    (proj / "assets").mkdir(parents=True)
    shutil.copy(base, proj / "assets" / "base.mp4")
    shutil.copy(TEMPLATE_DIR / "vendor" / "gsap.min.js", proj / "assets" / "gsap.min.js")
    kw = text.get("keyword", "")
    kw_in_l2 = kw and kw.lower() in text["line2"].lower()
    page = (TEMPLATE_DIR / "index.html").read_text()
    page = (page.replace("__DUR__", f"{duration(base):.3f}")
                .replace("__ACCENT__", style["accent"]).replace("__FONT__", style["font"])
                .replace("__TOP__", str(style["title_y"]))
                .replace("__LINE1__", _line(text["line1"], "" if kw_in_l2 else kw))
                .replace("__LINE2__", _line(text["line2"], kw if kw_in_l2 else ""))
                .replace("__CREDIT__", html.escape(text.get("credit", ""))))
    (proj / "index.html").write_text(page, encoding="utf-8")
    (proj / "hyperframes.json").write_text('{"paths":{"assets":"assets"},"media":{"autoProxy":true}}')
    raw = out.with_name(out.stem + ".hf.mp4")
    cmd = ["npx", "-y", f"hyperframes@{cfg.compose.hyperframes_version}", "render", str(proj),
           "-o", str(raw.resolve()), "-q", "delivery", "--quiet", "--fps", "30"]
    log.info("HyperFrames rendering %s", out.name)
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=1800,
                          env={**__import__("os").environ, "HYPERFRAMES_SKIP_SKILLS": "1"})
    if proc.returncode != 0 or not raw.exists():
        raise RuntimeError((proc.stderr or proc.stdout)[-1500:])
    # remux without the renderer's metadata tags (no quality loss)
    run(["ffmpeg", "-y", "-i", raw, "-c", "copy", "-map_metadata", "-1", "-map_chapters", "-1",
         "-fflags", "+bitexact", "-movflags", "+faststart", out])
    raw.unlink(missing_ok=True)
    shutil.rmtree(proj, ignore_errors=True)
    return out
