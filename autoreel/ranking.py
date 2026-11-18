"""Top-N ranking Shorts from licensed footage: "Top 5 knockouts from 1930s newsreels".

Pick a theme -> find short moments in already-downloaded, licence-checked sources that match it
-> score them (vision judge, else motion) -> rank -> label each in 2-4 grounded words ->
play in a shuffled order with #1 saved for last -> one 9:16 video with rank badges.
"""
import json
import random
import re
from pathlib import Path
from types import SimpleNamespace

from . import copywriter, log
from .compose import render_base
from .compose.ass import ass_color, esc, rect, ts
from .ff import duration, run
from .finder import find_moments
from .llm import parse_json

LABEL_PROMPT = """You label clips for a "Top {n}" ranking Short of real archival public-domain footage.
Theme: {theme}
For each clip, write a label of 2-4 words describing what happens (e.g. "The double knockout",
"Wall of fire", "One-wheel landing"). Use only what the notes say. No names unless they appear in the notes.
Clips:
{clips}
Return JSON: {{"labels": ["...", "..."]}} in the same order."""


def play_order(n: int, rng: random.Random) -> list[int]:
    """Ranks in playback order: shuffled, never plain descending, #1 always last."""
    ranks = list(range(2, n + 1))
    for _ in range(20):
        rng.shuffle(ranks)
        if ranks != sorted(ranks, reverse=True) or n <= 2:
            break
    return ranks + [1]


def _grounded(label: str, facts: str) -> bool:
    names = re.findall(r"(?<!^)\b[A-Z][a-z]{2,}", label)
    nums = re.findall(r"\d+", label)
    low = facts.lower()
    return all(n.lower() in low for n in names) and all(n in facts for n in nums) and 3 <= len(label) <= 32


def candidates(agent, ch, theme: dict, need: int, item_len) -> list[dict]:
    kws = [k.lower() for k in theme["keywords"]]
    pool = []
    for row in agent.db.usable_sources():
        hay = f"{row['title']} {row['meta'] or ''} {row['reason'] or ''}".lower()
        if not any(re.search(rf"\b{re.escape(k)}", hay) for k in kws):
            continue
        path = Path(row["local_path"])
        if not path.exists():
            continue
        used = agent.db.used_spans(row["id"])
        item = SimpleNamespace(source_id=row["source_id"], title=row["title"], year=row["year"],
                               text=json.loads(row["meta"] or "{}").get("text", ""))
        for m in find_moments(path, item_len[0], item_len[1], top_k=4, fps=agent.cfg.finder.analysis_fps,
                              skip_edges=agent.cfg.finder.skip_edges):
            if any(m.start < b and m.end > a for a, b in used):
                continue
            verdict = agent.judge(path, m.start, m.end, item, ch.voice, None, ch.get("judge_hint") or "")
            if not verdict["keep"]:
                continue
            score = float(verdict["score"]) if verdict.get("score") is not None else 5 + m.score
            pool.append({"row": row, "path": path, "start": m.start, "end": m.end, "score": score,
                         "what": verdict.get("what_happens") or "", "motion": m.score})
    # best per source first, so one film can't fill the whole list
    pool.sort(key=lambda c: c["score"], reverse=True)
    picked, per_src = [], {}
    for c in pool:
        if per_src.get(c["row"]["id"], 0) >= 2:
            continue
        picked.append(c)
        per_src[c["row"]["id"]] = per_src.get(c["row"]["id"], 0) + 1
        if len(picked) >= need:
            break
    return picked


def labels_for(llm, theme: str, items: list[dict]) -> list[str]:
    facts = " ".join(f"{i['row']['title']} {i['row']['year'] or ''} {i['what']}" for i in items)
    if llm.available():
        clips = "\n".join(f"{k + 1}. {i['row']['title']} ({i['row']['year'] or 'year unknown'}): "
                          f"{i['what'] or 'no notes'}" for k, i in enumerate(items))
        data = parse_json(llm.text(LABEL_PROMPT.format(n=len(items), theme=theme, clips=clips), as_json=True))
        labels = (data or {}).get("labels") or []
        if len(labels) == len(items) and all(_grounded(str(lb), facts) for lb in labels):
            return [str(lb) for lb in labels]
    return [f"{i['row']['year']}" if i["row"]["year"] else f"Moment {k + 1}" for k, i in enumerate(items)]


