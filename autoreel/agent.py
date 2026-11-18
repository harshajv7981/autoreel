"""The agent loop.

For each channel it keeps a buffer of finished Shorts ahead of the posting schedule:
  discover -> licence check -> download -> find moments -> judge (vision model)
  -> write copy (draft, lint, retry) -> compose -> review queue -> scheduled upload.

Decisions an LLM is good at (is this moment worth posting? what's happening? how would a
person caption it?) go to the model. Everything that must be reliable (licences, timing,
file handling, uploads) is plain code.
"""
import hashlib
import json
import random
from pathlib import Path

from . import copywriter, log, sources
from .compose import compose, pick_music
from .db import DB
from .ff import grab_frames
from .finder import find_moments
from .llm import make_llm, parse_json
from .review import notify_new_clip
from .sources.base import Policy

JUDGE_PROMPT = """You are screening clips for a YouTube Shorts channel. Channel voice: {voice}
These {n} frames are from one {length:.0f}-second clip of "{title}" ({year}), in order.

Return JSON:
"what_happens": one plain factual sentence describing the action you can see (no guessing names),
"score": 0-10, how likely a viewer is to watch to the end (clear action, a gag, a reveal, motion),
"keep": true or false,
"reason": short reason.

Set keep=false if the frames are mostly a title card or text, blank or dark, show a TV-channel logo or
watermark, show real injury or cruelty, or show nothing happening."""


def seed_for(*parts) -> int:
    return int(hashlib.sha1("|".join(map(str, parts)).encode()).hexdigest()[:8], 16)


