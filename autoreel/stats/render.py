"""Animated stat charts as 1080x1920 Shorts: PIL draws each frame, ffmpeg encodes.

Three formats: bar_race (values over seasons, bars overtake each other), ranking (countdown
#10 -> #1), duel (two careers racing). Motion is constant; ticks mark each change. No music.
"""
import hashlib
import math
import random
import subprocess
import wave
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from .. import log
from . import StatStory, fmt

W, H, FPS = 1080, 1920, 30
PALETTE = ["#4CC9F0", "#F72585", "#FFD60A", "#2EC4B6", "#FF8C42", "#B8F35D", "#9D4EDD", "#F25F5C",
           "#00BBF9", "#FEE440", "#06D6A0", "#EF476F", "#118AB2", "#FFB703"]
BGS = [("#0b1020", "#1a1035"), ("#0d1b1e", "#13292a"), ("#141414", "#2a1a12"), ("#0f0f1a", "#24123a")]
_fonts: dict = {}


def font(size: int, bold: bool = True):
    key = (size, bold)
    if key not in _fonts:
        name = "Inter:bold" if bold else "Inter:medium"
        path = None
        try:
            path = subprocess.run(["fc-match", "-f", "%{file}", name], capture_output=True, text=True).stdout.strip()
        except FileNotFoundError:
            pass
        for p in (path, "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", "C:/Windows/Fonts/arialbd.ttf",
                  "/System/Library/Fonts/Supplemental/Arial Bold.ttf"):
            if p and Path(p).exists():
                try:
                    _fonts[key] = ImageFont.truetype(p, size)
                    break
                except OSError:
                    continue
        else:
            _fonts[key] = ImageFont.load_default(size)
    return _fonts[key]


def color_for(name: str) -> str:
    return PALETTE[int(hashlib.md5(name.encode()).hexdigest(), 16) % len(PALETTE)]


def ease(x: float) -> float:
    return 0.5 - 0.5 * math.cos(math.pi * max(0.0, min(1.0, x)))


def hexrgb(h: str):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


class Canvas:
    def __init__(self, seed: int, accent: str):
        rng = random.Random(seed)
        top, bot = BGS[rng.randrange(len(BGS))]
        t, b = np.array(hexrgb(top), np.float32), np.array(hexrgb(bot), np.float32)
        grad = (t[None, :] * (1 - np.linspace(0, 1, H))[:, None] + b[None, :] * np.linspace(0, 1, H)[:, None])
        self.bg = Image.fromarray(np.repeat(grad[:, None, :], W, axis=1).astype(np.uint8), "RGB")
        self.accent = accent

    def frame(self):
        img = self.bg.copy()
        return img, ImageDraw.Draw(img)


def text_w(d, s, f):
    return d.textlength(s, font=f)


def fit(d, s, f, max_w):
    if text_w(d, s, f) <= max_w:
        return s
    while s and text_w(d, s + "…", f) > max_w:
        s = s[:-1]
    return s + "…"


def header(d, hook: dict, story: StatStory, accent: str, t: float):
    """Hook (two lines, keyword coloured) + subtitle; fades in during the first 0.4s."""
    a = min(1.0, t / 0.4)
    y = 150 + int((1 - ease(a)) * 30)
    kw = (hook.get("keyword") or "").lower()
    for line, size in ((hook["line1"], 76), (hook["line2"], 76)):
        f = font(size)
        words = line.split()
        x = (W - text_w(d, line, f)) / 2
        for i, w in enumerate(words):
            col = accent if kw and w.lower().strip(".,!?") in kw.split() else "#FFFFFF"
            d.text((x + 3, y + 4), w, font=f, fill="#000000")
            d.text((x, y), w, font=f, fill=col)
            x += text_w(d, w + (" " if i < len(words) - 1 else ""), f)
        y += 92
    sub = story.subtitle
    fs = font(36, False)
    d.text(((W - text_w(d, sub, fs)) / 2, y + 18), sub, font=fs, fill="#A9B1C7")


