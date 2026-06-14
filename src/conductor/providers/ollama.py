"""A provider that calls a native local Ollama server.

Drives a model running under Ollama over its native HTTP API
(``POST {base_url}/api/chat`` → ``message.content``). Local and unauthenticated,
so unlike :class:`ApiProvider` it needs no API key; only ``model`` is required and
``base_url`` defaults to the standard Ollama port. Stdlib ``urllib`` only.

``available()`` asks the server (``/api/tags``) whether it is up and the model is
actually pulled, which makes ``conductor doctor`` genuinely informative. Every
failure is surfaced as ``ProviderResult(ok=False, ...)`` rather than raised.
"""

from __future__ import annotations

import json
from urllib import error, request

from .base import Provider, ProviderRequest, ProviderResult


class OllamaProvider(Provider):
    DEFAULT_BASE_URL = "http://localhost:11434"

    def __init__(
        self,
        name: str,
        model: str,
        base_url: str = DEFAULT_BASE_URL,
        timeout: int = 600,
    ) -> None:
        self.name = name
        self.model = model
        self.base_url = base_url
        self.timeout = timeout

    def available(self) -> bool:
        """True when the Ollama server responds and ``model`` is installed."""
        url = self.base_url.rstrip("/") + "/api/tags"
        try:
            with request.urlopen(url, timeout=self.timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except (error.URLError, OSError, json.JSONDecodeError):
            return False
        installed = {m.get("name", "") for m in data.get("models", [])}
        # Ollama reports names as "model:tag"; match on the bare name too.
        bare = {name.split(":", 1)[0] for name in installed}
        wanted = self.model.split(":", 1)[0]
        return self.model in installed or wanted in bare

    def run(self, req: ProviderRequest) -> ProviderResult:
        url = self.base_url.rstrip("/") + "/api/chat"
        body = json.dumps(
            {
                "model": self.model,
                "messages": [{"role": "user", "content": req.prompt}],
                "stream": False,
            }
        ).encode("utf-8")
        http_req = request.Request(
            url,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        try:
            with request.urlopen(http_req, timeout=self.timeout) as resp:
                raw = resp.read().decode("utf-8")
        except error.HTTPError as exc:
            detail = ""
            try:
                detail = exc.read().decode("utf-8", "replace")[:200]
            except Exception:  # pragma: no cover - best-effort detail only
                pass
            return ProviderResult(
                ok=False,
                output="",
                provider=self.name,
                error=f"HTTP {exc.code}: {detail or exc.reason}",
            )
        except error.URLError as exc:
            return ProviderResult(
                ok=False,
                output="",
                provider=self.name,
                error=f"cannot reach Ollama at {self.base_url}: {exc.reason}",
            )
        except TimeoutError:
            return ProviderResult(
                ok=False,
                output="",
                provider=self.name,
                error=f"request timed out after {self.timeout}s",
            )

        try:
            data = json.loads(raw)
            content = data["message"]["content"]
        except (json.JSONDecodeError, KeyError, TypeError) as exc:
            return ProviderResult(
                ok=False,
                output="",
                provider=self.name,
                error=f"unparseable response: {exc}",
            )

        return ProviderResult(ok=True, output=content, provider=self.name)
