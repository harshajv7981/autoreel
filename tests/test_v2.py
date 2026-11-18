"""v2 features: trend radar, fact grounding, audio rules, layouts, weekly long-form."""
import json
import re
import subprocess

import pytest

from autoreel import copywriter, trends
from autoreel.agent import Agent
from autoreel.config import load_config
from autoreel.db import DB
from autoreel.ff import probe
from autoreel.longform import build_weekly
from autoreel.llm import NullLLM
from autoreel.publisher import publish_approved
from autoreel.sources.base import SourceItem

TOP = {"items": [{"project": "en.wikipedia", "access": "all-access", "year": "2026", "month": "11", "day": "17",
                  "articles": [
                      {"article": "Main_Page", "views": 5000000, "rank": 1},
                      {"article": "Special:Search", "views": 900000, "rank": 2},
                      {"article": "Artemis_II", "views": 640000, "rank": 3},
                      {"article": "Wicked:_For_Good_(film)", "views": 500000, "rank": 4},
                      {"article": "Frankenstein_(1931_film)", "views": 310000, "rank": 5},
                      {"article": "Deaths_in_2026", "views": 300000, "rank": 6},
                      {"article": "Manchester_United_F.C.", "views": 200000, "rank": 7},
                      {"article": "Zeppelin", "views": 150000, "rank": 8}]}]}
OTD = {"selected": [{"text": "The Holland Tunnel opens to traffic.", "year": 1927,
                     "pages": [{"title": "Holland_Tunnel", "normalizedtitle": "Holland Tunnel",
                                "extract": "The Holland Tunnel is a vehicular tunnel under the Hudson River opened in 1927.",
                                "content_urls": {"desktop": {"page": "https://en.wikipedia.org/wiki/Holland_Tunnel"}}}]}]}


@pytest.fixture()
def fake_wiki(monkeypatch):
    def http_json(url, params=None, retries=3):
        if "pageviews/top" in url:
            return TOP
        if "onthisday" in url:
            return OTD
        if "page/summary" in url:
            return {"extract": "Artemis II is a crewed lunar flyby mission by NASA.",
                    "content_urls": {"desktop": {"page": "https://en.wikipedia.org/wiki/Artemis_II"}}}
        raise AssertionError(url)
    monkeypatch.setattr(trends, "http_json", http_json)


def test_trend_candidates_filter_and_rank(project, fake_wiki):
    cfg = load_config(project / "config.yaml")
    titles = [t.title for t in trends.candidates(cfg)]
    assert "Artemis II" in titles and "Holland Tunnel" in titles and "Zeppelin" in titles
    for bad in ("Main Page", "Deaths in 2026", "Wicked: For Good (film)"):
        assert bad not in titles
    assert titles.index("Artemis II") < titles.index("Zeppelin")


def test_trend_plan_without_llm_routes_space_to_nasa(fake_wiki):
    t = trends.plan(trends.Topic("Artemis II", "trending", 1.0), NullLLM(), cutoff=1930)
    assert t.queries == {"internet_archive": "Artemis II", "nasa": "Artemis II"}
    z = trends.plan(trends.Topic("Zeppelin", "trending", 1.0, facts="A rigid airship."), NullLLM(), 1930)
    assert "nasa" not in z.queries


def test_relevance_check():
    item = SourceItem("internet_archive", "x", "Hindenburg zeppelin newsreel", 1936, "", "", "PD", "")
    assert trends.relevant(item, "zeppelin airship")
    assert not trends.relevant(item, "holland tunnel")


GOOD = {"hook_line1": "Before the tunnel", "hook_line2": "they took ferries", "keyword": "ferries",
        "title": "What crossing the Hudson looked like",
        "description": "Old footage of the river crossing. The tunnel changed that in 1927.",
        "beats": ["The Holland Tunnel opened in 1927", "It runs under the Hudson River"]}
FACTS = "Holland Tunnel opened in 1927 vehicular tunnel under the Hudson River ferries"


def test_beats_and_numbers_are_grounded():
    assert copywriter.lint(GOOD, FACTS) == []
    bad = {**GOOD, "beats": ["It cost 48 million dollars to build", "Opened in 1927"]}
    assert any("'48'" in p for p in copywriter.lint(bad, FACTS))
    bad = {**GOOD, "beats": ["Only one beat"]}
    assert any("2-3" in p for p in copywriter.lint(bad, FACTS))


def mean_volume(path) -> float:
    out = subprocess.run(["ffmpeg", "-hide_banner", "-i", str(path), "-af", "volumedetect", "-f", "null", "-"],
                         capture_output=True, text=True).stderr
    return float(re.search(r"mean_volume: (-?[\d.]+|-inf)", out).group(1).replace("-inf", "-200"))


def test_original_audio_kept_for_sound_era(project):
    cfg = load_config(project / "config.yaml")
    a = Agent(cfg, DB(cfg.paths.state_db))
    assert a.fill_channel(cfg.channels[0]) >= 1
    row = a.db.clips("review")[0]
    assert json.loads(row["meta"])["audio"] == "original"
    assert mean_volume(row["video"]) > -40           # the film's own tone is audible


def test_silent_era_film_is_muted(tmp_path, film):
    from tests.conftest import write_project
    proj = write_project(tmp_path, film, year=1921)
    cfg = load_config(proj / "config.yaml")
    a = Agent(cfg, DB(cfg.paths.state_db))
    a.fill_channel(cfg.channels[0])
    row = a.db.clips("review")[0]
    assert json.loads(row["meta"])["audio"] == "none"
    assert mean_volume(row["video"]) < -80


def test_weekly_longform(project):
    cfg = load_config(project / "config.yaml")
    db = DB(cfg.paths.state_db)
    a = Agent(cfg, db)
    a.fill_channel(cfg.channels[0])
    assert build_weekly(cfg, db, a.llm, cfg.channels[0]) is None        # nothing approved yet
    for r in db.clips("review"):
        db.set_clip(r["id"], status="approved")
    cid = build_weekly(cfg, db, a.llm, cfg.channels[0])
    assert cid
    row = db.clip(cid)
    assert row["kind"] == "long"
    v = next(s for s in probe(row["video"])["streams"] if s["codec_type"] == "video")
    assert (int(v["width"]), int(v["height"])) == (1920, 1080)
    meta = json.loads(row["meta"])
    assert "0:00 Intro" in meta["description"] and "Sources" in meta["description"]
    assert len(meta["parts"]) >= 2
    assert build_weekly(cfg, db, a.llm, cfg.channels[0]) is None        # once a week
    db.set_clip(cid, status="approved")
    assert publish_approved(cfg, db, dry_run=True) == 0                 # dry run uploads nothing


def test_layout_rotation_is_varied():
    from autoreel.compose import style_for
    layouts = {style_for(s)["layout"] for s in range(40)}
    frames = {style_for(s)["frame"] for s in range(40)}
    assert len(layouts) == 6 and len(frames) == 3
