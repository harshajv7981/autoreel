"""Turn (source video, moment, copy) into a finished 1080x1920 Short.

Every render varies slightly (font, accent colour, grade, grain) from a per-clip seed,
so a channel never looks like one template stamped out a hundred times.
The output carries no editing-tool metadata.
"""
import random
from pathlib import Path

from .. import log
from ..ff import duration, has_audio, run, video_size
from .ass import LAYOUTS, build_ass

FRAMES = ["blur", "square", "card"]

ACCENTS = ["#FFD60A", "#4CC9F0", "#2EC4B6", "#FF8C42", "#B8F35D", "#F25F5C"]
FONTS = ["Inter", "DejaVu Sans", "Liberation Sans", "Arial"]
MUSIC_EXT = {".mp3", ".wav", ".m4a", ".ogg", ".flac"}


def pick_music(music_dir: Path, rng: random.Random) -> Path | None:
    tracks = sorted(p for p in music_dir.glob("**/*") if p.suffix.lower() in MUSIC_EXT)
    return rng.choice(tracks) if tracks else None


def style_for(seed: int, layouts=None, frames=None) -> dict:
    rng = random.Random(seed)
    return {"accent": rng.choice(ACCENTS), "font": rng.choice(FONTS[:2]),
            "contrast": round(rng.uniform(0.97, 1.06), 3), "gamma": round(rng.uniform(0.96, 1.04), 3),
            "grain": rng.randint(4, 9), "title_y": rng.choice([190, 220, 250]),
            "pop": rng.choice([True, False]),
            "layout": rng.choice(list(layouts or LAYOUTS)), "frame": rng.choice(list(frames or FRAMES)),
            "card_bg": rng.choice(["#111111", "#14161c", "#1b1712", "#101a17"])}


def base_filter(src: Path, cfg, style: dict, ass: Path | None) -> str:
    W, H = cfg.compose.width, cfg.compose.height
    w, h = video_size(src)
    vertical = h > w
    frame = "blur" if vertical else style.get("frame", "blur")
    grade = f"eq=contrast={style['contrast']}:gamma={style['gamma']}"
    y = "(H-h)/2" if vertical else f"(H-h)/2+{int(H * 0.03)}"
    if frame == "card":
        bg = f"color=c={style.get('card_bg', '#111111')}:s={W}x{H}:r=30"
        g = (f"{bg}[bg];[0:v]setsar=1,scale={W - 80}:-2,{grade},"
             f"pad=iw+12:ih+12:6:6:color=white@0.85[fg];"
             f"[bg][fg]overlay=(W-w)/2:{y}:shortest=1,format=yuv420p")
    else:
        fg = (f"scale=-2:{H}" if vertical else
              f"crop='min(iw,ih)':'min(iw,ih)',scale={W}:{W}" if frame == "square" else f"scale={W}:-2")
        g = (f"[0:v]setsar=1,split[a][b];"
             f"[a]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},"
             f"boxblur=26:2,eq=brightness=-0.13:saturation=0.75[bg];"
             f"[b]{fg},{grade}[fg];"
             f"[bg][fg]overlay=(W-w)/2:{y},format=yuv420p")
    if cfg.compose.grain:
        g += f",noise=alls={style['grain']}:allf=t"
    if ass is not None:
        g += f",subtitles=filename={ass.name}"
    return g + "[v]"


def render_base(src: Path, start: float, end: float, out: Path, cfg, seed: int,
                ass_text: dict | None, music: Path | None, original_audio: bool = False) -> Path:
    """One ffmpeg pass: cut, reframe, grade, (optional) text overlay, audio, loudness."""
    style = style_for(seed, cfg.compose.get("layouts"), cfg.compose.get("frames"))
    work = out.parent
    ass = None
    if ass_text is not None:
        ass = work / f"{out.stem}.ass"
        ass.write_text(build_ass(ass_text, style, cfg.compose.width, cfg.compose.height,
                                 end - start), encoding="utf-8")
    length = end - start
    cmd = ["ffmpeg", "-y", "-ss", f"{start:.3f}", "-t", f"{length:.3f}", "-i", src.resolve()]
    original_audio = original_audio and has_audio(src)
    if original_audio:
        music = None
    if music:
        cmd += ["-stream_loop", "-1", "-i", music.resolve()]
    elif original_audio:
        pass
    else:
        cmd += ["-f", "lavfi", "-t", f"{length:.3f}", "-i", "anullsrc=r=48000:cl=stereo"]
    amap = "1:a"
    graph = base_filter(src, cfg, style, ass)
    fade_out = max(0.0, length - 0.8)
    if original_audio:
        graph += (f";[0:a]asetpts=PTS-STARTPTS,afade=t=in:d=0.25,afade=t=out:st={fade_out:.2f}:d=0.8,"
                  f"loudnorm=I={cfg.compose.loudness_lufs}:TP=-1.5:LRA=11,aresample=48000,"
                  f"aformat=channel_layouts=stereo[a]")
        amap = "[a]"
    elif music:
        fade_out = max(0.0, length - 1.5)
        graph += (f";[1:a]atrim=0:{length:.3f},asetpts=PTS-STARTPTS,"
                  f"afade=t=in:d=0.4,afade=t=out:st={fade_out:.2f}:d=1.5,"
                  f"volume={cfg.compose.music_volume},"
                  f"loudnorm=I={cfg.compose.loudness_lufs}:TP=-1.5:LRA=11,aresample=48000[a]")
        amap = "[a]"
    cmd += ["-filter_complex", graph, "-map", "[v]", "-map", amap, "-t", f"{length:.3f}",
            "-c:v", "libx264", "-preset", cfg.compose.preset, "-crf", str(cfg.compose.crf),
            "-r", "30", "-c:a", "aac", "-b:a", "160k", "-ar", "48000",
            "-map_metadata", "-1", "-map_chapters", "-1",
            "-fflags", "+bitexact", "-flags:v", "+bitexact", "-flags:a", "+bitexact",
            "-movflags", "+faststart", out.resolve()]
    run(cmd, cwd=work)
    return out


def compose(src: Path, start: float, end: float, copy: dict, credit_line: str, out: Path,
            cfg, seed: int, music: Path | None, original_audio: bool = False, label: str = "") -> Path:
    text = {"line1": copy["hook_line1"], "line2": copy["hook_line2"], "keyword": copy.get("keyword", ""),
            "beats": copy.get("beats") or [], "credit": credit_line, "label": label}
    renderer = cfg.compose.renderer
    if renderer == "hyperframes":
        from .hyperframes import render_overlay
        base = out.with_name(out.stem + ".base.mp4")
        render_base(src, start, end, base, cfg, seed, None, music, original_audio)
        try:
            render_overlay(base, out, text, style_for(seed), cfg)
            base.unlink(missing_ok=True)
            log.info("composed %s with HyperFrames (%.1fs)", out.name, duration(out))
            return out
        except Exception as e:  # noqa: BLE001
            log.warning("HyperFrames render failed (%s); falling back to ffmpeg text", e)
            base.unlink(missing_ok=True)
    render_base(src, start, end, out, cfg, seed, text, music, original_audio)
    log.info("composed %s (%.1fs)", out.name, duration(out))
    return out
