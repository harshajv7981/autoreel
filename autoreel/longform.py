"""Weekly long-form compilation: the week's best moments as one 16:9 video with chapters.

Why: from Feb 2027 the Shorts-only route into the Partner Program needs 20M views in 90 days,
while long-form watch hours (8,000/year) are reachable, and long-form keeps earning even when
Shorts views dip. Each part is re-cut from the ORIGINAL source with extra context around the
moment (not a re-upload of the vertical Short), with chapter cards, captions and provenance.
"""
import json
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import copywriter, log
from .compose.ass import ass_color, esc, ts
from .ff import duration, has_audio, run
from .llm import parse_json

W, H = 1920, 1080
ENC = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p", "-r", "30",
       "-c:a", "aac", "-b:a", "160k", "-ar", "48000", "-ac", "2"]


def _ass(events: list[str], accent: str) -> str:
    acc = ass_color(accent)
    return "\n".join([
        "[Script Info]", "ScriptType: v4.00+", f"PlayResX: {W}", f"PlayResY: {H}", "WrapStyle: 0",
        "ScaledBorderAndShadow: yes", "", "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, "
        "Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, "
        "MarginL, MarginR, MarginV, Encoding",
        "Style: Big,Inter,92,&H00FFFFFF,&H00FFFFFF,&H00000000,&H00000000,-1,0,0,0,100,100,0,0,1,0,0,5,120,120,0,1",
        f"Style: Num,Inter,60,{acc},{acc},&H00000000,&H00000000,-1,0,0,0,100,100,0,0,1,0,0,5,120,120,0,1",
        "Style: Small,Inter,40,&H30FFFFFF,&H30FFFFFF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,0,0,2,120,120,90,1",
        "Style: Chap,Inter,42,&H00FFFFFF,&H00FFFFFF,&H00000000,&H90000000,-1,0,0,0,100,100,0,0,3,10,0,7,60,60,50,1",
        "Style: Beat,Inter,50,&H00FFFFFF,&H00FFFFFF,&H00000000,&H00000000,-1,0,0,0,100,100,0,0,1,4,1,2,160,160,70,1",
        "Style: Credit,Inter,28,&H40FFFFFF,&H40FFFFFF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,2,0,3,50,50,40,1",
        "", "[Events]", "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
        *events, ""])


def render_card(out: Path, seconds: float, big: str, small: str, accent: str, bg: str, num: str = "") -> Path:
    ass = out.with_suffix(".ass")
    ev = [f"Dialogue: 1,{ts(0.1)},{ts(seconds)},Big,,0,0,0,,{{\\fad(250,250)}}{esc(big)}"]
    if num:
        ev.append(f"Dialogue: 1,{ts(0.1)},{ts(seconds)},Num,,0,0,170,,{{\\fad(250,250)\\pos(960,400)}}{esc(num)}")
    if small:
        ev.append(f"Dialogue: 1,{ts(0.3)},{ts(seconds)},Small,,0,0,0,,{{\\fad(300,250)}}{esc(small)}")
    ass.write_text(_ass(ev, accent), encoding="utf-8")
    run(["ffmpeg", "-y", "-f", "lavfi", "-t", f"{seconds:.2f}", "-i", f"color=c={bg}:s={W}x{H}:r=30",
         "-f", "lavfi", "-t", f"{seconds:.2f}", "-i", "anullsrc=r=48000:cl=stereo",
         "-vf", f"subtitles=filename={ass.name},format=yuv420p", *ENC, "-shortest", out.name], cwd=out.parent)
    return out


