"""A provider that drives an already-configured CLI in non-interactive mode.

This is the primary automation path: invoke a coding-agent CLI (Codex, Claude,
…) headlessly, pass the prompt, capture stdout. The conductor does **not** own
the CLI's authentication — the CLI is expected to be logged in on the machine.
A non-zero exit code is surfaced as a failed result so the engine can stop.

TODO(session-resume): The refine loop resends the full context (role prompt +
instructions + goal contract) on every round because each CLI call is stateless.
CLIs like ``claude`` (``--resume <id>``) and opencode support session resumption,
which would let rounds 2+ send only the new Q&A answers. This requires a new
``cli_session`` provider variant that starts a session, persists the session ID
in the workitem directory, and resumes it on subsequent calls. Not critical —
refine is typically 1–3 rounds with a small transcript — but worth doing once
there is a single preferred CLI (e.g. opencode configured with all models).
"""

from __future__ import annotations

import shutil
import subprocess
import threading
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

        if request.on_output:
            return self._run_streaming(cmd, input_text, request)
        return self._run_captured(cmd, input_text, request)

    def _run_captured(
        self, cmd: list[str], input_text: str | None, request: ProviderRequest
    ) -> ProviderResult:
        try:
            proc = subprocess.run(
                cmd,
                input=input_text,
                # When prompt goes via arg (not stdin), close stdin explicitly.
                # Explicit DEVNULL signals headless mode to CLIs that check
                # isatty(stdin); avoids inheriting the parent TTY which some
                # CLIs misdetect when stdout/stderr are already piped.
                stdin=subprocess.DEVNULL if input_text is None else None,
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

    def _run_streaming(
        self, cmd: list[str], input_text: str | None, request: ProviderRequest
    ) -> ProviderResult:
        """Run the subprocess and forward each stdout line to request.on_output."""
        try:
            proc = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE if input_text is not None else subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=request.cwd,
                text=True,
            )
        except OSError as exc:
            return ProviderResult(
                ok=False, output="", provider=self.name,
                error=f"failed to run '{self.command}': {exc}",
            )

        output_parts: list[str] = []
        stderr_parts: list[str] = []

        def _read_stdout() -> None:
            assert proc.stdout is not None
            for line in proc.stdout:
                output_parts.append(line)
                if request.on_output:
                    request.on_output(line)

        def _read_stderr() -> None:
            assert proc.stderr is not None
            for line in proc.stderr:
                stderr_parts.append(line)

        threads: list[threading.Thread] = []

        if input_text is not None:
            def _write_stdin() -> None:
                assert proc.stdin is not None
                try:
                    proc.stdin.write(input_text)
                finally:
                    proc.stdin.close()
            t_in = threading.Thread(target=_write_stdin, daemon=True)
            t_in.start()
            threads.append(t_in)

        t_out = threading.Thread(target=_read_stdout, daemon=True)
        t_err = threading.Thread(target=_read_stderr, daemon=True)
        t_out.start()
        t_err.start()
        threads.extend([t_out, t_err])

        timed_out = False
        try:
            proc.wait(timeout=self.timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
            timed_out = True

        for t in threads:
            t.join(timeout=5.0)

        if timed_out:
            return ProviderResult(
                ok=False,
                output="".join(output_parts),
                provider=self.name,
                error=f"'{self.command}' timed out after {self.timeout}s",
            )

        ok = proc.returncode == 0
        output = "".join(output_parts)
        error = None if ok else ("".join(stderr_parts).strip() or f"exit code {proc.returncode}")
        return ProviderResult(ok=ok, output=output, provider=self.name, error=error)
