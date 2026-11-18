"""Turn (source video, moment, copy) into a finished 1080x1920 Short.

Every render varies slightly (font, accent colour, grade, grain) from a per-clip seed,
so a channel never looks like one template stamped out a hundred times.
The output carries no editing-tool metadata.
"""
import random
from pathlib import Path

from .. import log
from ..ff import duration, run, video_size
from .ass import build_ass

ACCENTS = ["#FFD60A", "#4CC9F0", "#2EC4B6", "#FF8C42", "#B8F35D", "#F25F5C"]
FONTS = ["Inter", "DejaVu Sans", "Liberation Sans", "Arial"]
MUSIC_EXT = {".mp3", ".wav", ".m4a", ".ogg", ".flac"}


def pick_music(music_dir: Path, rng: random.Random) -> Path | None:
    tracks = sorted(p for p in music_dir.glob("**/*") if p.suffix.lower() in MUSIC_EXT)
    return rng.choice(tracks) if tracks else None


def style_for(seed: int) -> dict:
    rng = random.Random(seed)
    return {"accent": rng.choice(ACCENTS), "font": rng.choice(FONTS[:2]),
            "contrast": round(rng.uniform(0.97, 1.06), 3), "gamma": round(rng.uniform(0.96, 1.04), 3),
            "grain": rng.randint(4, 9), "title_y": rng.choice([210, 240, 270]),
            "pop": rng.choice([True, False])}


def base_filter(src: Path, cfg, style: dict, ass: Path | None) -> str:
    W, H = cfg.compose.width, cfg.compose.height
    w, h = video_size(src)
    vertical = h > w
    fg = f"scale=-2:{H}" if vertical else f"scale={W}:-2"
    y = "(H-h)/2" if vertical else f"(H-h)/2+{int(H * 0.03)}"
    g = (f"[0:v]setsar=1,split[a][b];"
         f"[a]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},"
         f"boxblur=26:2,eq=brightness=-0.13:saturation=0.75[bg];"
         f"[b]{fg},eq=contrast={style['contrast']}:gamma={style['gamma']}[fg];"
         f"[bg][fg]overlay=(W-w)/2:{y},format=yuv420p")
    if cfg.compose.grain:
        g += f",noise=alls={style['grain']}:allf=t"
    if ass is not None:
        g += f",subtitles=filename={ass.name}"
    return g + "[v]"


def render_base(src: Path, start: float, end: float, out: Path, cfg, seed: int,
                ass_text: dict | None, music: Path | None) -> Path:
    """One ffmpeg pass: cut, reframe, grade, (optional) text overlay, music, loudness."""
    style = style_for(seed)
    work = out.parent
    ass = None
    if ass_text is not None:
        ass = work / f"{out.stem}.ass"
        ass.write_text(build_ass(ass_text, style, cfg.compose.width, cfg.compose.height,
                                 end - start), encoding="utf-8")
    length = end - start
    cmd = ["ffmpeg", "-y", "-ss", f"{start:.3f}", "-t", f"{length:.3f}", "-i", src.resolve()]
    if music:
        cmd += ["-stream_loop", "-1", "-i", music.resolve()]
    else:
        cmd += ["-f", "lavfi", "-t", f"{length:.3f}", "-i", "anullsrc=r=48000:cl=stereo"]
    graph = base_filter(src, cfg, style, ass)
    if music:
        fade_out = max(0.0, length - 1.5)
        graph += (f";[1:a]atrim=0:{length:.3f},asetpts=PTS-STARTPTS,"
                  f"afade=t=in:d=0.4,afade=t=out:st={fade_out:.2f}:d=1.5,"
                  f"volume={cfg.compose.music_volume},"
                  f"loudnorm=I={cfg.compose.loudness_lufs}:TP=-1.5:LRA=11,aresample=48000[a]")
        amap = "[a]"
    else:
        amap = "1:a"
    cmd += ["-filter_complex", graph, "-map", "[v]", "-map", amap, "-t", f"{length:.3f}",
            "-c:v", "libx264", "-preset", cfg.compose.preset, "-crf", str(cfg.compose.crf),
            "-r", "30", "-c:a", "aac", "-b:a", "160k", "-ar", "48000",
            "-map_metadata", "-1", "-map_chapters", "-1",
            "-fflags", "+bitexact", "-flags:v", "+bitexact", "-flags:a", "+bitexact",
            "-movflags", "+faststart", out.resolve()]
    run(cmd, cwd=work)
    return out


def compose(src: Path, start: float, end: float, copy: dict, credit_line: str, out: Path,
            cfg, seed: int, music: Path | None) -> Path:
    text = {"line1": copy["hook_line1"], "line2": copy["hook_line2"],
            "keyword": copy.get("keyword", ""), "credit": credit_line}
    renderer = cfg.compose.renderer
    if renderer == "hyperframes":
        from .hyperframes import render_overlay
        base = out.with_name(out.stem + ".base.mp4")
        render_base(src, start, end, base, cfg, seed, None, music)
        try:
            render_overlay(base, out, text, style_for(seed), cfg)
            base.unlink(missing_ok=True)
            log.info("composed %s with HyperFrames (%.1fs)", out.name, duration(out))
            return out
        except Exception as e:  # noqa: BLE001
            log.warning("HyperFrames render failed (%s); falling back to ffmpeg text", e)
            base.unlink(missing_ok=True)
    render_base(src, start, end, out, cfg, seed, text, music)
    log.info("composed %s (%.1fs)", out.name, duration(out))
    return out