def footer(d, story: StatStory, t: float, total: float, accent: str):
    fs = font(28, False)
    credit = f"Data: {story.licence}"
    d.text(((W - text_w(d, credit, fs)) / 2, H - 90), credit, font=fs, fill="#7D8597")
    d.rectangle([0, 0, int(W * min(1.0, t / total)), 8], fill=accent)


# ------------------------------------------------------------------ bar race

def bar_race_frames(story: StatStory, hook: dict, seed: int, accent: str):
    steps = story.steps
    n = len(steps)
    per = max(0.55, min(1.6, 28.0 / max(1, n - 1)))
    hold = 3.5
    total = per * max(1, n - 1) + hold
    names = list(story.series)
    vals = np.array([story.series[k] for k in names], dtype=np.float64)       # entities x steps
    cv = Canvas(seed, accent)
    x0, x1, y0, y1 = 60, W - 60, 560, 1640
    rows, bar_h, gap = 10, 84, 22
    ticks = []
    frames = int(total * FPS)

    def ranks(i):
        order = np.argsort(-vals[:, i], kind="stable")
        r = np.empty(len(names))
        r[order] = np.arange(len(names))
        return r

    rank_cache = [ranks(i) for i in range(n)]
    for k in range(frames):
        t = k / FPS
        p = min(t / per, n - 1)
        i = int(p)
        j = min(i + 1, n - 1)
        fr = ease(p - i)
        if k and int((k - 1) / FPS / per) != int(t / per) and t / per < n - 1:
            ticks.append(t)
        v = vals[:, i] + (vals[:, j] - vals[:, i]) * fr
        r = rank_cache[i] + (rank_cache[j] - rank_cache[i]) * fr
        vmax = max(1.0, float(v.max()))
        img, d = cv.frame()
        header(d, hook, story, accent, t)
        label = steps[j] if fr > 0.5 else steps[i]
        fl = font(150)
        d.text((x1 - text_w(d, label, fl), y1 - 150), label, font=fl, fill="#2A3350")
        for e in np.argsort(r):
            if r[e] > rows - 0.2:
                continue
            y = y0 + r[e] * (bar_h + gap)
            length = max(6, (x1 - x0 - 220) * v[e] / vmax)
            col = color_for(names[e])
            d.rounded_rectangle([x0, y, x0 + length, y + bar_h], radius=14, fill=col)
            fn = font(40)
            nm = fit(d, names[e], fn, max(10, length - 30)) if length > 260 else ""
            if nm:
                d.text((x0 + 22, y + 20), nm, font=fn, fill="#0B0B0B")
            else:
                d.text((x0 + length + 18, y - 2), fit(d, names[e], font(30), 420), font=font(30), fill="#FFFFFF")
            fv = font(40)
            d.text((x0 + length + 18, y + (34 if not nm else 20)), fmt(v[e], story.unit), font=fv, fill="#FFFFFF")
        if t > total - hold + 0.3:
            lead = names[int(np.argmax(vals[:, -1]))]
            msg = f"#1  {lead}  ·  {fmt(vals[:, -1].max(), story.unit)} {story.unit}"
            fm = font(44)
            a = ease((t - (total - hold + 0.3)) / 0.5)
            yb = 1700 + int((1 - a) * 40)
            wbox = text_w(d, msg, fm) + 60
            d.rounded_rectangle([(W - wbox) / 2, yb, (W + wbox) / 2, yb + 76], radius=38, fill=accent)
            d.text(((W - text_w(d, msg, fm)) / 2, yb + 13), msg, font=fm, fill="#0B0B0B")
        footer(d, story, t, total, accent)
        yield img
    bar_race_frames.events = ticks + [total - hold + 0.3]
    bar_race_frames.total = total


# ------------------------------------------------------------------ ranking countdown

