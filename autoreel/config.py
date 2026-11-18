from pathlib import Path

import yaml


class Cfg(dict):
    """dict with attribute access, recursively."""

    def __getattr__(self, key):
        try:
            return self[key]
        except KeyError as e:
            raise AttributeError(key) from e

    def __setattr__(self, key, value):
        self[key] = value


def _wrap(obj):
    if isinstance(obj, dict):
        return Cfg({k: _wrap(v) for k, v in obj.items()})
    if isinstance(obj, list):
        return [_wrap(v) for v in obj]
    return obj


def load_config(path: str | Path) -> Cfg:
    path = Path(path).resolve()
    cfg = _wrap(yaml.safe_load(path.read_text()))
    base = path.parent
    cfg.base_dir = base

    def res(p):
        return None if p in (None, "") else (base / p).resolve()

    for k in list(cfg.paths):
        cfg.paths[k] = res(cfg.paths[k])
    for k in ("data", "music", "secrets"):
        cfg.paths[k].mkdir(parents=True, exist_ok=True)
    for sub in ("downloads", "work", "renders", "review"):
        (cfg.paths.data / sub).mkdir(parents=True, exist_ok=True)

    cfg.youtube.client_secret = res(cfg.youtube.client_secret)
    cap = (cfg.get("shorts") or {}).get("max_seconds", 58)
    for ch in cfg.channels:
        lo, hi = ch.clip_seconds
        if hi > cap:                 # claimed Shorts over 60s are blocked outright
            ch.clip_seconds = [min(lo, cap - 5), cap]
        ch.token = res(ch.token)
        for src in ch.sources:
            if src.get("folder"):
                src.folder = res(src.folder)
    return cfg
