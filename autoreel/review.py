"""Approval gate: a local review page, plus optional Telegram approve/reject from your phone."""
import html
import json
import os
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

from . import log


# ------------------------------------------------------------ local review page

def write_review_page(cfg, db) -> Path:
    out = cfg.paths.data / "review" / "index.html"
    cards = []
    for row in db.clips("review"):
        meta = json.loads(row["meta"])
        judged = json.loads(row["judged"] or "{}")
        rel = os.path.relpath(row["video"], out.parent)
        cards.append(f"""
<div class="card">
  <video src="{html.escape(rel)}" controls preload="metadata"></video>
  <div class="info">
    <div class="id">#{row['id']} · {html.escape(row['channel'])}</div>
    <h3>{html.escape(meta['title'])}</h3>
    <p class="hook">{html.escape(' / '.join(meta['hook']))}</p>
    <p class="muted">Packaging score {meta.get('packaging_score')}{''.join(
        f"<br>alt ({a['score']}): {html.escape(a['title'])} | {html.escape(a['hook'])}" for a in meta.get('alternates', []))}</p>
    <pre>{html.escape(meta['description'])}</pre>
    <p class="muted">Moment {row['start']:.0f}-{row['end']:.0f}s · score {row['score']:.2f}
      · judge: {html.escape(str(judged.get('reason', '')))} · copy: {html.escape(str(meta.get('copy_source')))}</p>
    <code>python run.py approve {row['id']}</code> &nbsp; <code>python run.py reject {row['id']}</code>
  </div>
</div>""")
    out.write_text(f"""<!doctype html><meta charset="utf-8"><title>AutoReel review</title>
<style>body{{font-family:system-ui;background:#111;color:#eee;margin:24px}}
.card{{display:flex;gap:20px;background:#1c1c1c;border-radius:12px;padding:16px;margin-bottom:16px}}
video{{width:270px;height:480px;background:#000;border-radius:8px}}.info{{flex:1;min-width:0}}
pre{{white-space:pre-wrap;background:#0b0b0b;padding:10px;border-radius:8px;font-size:13px}}
.hook{{font-size:20px;font-weight:700}}.muted{{color:#999;font-size:13px}}.id{{color:#4cc9f0}}
code{{background:#333;padding:3px 6px;border-radius:4px}}</style>
<h1>Waiting for review ({len(cards)})</h1>{''.join(cards) or '<p>Nothing to review.</p>'}""",
                   encoding="utf-8")
    return out


# ------------------------------------------------------------ Telegram (optional)

def _tg(cfg):
    r = cfg.review
    return (r.get("telegram_bot_token"), r.get("telegram_chat_id")) if r.get("telegram_bot_token") else (None, None)


def _tg_call(token: str, method: str, fields: dict, file_field: tuple | None = None, timeout=120):
    url = f"https://api.telegram.org/bot{token}/{method}"
    if not file_field:
        data = urllib.parse.urlencode(fields).encode()
        req = urllib.request.Request(url, data=data)
    else:
        boundary = uuid.uuid4().hex
        body = b""
        for k, v in fields.items():
            body += (f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n").encode()
        name, path = file_field
        body += (f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"; "
                 f"filename=\"{Path(path).name}\"\r\nContent-Type: video/mp4\r\n\r\n").encode()
        body += Path(path).read_bytes() + f"\r\n--{boundary}--\r\n".encode()
        req = urllib.request.Request(url, data=body,
                                     headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def notify_new_clip(cfg, row) -> None:
    token, chat = _tg(cfg)
    if not token:
        return
    meta = json.loads(row["meta"])
    caption = (f"#{row['id']} {row['channel']}\n{meta['title']}\n{' / '.join(meta['hook'])}\n\n"
               f"Reply: ok {row['id']}  or  no {row['id']}")
    try:
        _tg_call(token, "sendVideo", {"chat_id": chat, "caption": caption[:1000]}, ("video", row["video"]))
    except Exception as e:  # noqa: BLE001
        log.warning("telegram notify failed: %s", e)


def poll_telegram(cfg, db) -> int:
    """Apply 'ok 12' / 'no 12' replies from your chat. Returns number of decisions applied."""
    token, chat = _tg(cfg)
    if not token:
        return 0
    off_file = cfg.paths.data / "telegram_offset"
    offset = int(off_file.read_text()) if off_file.exists() else 0
    try:
        updates = _tg_call(token, "getUpdates", {"offset": offset, "timeout": 0})["result"]
    except Exception as e:  # noqa: BLE001
        log.warning("telegram poll failed: %s", e)
        return 0
    applied = 0
    for u in updates:
        offset = max(offset, u["update_id"] + 1)
        msg = u.get("message") or {}
        if str(msg.get("chat", {}).get("id")) != str(chat):
            continue                       # ignore anyone who isn't you
        parts = (msg.get("text") or "").lower().split()
        if len(parts) == 2 and parts[1].isdigit() and parts[0] in ("ok", "yes", "approve", "no", "reject"):
            clip_id = int(parts[1])
            row = db.clip(clip_id)
            if row and row["status"] == "review":
                db.set_clip(clip_id, status="approved" if parts[0] in ("ok", "yes", "approve") else "rejected")
                applied += 1
    off_file.write_text(str(offset))
    return applied
