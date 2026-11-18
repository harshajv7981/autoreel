"""Cricket stats from Cricsheet ball-by-ball JSON (credit Cricsheet in every description).

Downloads one zip per competition, loads every delivery into SQLite, then builds stories:
run and wicket races by season, most sixes, highest scores, fastest fifties, best economy, duels.
"""
import json
import sqlite3
import urllib.request
import zipfile
from collections import defaultdict
from pathlib import Path

from .. import log
from . import StatStory, fmt

ZIPS = {"ipl": "ipl_json.zip", "tests": "tests_json.zip", "odis": "odis_json.zip", "t20is": "t20s_json.zip",
        "bbl": "bbl_json.zip", "psl": "psl_json.zip", "wpl": "wpl_json.zip"}
NAMES = {"ipl": "IPL", "tests": "Test", "odis": "ODI", "t20is": "T20I", "bbl": "BBL", "psl": "PSL", "wpl": "WPL"}
URL = "https://cricsheet.org/downloads/{zip}"
SOURCE = ("Cricsheet", "https://cricsheet.org", "Data from Cricsheet (cricsheet.org)")
NOT_BOWLER_WICKETS = {"run out", "retired hurt", "retired out", "obstructing the field", "retired not out"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS matches(id TEXT PRIMARY KEY, comp TEXT, season TEXT, date TEXT, gender TEXT,
  team1 TEXT, team2 TEXT, winner TEXT, event TEXT);
CREATE TABLE IF NOT EXISTS balls(match_id TEXT, inning INTEGER, team TEXT, opp TEXT, seq INTEGER,
  batter TEXT, bowler TEXT, runs_bat INTEGER, runs_total INTEGER, wide INTEGER, noball INTEGER,
  wicket_kind TEXT, player_out TEXT);
CREATE INDEX IF NOT EXISTS balls_m ON balls(match_id);
"""


def season_key(s) -> str:
    s = str(s)
    return s if "/" not in s else s.split("/")[0]


def download(comp: str, cache: Path) -> Path | None:
    cache.mkdir(parents=True, exist_ok=True)
    z = cache / ZIPS[comp]
    log.info("downloading Cricsheet %s data", comp)
    try:
        with urllib.request.urlopen(URL.format(zip=ZIPS[comp]), timeout=120) as r:
            z.write_bytes(r.read())
    except Exception as e:  # noqa: BLE001
        if not z.exists():
            log.warning("Cricsheet %s unavailable: %s", comp, e)
            return None
        log.info("Cricsheet %s: using cached zip (%s)", comp, e)
    return z


def load_zip(zpath: Path, comp: str, db: sqlite3.Connection, gender: str = "male") -> int:
    db.executescript(SCHEMA)
    known = {r[0] for r in db.execute("SELECT id FROM matches WHERE comp=?", (comp,))}
    added = 0
    with zipfile.ZipFile(zpath) as zf:
        for name in zf.namelist():
            if not name.endswith(".json"):
                continue
            mid = f"{comp}:{Path(name).stem}"
            if mid in known:
                continue
            m = json.loads(zf.read(name))
            if load_match(m, mid, comp, db, gender):
                added += 1
    db.commit()
    return added


def load_match(m: dict, mid: str, comp: str, db: sqlite3.Connection, gender: str = "male") -> bool:
    info = m.get("info", {})
    if gender and info.get("gender", "male") != gender:
        return False
    teams = info.get("teams", ["?", "?"])
    db.execute("INSERT OR REPLACE INTO matches VALUES (?,?,?,?,?,?,?,?,?)",
               (mid, comp, season_key(info.get("season", (info.get("dates") or ["?"])[0][:4])),
                (info.get("dates") or [""])[0], info.get("gender", "male"), teams[0], teams[-1],
                (info.get("outcome") or {}).get("winner"), (info.get("event") or {}).get("name", "")))
    rows, seq = [], 0
    for i, inn in enumerate(m.get("innings", []), 1):
        team = inn.get("team")
        opp = teams[1] if team == teams[0] else teams[0]
        for over in inn.get("overs", []):
            for d in over.get("deliveries", []):
                seq += 1
                ex = d.get("extras") or {}
                w = (d.get("wickets") or [{}])[0]
                rows.append((mid, i, team, opp, seq, d.get("batter"), d.get("bowler"),
                             d["runs"]["batter"], d["runs"]["total"], int(ex.get("wides", 0)),
                             int(ex.get("noballs", 0)), w.get("kind"), w.get("player_out")))
    db.executemany("INSERT INTO balls VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
    return True


def _q(db, sql, args=()):
    return db.execute(sql, args).fetchall()


def stories(comp: str, db: sqlite3.Connection, min_balls_economy: int = 300) -> list[StatStory]:
    label = NAMES.get(comp, comp.upper())
    seasons = [r[0] for r in _q(db, "SELECT DISTINCT season FROM matches WHERE comp=? ORDER BY season", (comp,))]
    if not seasons:
        return []
    n_matches = _q(db, "SELECT COUNT(*) FROM matches WHERE comp=?", (comp,))[0][0]
    span = f"{seasons[0]}-{seasons[-1]}" if len(seasons) > 1 else seasons[0]
    extra = {"ipl": ["indian premier league"], "tests": ["test cricket", "ashes", "test match", "border-gavaskar"],
             "odis": ["one day international", "cricket world cup", "champions trophy"],
             "t20is": ["t20 world cup", "twenty20 international"]}.get(comp, [])
    base = dict(sport="cricket", competition=comp, source_name=SOURCE[0], source_url=SOURCE[1], licence=SOURCE[2],
                keywords=[label.lower(), "cricket", *extra])
    out: list[StatStory] = []
    tag = f"{seasons[-1]}:{n_matches}"
    join = "FROM balls b JOIN matches m ON b.match_id=m.id WHERE m.comp=?"

    def race(metric_sql, unit, title, subtitle, sid):
        per = defaultdict(lambda: defaultdict(float))
        for name, season, v in _q(db, f"SELECT {metric_sql} {join} GROUP BY 1, 2", (comp,)):
            if name:
                per[name][season] += v
        cum = {}
        for name, by in per.items():
            run, vals = 0.0, []
            for s in seasons:
                run += by.get(s, 0)
                vals.append(run)
            cum[name] = vals
        top = sorted(cum, key=lambda k: cum[k][-1], reverse=True)[:12]
        if not top:
            return
        final = [(k, cum[k][-1]) for k in top]
        out.append(StatStory(
            id=f"ck:{comp}:{sid}:{tag}", kind="bar_race", title=title, subtitle=subtitle, unit=unit,
            steps=list(seasons), series={k: cum[k] for k in top},
            facts=f"{title} ({span}, {n_matches} matches): " + "; ".join(f"{k} {fmt(v)} {unit}" for k, v in final[:10]) + ".",
            summary=title, **base))
        return final

    runs_final = race("b.batter, m.season, SUM(b.runs_bat)", "runs", f"Most {label} runs of all time",
                      "Career runs, season by season", "runs_race")
    race("b.bowler, m.season, SUM(CASE WHEN b.wicket_kind IS NOT NULL AND b.wicket_kind NOT IN "
         "('run out','retired hurt','retired out','obstructing the field','retired not out') THEN 1 ELSE 0 END)",
         "wickets", f"Most {label} wickets of all time", "Career wickets, season by season", "wickets_race")

    # most sixes (ranking)
    six = _q(db, f"SELECT b.batter, COUNT(*) {join} AND b.runs_bat=6 GROUP BY 1 ORDER BY 2 DESC LIMIT 10", (comp,))
    if six:
        out.append(StatStory(
            id=f"ck:{comp}:sixes:{tag}", kind="ranking", title=f"Most sixes in {label} history",
            subtitle=f"All matches, {span}", unit="sixes", ranking=[(n, v, "") for n, v in six],
            facts=f"Most sixes in {label} history ({span}): " + "; ".join(f"{n} {v} sixes" for n, v in six) + ".",
            summary=f"The {label}'s biggest six-hitters", **base))

    # highest individual scores
    hs = _q(db, f"SELECT b.batter, SUM(b.runs_bat) r, b.opp, m.season, SUM(CASE WHEN b.wide=0 THEN 1 ELSE 0 END) "
                f"{join} GROUP BY b.match_id, b.inning, b.batter ORDER BY r DESC LIMIT 10", (comp,))
    if hs:
        out.append(StatStory(
            id=f"ck:{comp}:top_scores:{tag}", kind="ranking", title=f"Highest individual scores in {label} history",
            subtitle=f"Runs in one innings, {span}", unit="runs",
            ranking=[(f"{n}", r, f"vs {opp}, {s} ({balls} balls)") for n, r, opp, s, balls in hs],
            facts=f"Highest {label} individual scores ({span}): " +
                  "; ".join(f"{n} {r} off {balls} balls vs {opp} in {s}" for n, r, opp, s, balls in hs) + ".",
            summary=f"The biggest single innings in {label} history", **base))

    # fastest fifties (balls faced to reach 50), limited-overs only
    if comp != "tests":
        fastest = []
        cur_key, runs, balls, done = None, 0, 0, False
        for mid, inn, bat, rb, wide, opp, season in _q(
                db, f"SELECT b.match_id, b.inning, b.batter, b.runs_bat, b.wide, b.opp, m.season {join} "
                    f"ORDER BY b.match_id, b.inning, b.batter, b.seq", (comp,)):
            key = (mid, inn, bat)
            if key != cur_key:
                cur_key, runs, balls, done = key, 0, 0, False
            if done:
                continue
            balls += 0 if wide else 1
            runs += rb
            if runs >= 50:
                fastest.append((bat, balls, f"vs {opp}, {season}"))
                done = True
        fastest = sorted(fastest, key=lambda r: r[1])[:10]
        if fastest:
            out.append(StatStory(
                id=f"ck:{comp}:fastest_fifties:{tag}", kind="ranking", title=f"Fastest fifties in {label} history",
                subtitle="Balls taken to reach 50", unit="balls", ranking=fastest,
                facts=f"Fastest {label} fifties ({span}): " +
                      "; ".join(f"{n} in {b} balls {d}" for n, b, d in fastest) + ".",
                summary=f"The quickest half-centuries in {label} history", **base))

    # best economy (min balls)
    eco = _q(db, f"SELECT b.bowler, SUM(b.runs_bat + b.wide + b.noball) * 6.0 / "
                 f"SUM(CASE WHEN b.wide=0 AND b.noball=0 THEN 1 ELSE 0 END) e, "
                 f"SUM(CASE WHEN b.wide=0 AND b.noball=0 THEN 1 ELSE 0 END) n {join} GROUP BY 1 "
                 f"HAVING n >= ? ORDER BY e ASC LIMIT 10", (comp, min_balls_economy))
    if eco and comp != "tests":
        out.append(StatStory(
            id=f"ck:{comp}:economy:{tag}", kind="ranking", title=f"Most economical bowlers in {label} history",
            subtitle=f"Runs per over, min {min_balls_economy // 6} overs", unit="economy",
            ranking=[(n, round(e, 2), f"{n_balls // 6} overs") for n, e, n_balls in eco],
            facts=f"Best {label} economy rates ({span}, min {min_balls_economy // 6} overs): " +
                  "; ".join(f"{n} {e:.2f} runs per over in {nb // 6} overs" for n, e, nb in eco) + ".",
            summary=f"The {label}'s hardest bowlers to score off", **base))

    # duel: top two run scorers
    if runs_final and len(runs_final) >= 2:
        (a, va), (b, vb) = runs_final[0], runs_final[1]
        ser = next(s for s in out if s.id.startswith(f"ck:{comp}:runs_race")).series
        out.append(StatStory(
            id=f"ck:{comp}:duel:{a}:{b}:{tag}", kind="duel", title=f"{a} vs {b}: {label} runs",
            subtitle="Career runs, season by season", unit="runs", steps=list(seasons),
            series={a: ser[a], b: ser[b]},
            facts=f"{a} {fmt(va)} runs and {b} {fmt(vb)} runs in the {label} ({span}), a gap of {fmt(abs(va - vb))}.",
            summary=f"{a} and {b} side by side", **base))
    log.info("cricket %s: %d stories, %d matches, %s", comp, len(out), n_matches, span)
    return out


def player_duel(db: sqlite3.Connection, comp: str, a: str, b: str) -> StatStory | None:
    """Duel for two named players (e.g. both trending)."""
    for st in stories(comp, db):
        if st.kind == "bar_race" and st.unit == "runs":
            if a in st.series and b in st.series:
                va, vb = st.series[a][-1], st.series[b][-1]
                return StatStory(id=f"ck:{comp}:duel:{a}:{b}:{st.id.split(':')[-1]}", sport="cricket",
                                 competition=comp, kind="duel", title=f"{a} vs {b}: {NAMES.get(comp, comp)} runs",
                                 subtitle="Career runs, season by season", unit="runs", steps=st.steps,
                                 series={a: st.series[a], b: st.series[b]},
                                 facts=f"{a} {fmt(va)} runs and {b} {fmt(vb)} runs, a gap of {fmt(abs(va - vb))}.",
                                 summary=f"{a} and {b} side by side", source_name=SOURCE[0],
                                 source_url=SOURCE[1], licence=SOURCE[2])
    return None
