"""The AI tab's pen drawing: the agent runs the framework's draw program (the same one the Draw tab
runs - `so101 draw` in agent mode) by answering its prompts, and asks the person only for what a
robot can't do: putting the pen in the jaws and taking it back.

The program owns the serial port while it runs (the dashboard's arm connection is released when it
starts and connected again when it ends). Every call returns within ~100 s so a tool call never
times out mid-drawing; `draw_wait` continues waiting.
"""
from __future__ import annotations

import json
import math
import time

from so101 import profile
from so101.tasks import draw as D

STEP_TIMEOUT_S = 100.0
MAX_LEAN_DEG = 6.0                         # pen length not measured in auto placement: stay where it's upright


class DrawAgent:
    def __init__(self, ctl, tasks):
        self.ctl, self.tasks = ctl, tasks
        self.mode = None                   # the dashboard's arm connection before the program took the port
        self._seen = 0.0                   # newest event time already reported

    # ------------------------------------------------------------------ helpers
    def _state(self):
        return self.tasks.state()

    def _mine(self, st):
        return (st.get("task") or [None])[0] == "draw" and st["state"] in ("starting", "running", "waiting")

    def _wait(self, timeout=STEP_TIMEOUT_S):
        """Until the program waits for an answer or ends (or the timeout)."""
        t0 = time.monotonic()
        time.sleep(0.6)
        while time.monotonic() - t0 < timeout:
            st = self._state()
            if st["state"] in ("waiting", "ended", "not_started") and not self.tasks.handing_over:
                return st
            time.sleep(0.5)
        return self._state()

    def _report(self, st):
        """What happened since the last report, and what the program asks now."""
        new = [e for e in st.get("events", []) if e.get("t", 0) > self._seen]
        if new:
            self._seen = max(e.get("t", 0) for e in new)
        keep = ("pen_gripped", "auto_placed", "drawing", "drawn", "error", "hand_placed")
        ev = [{k: v for k, v in e.items() if k != "t"} for e in new if e["type"] in keep]
        strokes = [e for e in new if e["type"] == "stroke_done"]
        if strokes:
            ev.append({"strokes_done": strokes[-1]["index"], "of": strokes[-1]["of"]})
        if st["state"] == "ended":
            self._reconnect()
            return json.dumps({"program": "ended", "events": ev, "arm": "reconnected to the dashboard"})
        if st["state"] == "waiting":
            return json.dumps({"program": "waiting", "asks": st["prompt"], "events": ev})
        return json.dumps({"program": st["state"], "events": ev,
                           "note": "still working (drawing?) - call draw_wait to keep waiting"})

    def _reconnect(self):
        if self.mode and self.ctl.backend is None:
            try:
                self.ctl.connect(self.mode)
            except Exception:
                pass
        self.mode = None

    # ------------------------------------------------------------------ tools
    def start(self):
        st = self._state()
        if self._mine(st):
            return self._report(self._wait(5))
        self.mode = getattr(self.ctl.backend, "kind", None) or "real"
        self._seen = time.time()
        self.tasks.start("draw")
        return self._report(self._wait(60))

    def answer(self, text):
        st = self._state()
        if not self._mine(st):
            return json.dumps({"program": "not running - call draw_start first"})
        if st["state"] != "waiting":
            return self._report(self._wait())
        self.tasks.answer(text)
        return self._report(self._wait())

    def wait(self):
        return self._report(self._wait())

    def make(self, what, size_mm=0.0):
        """Draw a shape or the Draw tab's last picture: saved as a drawing at the best size/place on the
        sheet, then `drawfile` at the program's menu."""
        st = self._state()
        if not (self._mine(st) and st["state"] == "waiting" and "'draw <shape>" in (st.get("prompt") or "")):
            return json.dumps({"error": "the drawing program is not at its menu yet (pen placed?)", "asks": st.get("prompt")})
        prof = profile.load(self.ctl.prof.name)
        if what in D.SHAPES:
            strokes = [[list(p) for p in st_] for st_ in D.shape(what, 100.0)]
        elif what == "picture":
            f = prof.run_dir / "drawings" / "upload.json"
            if not f.exists():
                return json.dumps({"error": "no picture uploaded - upload one in the Draw tab first"})
            strokes = json.loads(f.read_text())["strokes"]
        else:
            return json.dumps({"error": f"unknown drawing '{what}' (shapes: {', '.join(D.SHAPES)}, or 'picture')"})
        if size_mm and size_mm > 0:
            size, off = min(float(size_mm), prof["drawing"]["max_size_mm"]), (0.0, 0.0)
        else:
            from .drawing import lean_map
            size, off = D.fit_sheet(strokes, lean_map(prof), prof["drawing"]["max_size_mm"], MAX_LEAN_DEG)
        sim = D.simulate(prof, [[(u + off[0], v + off[1]) for u, v in s_] for s_ in D.fit_strokes(strokes, size)])
        if not sim["ok"]:
            return json.dumps({"error": f"can't draw it there: {sim['error']} - try a smaller size"})
        name = f"ai-{what}"
        d = prof.run_dir / "drawings"
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{name}.json").write_text(json.dumps({"strokes": strokes, "size_mm": size, "offset_mm": list(off)}))
        self.tasks.answer(f"drawfile {name} {size:.0f}")
        out = json.loads(self._report(self._wait()))
        out["drawing"] = {"what": what, "size_mm": round(size), "planned_seconds": round(sim["frames"][-1][0])}
        return json.dumps(out)
