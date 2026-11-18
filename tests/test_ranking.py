"""Ranking Shorts: shuffled order with #1 last, licensed footage only, no reused spans."""
import json
import random
import shutil

import yaml

from autoreel import ranking
from autoreel.agent import Agent
from autoreel.config import load_config
from autoreel.db import DB
from autoreel.ff import duration, probe


def test_play_order_rules():
    for n in (3, 4, 5, 7):
        for s in range(30):
            o = ranking.play_order(n, random.Random(s))
            assert sorted(o) == list(range(1, n + 1)) and o[-1] == 1
            assert o[:-1] != sorted(o[:-1], reverse=True)          # never a plain countdown


def boxing_project(project, film):
    inbox = project / "inbox"
    shutil.copy(film, inbox / "bout_a.mp4")
    (inbox / "bout_a.json").write_text('{"title": "Heavyweight boxing bout", "year": 1935}')
    (inbox / "old_film.json").write_text('{"title": "Boxing championship newsreel", "year": 1931}')
    cfg_path = project / "config.yaml"
    data = yaml.safe_load(cfg_path.read_text())
    ch = data["channels"][0]
    ch["clip_seconds"] = [6, 8]
    ch["buffer"] = 1
    ch["ranking"] = {"enabled": True, "share": 0.34, "items": 3, "item_seconds": [3, 4],
                     "themes": [{"name": "knockouts from old newsreels", "keywords": ["boxing", "bout"]}]}
    cfg_path.write_text(yaml.safe_dump(data))
    return load_config(cfg_path)


def test_ranking_short(project, film):
    cfg = boxing_project(project, film)
    ch = cfg.channels[0]
    a = Agent(cfg, DB(cfg.paths.state_db))
    from autoreel import sources
    for item in sources.discover(ch.sources[0]):        # licence-check and process both local films
        a.process_item(ch, item, 1)
    assert len(a.db.usable_sources()) == 2
    cid = ranking.build(a, ch, ch.ranking.themes[0], seed=11)
    assert cid
    row = a.db.clip(cid)
    meta = json.loads(row["meta"])
    assert meta["format"] == "ranking" and meta["order"][-1] == 1 and len(meta["order"]) == 3
    v = next(s for s in probe(row["video"])["streams"] if s["codec_type"] == "video")
    assert (int(v["width"]), int(v["height"])) == (1080, 1920)
    assert duration(row["video"]) <= 58
    assert "#1:" in meta["description"] and "Public domain" in meta["description"]

    # spans used by the ranking are never reused, by single Shorts or by the next ranking
    used = {(r["source_row"], r["start"], r["end"]) for r in a.db.c.execute("SELECT * FROM segments")}
    assert len(used) == 3
    cid2 = ranking.build(a, ch, ch.ranking.themes[0], seed=12)
    if cid2:
        later = {(r["source_row"], r["start"], r["end"]) for r in
                 a.db.c.execute("SELECT * FROM segments WHERE clip_id=?", (cid2,))}
        for src, s, e in later:
            assert not any(src == s0 and s < e0 and e > st0 for s0, st0, e0 in used)


def test_ranking_needs_enough_footage(project, film):
    cfg = boxing_project(project, film)
    ch = cfg.channels[0]
    a = Agent(cfg, DB(cfg.paths.state_db))
    assert ranking.build(a, ch, ch.ranking.themes[0], seed=1) is None   # nothing downloaded yet
