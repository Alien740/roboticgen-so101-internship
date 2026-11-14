"""AI tab: tell the arm what to do in plain words; Claude plans it with a few robot tools.

Claude never touches the bus: every tool goes through the controller (speed preset, joint limits,
table floor, E-STOP, gripper stall guard), exactly like the buttons. The agent runs in a
background thread; the page polls `state()` for the transcript. E-STOP or the Stop button ends the
run at the next tool call.

Needs the `anthropic` package and credentials in the environment of the process running the
dashboard (ANTHROPIC_API_KEY, or an `ant auth login` profile).
"""
from __future__ import annotations

import json
import math
import os
import threading
import time
from pathlib import Path

from so101 import kinematics as K

from .motions import MOTIONS

MODEL = "claude-opus-5-5"
MAX_STEPS = 24                     # model calls per instruction
NO_KEY = ("No Anthropic API key in the dashboard's environment. Stop the dashboard (Ctrl+C), then in "
          "Terminal: export ANTHROPIC_API_KEY=sk-ant-...  and start so101-dashboard again.")
SETTLE_TIMEOUT_S = 12.0
POSES = ("home", "clear_view", "ready")
READY = {"shoulder_lift": -20.0, "elbow_flex": 30.0, "wrist_flex": 70.0, "wrist_roll": 0.0, "gripper": 60.0}

SYSTEM = """You control a real SO-101 robot arm (5 joints + gripper) on a table, through tools.

Coordinates for the gripper: reach_cm = horizontal distance from the robot's base (15-33 cm is the
working area), height_cm = height of the gripper tip above the table (1 cm = touching the table for
grasping a small cube, 10-15 cm = carrying height), base_deg = direction (0 = straight ahead of the
robot, positive = to the robot's left, negative = to its right, at most +/-90).

How to work:
- Call get_state first if you need to know where the arm is.
- To pick something up: open the gripper, move above it at carrying height pointing down, go down to
  1-2 cm, close the gripper (it stops on the object by itself), go back up to carrying height.
- To put it down: move above the spot at carrying height, go down, open the gripper, go back up.
- Moves are slow and safe; each tool waits until the arm has arrived.
- You cannot see. If the user did not say where an object is, ask them (reach, side) instead of guessing.
- Pen drawing is done in the Draw tab, not with these tools - say so if asked to draw.
- If a tool returns an error (E-STOP, out of reach, not connected), stop and tell the user plainly.
Keep replies short: one or two sentences saying what you did or what you need."""

TOOLS = [
    {"name": "get_state", "strict": True,
     "description": "Where the arm is now: gripper position (reach_cm, height_cm, base_deg), gripper opening %, joints, and whether it is connected / E-STOPped.",
     "input_schema": {"type": "object", "properties": {}, "required": [], "additionalProperties": False}},
    {"name": "move_gripper_to", "strict": True,
     "description": "Move the gripper tip to a point (straight-line target, solved by inverse kinematics). Waits until the arm arrives.",
     "input_schema": {"type": "object", "additionalProperties": False,
                      "required": ["reach_cm", "height_cm", "base_deg", "pointing"],
                      "properties": {
                          "reach_cm": {"type": "number", "description": "15-33"},
                          "height_cm": {"type": "number", "description": "1-25 above the table"},
                          "base_deg": {"type": "number", "description": "-90..90, 0 = straight ahead, + = robot's left"},
                          "pointing": {"type": "string", "enum": ["down", "angled", "forward"],
                                       "description": "down for grasping from above"}}}},
    {"name": "set_gripper", "strict": True,
     "description": "Open or close the gripper: 0 = closed (stops on an object by itself), 95 = fully open. Waits until done.",
     "input_schema": {"type": "object", "additionalProperties": False, "required": ["percent"],
                      "properties": {"percent": {"type": "number", "description": "0-95"}}}},
    {"name": "go_to_pose", "strict": True,
     "description": "Go to a named pose: home (folded, resting on the table), clear_view (standing up tall), ready (above the table, gripper pointing down, half open).",
     "input_schema": {"type": "object", "additionalProperties": False, "required": ["name"],
                      "properties": {"name": {"type": "string", "enum": list(POSES)}}}},
    {"name": "set_joint", "strict": True,
     "description": "Move one joint to an angle in degrees (for small adjustments, e.g. wrist_roll to turn the gripper).",
     "input_schema": {"type": "object", "additionalProperties": False, "required": ["joint", "degrees"],
                      "properties": {"joint": {"type": "string", "enum": ["shoulder_pan", "shoulder_lift", "elbow_flex",
                                                                        "wrist_flex", "wrist_roll"]},
                                     "degrees": {"type": "number"}}}},
    {"name": "play_motion", "strict": True,
     "description": "Play a fun pre-programmed motion. Waits until it ends.",
     "input_schema": {"type": "object", "additionalProperties": False, "required": ["name"],
                      "properties": {"name": {"type": "string", "enum": list(MOTIONS)}}}},
    {"name": "wait", "strict": True,
     "description": "Pause for a few seconds (0.5-10).",
     "input_schema": {"type": "object", "additionalProperties": False, "required": ["seconds"],
                      "properties": {"seconds": {"type": "number"}}}},
]
PITCH = {"down": -90.0, "angled": -45.0, "forward": 0.0}


