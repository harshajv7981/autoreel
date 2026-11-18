"""Turns stat stories into posted Shorts: pick (trends first) -> grounded copy -> render -> queue."""
import copy as _copy
import random
import sqlite3
import time
from datetime import date
from pathlib import Path

from .. import copywriter, log, trends
from ..sources.base import SourceItem
from . import StatStory, cricket, football, render


def gather(cfg, spec) -> list[StatStory]:
    cache = cfg.paths.data / "stats"
    out: list[StatStory] = []
    if spec["sport"] == "football":
        for code in spec.get("leagues", ["en.1"]):
            try:
                out += football.stories(code, cache / "football", spec.get("first_season", 2010))
            except Exception as e:  # noqa: BLE001
                log.warning("football %s failed: %s", code, e)
    elif spec["sport"] == "cricket":
        cache.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(cache / "cricket.db")
        for comp in spec.get("competitions", ["ipl"]):
            z = cache / "cricket" / cricket.ZIPS[comp]
            fresh = z.exists() and time.time() - z.stat().st_mtime < 20 * 3600
            z = z if fresh else cricket.download(comp, cache / "cricket")
            if not z:
                continue
            try:
                added = cricket.load_zip(z, comp, db, spec.get("gender", "male"))
                if added:
                    log.info("cricket %s: %d new matches loaded", comp, added)
                out += cricket.stories(comp, db, spec.get("min_balls_economy", 600))
            except Exception as e:  # noqa: BLE001
                log.warning("cricket %s failed: %s", comp, e)
    return out


def prioritise(stories: list[StatStory], topics: list, seed: int) -> list[tuple[float, StatStory, object]]:
    """Trending competitions first; otherwise rotate formats so the feed doesn't repeat itself."""
    rng = random.Random(seed)
    scored = []
    for st in stories:
        best, match = 0.0, None
        for t in topics:
            hay = f" {t.title} {t.why} ".lower().replace("_", " ")
            if st.sport == "football" and ("cricket" in hay or "indian premier league" in hay):
                continue
            if any(f" {k} " in hay or f" {k}" in hay for k in st.keywords):
                if t.score > best:
                    best, match = t.score, t
        kind_bonus = {"bar_race": 0.3, "ranking": 0.25, "duel": 0.2}.get(st.kind, 0) * rng.random()
        scored.append((best + kind_bonus, st, match))
    return sorted(scored, key=lambda x: x[0], reverse=True)


def make(agent, ch, spec, need: int) -> int:
    cfg, db = agent.cfg, agent.db
    stories = gather(cfg, spec)
    if not stories:
        return 0
    topics = trends.todays_topics(cfg, agent.llm, cfg.paths.data, agent.policy.cutoff,
                                  max_topics=cfg.trends.max_topics, allow_sports=True, sports_only=True) \
        if spec.get("use_trends", True) else []
    made = 0
    for prio, st, topic in prioritise(stories, topics, seed=date.today().toordinal()):
        if made >= need:
            break
        if db.get_source("stats", st.id):
            continue
        item = SourceItem(source="stats", source_id=st.id, title=st.title, year=None, page_url=st.source_url,
                          download_url="", licence=st.licence, credit=f"Data: {st.source_name} ({st.source_url})",
                          text=st.facts, meta={"kind": st.kind, "competition": st.competition})
        t = None
        if topic is not None:              # trend context for the copy, without the old-footage rule
            t = _copy.copy(topic)
            t.kind = "stats"
            t.angle = f"Today's interest in {topic.title}, told with real numbers: {st.summary}"
        import hashlib
        seed = int(hashlib.sha1(st.id.encode()).hexdigest()[:7], 16)
        copy = copywriter.write(agent.llm, item, {"what_happens": st.summary}, ch.voice, seed, topic=t)
        out_dir = cfg.paths.data / "renders" / ch.name
        out_dir.mkdir(parents=True, exist_ok=True)
        out = out_dir / f"stat_{seed:07d}.mp4"
        try:
            render.render(st, {"line1": copy["hook_line1"], "line2": copy["hook_line2"],
                               "keyword": copy.get("keyword", "")}, out, seed)
        except Exception as e:  # noqa: BLE001
            log.exception("render failed for %s", st.id)
            db.upsert_source(item, "failed", f"render: {e}")
            continue
        sid = db.upsert_source(item, "ok", "statistics (facts are not copyrighted)", str(out))
        from ..ff import duration
        meta = {"title": copy["title"], "hook": [copy["hook_line1"], copy["hook_line2"]],
                "keyword": copy.get("keyword"), "beats": [], "copy_source": copy.get("source"),
                "packaging_score": copy.get("score"), "alternates": copy.get("alternates", []),
                "description": copywriter.description(copy, item, None, list(ch.tags), topic.url if topic else None)
                .replace("Footage is original archival film; edited for this channel (cut, reframed, captioned).",
                         "Animated chart made from match data. No match footage, photos or logos are used."),
                "tags": list(ch.tags) + st.keywords[:2], "audio": "sfx", "source_title": st.title,
                "source_url": st.source_url, "licence": st.licence, "stat_kind": st.kind,
                "topic": topic.to_json() if topic else None}
        status = "review" if cfg.review.require_approval else "approved"
        clip_id = db.add_clip(ch.name, sid, 0, duration(out), prio, {"reason": "stat story"}, out, meta, status)
        made += 1
        log.info("stat Short #%d for %s: %r%s", clip_id, ch.name, copy["title"],
                 f" (trend: {topic.title})" if topic else "")
        if status == "review":
            from ..review import notify_new_clip
            notify_new_clip(cfg, db.clip(clip_id))
    return made
