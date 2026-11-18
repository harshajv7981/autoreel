"""v3: Content ID traps, government films, 58s cap, scored packaging, motion graphics, thumbnails."""
import json

from autoreel import copywriter
from autoreel.agent import Agent
from autoreel.compose.ass import build_ass
from autoreel.config import Cfg, load_config
from autoreel.db import DB
from autoreel.ff import probe
from autoreel.longform import build_weekly
from autoreel.sources import internet_archive
from autoreel.sources.base import Policy
from tests.test_sources import IA_DOC, IA_META


def policy():
    return Policy(Cfg({"policy": Cfg({
        "blocked_terms": ["colorized"], "trusted_pd_collections": ["prelinger"],
        "gov_collections": ["fedflix", "nasa"],
        "content_id_risk_terms": ["restored", " 4k", "british pathe", "periscope film", "new score"]})}))


def ia(**md):
    return internet_archive.to_item(IA_DOC, {**IA_META, "metadata": {**IA_META["metadata"], **md}})


def test_content_id_traps_rejected():
    for title in ("The Vagabond (restored)", "The Vagabond 4K", "The Vagabond with a new score"):
        ok, why = policy().check(ia(title=title))
        assert not ok and "Content ID risk" in why, title
    ok, why = policy().check(ia(description="Courtesy of Periscope Film LLC archive"))
    assert not ok
    assert policy().check(ia())[0]            # the plain 1916 print is fine


def test_us_government_film_any_year():
    ok, why = policy().check(ia(year="1962", collection=["FedFlix"], licenseurl=""))
    assert ok and "government" in why
    assert not policy().check(ia(year="1962", collection=["opensource_movies"], licenseurl=""))[0]


def test_shorts_capped_under_a_minute(project):
    import yaml
    cfg_path = project / "config.yaml"
    data = yaml.safe_load(cfg_path.read_text())
    data["channels"][0]["clip_seconds"] = [40, 90]
    cfg_path.write_text(yaml.safe_dump(data))
    cfg = load_config(cfg_path)
    assert cfg.channels[0].clip_seconds[1] <= 58


FACTS = "R34 airship crossed the Atlantic 1919 108 hours Scotland New York"


class OptionsLLM:
    def available(self):
        return True

    def text(self, prompt, as_json=False):
        return json.dumps({
            "options": [
                {"hook_line1": "Watch this", "hook_line2": "old clip", "keyword": "clip",
                 "title": "A cool moment from an old film"},
                {"hook_line1": "Before planes could", "hook_line2": "this airship did", "keyword": "airship",
                 "title": "The airship that crossed the Atlantic in 1919"},
                {"hook_line1": "The record", "hook_line2": "nobody remembers", "keyword": "record",
                 "title": "The record Lindbergh never broke"},            # invented name -> rejected
            ],
            "description": "The R34 flew from Scotland to New York in 1919.",
            "beats": ["The R34 left Scotland in 1919", "108 hours later it reached New York"]})


def test_best_grounded_option_wins():
    from autoreel.sources.base import SourceItem
    item = SourceItem("internet_archive", "r34", "R34 airship newsreel", 1919, "", "", "PD", "",
                      text="R34 airship crossed the Atlantic Scotland New York 108 hours")
    copy = copywriter.write(OptionsLLM(), item, {}, "plain", seed=1)
    assert copy["title"] == "The airship that crossed the Atlantic in 1919"
    assert all("Lindbergh" not in a["title"] for a in copy["alternates"])
    assert copy["score"] > max(a["score"] for a in copy["alternates"])


def test_motion_graphics_events():
    style = {"font": "Inter", "accent": "#4CC9F0", "title_y": 220, "pop": False, "layout": "top_hook",
             "timeline": True, "progress": True}
    ass = build_ass({"line1": "The airship", "line2": "that beat planes", "keyword": "airship",
                     "beats": ["It crossed in 1919", "108 hours, no stops"], "credit": "c",
                     "year": 1919, "now_year": 2026}, style, 1080, 1920, 20)
    assert "\\move(" in ass                      # sliding callouts and moving timeline marker
    assert "107 YEARS AGO" in ass
    assert "\\t(0,20000,\\fscx100)" in ass       # progress bar fills over the clip
    assert "&H00F0C94C&\\b1}108" in ass           # numbers highlighted in the accent colour


def test_longform_has_thumbnail(project):
    cfg = load_config(project / "config.yaml")
    db = DB(cfg.paths.state_db)
    a = Agent(cfg, db)
    a.fill_channel(cfg.channels[0])
    for r in db.clips("review"):
        db.set_clip(r["id"], status="approved")
    cid = build_weekly(cfg, db, a.llm, cfg.channels[0])
    meta = json.loads(db.clip(cid)["meta"])
    v = probe(meta["thumbnail"])["streams"][0]
    assert (int(v["width"]), int(v["height"])) == (1280, 720)