def ranking_frames(story: StatStory, hook: dict, seed: int, accent: str):
    items = story.ranking[:10]
    n = len(items)
    per, final_hold = 1.55, 3.0
    total = per * n + final_hold
    cv = Canvas(seed, accent)
    y0, row = 520, 112
    events = []
    for k in range(int(total * FPS)):
        t = k / FPS
        img, d = cv.frame()
        header(d, hook, story, accent, t)
        shown = min(n, int(t / per) + 1)
        for s in range(shown):                       # s=0 reveals #n ... s=n-1 reveals #1
            idx = n - 1 - s
            name, value, detail = items[idx]
            appear = s * per
            a = ease((t - appear) / 0.35)
            y = y0 + idx * row
            x = 60 + int((1 - a) * 700)
            top = idx == 0
            h = row - 16
            d.rounded_rectangle([x, y, W - 60 + int((1 - a) * 700), y + h], radius=20,
                                fill=accent if top else "#1E2436")
            num = f"#{idx + 1}"
            d.text((x + 24, y + 18), num, font=font(54), fill="#0B0B0B" if top else accent)
            val = f"{fmt(value, story.unit)}"
            fv = font(46)
            room = (W - 60) - 150 - 60 - text_w(d, val, fv) - 24
            fn = font(40) if text_w(d, str(name), font(40)) <= room else font(32)
            d.text((x + 150, y + (10 if fn.size == 40 else 14)), fit(d, str(name), fn, room), font=fn,
                   fill="#0B0B0B" if top else "#FFFFFF")
            if detail:
                d.text((x + 150, y + 56), fit(d, str(detail), font(26, False), room), font=font(26, False),
                       fill="#1B1B1B" if top else "#A9B1C7")
            d.text((W - 90 - text_w(d, val, fv) + int((1 - a) * 700), y + 22), val, font=fv,
                   fill="#0B0B0B" if top else "#FFFFFF")
        unit = story.unit.upper()
        fu = font(30, False)
        d.text((W - 90 - text_w(d, unit, fu), y0 - 50), unit, font=fu, fill="#7D8597")
        footer(d, story, t, total, accent)
        yield img
    events = [s * per for s in range(n)]
    ranking_frames.events = events
    ranking_frames.total = total


# ------------------------------------------------------------------ duel

def duel_frames(story: StatStory, hook: dict, seed: int, accent: str):
    (a, va), (b, vb) = list(story.series.items())[:2]
    va, vb = np.array(va, float), np.array(vb, float)
    n = len(story.steps)
    per = max(0.6, min(1.5, 24.0 / max(1, n - 1)))
    hold = 3.5
    total = per * max(1, n - 1) + hold
    cv = Canvas(seed, accent)
    ca, cb = color_for(a), color_for(b)
    if ca == cb:
        cb = PALETTE[(PALETTE.index(ca) + 3) % len(PALETTE)]
    gx0, gx1, gy0, gy1 = 90, W - 90, 900, 1620
    vmax = max(va.max(), vb.max(), 1)
    ticks = []
    for k in range(int(total * FPS)):
        t = k / FPS
        p = min(t / per, n - 1)
        i = int(p)
        fr = ease(p - i)
        j = min(i + 1, n - 1)
        cur_a = va[i] + (va[j] - va[i]) * fr
        cur_b = vb[i] + (vb[j] - vb[i]) * fr
        if k and int((k - 1) / FPS / per) != int(t / per) and t / per < n - 1:
            ticks.append(t)
        img, d = cv.frame()
        header(d, hook, story, accent, t)
        for xi, (nm, val, col) in enumerate(((a, cur_a, ca), (b, cur_b, cb))):
            cx = W // 4 if xi == 0 else 3 * W // 4
            lead = val >= (cur_b if xi == 0 else cur_a)
            fn = font(40)
            d.text((cx - text_w(d, fit(d, nm, fn, 460), fn) / 2, 560), fit(d, nm, fn, 460), font=fn, fill=col)
            fv = font(110 if lead else 92)
            s = fmt(val, story.unit)
            d.text((cx - text_w(d, s, fv) / 2, 620), s, font=fv, fill="#FFFFFF" if lead else "#A9B1C7")
        d.text(((W - text_w(d, "VS", font(44))) / 2, 650), "VS", font=font(44), fill=accent)
        for series, col in ((va, ca), (vb, cb)):
            pts = []
            for q in range(i + 1):
                pts.append((gx0 + (gx1 - gx0) * q / max(1, n - 1), gy1 - (gy1 - gy0) * series[q] / vmax))
            if j > i:
                xq = gx0 + (gx1 - gx0) * (i + fr) / max(1, n - 1)
                yq = gy1 - (gy1 - gy0) * (series[i] + (series[j] - series[i]) * fr) / vmax
                pts.append((xq, yq))
            if len(pts) > 1:
                d.line(pts, fill=col, width=10, joint="curve")
            px, py = pts[-1]
            d.ellipse([px - 14, py - 14, px + 14, py + 14], fill=col)
        d.line([(gx0, gy1), (gx1, gy1)], fill="#3A4258", width=3)
        fl = font(30, False)
        d.text((gx0, gy1 + 16), story.steps[0], font=fl, fill="#7D8597")
        lab = story.steps[j] if fr > 0.5 else story.steps[i]
        d.text((gx1 - text_w(d, lab, fl), gy1 + 16), lab, font=fl, fill="#FFFFFF")
        if t > total - hold + 0.3:
            gap = abs(va[-1] - vb[-1])
            msg = f"Gap: {fmt(gap, story.unit)} {story.unit}"
            fm = font(48)
            wbox = text_w(d, msg, fm) + 70
            d.rounded_rectangle([(W - wbox) / 2, 1700, (W + wbox) / 2, 1784], radius=42, fill=accent)
            d.text(((W - text_w(d, msg, fm)) / 2, 1714), msg, font=fm, fill="#0B0B0B")
        footer(d, story, t, total, accent)
        yield img
    duel_frames.events = ticks + [total - hold + 0.3]
    duel_frames.total = total


