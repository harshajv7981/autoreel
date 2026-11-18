"""LLM providers. Default is Ollama on your own machine: free, private, no sign-up."""
import base64
import json
import re
import urllib.request
from pathlib import Path

from . import log


def parse_json(text: str | None) -> dict | None:
    if not text:
        return None
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return None


class NullLLM:
    name = "none"

    def available(self) -> bool:
        return False

    def text(self, prompt: str, as_json: bool = False) -> str | None:
        return None

    def vision(self, prompt: str, images: list[Path], as_json: bool = False) -> str | None:
        return None


class OllamaLLM(NullLLM):
    name = "ollama"

    def __init__(self, cfg):
        self.url = cfg.ollama_url.rstrip("/")
        self.text_model, self.vision_model = cfg.text_model, cfg.vision_model
        self.temperature = cfg.get("temperature", 0.8)
        self._ok = None

    def available(self) -> bool:
        if self._ok is None:
            try:
                with urllib.request.urlopen(f"{self.url}/api/tags", timeout=3) as r:
                    names = {m["name"] for m in json.loads(r.read())["models"]}
                self._ok = True
                for model in (self.text_model, self.vision_model):
                    if model and not any(n.startswith(model) for n in names):
                        log.warning("ollama model %s not pulled - run: ollama pull %s", model, model)
            except Exception:  # noqa: BLE001
                log.warning("ollama not reachable at %s - falling back to templates", self.url)
                self._ok = False
        return self._ok

    def _generate(self, model: str, prompt: str, images=None, as_json=False) -> str | None:
        if not (model and self.available()):
            return None
        body = {"model": model, "prompt": prompt, "stream": False,
                "options": {"temperature": self.temperature}}
        if as_json:
            body["format"] = "json"
        if images:
            body["images"] = [base64.b64encode(Path(p).read_bytes()).decode() for p in images]
        req = urllib.request.Request(f"{self.url}/api/generate", data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=300) as r:
                return json.loads(r.read())["response"]
        except Exception as e:  # noqa: BLE001
            log.warning("ollama call failed: %s", e)
            return None

    def text(self, prompt, as_json=False):
        return self._generate(self.text_model, prompt, as_json=as_json)

    def vision(self, prompt, images, as_json=False):
        return self._generate(self.vision_model, prompt, images=images, as_json=as_json)


class AnthropicLLM(NullLLM):
    """Claude API. Key comes from the ANTHROPIC_API_KEY environment variable (never from config files).

    Two models: a stronger one writes captions and plans trends (text), a fast one rates frames
    (vision) - frame judging runs many more times per day than copywriting.
    """
    name = "anthropic"
    JSON_SYSTEM = "Reply with a single JSON object only. No prose before or after it, no code fences."

    def __init__(self, cfg):
        import os
        self.text_model = cfg.get("anthropic_text_model") or cfg.get("anthropic_model") or "claude-sonnet-5-5"
        self.vision_model = cfg.get("anthropic_vision_model") or self.text_model
        self.temperature = cfg.get("temperature", 0.8)
        self.max_tokens = cfg.get("max_tokens", 1500)
        self.usage = {"calls": 0, "input_tokens": 0, "output_tokens": 0}
        self._ok = bool(os.getenv("ANTHROPIC_API_KEY"))
        self.client = None
        if not self._ok:
            log.warning("ANTHROPIC_API_KEY is not set - falling back to motion scores and caption templates")
            return
        try:
            import anthropic
        except ImportError:
            log.warning("anthropic package missing - run: pip install anthropic")
            self._ok = False
            return
        self.client = anthropic.Anthropic(max_retries=3, timeout=120)

    def available(self) -> bool:
        return self._ok

    def _call(self, model: str, content, as_json: bool) -> str | None:
        if not self._ok:
            return None
        kwargs = {"model": model, "max_tokens": self.max_tokens, "temperature": self.temperature,
                  "messages": [{"role": "user", "content": content}]}
        if as_json:
            kwargs["system"] = self.JSON_SYSTEM
        try:
            msg = self.client.messages.create(**kwargs)
        except Exception as e:  # noqa: BLE001
            log.warning("Claude call failed (%s): %s", model, e)
            return None
        u = getattr(msg, "usage", None)
        self.usage["calls"] += 1
        if u is not None:
            self.usage["input_tokens"] += getattr(u, "input_tokens", 0) or 0
            self.usage["output_tokens"] += getattr(u, "output_tokens", 0) or 0
        return "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")

    def text(self, prompt, as_json=False):
        return self._call(self.text_model, prompt, as_json)

    def vision(self, prompt, images, as_json=False):
        blocks = [{"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                               "data": base64.b64encode(Path(p).read_bytes()).decode()}}
                  for p in images]
        return self._call(self.vision_model, blocks + [{"type": "text", "text": prompt}], as_json)


def make_llm(cfg):
    provider = (cfg.provider or "none").lower()
    if provider == "ollama":
        return OllamaLLM(cfg)
    if provider == "anthropic":
        return AnthropicLLM(cfg)
    return NullLLM()
