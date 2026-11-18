# AutoReel

An agent that turns **public-domain footage** into YouTube Shorts and posts them on a schedule.
It follows what people are reading about today, finds real archival footage that belongs to that story,
tells it with on-screen text (no voice-over), and every week stitches the best moments into a long video.

```
 Trend radar: Wikipedia most-read + On this day   (keyless)
        │  skip gossip, deaths, live sport, new films; plan archive searches
        ▼
 Internet Archive / NASA / your NPS-USGS folder
        │  keyless APIs, licence check, provenance saved
        ▼
 Moment finder  ── motion, cuts, title-card + dark-frame detection (works on silent film)
        ▼
 Judge (local vision model)  ── "is this worth watching? what happens?"
        ▼
 Copywriter (local LLM)  ── hook + 2-3 fact captions, title, description
        │  lint: every number and name must be in the source facts; no hype, no AI tells
        ▼
 Composer  ── 9:16, 6 rotating layouts x 3 frame styles, original audio, loudness
        ▼
 Review gate  ── review page or Telegram "ok 12" / "no 12"
        ▼
 Scheduled upload  ── YouTube Data API, private until a jittered publish slot
        ▼
 Weekly long-form  ── best Shorts re-cut from the originals with context, 16:9, chapters, sources
```

Footage and editing use free, open-source tools with no accounts. The AI steps use the Claude API
(or a free local model via Ollama), and the final upload uses your YouTube account.

## What the channel audit changed (v3)

An audit of 18+ archival channels found no verified creator earning passive AdSense from unattended
clip-and-caption channels. The ones that grow (e.g. "Then & Now" history channels) win on **packaging**
and **motion**; the ones that stall post raw archive reels; the ones that get demonetized look like
slideshows or templates. So AutoReel now:

| Lesson | What the code does |
|---|---|
| Hooks decide everything | The copywriter drafts 4 title/hook options, rejects any with facts not in the sources, scores the rest (specific subject, real number, curiosity gap, no generic words) and keeps the best. Alternates appear on the review page. |
| Static visuals get flagged as reused content | Slow push-in on the footage, a then-to-now timeline with a moving marker, fact callouts that slide in with numbers highlighted, and a progress bar. Long-form parts get year stamps and chapter progress. |
| Restored/rescored copies get Content ID claims | `policy.content_id_risk_terms` skips restored, remastered, 4K/HD, rescored versions and re-uploads from archives that file claims (British Pathé, Periscope Film, CriticalPast and others). |
| Claimed Shorts over 1 minute are blocked | Shorts are capped at 58 seconds whatever the config says. |
| US/UK history, aerospace and tech pay best | Trend topics matching `trends.boost_terms` rank higher; US government films (FedFlix, NASA) are accepted at any year because federal works are public domain. |
| Long-form needs a thumbnail | Each weekly video gets a 1280x720 thumbnail from its sharpest frame with a two-line hook and year badge, uploaded automatically once your channel is phone-verified. |

Reality check from the same audit: archive long-form earns roughly $2-3 per 1,000 views and Shorts
about $0.17-0.33 even in the US/UK, and a solo archival channel can sit near zero for months. One video
making nearly all of a channel's views has also triggered YPP reviews. Plan for slow growth and steady variety.

## Sports & action channel

`sports-action-history` posts real sport and stunts from **1895-1967**, never modern matches.

* **Why not modern cricket/football?** League, board and broadcaster footage is fingerprinted and enforced hard:
  long-running archive channels have been terminated after copyright strikes, and boards have sent legal
  letters over even logos and still photos. One channel termination ends the project, so modern
  match footage, player photos and league logos are never used.
* **Where the footage comes from:** Universal Newsreels (1929-1967), which MCA/Universal gave to the
  US National Archives without copyright restrictions (accepted via `policy.pd_releases`), plus any
  film published up to the current US public-domain year (`{pd_cutoff}` in queries): early boxing,
  baseball, football, motor racing, air races, rodeo, and silent-film stunts (Lloyd, Keaton, Fairbanks).
* **Trends:** when a sport trends (World Cup, IPL, Ashes, Olympics, Super Bowl), the planner searches the
  *old* sport ("soccer newsreel"), never current names. Player pages are never used. Captions must
  say the footage is old (a year or "years ago"), so nobody mistakes it for today's match.
* **Expect:** US sports (boxing, baseball, American football, racing) are well covered. Vintage cricket and
  UK football are thin: most of it sits with British Pathé, the BFI, Reuters/ITN and India's Films Division,
  which claim their copies, so those sources are filtered out.

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

