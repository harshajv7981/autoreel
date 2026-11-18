"""Shared source types, HTTP helpers and the licence policy every clip must pass."""
import json
import re
import shutil
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from .. import log

UA = "AutoReel/0.1 (public-domain shorts pipeline; contact: owner)"


@dataclass
class SourceItem:
    source: str                 # internet_archive | nasa | local
    source_id: str
    title: str
    year: int | None
    page_url: str
    download_url: str
    licence: str                # human-readable, goes into the description
    credit: str                 # attribution line, goes into the description
    text: str = ""              # title + subjects + description, used by the policy and copywriter
    meta: dict = field(default_factory=dict)


def http_json(url: str, params: list | dict | None = None, retries: int = 3) -> dict:
    if params:
        url += ("&" if "?" in url else "?") + urllib.parse.urlencode(params, doseq=True)
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception as e:  # noqa: BLE001
            if attempt == retries - 1:
                raise
            log.warning("GET %s failed (%s); retrying", url[:120], e)
            time.sleep(2 ** attempt)
    raise RuntimeError("unreachable")


def download(url: str, dest: Path, max_mb: int = 1500) -> Path:
    if dest.exists() and dest.stat().st_size > 0:
        return dest
    tmp = dest.with_suffix(dest.suffix + ".part")
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=60) as r, open(tmp, "wb") as f:
        size = int(r.headers.get("Content-Length") or 0)
        if size > max_mb * 1024 * 1024:
            raise ValueError(f"file too large ({size >> 20} MB): {url}")
        shutil.copyfileobj(r, f, length=1 << 20)
    tmp.rename(dest)
    log.info("downloaded %s (%.0f MB)", dest.name, dest.stat().st_size / 1e6)
    return dest


def first_year(value) -> int | None:
    if isinstance(value, list):
        value = value[0] if value else None
    m = re.search(r"\b(1[89]\d\d|20\d\d)\b", str(value or ""))
    return int(m.group(1)) if m else None


def as_list(value) -> list:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


# ------------------------------------------------------------------ policy

def us_pd_cutoff(today: date | None = None) -> int:
    """Last publication year that is public domain in the US today (published Y -> PD on Jan 1, Y+96)."""
    return (today or date.today()).year - 96


class Policy:
    def __init__(self, cfg):
        self.blocked = [t.lower() for t in cfg.policy.blocked_terms]
        self.trusted = {c.lower() for c in cfg.policy.trusted_pd_collections}
        self.cutoff = us_pd_cutoff()

    def check(self, item: SourceItem) -> tuple[bool, str]:
        text = f"{item.title} {item.text}".lower()
        for term in self.blocked:
            if term in text:
                return False, f"blocked term: {term!r} (possible AI/altered media)"

        if item.source == "internet_archive":
            collections = {c.lower() for c in as_list(item.meta.get("collection"))}
            lic = (item.meta.get("licenseurl") or "").lower()
            if item.year and item.year <= self.cutoff:
                return True, f"published {item.year} (US public domain, cutoff {self.cutoff})"
            if collections & self.trusted and ("publicdomain" in lic or "/zero/" in lic or "mark" in lic):
                return True, "archive-vetted public-domain collection"
            return False, f"year {item.year} after cutoff {self.cutoff} and not in a vetted PD collection"

        if item.source == "nasa":
            creator = " ".join(as_list(item.meta.get("photographer")) +
                               as_list(item.meta.get("secondary_creator"))).strip().lower()
            if creator and "nasa" not in creator:
                return False, f"third-party footage ({creator}) - not a NASA work"
            return True, "US Government work (NASA media guidelines)"

        if item.source == "local":
            if not item.licence:
                return False, "no licence.json next to the file"
            return True, f"declared licence: {item.licence}"

        return False, "unknown source type"
