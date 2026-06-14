"""A provider that drives an already-configured CLI in non-interactive mode.

This is the primary automation path: invoke a coding-agent CLI (Codex, Claude,
…) headlessly, pass the prompt, capture stdout. The conductor does **not** own
the CLI's authentication — the CLI is expected to be logged in on the machine.
A non-zero exit code is surfaced as a failed result so the engine can stop.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from .base import Provider, ProviderRequest, ProviderResult


class CliOneShotProvider(Provider):
    def __init__(
        self,
        name: str,
        command: str,
        args: list[str] | None = None,
        prompt_via: str = "stdin",
        timeout: int = 600,
    ) -> None:
        self.name = name
        self.command = command
        self.args = list(args or [])
        self.prompt_via = prompt_via
        self.timeout = timeout

    def available(self) -> bool:
        return shutil.which(self.command) is not None

    def run(self, request: ProviderRequest) -> ProviderResult:
        if not self.available():
            return ProviderResult(
                ok=False,
                output="",
                provider=self.name,
                error=f"CLI '{self.command}' not found on PATH",
            )

        cmd = [self.command, *self.args]
        input_text: str | None = None
        if self.prompt_via == "arg":
            cmd.append(request.prompt)
        else:
            input_text = request.prompt

        try:
            proc = subprocess.run(
                cmd,
                input=input_text,
                cwd=request.cwd,
                capture_output=True,
                text=True,
                timeout=self.timeout,
            )
        except subprocess.TimeoutExpired:
            return ProviderResult(
                ok=False,
                output="",
                provider=self.name,
                error=f"'{self.command}' timed out after {self.timeout}s",
            )
        except OSError as exc:
            return ProviderResult(
                ok=False, output="", provider=self.name, error=f"failed to run '{self.command}': {exc}"
            )

        ok = proc.returncode == 0
        output = proc.stdout or ""
        error = None if ok else (proc.stderr.strip() or f"exit code {proc.returncode}")
        return ProviderResult(ok=ok, output=output, provider=self.name, error=error)