* Six text layouts and three frame styles rotate per clip, plus font, accent colour, grade and grain
  (YouTube's "inauthentic content" policy targets identical templated uploads).
* Posting times get a small random offset (`schedule.jitter_minutes`).
* Captions go through `copywriter.lint`, which rejects hype words, stock AI phrasing, em dashes,
  emoji spam, caps shouting, and **any name that isn't in the source facts**.
* You approve every Short before it's scheduled (`review.require_approval: true`).

## Setup (one time, ~30 minutes)

1. **Install tools**: Python 3.10+, `ffmpeg`, then `pip install -r requirements.txt`.
2. **Claude API (default)**: create a key at console.anthropic.com, then set it as an environment variable
   (never put it in config.yaml or commit it):
   * macOS/Linux: `export ANTHROPIC_API_KEY="sk-ant-..."` (add to `~/.bashrc` or the systemd unit)
   * Windows PowerShell: `setx ANTHROPIC_API_KEY "sk-ant-..."` then open a new terminal
   Models: `claude-sonnet-5-5` writes captions and plans trends; `claude-haiku-5-5` rates frames
   (it runs most often, so the faster, cheaper model). Each cycle logs how many tokens it used.
   Prefer free and local? Set `llm.provider: ollama` and run `ollama pull qwen2.5:7b qwen2.5vl:7b`.
3. **YouTube access** (the only account step; uses your existing Google account):
   1. Google Cloud Console → new project → enable **YouTube Data API v3**.
   2. OAuth consent screen → External → add yourself as a test user, then **Audience → Publish app**.
      Important: while the app is in "Testing", Google expires the sign-in after 7 days and uploads stop.
      Publishing for your own account doesn't need Google's review; you'll see an "unverified app"
      warning once when you sign in - click Advanced → continue.
   3. Credentials → Create OAuth client ID → **Desktop app** → download JSON →
      save as `secrets/client_secret.json`.
   4. `python run.py auth classic-comedy` and sign in with the channel's Google account.
      Repeat per channel. On a server use `--headless` and SSH-forward port 8765.
4. **Audio**: the default is each clip's own sound (`compose.audio: original`). Films made before 1928
   are silent, so any soundtrack on them was added later and may be copyrighted: those are muted.
   Prefer music? Set `compose.audio: music` and put tracks in `music/`.
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
python run.py trends             # what's trending today and which archive searches it maps to
python run.py longform           # build this week's compilation now (otherwise automatic, weekly)
python run.py daemon --every 60  # leave it running (or use the systemd unit below)
python run.py status
```

`deploy/autoreel.service` runs the daemon on a Linux server (adjust paths, then
`sudo systemctl enable --now autoreel`).

## Config highlights (`config.yaml`)

| Setting | What it does |
|---|---|
| `channels[].sources` | `trends`, `internet_archive` (Lucene query), `nasa` (keywords), `local` (folder) |
| `trends.feeds` | `wikipedia_top` (yesterday's most-read), `on_this_day` (anniversaries) |
| `compose.audio` | `original`, `none` or `music`; `mute_before_year` mutes silent-era films |
| `compose.layouts` / `frames` | Which layouts and frame styles rotate |
| `longform.*` | Weekly compilation: parts, target length, publish time |
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

* **Monetization (2027 rules)**: from 1 Feb 2027 new channels need 1,000 subscribers plus 8,000 watch hours
  in a year or 20M Shorts views in 90 days, and Shorts revenue needs 10M Shorts views per 90 days.
  The weekly long-form video is there for the watch-hour route.
* **Reused content**: YouTube wants added value. The fact captions and trend context are that value;
  keep `review.require_approval: true` and skim the queue weekly (catch wrong facts, weak clips).
* **Public-domain status is per country.** These rules follow US law. Some films (Chaplin's, for one) are still
  under copyright in parts of Europe.
* **API project audit**: Google's docs disagree on whether uploads from unverified API projects stay private.
  If your scheduled Shorts never go public, request the YouTube API audit for your Cloud project.
* **HyperFrames** records render telemetry by default; check its docs if that matters to you.
* Prefer one niche per channel; YouTube recommends better when the audience is consistent.

## Project layout

```
run.py                     CLI
autoreel/agent.py          the agent loop (trends → discover → judge → write → compose → queue)
autoreel/trends.py         trend radar (Wikipedia most-read, On this day) + topic planning
autoreel/longform.py       weekly 16:9 compilation with chapters
autoreel/sources/          internet_archive, nasa, local + licence policy
autoreel/finder.py         visual moment finder
autoreel/copywriter.py     caption drafting + lint
autoreel/compose/          ffmpeg composer, 6 ASS layouts, HyperFrames overlay
autoreel/review.py         review page + Telegram approvals
autoreel/publisher.py      scheduling + YouTube upload
templates/hyperframes/     HTML overlay template (+ vendored GSAP)
tests/                     40 tests, including full agent cycles and long-form builds
```
