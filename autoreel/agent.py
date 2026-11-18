"""The agent loop.

For each channel it keeps a buffer of finished Shorts ahead of the posting schedule:
  (trends ->) discover -> licence check -> download -> find moments -> judge (vision model)
  -> write copy: hook + fact beats (draft, lint, retry) -> compose -> review queue -> scheduled upload.

Decisions an LLM is good at (which trend has real archival footage behind it, is this moment
worth posting, how would a person caption it) go to the model. Everything that must be reliable
(licences, facts allowed in captions, timing, files, uploads) is plain code.
"""
import hashlib
import json
import random
from pathlib import Path

from . import copywriter, log, sources, trends
from .compose import compose, pick_music
from .db import DB
from .ff import grab_frames
from .finder import find_moments
from .llm import make_llm, parse_json
from .review import notify_new_clip
from .sources.base import Policy

JUDGE_PROMPT = """You are screening clips for a YouTube Shorts channel. Channel voice: {voice}
These {n} frames are from one {length:.0f}-second clip of "{title}" ({year}), in order.
{topic}
Return JSON:
"what_happens": one plain factual sentence describing the action you can see (no guessing names),
"score": 0-10, how likely a viewer is to watch to the end (clear action, a gag, a reveal, motion){fit},
"keep": true or false,
"reason": short reason.

{hint}
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
    def judge(self, video: Path, start: float, end: float, item, voice: str, topic=None, hint: str = "") -> dict:
        motion_only = {"what_happens": "", "score": None, "keep": True, "reason": "motion score only"}
        if not self.llm.available():
            return motion_only
        L = end - start
        frames = grab_frames(video, [start + L * f for f in (0.12, 0.38, 0.62, 0.88)],
                             self.work / f"frames_{seed_for(item.source_id, start)}")
        topic_line = f"The Short is about today's topic: {topic.title}. Angle: {topic.angle}\n" if topic else ""
        fit = " and how well it fits the topic" if topic else ""
        raw = self.llm.vision(JUDGE_PROMPT.format(voice=voice, n=len(frames), length=L, title=item.title,
                                                  year=item.year or "unknown", topic=topic_line, fit=fit,
                                                  hint=hint),
                              frames, as_json=True)
        verdict = parse_json(raw)
        if not verdict:
            return motion_only
        verdict["keep"] = bool(verdict.get("keep", True)) and float(verdict.get("score") or 0) >= 5
        return verdict

    def _audio(self, item) -> tuple[bool, Path | None, int]:
        """(use original audio, music track, seed salt) following compose.audio."""
        mode = self.cfg.compose.get("audio", "original")
        if mode == "original":
            silent_era = item.year and item.year < self.cfg.compose.get("mute_before_year", 1928)
            # A pre-talkie film's soundtrack was added decades later and is usually copyrighted.
            return (not silent_era), None, 0
        return False, None, 1 if mode == "none" else 2

    # ------------------------------------------------------------ one source -> clips
    def clips_from_source(self, ch, item, source_row: int, video: Path, want: int, topic=None) -> int:
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
            if any(m.start < b and m.end > a for a, b in self.db.used_spans(source_row)):
                continue
            verdict = self.judge(video, m.start, m.end, item, ch.voice, topic, ch.get("judge_hint") or "")
            if not verdict["keep"]:
                log.info("judge rejected %.0f-%.0fs: %s", m.start, m.end, verdict.get("reason"))
                continue
            seed = seed_for(ch.name, item.source_id, m.start)
            copy = copywriter.write(self.llm, item, verdict, ch.voice, seed, topic=topic)
            original, _, salt = self._audio(item)
            music = pick_music(self.cfg.paths.music, random.Random(seed)) if salt == 2 else None
            music_credit = self._music_credit(music)
            credit = f"{item.title[:48]}{f' ({item.year})' if item.year else ''} · {self._short_licence(item)}"
            label = f"{(topic.title if topic else 'From the archive')[:30]}" + (f" · {item.year}" if item.year else "")
            out_dir = self.renders / ch.name
            out_dir.mkdir(parents=True, exist_ok=True)
            out = out_dir / f"{seed:08x}.mp4"
            compose(video, m.start, m.end, copy, credit, out, self.cfg, seed, music,
                    original_audio=original, label=label, year=item.year)
            meta = {
                "title": copy["title"], "hook": [copy["hook_line1"], copy["hook_line2"]],
                "keyword": copy.get("keyword"), "beats": copy.get("beats", []), "copy_source": copy.get("source"),
                "packaging_score": copy.get("score"), "alternates": copy.get("alternates", []),
                "description": copywriter.description(copy, item, music_credit, list(ch.tags),
                                                      topic.url if topic else None),
                "tags": list(ch.tags) + ([topic.title] if topic else []), "music": str(music) if music else None,
                "audio": "original" if original else ("music" if music else "none"),
                "source_title": item.title, "source_url": item.page_url, "licence": item.licence,
                "topic": topic.to_json() if topic else None, "moment": m.features,
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
        side = music.with_suffix(".txt")
        return side.read_text().strip() if side.exists() else music.stem.replace("_", " ")

    # ------------------------------------------------------------ one candidate item
    def process_item(self, ch, item, want: int, topic=None) -> int:
        row = self.db.get_source(item.source, item.source_id)
        if row and row["status"] in ("rejected", "failed", "exhausted"):
            return 0
        ok, reason = self.policy.check(item)
        if not ok:
            self.db.upsert_source(item, "rejected", reason)
            log.info("skip %s: %s", item.source_id, reason)
            return 0
        try:
            path = sources.fetch(item, self.downloads)
        except Exception as e:  # noqa: BLE001
            self.db.upsert_source(item, "failed", f"download: {e}")
            log.warning("download failed %s: %s", item.source_id, e)
            return 0
        sid = self.db.upsert_source(item, "ok", reason, str(path))
        try:
            got = self.clips_from_source(ch, item, sid, path, want, topic)
        except Exception as e:  # noqa: BLE001
            log.exception("processing failed for %s", item.source_id)
            self.db.upsert_source(item, "failed", f"processing: {e}")
            return 0
        if got == 0 and not topic:
            self.db.upsert_source(item, "exhausted", "no more usable moments")
        return got

    def _spec_items(self, spec, pages=(1, 2, 3)):
        for page in pages:
            try:
                items = list(sources.discover(spec, page))
            except Exception as e:  # noqa: BLE001
                log.warning("source %s unavailable: %s", spec.get("type"), e)
                return
            if not items:
                return
            yield from items

    # ------------------------------------------------------------ one channel
    def fill_channel(self, ch) -> int:
        need = ch.buffer - self.db.pipeline_count(ch.name)
        if need <= 0:
            log.info("%s: buffer full", ch.name)
            return 0
        log.info("%s: making %d new Short(s)", ch.name, need)
        rk = ch.get("ranking") or {}
        n_rank = round(need * float(rk.get("share", 0.34))) if rk.get("enabled") else 0
        made = 0
        if n_rank and self.db.usable_sources():         # footage already on disk: rankings first
            from . import ranking
            made += ranking.make(self, ch, n_rank)
        for spec in ch.sources:
            if made >= need:
                break
            if spec["type"] == "trends":
                made += self._fill_from_trends(ch, spec, need - made)
                continue
            if spec["type"] == "stats":
                from .stats import maker
                made += maker.make(self, ch, spec, need - made)
                continue
            for item in self._spec_items(spec):
                if made >= need:
                    break
                made += self.process_item(ch, item, need - made)
        return made

    def _fill_from_trends(self, ch, spec, need: int) -> int:
        topics = trends.todays_topics(self.cfg, self.llm, self.cfg.paths.data, self.policy.cutoff,
                                      max_topics=self.cfg.trends.max_topics,
                                      allow_sports=bool(spec.get("allow_sports")),
                                      sports_only=bool(spec.get("sports_only")))
        made = 0
        per_topic = max(1, int(spec.get("per_topic", 1)))
        for topic in topics:
            if made >= need:
                break
            got = 0
            for archive in spec.get("archives", ["internet_archive", "nasa"]):
                q = topic.queries.get(archive)
                if not q or got >= per_topic:
                    continue
                for item in self._spec_items({"type": archive, "query": q, "rows": 12}, pages=(1,)):
                    if got >= per_topic or made + got >= need:
                        break
                    if not trends.relevant(item, q):
                        continue
                    got += self.process_item(ch, item, min(per_topic - got, need - made - got), topic)
            if got:
                log.info("trend %r -> %d Short(s)", topic.title, got)
            made += got
        return made

    def fill_all(self) -> dict:
        return {ch.name: self.fill_channel(ch) for ch in self.cfg.channels if ch.get("enabled", True)}


def describe_clip(row) -> str:
    meta = json.loads(row["meta"])
    kind = row["kind"] if "kind" in row.keys() else "short"
    span = (f"{row['start']:.0f}-{row['end']:.0f}s" if kind == "short"
            else f"{len(meta.get('parts', []))} parts, {meta.get('duration', 0) / 60:.1f} min")
    topic = f"\n   trend: {meta['topic']['title']}" if meta.get("topic") else ""
    return (f"#{row['id']} [{row['channel']}] {kind} {row['status']}: {meta['title']}\n"
            f"   hook: {' / '.join(meta.get('hook', []))}\n   from: {meta.get('source_title', 'compilation')} "
            f"{span}{topic}\n   file: {row['video']}")
