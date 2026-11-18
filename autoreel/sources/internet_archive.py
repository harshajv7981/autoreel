"""Internet Archive: keyless advanced search + metadata API."""
import urllib.parse

from .base import SourceItem, as_list, first_year, http_json

SEARCH = "https://archive.org/advancedsearch.php"
FIELDS = ["identifier", "title", "year", "date", "licenseurl", "collection", "subject", "downloads"]
GOOD_FORMATS = ("h.264", "mpeg4", "h.264 ia", "512kb mpeg4")


def search(query: str, rows: int = 50, page: int = 1) -> list[dict]:
    params = [("q", f"({query}) AND mediatype:(movies)"), ("rows", rows), ("page", page),
              ("output", "json"), ("sort[]", "downloads desc")]
    params += [("fl[]", f) for f in FIELDS]
    return http_json(SEARCH, params)["response"]["docs"]


def pick_file(metadata: dict) -> dict | None:
    """Best MP4 for editing: <=720p preferred, largest otherwise."""
    mp4s = [f for f in metadata.get("files", [])
            if f.get("name", "").lower().endswith(".mp4")
            and (f.get("format", "").lower() in GOOD_FORMATS or f.get("format", "").lower().startswith("h.264"))]
    if not mp4s:
        return None

    def height(f):
        try:
            return int(f.get("height") or 0)
        except ValueError:
            return 0

    sub720 = [f for f in mp4s if 0 < height(f) <= 720]
    pool = sub720 or mp4s
    return max(pool, key=lambda f: (height(f), int(f.get("size") or 0)))


def to_item(doc: dict, metadata: dict) -> SourceItem | None:
    ident = doc["identifier"]
    md = metadata.get("metadata", {})
    f = pick_file(metadata)
    if not f:
        return None
    title = md.get("title") or doc.get("title") or ident
    if isinstance(title, list):
        title = title[0]
    year = first_year(md.get("year") or md.get("date") or doc.get("year") or doc.get("date"))
    subjects = as_list(md.get("subject") or doc.get("subject"))
    desc = md.get("description") or ""
    if isinstance(desc, list):
        desc = " ".join(desc)
    lic = md.get("licenseurl") or doc.get("licenseurl") or ""
    return SourceItem(
        source="internet_archive", source_id=ident, title=str(title), year=year,
        page_url=f"https://archive.org/details/{ident}",
        download_url=f"https://archive.org/download/{ident}/{urllib.parse.quote(f['name'])}",
        licence="Public domain (US)" if not lic or "publicdomain" in lic or "mark" in lic else lic,
        credit=f"{title}{f' ({year})' if year else ''} via the Internet Archive",
        text=" ".join([*map(str, subjects), str(desc)[:1500]]),
        meta={"collection": as_list(md.get("collection") or doc.get("collection")),
              "licenseurl": lic, "file": f.get("name"), "length": f.get("length"),
              "sound": md.get("sound"), "runtime": md.get("runtime")},
    )


def discover(query: str, rows: int = 40, page: int = 1):
    for doc in search(query, rows=rows, page=page):
        meta = http_json(f"https://archive.org/metadata/{doc['identifier']}")
        item = to_item(doc, meta)
        if item:
            yield item
