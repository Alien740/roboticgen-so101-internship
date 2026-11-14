"""Run the framework's own procedures (draw, pick-place, calibrations, ...) from the dashboard.

They are the tested `so101` commands, started in agent mode (`so101 --agent <task>`): every
question they ask is a machine-readable @@WAIT line in runs/<profile>/session.log and the answer is
written to runs/<profile>/cmd.txt ('stop' halts motion). This module only starts them, relays the
prompt and the answers, and keeps the dashboard off the serial port while they run:

- a real arm connected to the dashboard is parked and released first (one owner of the bus);
- while a task runs, the dashboard refuses to connect an arm.
"""
from __future__ import annotations

import subprocess
import sys
import threading
import time

from so101 import agent
from so101.profile import FRAMEWORK_ROOT

# what the page may start (no free-form command lines from the browser)
ALLOWED = {
    "draw": ["draw"],
    "demo": ["demo"],
    "pick-place": ["pick-place"], "basket": ["basket"], "multi": ["multi"], "stack": ["stack"],
    "report": ["report"],
    "measure": ["doctor", "--measure"],
    "calibrate-table": ["calibrate", "table"], "calibrate-camera": ["calibrate", "camera"],
    "calibrate-hsv": ["calibrate", "hsv"],
}
OPTIONS = {"--trials": int, "--hover": bool, "--color": str, "--objects": list}
PARK_TIMEOUT_S = 20.0


class TaskRunner:
    def __init__(self, prof, ctl):
        self.prof = prof
        self.ctl = ctl
        self.handing_over = None             # task args while the arm is being parked for it
        self.error = ""
        self._lock = threading.Lock()
        ctl.busy = lambda: "a framework task (draw / task / calibration) is using the arm" if self.running() else None

    # ------------------------------------------------------------------ state
    def running(self):
        st = agent._state(self.prof)
        return self.handing_over is not None or st["state"] in ("waiting", "running")

    def state(self):
        st = agent._state(self.prof, n_events=12)
        out = []
        f = self.prof.run_dir / "agent.out"
        if st["state"] != "not_started" and f.exists():
            lines = f.read_text(encoding="utf-8", errors="replace").splitlines()
            out = [l for l in lines if not l.startswith(("@@WAIT", "@@EVENT"))][-40:]
        if self.handing_over is not None:
            st = {**st, "state": "starting", "prompt": None, "options": []}
        return {**st, "output": out, "error": self.error}

    # ------------------------------------------------------------------ commands
    def start(self, name, opts=None):
        if name not in ALLOWED:
            raise ValueError(f"unknown task '{name}' (allowed: {', '.join(ALLOWED)})")
        args = list(ALLOWED[name])
        for k, v in (opts or {}).items():
            if k not in OPTIONS:
                raise ValueError(f"option {k} not allowed")
            if OPTIONS[k] is bool:
                if v:
                    args.append(k)
            elif OPTIONS[k] is list:
                args += [k, *[str(x) for x in v]]
            else:
                args += [k, str(OPTIONS[k](v))]
        with self._lock:
            if self.running():
                raise RuntimeError("a task is already running - stop it first")
            self.handing_over, self.error = args, ""
        threading.Thread(target=self._hand_over_and_start, args=(args,), daemon=True).start()
        return {"starting": args}

    def answer(self, text):
        if agent._state(self.prof)["state"] != "waiting":
            raise RuntimeError("the task is not waiting for an answer")
        self._agent("answer", str(text)[:200])

    def stop(self):
        self._agent("stop")

    # ------------------------------------------------------------------ internals
    def _hand_over_and_start(self, args):
        try:
            c = self.ctl
            if c.backend is not None:
                c.disconnect()                    # real arm: parks at home, then releases the port
                t0 = time.monotonic()
                while c.backend is not None and time.monotonic() - t0 < PARK_TIMEOUT_S:
                    time.sleep(0.1)
                if c.backend is not None:
                    raise RuntimeError("the dashboard could not release the arm")
            self._agent("start", *args)
        except Exception as e:
            self.error = f"{type(e).__name__}: {e}"
        finally:
            self.handing_over = None

    def _agent(self, *argv):
        r = subprocess.run([sys.executable, "-m", "so101", "--profile", self.prof.name, "agent", *argv],
                           cwd=FRAMEWORK_ROOT, capture_output=True, text=True, timeout=30,
                           encoding="utf-8", errors="replace")
        if r.returncode != 0:
            raise RuntimeError((r.stdout + r.stderr).strip()[-300:] or f"agent {argv[0]} failed")
