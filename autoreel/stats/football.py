"""Football stats from openfootball (public domain): league results since 2010."""
import json
import re
import urllib.request
from collections import defaultdict
from datetime import date
from pathlib import Path

from .. import log
from . import StatStory, fmt

RAW = "https://raw.githubusercontent.com/openfootball/football.json/master/{season}/{code}.json"
LEAGUES = {"en.1": "Premier League", "es.1": "La Liga", "de.1": "Bundesliga", "it.1": "Serie A",
           "fr.1": "Ligue 1"}
SOURCE = ("openfootball", "https://github.com/openfootball/football.json",
          "Public domain (openfootball football.json)")


def clean(team: str) -> str:
    t = re.sub(r"^(AFC|FC|SC|CF|SSC|AC|US|AS|SV|VfB|VfL|TSG|RCD|CA|RC|UD|SD)\s+", "", team.strip())
    t = re.sub(r"\s+(FC|AFC|CF|SC|BC|Calcio|1846|1907|1909|1913)$", "", t)
    return t.replace(" & Hove Albion", "").replace("Wolverhampton Wanderers", "Wolves").strip()


def season_names(first: int, today: date | None = None) -> list[str]:
    today = today or date.today()
    last = today.year if today.month >= 8 else today.year - 1
    return [f"{y}-{(y + 1) % 100:02d}" for y in range(first, last + 1)]


def load_season(code: str, season: str, cache: Path) -> list[dict]:
    cache.mkdir(parents=True, exist_ok=True)
    f = cache / f"{code}_{season}.json"
    import time
    current = season == season_names(2000)[-1]
    stale = f.exists() and current and time.time() - f.stat().st_mtime > 12 * 3600
    if not f.exists() or stale:                    # the running season refreshes twice a day
        try:
            with urllib.request.urlopen(RAW.format(season=season, code=code), timeout=30) as r:
                f.write_bytes(r.read())
        except Exception as e:  # noqa: BLE001
            if not f.exists():
                log.debug("no %s %s: %s", code, season, e)
                return []
    data = json.loads(f.read_text())
    raw = data.get("matches") or [m for rnd in data.get("rounds", []) for m in rnd.get("matches", [])]
    out = []
    for m in raw:
        sc = m.get("score")
        if isinstance(sc, list):
            ft = sc
        elif isinstance(sc, dict):
            ft = sc.get("ft")
        else:
            ft = [m["score1"], m["score2"]] if m.get("score1") is not None else None
        if not ft or len(ft) < 2 or ft[0] is None or ft[1] is None:
            continue
        t1, t2 = m["team1"], m["team2"]
        t1 = t1["name"] if isinstance(t1, dict) else t1
        t2 = t2["name"] if isinstance(t2, dict) else t2
        out.append({"date": m.get("date", ""), "home": clean(t1), "away": clean(t2),
                    "hg": int(ft[0]), "ag": int(ft[1]), "season": season})
    return sorted(out, key=lambda m: m["date"])


def _points(m):
    if m["hg"] > m["ag"]:
        return 3, 0
    if m["hg"] < m["ag"]:
        return 0, 3
    return 1, 1


def complete(matches: list[dict]) -> bool:
    teams = {m["home"] for m in matches} | {m["away"] for m in matches}
    return len(teams) >= 16 and len(matches) >= len(teams) * (len(teams) - 1)


