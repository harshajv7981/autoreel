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
    name = "anthropic"

    def __init__(self, cfg):
        import anthropic
        self.client = anthropic.Anthropic()
        self.model = cfg.anthropic_model

    def available(self) -> bool:
        return True

    def _call(self, content) -> str | None:
        try:
            msg = self.client.messages.create(model=self.model, max_tokens=1200,
                                              messages=[{"role": "user", "content": content}])
            return "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")
        except Exception as e:  # noqa: BLE001
            log.warning("anthropic call failed: %s", e)
            return None

    def text(self, prompt, as_json=False):
        return self._call(prompt)

    def vision(self, prompt, images, as_json=False):
        blocks = [{"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                               "data": base64.b64encode(Path(p).read_bytes()).decode()}}
                  for p in images]
        return self._call(blocks + [{"type": "text", "text": prompt}])


def make_llm(cfg):
    provider = (cfg.provider or "none").lower()
    if provider == "ollama":
        return OllamaLLM(cfg)
    if provider == "anthropic":
        return AnthropicLLM(cfg)
    return NullLLM()