class Agent:
    def __init__(self, cfg, db: DB | None = None):
        self.cfg = cfg
        self.db = db or DB(cfg.paths.state_db)
        self.llm = make_llm(cfg.llm)
        self.policy = Policy(cfg)
        self.downloads = cfg.paths.data / "downloads"
        self.work = cfg.paths.data / "work"
        self.renders = cfg.paths.data / "renders"

    # ------------------------------------------------------------ judging
    def judge(self, video: Path, start: float, end: float, item, voice: str) -> dict:
        motion_only = {"what_happens": "", "score": None, "keep": True, "reason": "motion score only"}
        if not self.llm.available():
            return motion_only
        L = end - start
        frames = grab_frames(video, [start + L * f for f in (0.12, 0.38, 0.62, 0.88)],
                             self.work / f"frames_{seed_for(item.source_id, start)}")
        raw = self.llm.vision(JUDGE_PROMPT.format(voice=voice, n=len(frames), length=L,
                                                  title=item.title, year=item.year or "unknown"),
                              frames, as_json=True)
        verdict = parse_json(raw)
        if not verdict:
            return motion_only
        verdict["keep"] = bool(verdict.get("keep", True)) and float(verdict.get("score") or 0) >= 5
        return verdict

    # ------------------------------------------------------------ one source -> clips
    def clips_from_source(self, ch, item, source_row: int, video: Path, want: int) -> int:
        lo, hi = ch.clip_seconds
        existing = self.db.clips_from_source(source_row)
        room = self.cfg.policy.max_clips_per_source - len(existing)
        if room <= 0:
            return 0
        made = 0
        for m in find_moments(video, lo, hi, top_k=self.cfg.finder.candidates_per_source,
                              fps=self.cfg.finder.analysis_fps, skip_edges=self.cfg.finder.skip_edges):
            if made >= min(want, room):
                break
            if any(m.start < c["end"] and m.end > c["start"] for c in existing):
                continue
            verdict = self.judge(video, m.start, m.end, item, ch.voice)
            if not verdict["keep"]:
                log.info("judge rejected %.0f-%.0fs: %s", m.start, m.end, verdict.get("reason"))
                continue
            seed = seed_for(ch.name, item.source_id, m.start)
            copy = copywriter.write(self.llm, item, verdict, ch.voice, seed)
            music = pick_music(self.cfg.paths.music, random.Random(seed))
            music_credit = self._music_credit(music)
            credit = f"{item.title[:48]}{f' ({item.year})' if item.year else ''} · {self._short_licence(item)}"
            out_dir = self.renders / ch.name
            out_dir.mkdir(parents=True, exist_ok=True)
            out = out_dir / f"{seed:08x}.mp4"
            compose(video, m.start, m.end, copy, credit, out, self.cfg, seed, music)
            meta = {
                "title": copy["title"], "hook": [copy["hook_line1"], copy["hook_line2"]],
                "keyword": copy.get("keyword"), "copy_source": copy.get("source"),
                "description": copywriter.description(copy, item, music_credit, list(ch.tags)),
                "tags": list(ch.tags), "music": str(music) if music else None,
                "source_title": item.title, "source_url": item.page_url, "licence": item.licence,
                "moment": m.features,
            }
            status = "review" if self.cfg.review.require_approval else "approved"
            clip_id = self.db.add_clip(ch.name, source_row, m.start, m.end, m.score, verdict, out, meta, status)
            existing = self.db.clips_from_source(source_row)
            made += 1
            log.info("clip #%d ready for %s: %r", clip_id, ch.name, copy["title"])
            if status == "review":
                notify_new_clip(self.cfg, self.db.clip(clip_id))
        return made

    @staticmethod
    def _short_licence(item) -> str:
        return {"internet_archive": "Public domain", "nasa": "Footage: NASA"}.get(item.source, item.licence[:40])

    @staticmethod
    def _music_credit(music: Path | None) -> str | None:
        if not music:
            return None
        side = music.with_suffix(".txt")      # optional credit line next to the track
        return side.read_text().strip() if side.exists() else music.stem.replace("_", " ")

    # ------------------------------------------------------------ one channel
    def fill_channel(self, ch) -> int:
        need = ch.buffer - self.db.pipeline_count(ch.name)
        if need <= 0:
            log.info("%s: buffer full", ch.name)
            return 0
        log.info("%s: making %d new Short(s)", ch.name, need)
        made = 0
        for spec in ch.sources:
            for page in (1, 2, 3):
                try:
                    items = list(sources.discover(spec, page))
                except Exception as e:  # noqa: BLE001
                    log.warning("%s source %s unavailable: %s", ch.name, spec.get("type"), e)
                    break
                if not items:
                    break
                for item in items:
                    if made >= need:
                        return made
                    row = self.db.get_source(item.source, item.source_id)
                    if row and row["status"] in ("rejected", "failed", "exhausted"):
                        continue
                    ok, reason = self.policy.check(item)
                    if not ok:
                        self.db.upsert_source(item, "rejected", reason)
                        log.info("skip %s: %s", item.source_id, reason)
                        continue
                    try:
                        path = sources.fetch(item, self.downloads)
                    except Exception as e:  # noqa: BLE001
                        self.db.upsert_source(item, "failed", f"download: {e}")
                        log.warning("download failed %s: %s", item.source_id, e)
                        continue
                    sid = self.db.upsert_source(item, "ok", reason, str(path))
                    try:
                        got = self.clips_from_source(ch, item, sid, path, need - made)
                    except Exception as e:  # noqa: BLE001
                        log.exception("processing failed for %s", item.source_id)
                        self.db.upsert_source(item, "failed", f"processing: {e}")
                        continue
                    if got == 0:
                        self.db.upsert_source(item, "exhausted", "no more usable moments")
                    made += got
        return made

    def fill_all(self) -> dict:
        return {ch.name: self.fill_channel(ch) for ch in self.cfg.channels if ch.get("enabled", True)}


def describe_clip(row) -> str:
    meta = json.loads(row["meta"])
    return (f"#{row['id']} [{row['channel']}] {row['status']}: {meta['title']}\n"
            f"   hook: {' / '.join(meta['hook'])}\n   from: {meta['source_title']} "
            f"{row['start']:.0f}-{row['end']:.0f}s\n   file: {row['video']}")
