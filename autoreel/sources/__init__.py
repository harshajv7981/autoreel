"""Source registry: each source type yields SourceItem objects."""
from pathlib import Path

from . import internet_archive, local, nasa
from .base import Policy, SourceItem, download  # noqa: F401


def discover(spec, page: int = 1):
    kind = spec["type"]
    if kind == "internet_archive":
        return internet_archive.discover(spec["query"], rows=spec.get("rows", 30), page=page)
    if kind == "nasa":
        return nasa.discover(spec["query"], rows=spec.get("rows", 30), page=page)
    if kind == "local":
        return local.discover(Path(spec["folder"])) if page == 1 else iter(())
    raise ValueError(f"unknown source type {kind!r}")


def fetch(item: SourceItem, downloads_dir: Path) -> Path:
    if item.source == "local":
        return Path(item.meta["path"])
    safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in item.source_id)[:80]
    return download(item.download_url, downloads_dir / f"{item.source}_{safe}.mp4")
