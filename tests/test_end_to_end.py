"""Full agent cycle on a local public-domain source with no LLM: make -> review -> approve -> dry-run upload."""
import json
import subprocess

from autoreel.agent import Agent
from autoreel.config import load_config
from autoreel.db import DB
from autoreel.ff import probe
from autoreel.publisher import build_body, next_slot, publish_approved
from autoreel.review import write_review_page


def test_agent_cycle(project):
    cfg = load_config(project / "config.yaml")
    db = DB(cfg.paths.state_db)
    made = Agent(cfg, db).fill_channel(cfg.channels[0])
    assert made >= 1

    rows = db.clips("review")
    assert rows, "clips should wait for review by default"
    row = rows[0]
    info = probe(row["video"])
    v = next(s for s in info["streams"] if s["codec_type"] == "video")
    assert (int(v["width"]), int(v["height"])) == (1080, 1920)
    assert any(s["codec_type"] == "audio" for s in info["streams"])
    tags = info["format"].get("tags", {})
    assert not any(k in tags for k in ("encoder", "comment", "hyperframes_version")), tags

    meta = json.loads(row["meta"])
    assert "Licence: Public domain (test)" in meta["description"]

    page = write_review_page(cfg, db)
    assert f"#{row['id']}" in page.read_text()

    # nothing uploads until approved
    assert publish_approved(cfg, db, dry_run=True) == 0
    db.set_clip(row["id"], status="approved")
    publish_approved(cfg, db, dry_run=True)
    assert db.clip(row["id"])["status"] == "approved"          # dry run changes nothing

    # second fill keeps the buffer, doesn't duplicate overlapping moments
    Agent(cfg, db).fill_channel(cfg.channels[0])
    clips = db.clips()
    spans = sorted((c["start"], c["end"]) for c in clips)
    assert all(a[1] <= b[0] for a, b in zip(spans, spans[1:])), spans


def test_upload_body_is_honest_and_scheduled(project):
    cfg = load_config(project / "config.yaml")
    key, when = next_slot(cfg, set(), clip_id=5)
    body = build_body(cfg, {"title": "t", "description": "d", "tags": ["a"]}, when)
    assert body["status"]["containsSyntheticMedia"] is False
    assert body["status"]["privacyStatus"] == "private" and body["status"]["publishAt"] == when
    key2, _ = next_slot(cfg, {key}, clip_id=6)
    assert key2 != key


def test_cli_make_review_approve(project):
    from pathlib import Path
    run = Path(__file__).resolve().parents[1] / "run.py"
    cfg = str(project / "config.yaml")

    def cli(*a):
        p = subprocess.run(["python3", str(run), *a, "-c", cfg, "--dry-run"],
                           capture_output=True, text=True, timeout=600)
        assert p.returncode == 0, p.stderr[-2000:]
        return p.stdout + p.stderr

    cli("make")
    out = cli("review")
    assert "#1" in out and "review page" in out
    assert "#1: approved" in cli("approve", "1")
    assert "[dry-run] #1" in cli("upload")
    assert "approved" in cli("status")


class FakeLocalLLM:
    """Stands in for Ollama: rejects the first moment as a title card, keeps the rest, writes copy."""
    name = "fake"

    def __init__(self):
        self.judged = 0

    def available(self):
        return True

    def vision(self, prompt, images, as_json=False):
        assert len(images) == 4 and all(p.exists() for p in images)
        self.judged += 1
        if self.judged == 1:
            return json.dumps({"what_happens": "a title card", "score": 2, "keep": False, "reason": "text only"})
        return json.dumps({"what_happens": "cells swarm across the frame", "score": 8, "keep": True,
                           "reason": "constant motion"})

    def text(self, prompt, as_json=False):
        assert "cells swarm" in prompt
        return json.dumps({"hook_line1": "Watch them", "hook_line2": "take over the frame",
                           "keyword": "take over", "title": "The busiest shot in The Vagabond",
                           "description": "Every cell in this shot moves at once. Keep an eye on the left edge.",
                           "beats": ["A tramp violinist in 1916", "Every cell moves at once"]})


def test_internet_archive_path_with_vision_judge(project, film, monkeypatch):
    from autoreel import agent as agent_mod
    from autoreel.sources import internet_archive
    from tests.test_sources import IA_DOC, IA_META

    cfg = load_config(project / "config.yaml")
    ch = cfg.channels[0]
    ch.sources = [{"type": "internet_archive", "query": "x"}]
    item = internet_archive.to_item(IA_DOC, IA_META)
    monkeypatch.setattr(agent_mod.sources, "discover", lambda spec, page: [item] if page == 1 else [])
    monkeypatch.setattr(agent_mod.sources, "fetch", lambda it, d: film)
    a = Agent(cfg, DB(cfg.paths.state_db))
    a.llm = FakeLocalLLM()
    assert a.fill_channel(ch) == ch.buffer
    rows = a.db.clips("review")
    meta = json.loads(rows[0]["meta"])
    assert meta["copy_source"].startswith("llm")
    assert meta["title"] == "The busiest shot in The Vagabond"
    assert "archive.org/details/CC_1916_07_10_TheVagabond" in meta["description"]
    assert a.llm.judged >= 3                     # one rejection, then keeps
