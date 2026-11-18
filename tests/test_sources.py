from datetime import date

from autoreel.config import Cfg
from autoreel.sources import internet_archive, nasa
from autoreel.sources.base import Policy, SourceItem, us_pd_cutoff

IA_DOC = {"identifier": "CC_1916_07_10_TheVagabond"}
IA_META = {
    "metadata": {"title": 'Charlie Chaplin\'s "The Vagabond"', "year": "1916",
                 "licenseurl": "http://creativecommons.org/licenses/publicdomain/",
                 "collection": ["silent_films", "Comedy_Films"], "subject": "Charlie Chaplin; Silents;"},
    "files": [
        {"name": "CC_1916_07_10_TheVagabond.mpg", "format": "MPEG1", "size": "256571396"},
        {"name": "CC_1916_07_10_TheVagabond.mp4", "format": "h.264", "size": "150003982",
         "height": "480", "width": "640", "length": "1483.22"},
        {"name": "CC_1916_07_10_TheVagabond.ogv", "format": "Ogg Video"},
    ],
}
NASA_ENTRY = {"data": [{"nasa_id": "KSC-launch-1", "title": "Launch", "center": "KSC",
                        "date_created": "2023-05-07T00:00:00Z", "description": "A launch",
                        "keywords": ["Rocket"], "photographer": "Rocket Lab"}]}
NASA_MANIFEST = {"collection": {"items": [
    {"href": "http://images-assets.nasa.gov/video/KSC-launch-1/KSC-launch-1~orig.mp4"},
    {"href": "http://images-assets.nasa.gov/video/KSC-launch-1/KSC-launch-1~medium.mp4"},
    {"href": "http://images-assets.nasa.gov/video/KSC-launch-1/KSC-launch-1~thumb.jpg"}]}}


def policy():
    return Policy(Cfg({"policy": Cfg({"blocked_terms": ["ai generated", "colorized"],
                                      "trusted_pd_collections": ["prelinger"]})}))


def test_cutoff_rolls_forward():
    assert us_pd_cutoff(date(2026, 11, 18)) == 1930
    assert us_pd_cutoff(date(2027, 1, 1)) == 1931


def test_ia_picks_mp4_and_passes_policy():
    item = internet_archive.to_item(IA_DOC, IA_META)
    assert item.download_url.endswith("TheVagabond.mp4")
    assert item.year == 1916
    ok, why = policy().check(item)
    assert ok, why


def test_ia_rejects_modern_and_ai_items():
    meta = {**IA_META, "metadata": {**IA_META["metadata"], "year": "1955"}}
    assert not policy().check(internet_archive.to_item(IA_DOC, meta))[0]
    meta = {**IA_META, "metadata": {**IA_META["metadata"], "title": "The Vagabond (AI generated colorized)"}}
    ok, why = policy().check(internet_archive.to_item(IA_DOC, meta))
    assert not ok and "blocked term" in why


def test_ia_trusted_collection_after_cutoff():
    meta = {**IA_META, "metadata": {**IA_META["metadata"], "year": "1952", "collection": ["prelinger"],
                                    "licenseurl": "http://creativecommons.org/publicdomain/mark/1.0/"}}
    assert policy().check(internet_archive.to_item(IA_DOC, meta))[0]


def test_nasa_asset_choice_and_third_party_rejected():
    url = nasa.pick_asset(NASA_MANIFEST)
    assert url.startswith("https://") and url.endswith("~medium.mp4")
    item = nasa.to_item(NASA_ENTRY, url)
    ok, why = policy().check(item)
    assert not ok and "third-party" in why
    entry = {"data": [{**NASA_ENTRY["data"][0], "photographer": "NASA/Kim Shiflett"}]}
    assert policy().check(nasa.to_item(entry, url))[0]


def test_local_requires_licence(tmp_path):
    from autoreel.sources import local
    (tmp_path / "a.mp4").write_bytes(b"x")
    item = next(local.discover(tmp_path))
    assert not policy().check(item)[0]
    (tmp_path / "licence.json").write_text('{"licence": "Public domain (NPS)", "credit": "NPS"}')
    item = next(local.discover(tmp_path))
    assert policy().check(item)[0]


def test_item_dataclass_roundtrip():
    item = SourceItem("local", "x", "T", 1920, "", "", "PD", "c")
    assert item.meta == {}
