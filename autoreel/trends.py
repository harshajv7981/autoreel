"""Trend radar: what people are looking up today, matched to real archival footage.

Feeds (all free, keyless):
  * Wikipedia most-read articles (yesterday, English) - the strongest "trending now" signal
  * Wikipedia "On this day" events - anniversaries that archives can actually illustrate

Each candidate becomes a Topic with search queries for the archives and a fact pack
(Wikipedia summary) that the copywriter is allowed to quote. Topics that archives can't
illustrate honestly (celebrity gossip, deaths, live sport, new films/TV) are skipped:
we only ride a trend when real public-domain footage genuinely belongs to it.
"""
import json
import re
import urllib.parse
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta, timezone

from . import log
from .llm import parse_json
from .sources.base import http_json

WIKI = "https://en.wikipedia.org/api/rest_v1"
PAGEVIEWS = "https://wikimedia.org/api/rest_v1/metrics/pageviews/top/en.wikipedia/all-access"
META = re.compile(r"^(Main_Page|Special:|Wikipedia:|File:|Portal:|Help:|Talk:|Category:|Template:|-$|"
                  r"Deaths_in_|List_of_|.*\(disambiguation\)$|Cleopatra_\(disambiguation\)|XXX)", re.I)
# Topics we never chase: footage would be copyrighted, or exploiting it would be in poor taste.
AVOID = re.compile(r"(\b(19[4-9]\d|20\d\d)_?(film|TV_series|season|video_game|album|song)\b|"
                   r"_\((film|TV_series|album|song|video_game|singer|rapper|actor|actress|footballer|"
                   r"cricketer|politician|YouTuber|influencer|wrestler)\)|"
                   r"_F\.C\.|_FC\b|football_club|shooting|massacre|murder|killing|attack|death_of|"
                   r"OnlyFans|pornograph|ChatGPT|Squid_Game)", re.I)
# Sports topics: only for channels that allow them, and only ever shown with HISTORIC footage of the sport.
SPORT = re.compile(r"(cricket|ashes|indian_premier_league|\bipl\b|t20|test_match|football|soccer|fifa|world_cup|"
                   r"premier_league|champions_league|la_liga|euro_20|copa|olympic|boxing|heavyweight|wimbledon|"
                   r"grand_prix|formula_one|\bf1\b|indianapolis|super_bowl|\bnfl\b|\bnba\b|world_series|"
                   r"baseball|rugby|tour_de_france|marathon|\bufc\b|wrestling|horse_racing|kentucky_derby)", re.I)
SPACE = re.compile(r"\b(nasa|apollo|artemis|moon|lunar|mars|rover|rocket|launch|space|shuttle|iss|"
                   r"astronaut|orbit|hubble|webb|saturn|jupiter|eclipse|comet|meteor|satellite|spacex)\b", re.I)


@dataclass
class Topic:
    title: str                      # Wikipedia article title (human-readable)
    why: str                        # why it's trending, shown to the copywriter
    score: float                    # higher = more urgent
    facts: str = ""                 # Wikipedia summary text (CC BY-SA), allowed facts
    url: str = ""
    year: int | None = None         # event year for On-this-day items
    queries: dict = field(default_factory=dict)   # {"internet_archive": "...", "nasa": "..."}
    angle: str = ""
    kind: str = "general"           # general | sport

    def to_json(self) -> dict:
        return asdict(self)


def _title(article: str) -> str:
    return article.replace("_", " ")


def wikipedia_top(day: date | None = None, limit: int = 60) -> list[tuple[str, int, int]]:
    """[(article, views, rank)] for the most-read English Wikipedia articles."""
    day = day or (datetime.now(timezone.utc).date() - timedelta(days=1))
    for d in (day, day - timedelta(days=1)):          # data lands a few hours after midnight UTC
        try:
            data = http_json(f"{PAGEVIEWS}/{d:%Y/%m/%d}")
            arts = data["items"][0]["articles"]
            out = [(a["article"], a["views"], a["rank"]) for a in arts if not META.match(a["article"])]
            return out[:limit]
        except Exception as e:  # noqa: BLE001
            log.debug("pageviews %s unavailable: %s", d, e)
    return []