def render_part(src: Path, start: float, end: float, out: Path, chapter: str, beats: list[str],
                credit: str, accent: str, original_audio: bool) -> Path:
    length = end - start
    ass = out.with_suffix(".ass")
    ev = [f"Dialogue: 1,{ts(0.2)},{ts(min(6.0, length))},Chap,,0,0,0,,{{\\fad(250,300)}}{esc(chapter)}",
          f"Dialogue: 0,{ts(0)},{ts(length)},Credit,,0,0,0,,{esc(credit)}"]
    if beats:
        span = (length - 7.0) / len(beats) if length > 12 else length / len(beats)
        t0 = 6.5 if length > 12 else 0.5
        for i, b in enumerate(beats):
            ev.append(f"Dialogue: 1,{ts(t0 + i * span)},{ts(t0 + (i + 1) * span - 0.2)},Beat,,0,0,0,,"
                      f"{{\\fad(200,200)}}{esc(b)}")
    ass.write_text(_ass(ev, accent), encoding="utf-8")
    use_audio = original_audio and has_audio(src)
    cmd = ["ffmpeg", "-y", "-ss", f"{start:.3f}", "-t", f"{length:.3f}", "-i", str(src.resolve())]
    if not use_audio:
        cmd += ["-f", "lavfi", "-t", f"{length:.3f}", "-i", "anullsrc=r=48000:cl=stereo"]
    graph = (f"[0:v]setsar=1,split[a][b];[a]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},"
             f"boxblur=30:2,eq=brightness=-0.15:saturation=0.7[bg];[b]scale=-2:{H}[fg];"
             f"[bg][fg]overlay=(W-w)/2:0,subtitles=filename={ass.name},format=yuv420p[v]")
    if use_audio:
        graph += ";[0:a]asetpts=PTS-STARTPTS,loudnorm=I=-14:TP=-1.5:LRA=11,aresample=48000[au]"
    cmd += ["-filter_complex", graph, "-map", "[v]", "-map", "[au]" if use_audio else "1:a",
            "-t", f"{length:.3f}", *ENC, out.name]
    run(cmd, cwd=out.parent)
    return out


def _stamp(t: float) -> str:
    t = int(t)
    return f"{t // 3600}:{t % 3600 // 60:02}:{t % 60:02}" if t >= 3600 else f"{t // 60}:{t % 60:02}"


TITLE_PROMPT = """Write a YouTube title (40-70 characters, sentence case, no hype words, no em dashes,
no hashtags) for a compilation of real archival public-domain footage. The parts are:
{parts}
Return JSON: {{"title": "...", "description": "2 short sentences in plain words, no invented facts"}}"""


