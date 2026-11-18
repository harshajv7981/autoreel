# AutoReel

An agent that turns **public-domain footage** into YouTube Shorts and posts them on a schedule.

```
 Internet Archive / NASA / your NPS-USGS folder
        │  keyless APIs, licence check, provenance saved
        ▼
 Moment finder  ── motion, cuts, title-card + dark-frame detection (works on silent film)
        ▼
 Judge (local vision model)  ── "is this worth watching? what happens?"
        ▼
 Copywriter (local LLM)  ── hook, title, description → lint → retry with feedback
        ▼
 Composer  ── 9:16 reframe, film grade, hook text, credit, music, loudness
        ▼
 Review gate  ── review page or Telegram "ok 12" / "no 12"
        ▼
 Scheduled upload  ── YouTube Data API, private until a jittered publish slot
```

Everything except the final upload runs on your machine with free, open-source tools.
No account is needed for footage, AI models or editing.

## Why there's no AI label

YouTube labels **realistic altered or synthetic** content: AI-generated people, voices, places or events.
AutoReel never creates any of that:

* footage is real archival film, only cut, reframed, graded and captioned (normal editing);
* no AI voices, AI images, AI video, face swaps or AI "restoration"/colourisation
  (sources mentioning those are blocked by `policy.blocked_terms`);
* AI is only used to *pick* moments and *draft* captions, which YouTube doesn't require you to disclose;
* each upload truthfully sets `containsSyntheticMedia: false`;
* rendered files carry no editing-tool metadata.

If you ever add AI voiceover or generated visuals, set the flag to `true`. YouTube can apply
the label itself when it detects undisclosed synthetic media.

## Why it doesn't look automated

* Every render varies its font, accent colour, grade, grain and title position (seeded per clip).
* Posting times get a small random offset (`schedule.jitter_minutes`).
* Captions go through `copywriter.lint`, which rejects hype words, stock AI phrasing, em dashes,
  emoji spam, caps shouting, and **any name that isn't in the source facts**.
* You approve every Short before it's scheduled (`review.require_approval: true`).

## Setup (one time, ~30 minutes)

1. **Install tools**: Python 3.10+, `ffmpeg`, then `pip install -r requirements.txt`.
2. **Local AI (free, no sign-up)**: install [Ollama](https://ollama.com), then
   `ollama pull qwen2.5:7b` and `ollama pull qwen2.5vl:7b`.
   No GPU? It still works, just slower. Or set `llm.provider: none` to use motion scores and caption templates.
3. **YouTube access** (the only account step; uses your existing Google account):
   1. Google Cloud Console → new project → enable **YouTube Data API v3**.
   2. OAuth consent screen → External → add yourself as a test user.
   3. Credentials → Create OAuth client ID → **Desktop app** → download JSON →
      save as `secrets/client_secret.json`.
   4. `python run.py auth classic-comedy` and sign in with the channel's Google account.
      Repeat per channel. On a server use `--headless` and SSH-forward port 8765.
4. **Music**: download a few tracks from Mixkit (free, no account, commercial use)
   into `music/`. Optionally add `track.txt` next to each with a credit line.
5. **Animal channel (optional)**: download public-domain b-roll from NPS / USGS / USFWS pages into
   `inbox/animals/` with a `licence.json`:
   ```json
   {"licence": "Public domain (National Park Service)", "credit": "Courtesy of the National Park Service",
    "url": "https://www.nps.gov/grca/learn/photosmultimedia/b-roll_hd20.htm", "title": "Grand Canyon wildlife"}
   ```
6. **Phone approvals (optional)**: create a bot with Telegram's @BotFather, put its token and your
   chat id in `review.telegram_*`. New Shorts arrive as videos; reply `ok 12` or `no 12`.

## Daily use

```bash
python run.py run --dry-run      # first try: makes Shorts, schedules nothing
python run.py review             # list + data/review/index.html
python run.py approve 3 4 7      # or reply in Telegram
python run.py run                # make, apply approvals, upload into the next slots
python run.py daemon --every 60  # leave it running (or use the systemd unit below)
python run.py status
```

`deploy/autoreel.service` runs the daemon on a Linux server (adjust paths, then
`sudo systemctl enable --now autoreel`).

## Config highlights (`config.yaml`)

| Setting | What it does |
|---|---|
| `channels[].sources` | `internet_archive` (Lucene query), `nasa` (keywords), `local` (folder) |
| `channels[].voice` | How captions should sound for that channel |
| `channels[].buffer` | Shorts kept ready ahead of schedule |
| `channels[].clip_seconds` | Min/max clip length |
| `compose.renderer` | `ffmpeg` (fast) or `hyperframes` (animated text; ~6x slower; Node 22+) |
| `schedule.slots` | Local posting times; one Short per slot per channel |
| `policy.max_clips_per_source` | Stops the channel from milking one film |

## Rules the code enforces

* **Internet Archive**: only films published in or before the current US public-domain cutoff year
  (published + 96 years; 1930 in 2026, 1931 from Jan 2027), or archive-vetted PD collections
  (Prelinger). Uploader-declared licences alone are not trusted.
* **NASA**: only NASA's own footage; items credited to third parties (e.g. a private launch company) are skipped.
* **Local**: no `licence.json`, no clip.
* Original audio is always replaced with your music (old prints often carry later, copyrighted scores).
* Every description carries the source, licence, link and music credit, which you'll need
  if Content ID makes a false claim on public-domain material (it happens; dispute with that link).

## Things to know

* **Monetization**: YouTube's reused-content policy wants added value. The hook text and captions help;
  ranking formats, fun facts or your own voice notes help more.
* **Public-domain status is per country.** These rules follow US law. Some films (Chaplin's, for one) are still
  under copyright in parts of Europe.
* **API project audit**: Google's docs disagree on whether uploads from unverified API projects stay private.
  If your scheduled Shorts never go public, request the YouTube API audit for your Cloud project.
* **HyperFrames** records render telemetry by default; check its docs if that matters to you.
* Prefer one niche per channel; YouTube recommends better when the audience is consistent.

## Project layout

```
run.py                     CLI
autoreel/agent.py          the agent loop (discover → judge → write → compose → queue)
autoreel/sources/          internet_archive, nasa, local + licence policy
autoreel/finder.py         visual moment finder
autoreel/copywriter.py     caption drafting + lint
autoreel/compose/          ffmpeg composer, ASS text, HyperFrames overlay
autoreel/review.py         review page + Telegram approvals
autoreel/publisher.py      scheduling + YouTube upload
templates/hyperframes/     HTML overlay template (+ vendored GSAP)
tests/                     19 tests, including full agent cycles
```