def stories(code: str, cache: Path, first_season: int = 2010) -> list[StatStory]:
    league = LEAGUES.get(code, code)
    seasons = {s: load_season(code, s, cache) for s in season_names(first_season)}
    seasons = {s: m for s, m in seasons.items() if m}
    if not seasons:
        return []
    names = sorted(seasons)
    done = [s for s in names if complete(seasons[s])]
    out: list[StatStory] = []
    base = dict(sport="football", competition=code, source_name=SOURCE[0], source_url=SOURCE[1],
                licence=SOURCE[2], keywords=[league.lower(), "football", "soccer", code])

    # 1) all-time points race across seasons
    teams = sorted({t for ms in seasons.values() for m in ms for t in (m["home"], m["away"])})
    tot = defaultdict(float)
    series = {t: [] for t in teams}
    for s in names:
        for m in seasons[s]:
            a, b = _points(m)
            tot[m["home"]] += a
            tot[m["away"]] += b
        for t in teams:
            series[t].append(tot[t])
    top = sorted(teams, key=lambda t: tot[t], reverse=True)[:12]
    span = f"{names[0][:4]}-{names[-1][-2:]}"
    last_date = max(m["date"][:10] for m in seasons[names[-1]])
    upto = f" (results up to {last_date})" if names[-1] not in done else ""
    final = sorted(((t, tot[t]) for t in top), key=lambda kv: kv[1], reverse=True)
    out.append(StatStory(
        id=f"fb:{code}:alltime_points:{names[-1]}:{len(seasons[names[-1]])}", kind="bar_race",
        title=f"Most {league} points since {names[0][:4]}", subtitle="Total league points, season by season",
        unit="points", steps=[s.replace("-", "/") for s in names], series={t: series[t] for t in top},
        facts=f"{league} total points from {names[0]} to {names[-1]}{upto}: " +
              "; ".join(f"{t} {fmt(v)}" for t, v in final[:10]) + f". Seasons: {len(names)}.",
        summary=f"Which club has collected the most {league} points since {names[0][:4]}", **base))

    # 2) title race, week by week: the running season once it has enough results, else the last full one
    cur = names[-1] if len(seasons[names[-1]]) >= 150 or not done else done[-1]
    ms = seasons[cur]
    if len(ms) >= 60:
        weeks = sorted({m["date"][:10] for m in ms})
        bins = [weeks[i] for i in range(0, len(weeks), max(1, len(weeks) // 30))]
        pts = defaultdict(float)
        cteams = sorted({m["home"] for m in ms} | {m["away"] for m in ms})
        ser = {t: [] for t in cteams}
        i = 0
        for edge in bins[1:] + ["9999"]:
            while i < len(ms) and ms[i]["date"][:10] < edge:
                a, b = _points(ms[i])
                pts[ms[i]["home"]] += a
                pts[ms[i]["away"]] += b
                i += 1
            for t in cteams:
                ser[t].append(pts[t])
        top = sorted(cteams, key=lambda t: pts[t], reverse=True)[:10]
        played = defaultdict(int)
        for m in ms:
            played[m["home"]] += 1
            played[m["away"]] += 1
        state = "final table" if cur in done else f"table after {max(played.values())} games"
        out.append(StatStory(
            id=f"fb:{code}:season_race:{cur}:{len(ms)}", kind="bar_race",
            title=f"The {league} {cur.replace('-', '/')} title race", subtitle=f"Points as the season unfolded",
            unit="points", steps=[d[5:].replace("-", "/") for d in bins] if len(bins) == len(ser[top[0]])
            else [str(k + 1) for k in range(len(ser[top[0]]))],
            series={t: ser[t] for t in top},
            facts=f"{league} {cur} {state}: " + "; ".join(f"{t} {fmt(pts[t])} points" for t in top) + ".",
            summary=f"How the {league} {cur} table developed", **base))

    # 3) biggest wins
    allm = [m for s in names for m in seasons[s]]
    big = sorted(allm, key=lambda m: (abs(m["hg"] - m["ag"]), m["hg"] + m["ag"]), reverse=True)[:10]
    rank = [(f"{m['home']} {m['hg']}-{m['ag']} {m['away']}", abs(m["hg"] - m["ag"]), m["date"][:10]) for m in big]
    out.append(StatStory(
        id=f"fb:{code}:biggest_wins:{len(allm)}", kind="ranking",
        title=f"The biggest wins in the {league} since {names[0][:4]}", subtitle="Ranked by goal margin",
        unit="goal margin", ranking=rank,
        facts=f"Biggest {league} wins since {names[0][:4]}: " + "; ".join(f"{n} on {d}" for n, _, d in rank) + ".",
        summary=f"Ten most lopsided {league} results since {names[0][:4]}", **base))

    # 4) most goals in a single season (club)
    rows = []
    for s in done:
        g = defaultdict(int)
        for m in seasons[s]:
            g[m["home"]] += m["hg"]
            g[m["away"]] += m["ag"]
        rows += [(t, v, s.replace("-", "/")) for t, v in g.items()]
    if rows:
        best = sorted(rows, key=lambda r: r[1], reverse=True)[:10]
        out.append(StatStory(
            id=f"fb:{code}:most_goals_season:{done[-1]}", kind="ranking",
            title=f"Most goals in one {league} season since {names[0][:4]}", subtitle="Club goals in a season",
            unit="goals", ranking=[(f"{t} ({s})", v, s) for t, v, s in best],
            facts=f"Most {league} goals in a season since {names[0][:4]}: " +
                  "; ".join(f"{t} {v} goals in {s}" for t, v, s in best) + ".",
            summary=f"The highest-scoring {league} seasons by a club", **base))

    # 5) duel: the two biggest clubs by all-time points
    if len(final) >= 2:
        a, b = final[0][0], final[1][0]
        out.append(StatStory(
            id=f"fb:{code}:duel:{a}:{b}:{names[-1]}", kind="duel",
            title=f"{a} vs {b}: {league} points since {names[0][:4]}", subtitle="Total league points",
            unit="points", steps=[s.replace("-", "/") for s in names], series={a: series[a], b: series[b]},
            facts=f"{a} {fmt(tot[a])} points and {b} {fmt(tot[b])} points in the {league} from {names[0]} to "
                  f"{names[-1]}{upto}, a gap of {fmt(abs(tot[a] - tot[b]))}.",
            summary=f"{a} and {b} side by side since {names[0][:4]}", **base))
    log.info("football %s: %d stories from %d seasons (%s)", code, len(out), len(names), span)
    return out
