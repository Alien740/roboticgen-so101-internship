"""How a running task talks to whoever drives it: a person at a terminal, or an AI agent.

Two modes, same task code:
  tty   - prompts on stdout, answers from stdin, Ctrl+C stops.            (default)
  agent - prompts on stdout, answers are written to a command file;        (--agent or SO101_AGENT=1)
          writing "stop" to that file aborts the current motion at once.

Every prompt / event is ALSO printed as one machine-readable line:
  @@WAIT  {"prompt": ..., "options": [...]}
  @@EVENT {"type": ..., ...}
so any AI (Claude, ChatGPT, a local model) can drive a session by reading the log
(runs/<profile>/session.log) and writing answers to runs/<profile>/cmd.txt.
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path


class Stopped(RuntimeError):
    """Raised when the operator sends 'stop' (agent mode) or presses Ctrl+C."""


class Session:
    def __init__(self, run_dir: Path, agent: bool | None = None):
        self.agent = bool(int(os.environ.get("SO101_AGENT", "0"))) if agent is None else agent
        self.cmd_file = Path(run_dir) / "cmd.txt"
        self.log_file = Path(run_dir) / "session.log"
        self.cmd_file.unlink(missing_ok=True)
        self._log = open(self.log_file, "a", encoding="utf-8")
        self.event("session_start", mode="agent" if self.agent else "tty", cmd_file=str(self.cmd_file))

    # ---------------------------------------------------------------- output
    def _emit(self, line: str):
        print(line, flush=True)
        self._log.write(line + "\n"); self._log.flush()

    def say(self, msg: str):
        self._emit(msg)

    def event(self, type_: str, **data):
        self._emit("@@EVENT " + json.dumps({"type": type_, **data}, default=str))

    # ---------------------------------------------------------------- input
    def ask(self, prompt: str, options: list[str] | None = None) -> str:
        """Block until the operator answers. Returns the stripped answer ('' allowed)."""
        self._emit(f"WAITING: {prompt}")
        self._emit("@@WAIT " + json.dumps({"prompt": prompt, "options": options or []}))
        if not self.agent:
            try:
                ans = input("> ").strip()
            except (KeyboardInterrupt, EOFError) as e:
                raise Stopped("stopped at prompt") from e
        else:
            self.cmd_file.unlink(missing_ok=True)
            while not self.cmd_file.exists():
                time.sleep(0.2)
            time.sleep(0.05)
            ans = self.cmd_file.read_text(encoding="utf-8").strip()
            self.cmd_file.unlink(missing_ok=True)
        self._emit(f"> {ans}")
        if ans.lower() == "stop":
            raise Stopped("operator sent stop")
        return ans

    def confirm(self, prompt: str) -> bool:
        return self.ask(prompt + "  ('go' / 'no')", ["go", "no"]).lower() in ("go", "y", "yes", "ok", "")

    def check_stop(self):
        """Call inside motion loops. Agent mode: 'stop' in the command file aborts."""
        if self.agent and self.cmd_file.exists():
            if self.cmd_file.read_text(encoding="utf-8").strip().lower() == "stop":
                self.cmd_file.unlink(missing_ok=True)
                raise Stopped("operator sent stop")

    def close(self):
        self.event("session_end")
        self._log.close()


def stdout_utf8():
    """Windows consoles default to cp1252; keep prints from crashing on symbols."""
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
