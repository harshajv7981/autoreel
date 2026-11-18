"""Scheduling + YouTube upload (Data API v3, your own OAuth client, one token per channel)."""
import json
import random
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from . import log

SCOPES = ["https://www.googleapis.com/auth/youtube.upload",
          "https://www.googleapis.com/auth/youtube"]
RETRY = {500, 502, 503, 504}


class QuotaExceeded(RuntimeError):
    pass


# ------------------------------------------------------------ scheduling

def next_slot(cfg, taken: set[str], clip_id: int, slots=None, prefix: str = "") -> tuple[str, str]:
    """Return (slot_key, publishAt UTC). slot_key is the un-jittered local slot."""
    sch = cfg.youtube.schedule
    slots = slots or sch.slots
    tz = ZoneInfo(sch.timezone)
    earliest = datetime.now(tz) + timedelta(minutes=sch.min_lead_minutes)
    day = earliest.date()
    rng = random.Random(clip_id)
    for _ in range(400):
        for hhmm in sorted(slots):
            h, m = map(int, hhmm.split(":"))
            base = datetime(day.year, day.month, day.day, h, m, tzinfo=tz)
            key = prefix + base.strftime("%Y-%m-%dT%H:%M")
            jit = rng.randint(-sch.jitter_minutes, sch.jitter_minutes) if sch.jitter_minutes else 0
            when = base + timedelta(minutes=jit, seconds=rng.randint(0, 59))
            if when >= earliest and key not in taken:
                return key, when.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        day += timedelta(days=1)
    raise RuntimeError("no free slot within a year")


# ------------------------------------------------------------ auth + upload

def get_service(cfg, token_path: Path, headless: bool = False):
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build

    creds = Credentials.from_authorized_user_file(str(token_path), SCOPES) if token_path.exists() else None
    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
    elif not creds or not creds.valid:
        secret = cfg.youtube.client_secret
        if not secret.exists():
            raise FileNotFoundError(f"missing OAuth client file {secret} (README step 3)")
        flow = InstalledAppFlow.from_client_secrets_file(str(secret), SCOPES)
        creds = flow.run_local_server(port=8765 if headless else 0, open_browser=not headless)
    token_path.parent.mkdir(parents=True, exist_ok=True)
    token_path.write_text(creds.to_json())
    return build("youtube", "v3", credentials=creds, cache_discovery=False)


def _reason(err) -> str:
    try:
        return json.loads(err.content)["error"]["errors"][0]["reason"]
    except Exception:  # noqa: BLE001
        return ""


def build_body(cfg, meta: dict, publish_at: str | None) -> dict:
    status = {
        "privacyStatus": "private",              # stays private until publishAt
        "selfDeclaredMadeForKids": bool(cfg.youtube.made_for_kids),
        # Truthful declaration: real archival footage, edited (cut, reframed, captioned).
        # No AI-generated or AI-altered imagery or voices are added by this pipeline.
        "containsSyntheticMedia": False,
    }
    if publish_at:
        status["publishAt"] = publish_at
    return {
        "snippet": {"title": meta["title"][:100], "description": meta["description"][:5000],
                    "tags": meta.get("tags", [])[:15], "categoryId": str(cfg.youtube.category_id),
                    "defaultLanguage": cfg.youtube.default_language,
                    "defaultAudioLanguage": cfg.youtube.default_language},
        "status": status,
    }


def upload(service, video: Path, body: dict, playlist_id: str | None, thumbnail: Path | None = None) -> str:
    from googleapiclient.errors import HttpError
    from googleapiclient.http import MediaFileUpload

    media = MediaFileUpload(str(video), mimetype="video/mp4", chunksize=8 << 20, resumable=True)
    req = service.videos().insert(part="snippet,status", body=body, media_body=media)
    response, attempt = None, 0
    while response is None:
        try:
            _, response = req.next_chunk()
        except HttpError as e:
            reason = _reason(e)
            if reason in ("quotaExceeded", "dailyLimitExceeded", "uploadLimitExceeded"):
                raise QuotaExceeded(reason) from e
            if e.resp.status not in RETRY:
                raise
            attempt = _backoff(attempt, e)
        except (ConnectionError, TimeoutError, OSError) as e:
            attempt = _backoff(attempt, e)
    vid = response["id"]
    if thumbnail and Path(thumbnail).exists():
        try:     # custom thumbnails need a phone-verified channel
            service.thumbnails().set(videoId=vid, media_body=MediaFileUpload(str(thumbnail))).execute()
        except HttpError as e:
            log.warning("thumbnail not set (%s) - verify the channel at youtube.com/verify", _reason(e) or e)
    if playlist_id:
        try:
            service.playlistItems().insert(part="snippet", body={"snippet": {
                "playlistId": playlist_id, "resourceId": {"kind": "youtube#video", "videoId": vid}}}).execute()
        except HttpError as e:
            log.warning("playlist add failed: %s", _reason(e) or e)
    return vid


def _backoff(attempt, err):
    attempt += 1
    if attempt > 8:
        raise RuntimeError(f"upload failed after retries: {err}")
    wait = min(2 ** attempt, 120) + random.random()
    log.warning("upload hiccup (%s); retry in %.0fs", err, wait)
    time.sleep(wait)
    return attempt


def publish_approved(cfg, db, dry_run: bool = False, headless: bool = False) -> int:
    since = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat(timespec="seconds")
    budget = cfg.youtube.max_uploads_per_day - db.uploads_since(since)
    if budget <= 0:
        log.info("daily upload cap reached")
        return 0
    channels = {c.name: c for c in cfg.channels}
    services, taken, done = {}, {}, 0
    for row in db.clips("approved")[:budget]:
        ch = channels.get(row["channel"])
        if not ch:
            continue
        meta = json.loads(row["meta"])
        if ch.name not in taken:
            taken[ch.name] = db.taken_slots(ch.name)
        is_long = row["kind"] == "long"
        slot, publish_at = next_slot(cfg, taken[ch.name], row["id"],
                                     slots=cfg.longform.publish_slots if is_long else None,
                                     prefix="L" if is_long else "")
        taken[ch.name].add(slot)
        body = build_body(cfg, meta, publish_at)
        if dry_run:
            log.info("[dry-run] #%d -> %s at %s | %r", row["id"], ch.name, publish_at, meta["title"])
            continue
        try:
            if ch.name not in services:
                services[ch.name] = get_service(cfg, ch.token, headless)
            thumb = meta.get("thumbnail") if cfg.youtube.get("set_thumbnail", True) else None
            vid = upload(services[ch.name], Path(row["video"]), body, ch.get("playlist_id"), thumb)
            db.set_clip(row["id"], status="uploaded", youtube_id=vid, publish_at=publish_at, slot=slot,
                        error=None)
            log.info("uploaded #%d https://youtu.be/%s (goes public %s)", row["id"], vid, publish_at)
            done += 1
        except QuotaExceeded as e:
            log.warning("quota hit (%s); remaining clips stay queued", e)
            break
        except Exception as e:  # noqa: BLE001
            log.exception("upload failed for #%d", row["id"])
            db.set_clip(row["id"], status="failed", error=str(e)[:1500])
    return done
