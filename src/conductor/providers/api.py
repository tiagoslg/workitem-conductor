"""A provider that calls an OpenAI-compatible chat-completions endpoint.

This drives a model over HTTP rather than through a local CLI: OpenAI, Qwen,
vLLM, LM Studio, a gateway — anything that speaks ``POST {base_url}/chat/
completions`` and returns ``choices[0].message.content``. It uses only the
standard library (``urllib``) so the conductor keeps its minimal dependency set.

The conductor does **not** own authentication: the API key is read from the
environment variable named by ``api_key_env`` and never stored. Every failure is
surfaced as ``ProviderResult(ok=False, ...)`` so the engine can stop cleanly.
"""

from __future__ import annotations

import json
import os
from urllib import error, request

from .base import Provider, ProviderRequest, ProviderResult


class ApiProvider(Provider):
    def __init__(
        self,
        name: str,
        model: str,
        base_url: str,
        api_key_env: str,
        timeout: int = 600,
    ) -> None:
        self.name = name
        self.model = model
        self.base_url = base_url
        self.api_key_env = api_key_env
        self.timeout = timeout

    def available(self) -> bool:
        return bool(self.api_key_env and os.environ.get(self.api_key_env))

    def run(self, req: ProviderRequest) -> ProviderResult:
        key = os.environ.get(self.api_key_env) if self.api_key_env else None
        if not key:
            return ProviderResult(
                ok=False,
                output="",
                provider=self.name,
                error=f"env var '{self.api_key_env}' is not set",
            )

        url = self.base_url.rstrip("/") + "/chat/completions"
        body = json.dumps(
            {
                "model": self.model,
                "messages": [{"role": "user", "content": req.prompt}],
            }
        ).encode("utf-8")
        http_req = request.Request(
            url,
            data=body,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {key}",
            },
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
                ok=False, output="", provider=self.name, error=f"request failed: {exc.reason}"
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
            content = data["choices"][0]["message"]["content"]
        except (json.JSONDecodeError, KeyError, IndexError, TypeError) as exc:
            return ProviderResult(
                ok=False,
                output="",
                provider=self.name,
                error=f"unparseable response: {exc}",
            )

        return ProviderResult(ok=True, output=content, provider=self.name)