def on_this_day(day: date | None = None) -> list[dict]:
    day = day or date.today()
    data = http_json(f"{WIKI}/feed/onthisday/selected/{day:%m/%d}")
    return data.get("selected") or data.get("events") or []


def summary(title: str) -> dict:
    try:
        return http_json(f"{WIKI}/page/summary/{urllib.parse.quote(title.replace(' ', '_'), safe='')}")
    except Exception:  # noqa: BLE001
        return {}


def _kind(title: str) -> str:
    return "sport" if SPORT.search(title.replace(" ", "_")) else "general"


def candidates(cfg, today: date | None = None, allow_sports: bool = False, sports_only: bool = False) -> list[Topic]:
    today = today or date.today()
    tcfg = cfg.trends
    topics: list[Topic] = []
    if "wikipedia_top" in tcfg.feeds:
        for article, views, rank in wikipedia_top(limit=tcfg.top_n):
            if AVOID.search(article):
                continue
            topics.append(Topic(_title(article), f"#{rank} most-read on Wikipedia yesterday ({views:,} views)",
                                score=1.0 + (tcfg.top_n - rank) / tcfg.top_n, kind=_kind(article)))
    if "on_this_day" in tcfg.feeds:
        for ev in on_this_day(today):
            pages = ev.get("pages") or []
            if not pages:
                continue
            p = pages[0]
            t = p.get("normalizedtitle") or _title(p.get("title", ""))
            if not t or AVOID.search(t.replace(" ", "_")):
                continue
            yr = ev.get("year")
            anniversary = (today.year - yr) if isinstance(yr, int) else None
            round_bonus = 0.6 if anniversary and anniversary % 25 == 0 else 0.0
            topics.append(Topic(t, f"On this day in {yr}: {ev.get('text', '')}"[:300], score=0.8 + round_bonus,
                                facts=p.get("extract", ""), url=(p.get("content_urls") or {}).get("desktop", {})
                                .get("page", ""), year=yr if isinstance(yr, int) else None,
                                kind=_kind(f"{t} {ev.get('text', '')}")))
    topics = [t for t in topics if (t.kind == "sport" and allow_sports) or (t.kind != "sport" and not sports_only)]
    boost = [b.lower() for b in tcfg.get("boost_terms", [])]
    for t in topics:
        hay = f" {t.title} {t.why} {t.facts[:300]} ".lower()
        if any(re.search(rf"\b{re.escape(b)}\b", hay) for b in boost):
            t.score += 0.5          # US/UK history, aerospace and tech pay best and have the most footage
    seen, uniq = set(), []
    for t in sorted(topics, key=lambda t: t.score, reverse=True):
        if t.title.lower() not in seen:
            seen.add(t.title.lower())
            uniq.append(t)
    return uniq


PLAN_PROMPT = """You plan Shorts for a channel that shows REAL archival public-domain footage
(films and newsreels from before {cutoff}, US government films, NASA footage) connected to what people
are reading about today. The audience is in the US and UK; favour history, aerospace, engineering,
transport, the Cold War and old Hollywood, where archives have strong footage.

Trending topic: {title}
Why it's trending: {why}
Facts: {facts}

Can real archival footage honestly illustrate this topic? Think of the history behind it, an older
event of the same kind, or the place/technology/animal itself. If it's about a living celebrity,
a recent death or tragedy, a current sports match, or a new film/show, answer skip.

{sport_rule}
Return JSON: {{"skip": true/false, "reason": "...", "angle": "one sentence: what the Short shows and why it's
relevant today", "internet_archive_query": "2-5 plain search words for old films/newsreels, or null",
"nasa_query": "2-4 words if space-related, else null"}}"""

SPORT_RULE = """This is a SPORTS topic. Current matches, players, leagues' footage, logos and photos are off-limits.
The only allowed angle is historic footage of the same sport from old newsreels (before 1968), e.g. "how
football looked in the 1930s" or "boxing's heavyweight title fights a century ago". The search words must
describe the old sport (e.g. "soccer match newsreel", "heavyweight boxing fight"), never current names."""


