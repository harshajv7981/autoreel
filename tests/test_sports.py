"""Sports & action: Universal Newsreels rule, sports-only trends, era cue, {pd_cutoff} queries."""
import json

from autoreel import copywriter, sources, trends
from autoreel.config import Cfg
from autoreel.sources import internet_archive
from autoreel.sources.base import Policy, SourceItem, us_pd_cutoff
from tests.test_sources import IA_DOC, IA_META
from tests.test_v2 import fake_wiki  # noqa: F401  (fixture)


def policy():
    return Policy(Cfg({"policy": Cfg({
        "blocked_terms": [], "trusted_pd_collections": [], "gov_collections": ["fedflix"],
        "content_id_risk_terms": ["movietone", " bfi ", "films division"],
        "pd_releases": [{"match": "universal newsreel", "years": [1929, 1967], "reason": "Universal Newsreels"}]})}))


def ia(**md):
    return internet_archive.to_item(IA_DOC, {**IA_META, "metadata": {**IA_META["metadata"], **md}})


def test_universal_newsreels_accepted_within_their_years():
    ok, why = policy().check(ia(title="Universal Newsreel 1936 Release 507: Olympic Games", year="1936",
                                collection=["universal_newsreels"], licenseurl=""))
    assert ok and "Universal" in why
    assert not policy().check(ia(title="Universal Newsreel 1975", year="1975", licenseurl=""))[0]


def test_claiming_newsreel_owners_rejected():
    assert not policy().check(ia(title="Fox Movietone: cup final 1927", year="1927"))[0]
    assert not policy().check(ia(description="Courtesy Films Division of India", year="1925"))[0]


def test_sports_trends_only_where_allowed(fake_wiki, monkeypatch):
    from tests.test_v2 import TOP
    top = json.loads(json.dumps(TOP))
    top["items"][0]["articles"] += [{"article": "Indian_Premier_League", "views": 400000, "rank": 9},
                                    {"article": "Virat_Kohli_(cricketer)", "views": 390000, "rank": 10}]
    monkeypatch.setattr(trends, "http_json",
                        lambda url, params=None, retries=3: top if "pageviews" in url else {"selected": []})
    cfg = Cfg({"trends": Cfg({"feeds": ["wikipedia_top", "on_this_day"], "top_n": 60, "boost_terms": []})})
    general = [t.title for t in trends.candidates(cfg)]
    sports = [t.title for t in trends.candidates(cfg, allow_sports=True, sports_only=True)]
    assert "Indian Premier League" not in general and "Zeppelin" in general
    assert sports == ["Indian Premier League"]            # player bios never, even on the sports channel


class SportsLLM:
    def __init__(self, with_year):
        self.with_year = with_year

    def available(self):
        return True

    def text(self, prompt, as_json=False):
        assert "decades old" in prompt
        title = "Soccer in 1930 looked nothing like today" if self.with_year else "Soccer looked nothing like today"
        return json.dumps({"options": [{"hook_line1": "Same game", "hook_line2": "different world",
                                        "keyword": "different", "title": title}],
                           "description": "A soccer match filmed for a cinema newsreel.",
                           "beats": ["Players wore heavy leather boots", "The ball was laced leather"]})


def test_sports_copy_must_say_the_footage_is_old():
    item = SourceItem("internet_archive", "s", "Soccer match newsreel", 1930, "", "", "PD", "",
                      text="soccer match cinema newsreel heavy leather boots laced leather ball")
    topic = trends.Topic("2026 FIFA World Cup", "trending", 1.0, facts="football tournament", kind="sport")
    bad = copywriter.write(SportsLLM(False), item, {}, "plain", seed=1, topic=topic)
    assert bad["source"] == "template"                    # no era cue -> rejected every time
    good = copywriter.write(SportsLLM(True), item, {}, "plain", seed=1, topic=topic)
    assert good["title"] == "Soccer in 1930 looked nothing like today"


def test_pd_cutoff_placeholder(monkeypatch):
    seen = {}
    monkeypatch.setattr(sources.internet_archive, "discover",
                        lambda q, rows=30, page=1: seen.setdefault("q", q) and iter(()))
    list(sources.discover({"type": "internet_archive", "query": "boxing AND year:[1895 TO {pd_cutoff}]"}))
    assert seen["q"].endswith(f"TO {us_pd_cutoff()}]")