def overlay_ass(title: dict, order: list[int], items_by_rank: dict, bounds: list[float], total: float,
                accent: str, W=1080, H=1920) -> str:
    acc = ass_color(accent)
    l1, l2 = esc(title["line1"]), esc(title["line2"])
    kw = title.get("keyword", "")
    if kw and kw.lower() in title["line1"].lower() and kw.lower() not in title["line2"].lower():
        i = title["line1"].lower().find(kw.lower())
        l1 = (esc(title["line1"][:i]) + "{\\c" + acc + "&}" + esc(title["line1"][i:i + len(kw)]) +
              "{\\c&H00FFFFFF&}" + esc(title["line1"][i + len(kw):]))
    if kw and kw.lower() in title["line2"].lower():
        i = title["line2"].lower().find(kw.lower())
        l2 = (esc(title["line2"][:i]) + "{\\c" + acc + "&}" + esc(title["line2"][i:i + len(kw)]) +
              "{\\c&H00FFFFFF&}" + esc(title["line2"][i + len(kw):]))
    ev = [f"Dialogue: 2,{ts(0)},{ts(total)},Title,,0,0,0,,{{\\fad(200,0)}}{l1}\\N{l2}",
          f"Dialogue: 3,{ts(0)},{ts(total)},Small,,0,0,0,,{{\\an7\\pos(0,0)\\p1\\c{acc}&\\fscx0"
          f"\\t(0,{int(total * 1000)},\\fscx100)}}{rect(W, 8)}{{\\p0}}"]
    for k, rank in enumerate(order):
        a, b = bounds[k], bounds[k + 1]
        it = items_by_rank[rank]
        big = rank == 1
        badge = f"#{rank}"
        ev.append(f"Dialogue: 4,{ts(a + 0.05)},{ts(b)},Badge{'1' if big else ''},,0,0,0,,"
                  f"{{\\an1\\pos(70,{H - 360})\\fscx40\\fscy40\\t(0,180,\\fscx112\\fscy112)\\t(180,300,\\fscx100\\fscy100)}}"
                  f"{badge}")
        ev.append(f"Dialogue: 4,{ts(a + 0.2)},{ts(b)},Label,,0,0,0,,{{\\an1\\move({370 if big else 340},{H - 380},"
                  f"{320 if big else 290},{H - 380},0,250)"
                  f"\\fad(150,100)}}{esc(it['label'])}")
        credit = f"{it['row']['title'][:44]}{f' ({it['row']['year']})' if it['row']['year'] else ''} · Public domain"
        ev.append(f"Dialogue: 1,{ts(a)},{ts(b)},Small,,0,0,0,,{esc(credit)}")
        if k:                                   # quick flash between items
            ev.append(f"Dialogue: 5,{ts(a)},{ts(a + 0.16)},Small,,0,0,0,,{{\\an7\\pos(0,0)\\p1\\c&HFFFFFF&"
                      f"\\alpha&H60&\\t(0,160,\\alpha&HFF&)}}{rect(W, H)}{{\\p0}}")
    return "\n".join([
        "[Script Info]", "ScriptType: v4.00+", f"PlayResX: {W}", f"PlayResY: {H}", "WrapStyle: 0",
        "ScaledBorderAndShadow: yes", "", "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, "
        "Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, "
        "MarginL, MarginR, MarginV, Encoding",
        "Style: Title,Inter,80,&H00FFFFFF,&H00FFFFFF,&H00000000,&H64000000,-1,0,0,0,100,100,0,0,1,7,2,8,60,60,190,1",
        f"Style: Badge,Inter,120,&H00101010,&H00101010,{acc},{acc},-1,0,0,0,100,100,0,0,3,14,0,1,60,60,0,1",
        "Style: Badge1,Inter,140,&H00101010,&H00101010,&H0000D7FF,&H0000D7FF,-1,0,0,0,100,100,0,0,3,18,0,1,60,60,0,1",
        "Style: Label,Inter,58,&H00FFFFFF,&H00FFFFFF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,6,2,1,60,60,0,1",
        "Style: Small,Inter,28,&H30FFFFFF,&H30FFFFFF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,2,0,2,60,60,110,1",
        "", "[Events]", "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
        *ev, ""])