def plan(topic: Topic, llm, cutoff: int) -> Topic | None:
    if not topic.facts:
        s = summary(topic.title)
        topic.facts = (s.get("extract") or "")[:1200]
        topic.url = topic.url or (s.get("content_urls") or {}).get("desktop", {}).get("page", "")
    raw = llm.text(PLAN_PROMPT.format(cutoff=cutoff + 1, title=topic.title, why=topic.why,
                                      facts=topic.facts[:900] or "none",
                                      sport_rule=SPORT_RULE if topic.kind == "sport" else ""),
                   as_json=True) if llm.available() else None
    data = parse_json(raw)
    if data:
        if data.get("skip"):
            log.info("trend skipped %r: %s", topic.title, data.get("reason"))
            return None
        topic.angle = data.get("angle") or ""
        if data.get("internet_archive_query"):
            topic.queries["internet_archive"] = data["internet_archive_query"]
        if data.get("nasa_query"):
            topic.queries["nasa"] = data["nasa_query"]
    elif topic.kind == "sport":   # no model: search the old sport, never the current event's names
        m = SPORT.search(topic.title.replace(" ", "_"))
        sport = {"ipl": "cricket", "ashes": "cricket", "t20": "cricket", "test_match": "cricket",
                 "fifa": "soccer", "world_cup": "soccer", "premier_league": "soccer", "champions_league": "soccer",
                 "la_liga": "soccer", "copa": "soccer", "euro_20": "soccer", "heavyweight": "boxing",
                 "grand_prix": "auto racing", "formula_one": "auto racing", "f1": "auto racing",
                 "indianapolis": "auto racing", "super_bowl": "football", "nfl": "football",
                 "world_series": "baseball", "indian_premier_league": "cricket", "wimbledon": "tennis",
                 "tour_de_france": "cycling", "kentucky_derby": "horse racing", "ufc": "boxing",
                 "olympic": "olympics", "nba": "basketball", "euro_20": "soccer"}
        generic = {"cricket", "football", "soccer", "boxing", "baseball", "rugby", "marathon", "wrestling",
                   "horse_racing", "olympics"}
        key = m.group(1).lower().strip("_")
        sport = sport.get(key) or (key.replace("_", " ") if key in generic else "sports")
        topic.queries["internet_archive"] = f"{sport} newsreel"
        topic.angle = f"How {sport} looked in old newsreels"
    else:   # no model: the article title is the query
        base = re.sub(r"\(.*?\)", "", topic.title).strip()
        topic.queries["internet_archive"] = base
        if SPACE.search(f"{topic.title} {topic.facts[:300]}"):
            topic.queries["nasa"] = base
    return topic if topic.queries else None


def relevant(item, query: str) -> bool:
    """At least one meaningful query word must appear in the item's title/subjects/description."""
    words = [w for w in re.findall(r"[a-z]{4,}", query.lower())
             if w not in {"film", "films", "footage", "newsreel", "video", "history", "vintage", "classic"}]
    hay = f"{item.title} {item.text}".lower()
    return not words or any(w in hay for w in words)


def todays_topics(cfg, llm, cache_dir, cutoff: int, max_topics: int = 8,
                  allow_sports: bool = False, sports_only: bool = False) -> list[Topic]:
    mode = "sports" if sports_only else ("all" if allow_sports else "general")
    cache = cache_dir / f"trends_{date.today():%Y-%m-%d}_{mode}.json"
    if cache.exists():
        return [Topic(**t) for t in json.loads(cache.read_text())]
    planned = []
    try:
        cands = candidates(cfg, allow_sports=allow_sports, sports_only=sports_only)
    except Exception as e:  # noqa: BLE001
        log.warning("trend feeds unavailable: %s", e)
        return []
    for t in cands:
        if len(planned) >= max_topics:
            break
        p = plan(t, llm, cutoff)
        if p:
            planned.append(p)
    cache.write_text(json.dumps([t.to_json() for t in planned], indent=1))
    log.info("trends: %d topics planned: %s", len(planned), ", ".join(t.title for t in planned))
    return planned
