"""Stat Shorts: football + cricket stories, rendering, trend priority, full agent cycle."""
import json
import sqlite3
from pathlib import Path

import yaml

from autoreel.agent import Agent
from autoreel.config import load_config
from autoreel.db import DB
from autoreel.ff import duration, probe
from autoreel.stats import cricket, football, maker, render
from autoreel.trends import Topic
from tests.cricsheet_fixture import make_zip


def season_file(cache: Path, season: str, results):
    cache.mkdir(parents=True, exist_ok=True)
    teams = ["Arsenal FC", "Chelsea FC", "Liverpool FC", "Everton FC"]
    matches = []
    k = 0
    for i, a in enumerate(teams):
        for j, b in enumerate(teams):
            if a != b:
                hg, ag = results[k % len(results)]
                matches.append({"date": f"{season[:4]}-09-{(k % 28) + 1:02d}", "team1": a, "team2": b,
                                "score": {"ft": [hg, ag]} if k % 5 else [hg, ag]})   # both score formats
                k += 1
    (cache / f"en.1_{season}.json").write_text(json.dumps({"name": "test", "matches": matches}))


def football_cache(tmp: Path) -> Path:
    cache = tmp / "football"
    season_file(cache, "2010-11", [(2, 1), (0, 0), (5, 0), (1, 3)])
    season_file(cache, "2011-12", [(3, 1), (1, 1), (0, 2), (6, 1)])
    return cache


def test_football_stories(tmp_path, monkeypatch):
    monkeypatch.setattr(football, "season_names", lambda first, today=None: ["2010-11", "2011-12"])
    st = football.stories("en.1", football_cache(tmp_path), 2010)
    kinds = {s.kind for s in st}
    assert {"bar_race", "ranking", "duel"} <= kinds
    race = next(s for s in st if s.id.startswith("fb:en.1:alltime"))
    assert set(race.series) == {"Arsenal", "Chelsea", "Liverpool", "Everton"}     # " FC" cleaned
    assert all(len(v) == 2 for v in race.series.values())
    assert "public domain" in race.licence.lower()


def test_cricket_stories(tmp_path):
    db = sqlite3.connect(":memory:")
    assert cricket.load_zip(make_zip(tmp_path / "ipl.zip"), "ipl", db) == 18
    st = {s.id.split(":")[2]: s for s in cricket.stories("ipl", db, min_balls_economy=60)}
    assert {"runs_race", "wickets_race", "sixes", "top_scores", "fastest_fifties", "economy", "duel"} <= set(st)
    assert st["runs_race"].steps == ["2023", "2024", "2025"]
    # facts carry every number shown in rankings
    for name, value, _ in st["sixes"].ranking:
        assert f"{name} {value} sixes" in st["sixes"].facts
    assert cricket.load_zip(tmp_path / "ipl.zip", "ipl", db) == 0                # no double loading


def test_render_all_formats(tmp_path):
    db = sqlite3.connect(":memory:")
    cricket.load_zip(make_zip(tmp_path / "ipl.zip", seasons=("2024", "2025"), per_season=2), "ipl", db)
    st = {s.kind: s for s in cricket.stories("ipl", db, min_balls_economy=30)}
    for kind, s in st.items():
        if kind == "ranking":
            s.ranking = s.ranking[:3]
        out = render.render(s, {"line1": "Who hits", "line2": "the most sixes", "keyword": "sixes"},
                            tmp_path / f"{kind}.mp4", seed=1)
        info = probe(out)
        v = next(x for x in info["streams"] if x["codec_type"] == "video")
        assert (int(v["width"]), int(v["height"])) == (1080, 1920)
        assert any(x["codec_type"] == "audio" for x in info["streams"])
        assert duration(out) <= 58


def test_trend_priority_ipl_not_premier_league(tmp_path, monkeypatch):
    monkeypatch.setattr(football, "season_names", lambda first, today=None: ["2010-11", "2011-12"])
    fb = football.stories("en.1", football_cache(tmp_path), 2010)
    db = sqlite3.connect(":memory:")
    cricket.load_zip(make_zip(tmp_path / "ipl.zip"), "ipl", db)
    ck = cricket.stories("ipl", db, 60)
    ipl = Topic("Indian Premier League", "#9 most-read on Wikipedia yesterday", 2.0, kind="sport")
    ranked = maker.prioritise(fb + ck, [ipl], seed=1)
    top = ranked[0]
    assert top[1].sport == "cricket" and top[2] is ipl
    assert all(m is None for _, s, m in ranked if s.sport == "football")


def test_stats_channel_cycle(project, tmp_path, monkeypatch):
    cfg_path = project / "config.yaml"
    data = yaml.safe_load(cfg_path.read_text())
    data["channels"] = [{"name": "stats", "enabled": True, "token": "./secrets/t.json", "longform": False,
                         "voice": "plain", "clip_seconds": [15, 45], "buffer": 2, "tags": ["cricket stats"],
                         "sources": [{"type": "stats", "sport": "cricket", "competitions": ["ipl"],
                                      "use_trends": False, "min_balls_economy": 60}]}]
    cfg_path.write_text(yaml.safe_dump(data))
    cfg = load_config(cfg_path)
    z = make_zip(tmp_path / "ipl_json.zip", seasons=("2024", "2025"), per_season=2)
    monkeypatch.setattr(cricket, "download", lambda comp, cache: z)
    a = Agent(cfg, DB(cfg.paths.state_db))
    assert a.fill_channel(cfg.channels[0]) == 2
    rows = a.db.clips("review")
    meta = json.loads(rows[0]["meta"])
    assert "Cricsheet" in meta["description"] and "No match footage" in meta["description"]
    assert a.fill_channel(cfg.channels[0]) == 0               # buffer full, nothing duplicated
    from autoreel.longform import build_weekly
    for r in rows:
        a.db.set_clip(r["id"], status="approved")
    assert build_weekly(cfg, a.db, a.llm, cfg.channels[0], force=True) is None   # long-form off for stats