def build(agent, ch, theme: dict, seed: int) -> int | None:
    cfg, db = agent.cfg, agent.db
    rk = ch.ranking
    n = int(rk.get("items", 5))
    item_len = rk.get("item_seconds", [6, 9])
    picks = candidates(agent, ch, theme, n, item_len)
    if len(picks) < max(3, n - 1):
        log.info("ranking %r: only %d usable moments - need more downloaded footage first", theme["name"], len(picks))
        return None
    n = len(picks)
    picks.sort(key=lambda c: c["score"], reverse=True)              # rank 1 = best
    for c, label in zip(picks, labels_for(agent.llm, theme["name"], picks)):
        c["label"] = label
    items_by_rank = {r + 1: c for r, c in enumerate(picks)}
    rng = random.Random(seed)
    order = play_order(n, rng)

    years = sorted({c["row"]["year"] for c in picks if c["row"]["year"]})
    era = f"{years[0]}-{years[-1]}" if len(years) > 1 else (str(years[0]) if years else "")
    facts = (f"Top {n} {theme['name']} from public-domain archive footage {era}. " +
             " ".join(f"#{r}: {c['label']} - {c['row']['title']} ({c['row']['year'] or 'year unknown'}): {c['what']}."
                      for r, c in items_by_rank.items()))
    item = SimpleNamespace(source="internet_archive", source_id=f"ranking:{theme['name']}:{seed}",
                           title=f"Top {n} {theme['name']}", year=None, text=facts, licence="Public domain",
                           page_url="", credit="")
    copy = copywriter.write(agent.llm, item, {"what_happens": theme.get("angle", "")}, ch.voice, seed)
    if copy.get("source") == "template":           # no model: plain, honest ranking packaging
        words = theme["name"].split()
        copy.update({"hook_line1": f"Top {n}", "hook_line2": " ".join(words[:3]), "keyword": f"Top {n}",
                     "title": f"Top {n} {theme['name']}" + (f" ({era})" if era else ""),
                     "description": f"{n} moments from public-domain archive footage, ranked. "
                                    f"{theme.get('angle', '').capitalize()}."})
    hook = {"line1": copy["hook_line1"], "line2": copy["hook_line2"], "keyword": copy.get("keyword", "")}

    out_dir = cfg.paths.data / "renders" / ch.name
    work = cfg.paths.data / "work" / f"rank_{seed}"
    work.mkdir(parents=True, exist_ok=True)
    parts, bounds = [], [0.0]
    for k, rank in enumerate(order):
        c = items_by_rank[rank]
        year = c["row"]["year"]
        original = cfg.compose.get("audio", "original") == "original" and not (
            year and year < cfg.compose.get("mute_before_year", 1928))
        part = render_base(c["path"], c["start"], c["end"], work / f"{k:02d}.mp4", cfg, seed, None, None, original)
        parts.append(part)
        bounds.append(bounds[-1] + duration(part))
    (work / "list.txt").write_text("".join(f"file '{p.name}'\n" for p in parts))
    run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", "list.txt", "-c", "copy", "joined.mp4"], cwd=work)
    total = bounds[-1]
    accent = rng.choice(["#FFD60A", "#4CC9F0", "#2EC4B6", "#FF8C42"])
    (work / "overlay.ass").write_text(overlay_ass(hook, order, items_by_rank, bounds, total, accent), encoding="utf-8")
    from .stats.render import sfx
    sfx(bounds[1:-1], total, work / "sfx.wav", final_at=bounds[order.index(1)])
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"rank_{seed:08x}.mp4"
    run(["ffmpeg", "-y", "-i", "joined.mp4", "-i", "sfx.wav", "-filter_complex",
         "[0:v]subtitles=filename=overlay.ass,format=yuv420p[v];[0:a][1:a]amix=inputs=2:duration=first:"
         "normalize=0,alimiter=limit=0.95[a]", "-map", "[v]", "-map", "[a]", "-c:v", "libx264", "-preset",
         cfg.compose.preset, "-crf", str(cfg.compose.crf), "-c:a", "aac", "-b:a", "160k", "-map_metadata", "-1",
         "-fflags", "+bitexact", "-movflags", "+faststart", str(out.resolve())], cwd=work)
    for f in work.iterdir():
        f.unlink()
    work.rmdir()

    sources_lines = [f"#{r}: {c['row']['title']}" + (f" ({c['row']['year']})" if c['row']['year'] else "") +
                     f" - {c['row']['licence']} - {c['row']['page_url']}" for r, c in items_by_rank.items()]
    desc = "\n".join([copy["description"], "", "Clips", *sources_lines, "",
                      "Footage is original archival film; edited for this channel (cut, reframed, ranked, captioned)."]
                     + (["", " ".join("#" + re.sub(r"\W", "", t.title()) for t in list(ch.tags)[:3])] if ch.tags else []))
    meta = {"title": copy["title"], "hook": [copy["hook_line1"], copy["hook_line2"]], "keyword": copy.get("keyword"),
            "beats": [], "copy_source": copy.get("source"), "packaging_score": copy.get("score"),
            "alternates": copy.get("alternates", []), "description": desc[:5000], "tags": list(ch.tags),
            "format": "ranking", "theme": theme["name"], "order": order,
            "items": [{"rank": r, "label": c["label"], "source": c["row"]["title"], "start": c["start"],
                       "end": c["end"]} for r, c in items_by_rank.items()],
            "source_title": f"Top {n} {theme['name']}", "audio": "original+sfx"}
    status = "review" if cfg.review.require_approval else "approved"
    first = items_by_rank[1]
    clip_id = db.add_clip(ch.name, first["row"]["id"], 0, total, first["score"], {"reason": "ranking"}, out, meta,
                          status)
    db.add_segments(clip_id, [(c["row"]["id"], c["start"], c["end"]) for c in picks])
    log.info("ranking Short #%d for %s: %r (%d items, %.1fs, order %s)", clip_id, ch.name, copy["title"], n, total,
             order)
    if status == "review":
        from .review import notify_new_clip
        notify_new_clip(cfg, db.clip(clip_id))
    return clip_id


def make(agent, ch, want: int) -> int:
    rk = ch.get("ranking") or {}
    if not rk.get("enabled") or want <= 0:
        return 0
    themes = list(rk.get("themes", []))
    if not themes:
        return 0
    from datetime import date
    import hashlib
    rng = random.Random(date.today().toordinal() + int(hashlib.sha1(ch.name.encode()).hexdigest()[:6], 16))
    rng.shuffle(themes)
    made = 0
    for th in themes:
        if made >= want:
            break
        seed = rng.randrange(1, 2 ** 31)
        if build(agent, ch, th, seed):
            made += 1
    return made
