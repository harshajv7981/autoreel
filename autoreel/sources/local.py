"""Local folder source: footage you downloaded yourself (NPS, USGS, USFWS, Mixkit...).

Each folder (or each video) needs a licence file so provenance is never lost:
  licence.json            applies to every video in the folder
  <video>.json            overrides for one video
  {"licence": "Public domain (National Park Service)",
   "credit": "Courtesy of the National Park Service",
   "url": "https://www.nps.gov/grca/learn/photosmultimedia/b-roll_hd20.htm",
   "title": "Desert bighorn sheep at the Grand Canyon", "year": 2022}
"""
import hashlib
import json
from pathlib import Path

from .base import SourceItem

VIDEO_EXT = {".mp4", ".mov", ".mkv", ".m4v", ".webm"}


def _load(p: Path) -> dict:
    return json.loads(p.read_text()) if p.exists() else {}


def discover(folder: Path):
    folder = Path(folder)
    if not folder.exists():
        return
    shared = _load(folder / "licence.json")
    for video in sorted(folder.iterdir()):
        if video.suffix.lower() not in VIDEO_EXT:
            continue
        info = {**shared, **_load(video.with_suffix(".json"))}
        digest = hashlib.sha1(f"{video.name}:{video.stat().st_size}".encode()).hexdigest()[:12]
        yield SourceItem(
            source="local", source_id=f"{video.name}:{digest}",
            title=info.get("title") or video.stem.replace("_", " "),
            year=info.get("year"), page_url=info.get("url", ""),
            download_url=video.resolve().as_uri(), licence=info.get("licence", ""),
            credit=info.get("credit", ""), text=info.get("description", ""),
            meta={"path": str(video.resolve())},
        )
