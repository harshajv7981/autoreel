"""Visual moment finder: works on silent footage (no transcript needed).

1. Decode a tiny grayscale proxy (160x120 @ 4 fps) through ffmpeg.
2. Per frame: motion (mean abs diff), brightness, contrast; detect hard cuts.
3. Slide windows of the target length, score them:
   + motion relative to the film's own baseline (a gag is busier than its setup)
   + bursts (peaks over mean: falls, chases, crashes)
   - static frames (intertitles, title cards), dark frames, choppy cutting
4. Snap boundaries to nearby cuts so clips start and end on a shot, then
   non-max-suppress to get distinct, non-overlapping moments.
"""
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from . import log
from .ff import duration, run

W, H = 160, 120


@dataclass
class Moment:
    start: float
    end: float
    score: float
    features: dict = field(default_factory=dict)

    @property
    def length(self) -> float:
        return self.end - self.start


def profile(video: Path, fps: float = 4.0) -> dict:
    raw = run(["ffmpeg", "-v", "error", "-i", video, "-an", "-vf",
               f"fps={fps},scale={W}:{H},format=gray", "-f", "rawvideo", "-"],
              capture_bytes=True).stdout
    frames = np.frombuffer(raw, dtype=np.uint8)
    frames = frames[: len(frames) // (W * H) * (W * H)].reshape(-1, H, W).astype(np.float32)
    if len(frames) < 4:
        raise ValueError(f"video too short to analyse: {video}")
    diff = np.zeros(len(frames), np.float32)
    diff[1:] = np.abs(frames[1:] - frames[:-1]).mean(axis=(1, 2))
    luma = frames.mean(axis=(1, 2))
    contrast = frames.std(axis=(1, 2))
    med = float(np.median(diff[1:])) or 1.0
    cut_thr = max(28.0, med * 6)
    cuts = np.where(diff > cut_thr)[0]
    motion = diff.copy()
    motion[cuts] = med                     # a cut is not "action"
    return {"fps": fps, "motion": motion, "luma": luma, "contrast": contrast,
            "cuts": cuts / fps, "baseline": med, "n": len(frames)}


def _snap(t: float, cuts: np.ndarray, radius: float) -> float:
    if not len(cuts):
        return t
    near = cuts[np.abs(cuts - t) <= radius]
    return float(near[np.argmin(np.abs(near - t))]) if len(near) else t


def _tighten(s: float, e: float, m: np.ndarray, fps: float, min_len: float, max_trim: float = 4.0):
    """Drop dead seconds at either end (a Short must start on movement, not on setup)."""
    win_mean = float(m[int(s * fps):int(e * fps)].mean()) or 1.0

    def sec_motion(t):
        return float(m[int(t * fps):int((t + 1) * fps)].mean() or 0)

    trimmed = 0.0
    while e - s - 1 >= min_len and trimmed < max_trim and sec_motion(s) < 0.35 * win_mean:
        s, trimmed = s + 1, trimmed + 1
    trimmed = 0.0
    while e - s - 1 >= min_len and trimmed < max_trim and sec_motion(e - 1) < 0.35 * win_mean:
        e, trimmed = e - 1, trimmed + 1
    return s, e


def find_moments(video: Path, min_len: float = 18, max_len: float = 40, top_k: int = 8,
                 fps: float = 4.0, skip_edges: float = 0.06) -> list[Moment]:
    p = profile(video, fps)
    total = duration(video)
    m, luma, contrast, cuts = p["motion"], p["luma"], p["contrast"], p["cuts"]
    base = max(p["baseline"], 0.5)
    lo_t, hi_t = total * skip_edges, total * (1 - skip_edges)
    lengths = sorted({min_len, (min_len + max_len) / 2, max_len})

    cands: list[Moment] = []
    for L in lengths:
        n = int(L * fps)
        t = lo_t
        while t + L <= hi_t:
            a, b = int(t * fps), int(t * fps) + n
            win = m[a:b]
            if len(win) < n * 0.9:
                break
            rel = win / base
            activity = float(np.clip(rel.mean(), 0, 6))
            burst = float(np.clip(np.percentile(rel, 90) / (rel.mean() + 1e-3), 0, 4))
            static = float((rel < 0.25).mean())
            dark = float((luma[a:b] < 22).mean())
            flat = float((contrast[a:b] < 12).mean())       # fades, blank leader
            n_cuts = int(((cuts >= t) & (cuts < t + L)).sum())
            cut_rate = n_cuts / L * 10
            score = (activity + 0.35 * burst - 2.2 * static - 2.0 * dark - 1.5 * flat
                     - 0.25 * max(0.0, cut_rate - 4) + 0.05 * L / max_len)
            cands.append(Moment(t, t + L, score, {
                "activity": round(activity, 2), "burst": round(burst, 2), "static": round(static, 2),
                "dark": round(dark, 2), "cuts": n_cuts}))
            t += 1.0

    cands.sort(key=lambda c: c.score, reverse=True)
    chosen: list[Moment] = []
    for c in cands:
        s = _snap(c.start, cuts, 2.0)
        e = _snap(c.end, cuts, 2.0)
        if e - s < min_len * 0.85 or e - s > max_len * 1.1:
            s, e = c.start, c.end
        s, e = _tighten(s, e, m, fps, min_len)
        if any(s < k.end + 3 and e > k.start - 3 for k in chosen):
            continue
        chosen.append(Moment(round(s, 2), round(e, 2), round(c.score, 3), c.features))
        if len(chosen) >= top_k:
            break
    log.info("finder: %s -> %d moments (baseline motion %.2f, %d cuts)",
             Path(video).name, len(chosen), base, len(cuts))
    return chosen
