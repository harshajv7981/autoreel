"""SQLite state: every source and clip, with provenance, so nothing is posted twice."""
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS sources(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  source TEXT, source_id TEXT, title TEXT, year INTEGER, page_url TEXT, download_url TEXT,
  licence TEXT, credit TEXT, local_path TEXT, status TEXT, reason TEXT, meta TEXT,
  created TEXT, UNIQUE(source, source_id));
CREATE TABLE IF NOT EXISTS clips(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  channel TEXT, source_row INTEGER, start REAL, end REAL, score REAL, judged TEXT,
  status TEXT, video TEXT, meta TEXT, youtube_id TEXT, publish_at TEXT, slot TEXT, error TEXT,
  created TEXT, updated TEXT);
CREATE INDEX IF NOT EXISTS clips_status ON clips(status);
CREATE TABLE IF NOT EXISTS segments(clip_id INTEGER, source_row INTEGER, start REAL, end REAL);
"""


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class DB:
    def __init__(self, path: Path):
        self.c = sqlite3.connect(path)
        self.c.row_factory = sqlite3.Row
        self.c.executescript(SCHEMA)
        cols = {r[1] for r in self.c.execute("PRAGMA table_info(clips)")}
        if "kind" not in cols:                         # v2: long-form compilations share the table
            self.c.execute("ALTER TABLE clips ADD COLUMN kind TEXT DEFAULT 'short'")
            self.c.commit()

    # ---- sources
    def get_source(self, source: str, source_id: str):
        return self.c.execute("SELECT * FROM sources WHERE source=? AND source_id=?",
                              (source, source_id)).fetchone()

    def upsert_source(self, item, status: str, reason: str = "", local_path: str | None = None) -> int:
        row = self.get_source(item.source, item.source_id)
        if row:
            self.c.execute("UPDATE sources SET status=?, reason=?, local_path=COALESCE(?, local_path) "
                           "WHERE id=?", (status, reason, local_path, row["id"]))
            self.c.commit()
            return row["id"]
        cur = self.c.execute(
            "INSERT INTO sources(source,source_id,title,year,page_url,download_url,licence,credit,"
            "local_path,status,reason,meta,created) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (item.source, item.source_id, item.title, item.year, item.page_url, item.download_url,
             item.licence, item.credit, local_path, status, reason,
             json.dumps({**item.meta, "text": (item.text or "")[:2000]}), now()))
        self.c.commit()
        return cur.lastrowid

    def source_row(self, row_id: int):
        return self.c.execute("SELECT * FROM sources WHERE id=?", (row_id,)).fetchone()

    def clips_from_source(self, row_id: int) -> list:
        return self.c.execute("SELECT * FROM clips WHERE source_row=? AND kind='short'", (row_id,)).fetchall()

    def used_spans(self, row_id: int) -> list[tuple[float, float]]:
        """Every span of a source already used, by single Shorts or by ranking items."""
        spans = [(r["start"], r["end"]) for r in self.clips_from_source(row_id)]
        spans += [(r[0], r[1]) for r in self.c.execute(
            "SELECT s.start, s.end FROM segments s JOIN clips c ON c.id=s.clip_id "
            "WHERE s.source_row=? AND c.status != 'rejected'", (row_id,))]
        return spans

    def add_segments(self, clip_id: int, segs: list[tuple[int, float, float]]):
        self.c.executemany("INSERT INTO segments VALUES (?,?,?,?)", [(clip_id, r, a, b) for r, a, b in segs])
        self.c.commit()

    def usable_sources(self) -> list:
        return self.c.execute("SELECT * FROM sources WHERE status IN ('ok','exhausted') AND local_path IS NOT NULL "
                              "AND source != 'stats'").fetchall()

    # ---- clips
    def add_clip(self, channel, source_row, start, end, score, judged, video, meta, status="review",
                 kind="short") -> int:
        cur = self.c.execute(
            "INSERT INTO clips(channel,source_row,start,end,score,judged,status,video,meta,created,updated,kind)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (channel, source_row, start, end, score, json.dumps(judged), status, str(video),
             json.dumps(meta), now(), now(), kind))
        self.c.commit()
        return cur.lastrowid

    def clip(self, clip_id: int):
        return self.c.execute("SELECT * FROM clips WHERE id=?", (clip_id,)).fetchone()

    def clips(self, status: str | None = None, channel: str | None = None) -> list:
        q, args = "SELECT * FROM clips WHERE 1=1", []
        if status:
            q += " AND status=?"
            args.append(status)
        if channel:
            q += " AND channel=?"
            args.append(channel)
        return self.c.execute(q + " ORDER BY id", args).fetchall()

    def set_clip(self, clip_id: int, **fields):
        fields["updated"] = now()
        cols = ", ".join(f"{k}=?" for k in fields)
        self.c.execute(f"UPDATE clips SET {cols} WHERE id=?", (*fields.values(), clip_id))
        self.c.commit()

    def pipeline_count(self, channel: str) -> int:
        """Shorts made but not yet published (waiting for review, approved, or scheduled in future)."""
        return self.c.execute(
            "SELECT COUNT(*) FROM clips WHERE channel=? AND kind='short' AND (status IN ('review','approved') OR "
            "(status='uploaded' AND publish_at > ?))", (channel, now().replace("+00:00", "Z"))).fetchone()[0]

    def uploads_since(self, iso: str) -> int:
        return self.c.execute("SELECT COUNT(*) FROM clips WHERE status='uploaded' AND updated >= ?",
                              (iso,)).fetchone()[0]

    def taken_slots(self, channel: str) -> set[str]:
        rows = self.c.execute("SELECT slot FROM clips WHERE channel=? AND slot IS NOT NULL", (channel,))
        return {r[0] for r in rows}        # un-jittered slot keys

    def recent_shorts(self, channel: str, since_iso: str) -> list:
        return self.c.execute(
            "SELECT * FROM clips WHERE channel=? AND kind='short' AND status IN ('approved','uploaded') "
            "AND created >= ? ORDER BY score DESC", (channel, since_iso)).fetchall()

    def last_long(self, channel: str):
        return self.c.execute("SELECT * FROM clips WHERE channel=? AND kind='long' ORDER BY id DESC LIMIT 1",
                              (channel,)).fetchone()