class AIAgent:
    def __init__(self, ctl):
        self.ctl = ctl
        self.messages = []                   # the API conversation (content blocks echoed back as-is)
        self.log = []                        # what the page shows: {role: user|assistant|tool|error, text}
        self.busy = False
        self._stop = threading.Event()
        self._client = None

    # ------------------------------------------------------------------ page API
    def state(self):
        return {"busy": self.busy, "log": self.log[-60:], "model": MODEL, "ready": self._sdk_problem() is None,
                "problem": self._sdk_problem()}

    def ask(self, text):
        text = str(text).strip()[:1000]
        if not text:
            raise ValueError("type what the arm should do")
        if self.busy:
            raise RuntimeError("still working on the last instruction - wait or press Stop")
        problem = self._sdk_problem()
        if problem:
            raise RuntimeError(problem)
        self.busy = True
        self._stop.clear()
        self.log.append({"role": "user", "text": text})
        self.messages.append({"role": "user", "content": text})
        threading.Thread(target=self._run, daemon=True).start()

    def stop(self):
        self._stop.set()

    def reset(self):
        if self.busy:
            raise RuntimeError("stop the current instruction first")
        self.messages, self.log = [], []

    # ------------------------------------------------------------------ agent loop
    def _sdk_problem(self):
        try:
            import anthropic  # noqa: F401
        except ImportError:
            return "the 'anthropic' package is not installed in the dashboard's Python (pip install anthropic)"
        # the SDK also reads `ant auth login` profiles from ~/.config/anthropic
        if not (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")
                or (Path.home() / ".config" / "anthropic").exists()):
            return NO_KEY
        return None

    def _run(self):
        import anthropic
        try:
            if self._client is None:
                self._client = anthropic.Anthropic()
            for _ in range(MAX_STEPS):
                if self._stopped():
                    break
                resp = self._client.beta.messages.create(
                    model=MODEL, max_tokens=16000, system=SYSTEM, tools=TOOLS,
                    output_config={"effort": "low"},          # short robot commands: quick replies
                    betas=["server-side-fallback-2026-07-01"], fallbacks="default",
                    messages=self.messages)
                content = _echoable(resp.content)
                self.messages.append({"role": "assistant", "content": content})
                for b in content:
                    if b.type == "text" and b.text.strip():
                        self.log.append({"role": "assistant", "text": b.text.strip()})
                if resp.stop_reason == "refusal":
                    self.log.append({"role": "error", "text": "Claude declined this request."})
                    self.messages.pop(); break
                calls = [b for b in content if b.type == "tool_use"]
                if resp.stop_reason != "tool_use" or not calls:
                    break
                results = []
                for call in calls:
                    out, err = self._tool(call.name, call.input)
                    self.log.append({"role": "tool", "text": f"{call.name}({_short(call.input)}) → {out}"})
                    results.append({"type": "tool_result", "tool_use_id": call.id, "content": out, **({"is_error": True} if err else {})})
                self.messages.append({"role": "user", "content": results})
            else:
                self.log.append({"role": "error", "text": f"stopped after {MAX_STEPS} steps"})
        except anthropic.AuthenticationError:
            self.log.append({"role": "error", "text": "The Anthropic API key was rejected. " + NO_KEY})
            self.messages.pop()
        except anthropic.RateLimitError:
            self.log.append({"role": "error", "text": "Rate limited by the API - try again in a minute."})
            self.messages.pop()
        except anthropic.APIStatusError as e:
            self.log.append({"role": "error", "text": f"API error {e.status_code}: {e.message}"})
            self.messages.pop()
        except anthropic.APIConnectionError:
            self.log.append({"role": "error", "text": "No connection to the Anthropic API (internet?)."})
            self.messages.pop()
        except Exception as e:
            self.log.append({"role": "error", "text": f"{type(e).__name__}: {e}"})
        finally:
            self._tidy()
            self.busy = False

    def _tidy(self):
        """Leave the conversation valid for the next instruction: it must end on a user tool_result
        or an assistant turn, never on an assistant turn with unanswered tool_use blocks."""
        while self.messages and self.messages[-1]["role"] == "assistant" and any(
                getattr(b, "type", None) == "tool_use" for b in self.messages[-1]["content"]):
            self.messages.pop()
        while self.messages and self.messages[-1]["role"] == "user":
            self.messages.pop()                               # unanswered instruction / orphaned tool results
            if self.messages and self.messages[-1]["role"] == "assistant" and any(
                    getattr(b, "type", None) == "tool_use" for b in self.messages[-1]["content"]):
                self.messages.pop()

    def _stopped(self):
        if self._stop.is_set() or self.ctl.estop:
            self.log.append({"role": "error", "text": "Stopped."})
            return True
        return False

    # ------------------------------------------------------------------ tools
    def _tool(self, name, a):
        """Returns (text for Claude, is_error)."""
        c = self.ctl
        try:
            if self._stop.is_set() or c.estop:
                return "stopped by the user (E-STOP / Stop) - do not continue", True
            if name == "get_state":
                return json.dumps(self._pose_summary()), False
            if name == "wait":
                time.sleep(max(0.5, min(10.0, float(a["seconds"]))))
                return "ok", False
            if name == "move_gripper_to":
                r, z = float(a["reach_cm"]) / 100, float(a["height_cm"]) / 100
                out = c.hand_input(pan=float(a["base_deg"]), r=r, z=z, pitch=PITCH[a["pointing"]])
                self._settle()
                s = self._pose_summary()
                note = "" if out["reachable"] else " (target was beyond reach - stopped at the edge)"
                return f"arrived at reach {s['reach_cm']} cm, height {s['height_cm']} cm, base {s['base_deg']} deg{note}", False
            if name == "set_gripper":
                c.set_joints({"gripper": max(0.0, min(95.0, float(a["percent"])))}, source="ai")
                self._settle()
                return f"gripper at {c.measured.get('gripper', 0):.0f} %", False
            if name == "go_to_pose":
                c.goto(READY if a["name"] == "ready" else a["name"])
                self._settle()
                return f"at {a['name']}", False
            if name == "set_joint":
                c.set_joints({a["joint"]: float(a["degrees"])}, source="ai")
                self._settle()
                return f"{a['joint']} at {c.measured.get(a['joint'], 0):.0f} deg", False
            if name == "play_motion":
                c.play(MOTIONS[a["name"]]["frames"], MOTIONS[a["name"]]["label"])
                self._settle(timeout=40)
                return "done", False
            return f"unknown tool {name}", True
        except (RuntimeError, ValueError, KeyError) as e:
            return f"error: {e}", True

    def _settle(self, timeout=SETTLE_TIMEOUT_S):
        """Wait until the arm stops: no trajectory/playback and the goal has reached the target."""
        c = self.ctl
        t0 = time.monotonic()
        time.sleep(0.15)
        while time.monotonic() - t0 < timeout:
            if self._stop.is_set() or c.estop:
                raise RuntimeError("stopped by the user")
            with c.lock:
                moving = c.traj is not None or c.playback is not None or any(
                    abs(c.target[j] - c.goal.get(j, c.target[j])) > 0.5 for j in c.target)
                lag = max([abs(c.goal[j] - c.measured.get(j, c.goal[j])) for j in c.goal] + [0.0])
            if not moving and lag < 3.0:
                return
            time.sleep(0.1)

    def _pose_summary(self):
        c = self.ctl
        s = {"connected": c.backend is not None, "estop": c.estop, "speed": c.speed}
        if c.measured:
            x, y, z = K.fk(c.measured)[:3, 3]
            s.update(reach_cm=round(math.hypot(x, y) * 100, 1), height_cm=round(z * 100, 1),
                     base_deg=round(math.degrees(math.atan2(y, x)), 1),
                     gripper_pct=round(c.measured.get("gripper", 0)),
                     joints={k: round(v, 1) for k, v in c.measured.items()})
        return s


def _echoable(content):
    """After a mid-output refusal fallback, blocks before the last `fallback` marker other than text
    must not be echoed back (thinking / tool_use of the declined attempt)."""
    idx = max((i for i, b in enumerate(content) if b.type == "fallback"), default=None)
    if idx is None:
        return list(content)
    return [b for i, b in enumerate(content) if i > idx or b.type == "text"]


def _short(d):
    return ", ".join(f"{k}={v}" for k, v in d.items())