def build_weekly(cfg, db, llm, ch, force: bool = False) -> int | None:
    lf = cfg.longform
    last = db.last_long(ch.name)
    if last and not force:
        age = datetime.now(timezone.utc) - datetime.fromisoformat(last["created"])
        if age < timedelta(days=lf.every_days):
            log.info("%s: long-form made %s ago; next in %s", ch.name, age, timedelta(days=lf.every_days) - age)
            return None
    since = (datetime.now(timezone.utc) - timedelta(days=lf.lookback_days)).isoformat(timespec="seconds")
    used = set()
    for row in db.clips(channel=ch.name):
        if row["kind"] == "long":
            used.update(json.loads(row["meta"]).get("parts", []))
    rows = [r for r in db.recent_shorts(ch.name, since) if r["id"] not in used][: lf.max_parts]
    if len(rows) < lf.min_parts:
        log.info("%s: only %d unused approved Shorts this week (need %d) - no long-form yet",
                 ch.name, len(rows), lf.min_parts)
        return None

    rows.sort(key=lambda r: r["score"])                # build up to the strongest moment
    rng = random.Random(rows[0]["id"])
    accent = rng.choice(["#FFD60A", "#4CC9F0", "#2EC4B6", "#FF8C42"])
    bg = rng.choice(["#111111", "#14161c", "#1b1712"])
    out_dir = cfg.paths.data / "renders" / ch.name / f"long_{rows[0]['id']}_{rows[-1]['id']}"
    out_dir.mkdir(parents=True, exist_ok=True)

    # context padding so the video lands near the target length
    base = sum(r["end"] - r["start"] for r in rows) + 2.5 * len(rows) + 9
    pad = max(0.0, min(lf.max_context, (lf.target_minutes * 60 - base) / (2 * len(rows))))

    files, chapters, provenance, t = [], [], [], 0.0
    files.append(render_card(out_dir / "00_intro.mp4", 4.0, "This week in the archive",
                             "Real footage from the public-domain archive",
                             accent, bg))
    t += 4.0
    for i, r in enumerate(rows, 1):
        meta = json.loads(r["meta"])
        src_row = db.source_row(r["source_row"])
        src = Path(src_row["local_path"])
        total = duration(src)
        s, e = max(0.0, r["start"] - pad), min(total, r["end"] + pad)
        year = src_row["year"]
        card = render_card(out_dir / f"{i:02d}_card.mp4", 2.5, meta["title"], f"{src_row['title']}"
                           + (f" ({year})" if year else ""), accent, bg, num=f"{len(rows) - i + 1}")
        chapters.append(f"{_stamp(t)} {meta['title']}")
        t += 2.5
        mode = cfg.compose.get("audio", "original")
        original = mode == "original" and not (year and year < cfg.compose.get("mute_before_year", 1928))
        part = render_part(src, s, e, out_dir / f"{i:02d}_part.mp4", meta["title"], meta.get("beats", []),
                           f"{src_row['title'][:50]} · {src_row['licence'][:40]}", accent, original)
        files += [card, part]
        t += e - s
        provenance.append(f"{i}. {src_row['title']}" + (f" ({year})" if year else "") +
                          f" - {src_row['licence']} - {src_row['page_url']}")
    files.append(render_card(out_dir / "99_outro.mp4", 5.0, "That's the archive this week",
                             "Every clip is public domain. Sources are in the description.", accent, bg))
    chapters.insert(0, "0:00 Intro")

    listing = out_dir / "concat.txt"
    listing.write_text("".join(f"file '{f.name}'\n" for f in files))
    final = out_dir.parent / f"{out_dir.name}.mp4"
    run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", listing.name, "-c", "copy",
         "-map_metadata", "-1", "-fflags", "+bitexact", "-movflags", "+faststart", str(final.resolve())],
        cwd=out_dir)

    parts_text = "\n".join(f"- {json.loads(r['meta'])['title']}" for r in rows)
    data = parse_json(llm.text(TITLE_PROMPT.format(parts=parts_text), as_json=True)) if llm.available() else None
    facts = f"{len(rows)} " + " ".join(f"{json.loads(r['meta'])['title']} {db.source_row(r['source_row'])['title']} "
                                       f"{db.source_row(r['source_row'])['year'] or ''}" for r in rows)
    title = (data or {}).get("title") or f"{len(rows)} moments from the film archive"
    blurb = (data or {}).get("description") or "Real moments from old films and newsreels, all in the public domain."
    probe_copy = {"hook_line1": "x x x", "hook_line2": "x x x", "title": title, "description": blurb,
                  "beats": ["xxxxxxxxx", "xxxxxxxxx"]}
    if copywriter.lint(probe_copy, facts):
        title, blurb = (f"{len(rows)} moments from the film archive",
                        "Real moments from old films and newsreels, all in the public domain.")
    desc = "\n".join([blurb, "", "Chapters", *chapters, "", "Sources", *provenance, "",
                      "Footage is original archival film; edited for this channel (cut, reframed, captioned)."])
    meta = {"title": title[:100], "description": desc[:5000], "tags": list(ch.tags), "parts": [r["id"] for r in rows],
            "duration": round(duration(final), 1), "hook": [], "copy_source": "llm" if data else "template",
            "source_title": f"{len(rows)}-part compilation"}
    status = "review" if cfg.review.require_approval else "approved"
    clip_id = db.add_clip(ch.name, None, 0, meta["duration"], max(r["score"] for r in rows), {}, final, meta,
                          status, kind="long")
    log.info("long-form #%d for %s: %r (%.1f min, %d parts)", clip_id, ch.name, title,
             meta["duration"] / 60, len(rows))
    for f in out_dir.iterdir():
        f.unlink()
    out_dir.rmdir()
    return clip_id
