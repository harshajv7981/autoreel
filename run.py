#!/usr/bin/env python3
"""AutoReel: public-domain footage -> YouTube Shorts, on autopilot.

  python run.py auth CHANNEL        one-time Google sign-in for a channel (saves its token)
  python run.py run                 one full cycle: make Shorts, check approvals, upload approved
  python run.py daemon              run a cycle every hour, forever
  python run.py make                only make new Shorts (fill each channel's buffer)
  python run.py review              list Shorts waiting for you + write data/review/index.html
  python run.py approve ID [ID..]   approve Shorts for upload
  python run.py reject ID [ID..]    reject Shorts
  python run.py upload              upload approved Shorts into the next free slots
  python run.py trends              show today's trending topics and their archive searches
  python run.py longform            build this week's long-form compilation now
  python run.py status              everything the agent has made
Flags: --dry-run (never upload), --headless (sign in on a server), -v (debug logs)
"""
import argparse
import logging
import sys
import time
from pathlib import Path

from autoreel import log
from autoreel.agent import Agent, describe_clip
from autoreel.config import load_config
from autoreel.db import DB
from autoreel.longform import build_weekly
from autoreel.trends import todays_topics
from autoreel.publisher import get_service, publish_approved
from autoreel.review import poll_telegram, write_review_page


def cycle(cfg, db, args):
    agent = Agent(cfg, db)
    made = agent.fill_all()
    log.info("made: %s", made)
    if cfg.longform.enabled:
        for ch in cfg.channels:
            if ch.get("enabled", True):
                build_weekly(cfg, db, agent.llm, ch)
    applied = poll_telegram(cfg, db)
    if applied:
        log.info("applied %d decision(s) from Telegram", applied)
    page = write_review_page(cfg, db)
    waiting = len(db.clips("review"))
    if waiting:
        log.info("%d Short(s) waiting for review: %s", waiting, page)
    n = publish_approved(cfg, db, dry_run=args.dry_run, headless=args.headless)
    log.info("uploaded %d", n)
    usage = getattr(agent.llm, "usage", None)
    if usage and usage["calls"]:
        log.info("Claude usage this cycle: %d calls, %d input / %d output tokens",
                 usage["calls"], usage["input_tokens"], usage["output_tokens"])


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["auth", "run", "daemon", "make", "review", "approve",
                                        "reject", "upload", "status", "trends", "longform"])
    ap.add_argument("args", nargs="*")
    ap.add_argument("-c", "--config", default=Path(__file__).with_name("config.yaml"))
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--headless", action="store_true")
    ap.add_argument("--every", type=int, default=60, help="daemon interval in minutes")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)-7s %(message)s", datefmt="%H:%M:%S")
    cfg = load_config(args.config)
    db = DB(cfg.paths.state_db)

    if args.command == "auth":
        names = {c.name: c for c in cfg.channels}
        if not args.args or args.args[0] not in names:
            sys.exit(f"usage: run.py auth CHANNEL   (one of: {', '.join(names)})")
        ch = names[args.args[0]]
        svc = get_service(cfg, ch.token, args.headless)
        me = svc.channels().list(part="snippet", mine=True).execute()["items"][0]["snippet"]["title"]
        log.info("%s is signed in as YouTube channel %r", ch.name, me)
    elif args.command == "run":
        cycle(cfg, db, args)
    elif args.command == "daemon":
        while True:
            try:
                cycle(cfg, db, args)
            except Exception:  # noqa: BLE001
                log.exception("cycle failed; retrying next interval")
            time.sleep(args.every * 60)
    elif args.command == "make":
        log.info("made: %s", Agent(cfg, db).fill_all())
        write_review_page(cfg, db)
    elif args.command == "review":
        poll_telegram(cfg, db)
        for row in db.clips("review"):
            print(describe_clip(row))
        print(f"\nreview page: {write_review_page(cfg, db)}")
    elif args.command in ("approve", "reject"):
        for a in args.args:
            row = db.clip(int(a))
            if not row:
                print(f"#{a}: not found")
                continue
            db.set_clip(int(a), status="approved" if args.command == "approve" else "rejected")
            print(f"#{a}: {args.command}d")
    elif args.command == "upload":
        log.info("uploaded %d", publish_approved(cfg, db, dry_run=args.dry_run, headless=args.headless))
    elif args.command == "trends":
        agent = Agent(cfg, db)
        for t in todays_topics(cfg, agent.llm, cfg.paths.data, agent.policy.cutoff, cfg.trends.max_topics):
            print(f"- {t.title}  [{t.why}]\n    angle: {t.angle or '-'}\n    searches: {t.queries}")
    elif args.command == "longform":
        agent = Agent(cfg, db)
        for ch in cfg.channels:
            if ch.get("enabled", True):
                cid = build_weekly(cfg, db, agent.llm, ch, force=True)
                print(f"{ch.name}: {'long-form #' + str(cid) if cid else 'not enough approved Shorts yet'}")
    elif args.command == "status":
        for row in db.clips():
            print(describe_clip(row) + (f"\n   youtube: https://youtu.be/{row['youtube_id']} "
                                        f"(public at {row['publish_at']})" if row["youtube_id"] else "")
                  + (f"\n   error: {row['error'][:200]}" if row["error"] else ""))


if __name__ == "__main__":
    main()
