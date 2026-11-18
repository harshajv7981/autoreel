"""NASA Image and Video Library: keyless search + asset manifest."""
import urllib.parse

from .base import SourceItem, first_year, http_json

API = "https://images-api.nasa.gov"
PREFERENCE = ("~large.mp4", "~medium.mp4", "~orig.mp4", "~mobile.mp4", "~small.mp4", ".mp4")


def search(query: str, page_size: int = 40, page: int = 1) -> list[dict]:
    data = http_json(f"{API}/search", {"q": query, "media_type": "video",
                                       "page_size": page_size, "page": page})
    return data["collection"]["items"]


def pick_asset(manifest: dict) -> str | None:
    hrefs = [i["href"] for i in manifest["collection"]["items"]]
    for suffix in PREFERENCE:
        for h in hrefs:
            if h.lower().endswith(suffix):
                return urllib.parse.quote(h.replace("http://", "https://", 1), safe=":/~%")
    return None


def to_item(entry: dict, asset_url: str) -> SourceItem:
    d = entry["data"][0]
    title = d.get("title", d["nasa_id"])
    year = first_year(d.get("date_created"))
    return SourceItem(
        source="nasa", source_id=d["nasa_id"], title=title, year=year,
        page_url=f"https://images.nasa.gov/details/{urllib.parse.quote(d['nasa_id'])}",
        download_url=asset_url,
        licence="NASA media (US Government work, not subject to US copyright)",
        credit=f"Footage: NASA{' / ' + d['center'] if d.get('center') else ''}",
        text=" ".join([d.get("description", "")[:1500], *d.get("keywords", [])]),
        meta={k: d.get(k) for k in ("center", "photographer", "secondary_creator", "keywords", "location")},
    )


def discover(query: str, rows: int = 40, page: int = 1):
    for entry in search(query, page_size=rows, page=page):
        nasa_id = entry["data"][0]["nasa_id"]
        manifest = http_json(f"{API}/asset/{urllib.parse.quote(nasa_id)}")
        url = pick_asset(manifest)
        if url:
            yield to_item(entry, url)