# ------------------------------------------------------------------ sound + encode

def sfx(events: list[float], total: float, out: Path, final_at: float | None = None):
    sr = 48000
    buf = np.zeros(int(sr * (total + 0.5)), np.float32)

    def add(at, dur, freqs, amp, decay):
        s = int(at * sr)
        tt = np.arange(int(dur * sr)) / sr
        wave_ = sum(np.sin(2 * np.pi * f * tt) for f in freqs) / len(freqs)
        seg = (amp * wave_ * np.exp(-tt * decay)).astype(np.float32)
        buf[s:s + len(seg)] += seg[: max(0, len(buf) - s)]

    for at in events:
        add(at, 0.06, [1500], 0.18, 60)
    if final_at is not None:
        add(final_at, 0.6, [110, 220], 0.45, 7)
        add(final_at, 0.25, [880, 1320], 0.15, 14)
    pcm = (np.clip(buf, -1, 1) * 32767).astype(np.int16)
    with wave.open(str(out), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(np.repeat(pcm[:, None], 2, axis=1).tobytes())
    return out


def render(story: StatStory, hook: dict, out: Path, seed: int, accent: str | None = None) -> Path:
    accent = accent or random.Random(seed).choice(["#FFD60A", "#4CC9F0", "#2EC4B6", "#FF8C42", "#B8F35D"])
    gen = {"bar_race": bar_race_frames, "ranking": ranking_frames, "duel": duel_frames}[story.kind]
    raw = out.with_suffix(".video.mp4")
    proc = subprocess.Popen(["ffmpeg", "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}",
                             "-r", str(FPS), "-i", "-", "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
                             "-pix_fmt", "yuv420p", str(raw)], stdin=subprocess.PIPE)
    count = 0
    for img in gen(story, hook, seed, accent):
        proc.stdin.write(img.tobytes())
        count += 1
    proc.stdin.close()
    if proc.wait() != 0:
        raise RuntimeError("ffmpeg failed while encoding chart frames")
    events = list(gen.events)
    final_at = events.pop() if story.kind in ("bar_race", "duel") else events[-1] if events else None
    wav = sfx(events, gen.total, out.with_suffix(".wav"), final_at)
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(raw), "-i", str(wav), "-c:v", "copy", "-c:a", "aac",
                    "-b:a", "128k", "-shortest", "-map_metadata", "-1", "-fflags", "+bitexact",
                    "-movflags", "+faststart", str(out)], check=True)
    raw.unlink(missing_ok=True)
    wav.unlink(missing_ok=True)
    log.info("stat chart %s: %s, %d frames (%.1fs)", out.name, story.kind, count, count / FPS)
    return out
