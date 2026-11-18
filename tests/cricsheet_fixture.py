"""Builds a small Cricsheet-format zip (same JSON layout as cricsheet.org) for offline tests."""
import json
import random
import zipfile
from pathlib import Path

BATTERS = {"Royal Strikers": ["A Kumar", "R Mehta", "S Patel", "J Doe"],
           "Coastal Kings": ["V Rao", "M Khan", "T Smith", "P Iyer"]}
BOWLERS = {"Royal Strikers": ["B Singh", "C Das"], "Coastal Kings": ["D Roy", "E Nair"]}


def innings(team, opp, rng, power):
    overs, bi, striker = [], 0, BATTERS[team][0]
    for o in range(20):
        deliveries = []
        for _ in range(6):
            r = rng.choices([0, 1, 2, 4, 6], weights=[30, 35, 10, 15, power.get(striker, 6)])[0]
            d = {"batter": striker, "bowler": BOWLERS[opp][o % 2], "non_striker": "x",
                 "runs": {"batter": r, "extras": 0, "total": r}}
            if rng.random() < 0.03:
                d["extras"] = {"wides": 1}
                d["runs"] = {"batter": 0, "extras": 1, "total": 1}
            elif rng.random() < 0.035 and bi < 3:
                d["wickets"] = [{"player_out": striker, "kind": rng.choice(["caught", "bowled", "run out"])}]
                bi += 1
                striker = BATTERS[team][bi]
            deliveries.append(d)
        overs.append({"over": o, "deliveries": deliveries})
    return {"team": team, "overs": overs}


def make_zip(path: Path, seasons=("2023", "2024", "2025"), per_season=6, seed=7) -> Path:
    rng = random.Random(seed)
    power = {"A Kumar": 14, "V Rao": 10}          # two clear six-hitters
    with zipfile.ZipFile(path, "w") as z:
        mid = 100000
        for s in seasons:
            for k in range(per_season):
                mid += 1
                t1, t2 = ("Royal Strikers", "Coastal Kings") if k % 2 else ("Coastal Kings", "Royal Strikers")
                m = {"meta": {"data_version": "1.1.0"},
                     "info": {"season": s, "dates": [f"{s}-04-{k + 1:02d}"], "gender": "male",
                              "teams": [t1, t2], "event": {"name": "Indian Premier League"},
                              "outcome": {"winner": t1}},
                     "innings": [innings(t1, t2, rng, power), innings(t2, t1, rng, power)]}
                z.writestr(f"{mid}.json", json.dumps(m))
        z.writestr("README.txt", "fixture")
    return path
