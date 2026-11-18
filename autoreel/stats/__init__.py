"""Sports stat Shorts: animated charts built only from match data (no footage, photos or logos).

Facts aren't copyrighted, so stat videos avoid the strikes that end sports-clip channels.
Every number and name shown is computed from the data and passed to the copywriter as
its only allowed facts.
"""
from dataclasses import dataclass, field


@dataclass
class StatStory:
    id: str                         # stable; changes when new data arrives -> a new video
    sport: str                      # cricket | football
    competition: str                # ipl, tests, en.1 ...
    kind: str                       # bar_race | ranking | duel
    title: str                      # neutral factual title, e.g. "Most IPL runs, 2008-2025"
    subtitle: str                   # small line under the hook, e.g. "Cumulative runs by season"
    unit: str                       # runs, wickets, points, goals ...
    steps: list = field(default_factory=list)          # step labels (bar_race / duel)
    series: dict = field(default_factory=dict)         # name -> values per step (bar_race / duel)
    ranking: list = field(default_factory=list)        # [(name, value, detail)] #1 first (ranking)
    facts: str = ""                 # every name and number the video shows
    summary: str = ""               # one line: what the chart shows
    source_name: str = ""
    source_url: str = ""
    licence: str = ""
    keywords: list = field(default_factory=list)       # for matching trends

    def final_top(self, n=10):
        last = {k: v[-1] for k, v in self.series.items()}
        return sorted(last.items(), key=lambda kv: kv[1], reverse=True)[:n]


def fmt(v: float, unit: str = "") -> str:
    if unit in ("economy", "strike rate", "average"):
        return f"{v:.2f}"
    return f"{int(round(v)):,}"
