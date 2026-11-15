"""AI tab: tell the arm what to do in plain words; Claude plans it with a few robot tools.

Claude never touches the bus: every tool goes through the controller (speed preset, joint limits,
table floor, E-STOP, gripper stall guard), exactly like the buttons. The agent runs in a
background thread; the page polls `state()` for the transcript. E-STOP or the Stop button ends the
run at the next tool call.

Two backends, picked automatically:
- "api": the Anthropic SDK with ANTHROPIC_API_KEY in the dashboard's environment (tool loop here).
- "claude-code": the Claude Code CLI with your claude.ai login (`claude auth login`). It runs headless
  (`claude -p`) with every tool switched off except Bash, and Bash may only run `so101-robot`
  (robot_cli.py), which calls back into this agent's tools through POST /api/ai_tool.
"""
from __future__ import annotations

import json
import math
import os
import shutil
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

from so101 import kinematics as K

from . import vision
from .motions import MOTIONS

MODEL = "claude-opus-5-5"
MAX_STEPS = 24                     # model calls per instruction
CLAUDE_CLI = shutil.which("claude") or str(Path.home() / ".local" / "bin" / "claude")
ROBOT_CLI = shutil.which("so101-robot") or str(Path(sys.executable).parent / "so101-robot")
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
- To put it down: put_down at a spot (reach 17-30 cm, base -60..60 deg).
- Moves are slow and safe; each tool waits until the arm has arrived.
- You can see the table through the overhead camera: find_objects lists the cubes/blocks on it with
  their position. To pick one up, use pick_up (it finds it, grasps it and retries by itself).
  If find_objects sees nothing, say so - don't guess a position.
- Pen drawing on the A4 sheet (shapes, or the picture uploaded in the Draw tab): you run the drawing
  program step by step. draw_start starts it; it opens the gripper and asks for the pen. A robot can't
  put a pen in its own jaws: tell the user to lay the pen ACROSS the open jaws (tip out 5-7 cm) and to
  tell you when it's in, then end your turn. When they confirm, answer the program's questions:
  pen in -> draw_answer "go"; jaws closed below 4 % ("too thin") -> "ignore" (this pen is thin but
  works); tug check -> "go"; "HOLD THE ARM" -> "auto" (no hands); "pen over the drawing spot" ->
  "last" (lowers onto the paper at the height measured before); at the menu -> draw_make (a shape name
  or "picture"; size_mm 0 = the biggest clean size on the sheet). If draw_make says it's still
  drawing, call draw_wait. Prefer big drawings (size_mm 0 = about 130 mm): the arm lands within ~5 mm,
  so shapes under ~100 mm come out distorted - if the user asks for a small one, say so and suggest
  bigger. Afterwards draw_answer "aside" so the camera sees the sheet; before another drawing answer
  "go" (it brings the arm back over the paper). To finish:
  draw_answer "quit" - it then asks someone to hold the pen; tell the user, and when they confirm,
  draw_answer "go" to open the jaws (the arm then reconnects to the dashboard).
  Answer only the question the program is asking right now (the user may also press buttons in the
  Draw tab): if you're not sure what it asks, call draw_wait first - it returns the current question.
  While the drawing program runs, the other robot tools are unavailable.
- If a tool returns an error (E-STOP, out of reach, not connected), stop and tell the user plainly.
Before each tool call, write one short line (under 12 words) saying what you are about to do and
why - the user sees it as your thinking. Keep the final reply short: one or two sentences saying what
you did or what you need."""

CLI_SYSTEM = SYSTEM.replace("through tools.", "with ONE shell command, run through the Bash tool:") + f"""

The command is {ROBOT_CLI} (always use this full path; nothing else is allowed):
  {ROBOT_CLI} state                                   where the arm is (reach/height/base, gripper %, joints)
  {ROBOT_CLI} move REACH_CM HEIGHT_CM BASE_DEG down    move the gripper tip (pointing: down | angled | forward)
  {ROBOT_CLI} gripper PERCENT                          0 = closed (stops on an object) ... 95 = open
  {ROBOT_CLI} pose home|clear_view|ready
  {ROBOT_CLI} joint NAME DEGREES                       shoulder_pan shoulder_lift elbow_flex wrist_flex wrist_roll
  {ROBOT_CLI} motion hello|yes|no|dance|bow|chomp
  {ROBOT_CLI} find                                    what the overhead camera sees on the table (and where)
  {ROBOT_CLI} pick red|yellow                         find that object and pick it up (ends lifted, holding it)
  {ROBOT_CLI} put REACH_CM BASE_DEG                   put what it holds down on the table there
  {ROBOT_CLI} draw start                              start the pen-drawing program (then answer its questions)
  {ROBOT_CLI} draw answer WORDS                       answer it (go, ignore, auto, last, aside, quit, ...)
  {ROBOT_CLI} draw make WHAT [SIZE_MM]                at its menu: draw a shape (circle square star spiral smiley house) or "picture"; size 0 = biggest clean
  {ROBOT_CLI} draw wait                               keep waiting while it draws
  {ROBOT_CLI} wait SECONDS
Run one command per Bash call; each waits until the arm stops. Exit code 1 = error: stop and say why."""

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
    {"name": "find_objects", "strict": True,
     "description": "Look at the table with the overhead camera: the cubes/blocks it sees, with reach_cm, base_deg.",
     "input_schema": {"type": "object", "additionalProperties": False, "properties": {}}},
    {"name": "pick_up", "strict": True,
     "description": "Find an object with the camera and pick it up: open, move above it, line the jaws up, go down, "
                    "close, lift to carrying height. Ends holding it (or says it missed).",
     "input_schema": {"type": "object", "additionalProperties": False, "required": ["color"],
                      "properties": {"color": {"type": "string", "enum": ["red", "yellow"]}}}},
    {"name": "put_down", "strict": True,
     "description": "Put what the gripper holds down on the table at a spot (reach 17-30 cm, base -60..60 deg): "
                    "carry height, straight down, open, back up.",
     "input_schema": {"type": "object", "additionalProperties": False, "required": ["reach_cm", "base_deg"],
                      "properties": {"reach_cm": {"type": "number"}, "base_deg": {"type": "number"}}}},
    {"name": "draw_start", "strict": True,
     "description": "Start the pen-drawing program (it parks the arm and opens the gripper for the pen). "
                    "Returns what it asks.",
     "input_schema": {"type": "object", "additionalProperties": False, "properties": {}}},
    {"name": "draw_answer", "strict": True,
     "description": "Answer the drawing program's current question (go, ignore, auto, last, aside, quit, ...). "
                    "Returns its next question and what happened.",
     "input_schema": {"type": "object", "additionalProperties": False, "required": ["text"],
                      "properties": {"text": {"type": "string"}}}},
    {"name": "draw_make", "strict": True,
     "description": "At the drawing program's menu: draw a shape (circle, square, star, spiral, smiley, house) or "
                    "'picture' (the Draw tab's uploaded picture). size_mm 0 = the biggest clean size on the sheet.",
     "input_schema": {"type": "object", "additionalProperties": False, "required": ["what", "size_mm"],
                      "properties": {"what": {"type": "string"}, "size_mm": {"type": "number"}}}},
    {"name": "draw_wait", "strict": True,
     "description": "Keep waiting while the drawing program works (up to ~100 s per call).",
     "input_schema": {"type": "object", "additionalProperties": False, "properties": {}}},
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
        self._proc = None                    # claude-code backend: the running `claude -p`
        self._session = None                 # claude-code backend: conversation id to resume
        self.workdir = None                  # set by the server: where Claude Code keeps this chat
        self.frame = lambda: (0.0, None)     # set by the server: the overhead camera's latest JPEG
        self.draw = None                     # set by the server: pen drawing through the framework (ai_draw)

    # ------------------------------------------------------------------ page API
    def backend(self):
        if os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"):
            return "api"
        if Path(CLAUDE_CLI).exists():
            return "claude-code"
        return None

    def state(self):
        problem = self._sdk_problem()
        return {"busy": self.busy, "log": self.log[-60:], "model": MODEL, "ready": problem is None,
                "problem": problem, "backend": self.backend()}

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
        run = self._run if self.backend() == "api" else self._run_cli
        threading.Thread(target=run, args=() if run == self._run else (text,), daemon=True).start()

    def stop(self):
        self._stop.set()
        p = self._proc
        if p is not None and p.poll() is None:
            p.terminate()

    def reset(self):
        if self.busy:
            raise RuntimeError("stop the current instruction first")
        self.messages, self.log, self._session = [], [], None

    # ------------------------------------------------------------------ agent loop
    def _sdk_problem(self):
        b = self.backend()
        if b == "claude-code":
            return None if Path(ROBOT_CLI).exists() else f"{ROBOT_CLI} is missing - reinstall the dashboard (install.sh)"
        if b is None:
            return NO_KEY + " Or install Claude Code and run: claude auth login"
        try:
            import anthropic  # noqa: F401
        except ImportError:
            return "the 'anthropic' package is not installed in the dashboard's Python (pip install anthropic)"
        return None

    # ------------------------------------------------------------------ claude-code backend
    def _run_cli(self, text):
        """One instruction through `claude -p` (your claude.ai login). Only Bash is enabled and Bash
        may only run so101-robot; anything else is denied without asking (permission mode dontAsk)."""
        try:
            first = self._session is None
            if first:
                self._session = str(uuid.uuid4())
            cmd = [CLAUDE_CLI, "-p", text, "--model", MODEL, "--tools", "Bash",
                   "--allowedTools", f"Bash({ROBOT_CLI}:*)", "--permission-mode", "dontAsk",
                   "--append-system-prompt", CLI_SYSTEM, "--output-format", "stream-json", "--verbose",
                   # no MCP servers / connectors: with many connected, a resumed chat failed with
                   # "rendered tool text exceeds 4194304 bytes" (and the robot needs none of them)
                   "--strict-mcp-config", "--mcp-config", '{"mcpServers": {}}',
                   *(["--session-id", self._session] if first else ["--resume", self._session])]
            wd = Path(self.workdir or Path.home())
            wd.mkdir(parents=True, exist_ok=True)
            self._proc = subprocess.Popen(cmd, cwd=wd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                          text=True, encoding="utf-8", errors="replace")
            ok = False
            for line in self._proc.stdout:
                try:
                    d = json.loads(line)
                except ValueError:
                    continue
                t = d.get("type")
                if t == "assistant":
                    for b in d["message"]["content"]:
                        if b.get("type") == "text" and b["text"].strip():
                            self.log.append({"role": "thinking", "text": b["text"].strip()})
                        elif b.get("type") == "thinking" and (b.get("thinking") or "").strip():
                            self.log.append({"role": "thinking", "text": b["thinking"].strip()[:400]})
                        elif b.get("type") == "tool_use":
                            c = str(b.get("input", {}).get("command", "")).replace(ROBOT_CLI, "robot")
                            self.log.append({"role": "tool", "text": f"{c} …"})
                elif t == "user":
                    for b in d["message"]["content"]:
                        if isinstance(b, dict) and b.get("type") == "tool_result":
                            out = b.get("content")
                            out = " ".join(x.get("text", "") for x in out) if isinstance(out, list) else str(out)
                            if self.log and self.log[-1]["role"] == "tool":
                                self.log[-1]["text"] = self.log[-1]["text"].replace(" …", f" → {out.strip()[:200]}")
                elif t == "result":
                    ok = d.get("subtype") == "success" and not d.get("is_error")
                    if not ok:
                        self.log.append({"role": "error", "text": str(d.get("result") or d.get("subtype"))[:300]})
            self._proc.wait()
            self._final_answer()
            if self._stop.is_set():
                self.log.append({"role": "error", "text": "Stopped."})
            elif not ok and self._proc.returncode:
                self.log.append({"role": "error", "text": f"Claude Code exited with {self._proc.returncode} - is it logged in? (claude auth login)"})
                if first:
                    self._session = None
        except Exception as e:
            self.log.append({"role": "error", "text": f"{type(e).__name__}: {e}"})
        finally:
            self._proc = None
            self.busy = False

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
                        self.log.append({"role": "thinking", "text": b.text.strip()})
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
            self._final_answer()
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

    def _final_answer(self):
        """Texts written along the way are shown as thinking; the last one of the run is the answer."""
        for m in reversed(self.log):
            if m["role"] == "user":
                return
            if m["role"] == "thinking":
                m["role"] = "assistant"
                return
            if m["role"] in ("tool", "error"):
                return

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
                if a["pointing"] == "down":                   # the safe top-down solver (no "closest pose" dive)
                    pan = math.radians(float(a["base_deg"]))
                    self._top_down(r * math.cos(pan), r * math.sin(pan), max(z, 0.0), c.goal.get("wrist_roll", 0.0))
                    out = {"reachable": True}
                else:
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
            if name == "find_objects":
                seen = vision.find(self.frame, self.ctl.prof.name)
                return (json.dumps([{k: o[k] for k in ("what", "reach_cm", "base_deg")} for o in seen])
                        if seen else "nothing found on the table"), False
            if name == "draw_start":
                return self.draw.start(), False
            if name == "draw_answer":
                return self.draw.answer(str(a["text"])[:100]), False
            if name == "draw_make":
                return self.draw.make(str(a["what"]).lower().strip(), float(a.get("size_mm") or 0)), False
            if name == "draw_wait":
                return self.draw.wait(), False
            if name == "pick_up":
                # the arm lands within ~1 cm and the camera map is about as good: a miss usually only
                # nudges the cube, so look again and retry (each try re-finds it)
                for attempt in range(3):
                    if attempt:                               # out of the overhead camera's view, then look again
                        c.goto("clear_view")
                        self._settle(timeout=20)
                    text, err = self._pick_up(a["color"])
                    if not err or "missed" not in text:
                        return (text + (f" (try {attempt + 1})" if attempt else "")), err
                return text + " - tried 3 times", True
            if name == "put_down":
                m = c.prof["motion"]
                r, pan = float(a["reach_cm"]) / 100, math.radians(float(a["base_deg"]))
                x, y, roll = r * math.cos(pan), r * math.sin(pan), c.goal.get("wrist_roll", 0.0)
                self._top_down(x, y, m["travel_z"], roll)
                self._top_down(x, y, m["approach_z"], roll)
                self._top_down(x, y, m["grasp_z"] + 0.004, roll)
                c.set_joints({"gripper": 90.0}, source="ai")
                self._settle()
                self._top_down(x, y, m["travel_z"], roll)
                return f"put it down at reach {r * 100:.0f} cm, base {float(a['base_deg']):.0f} deg", False
            if name == "play_motion":
                c.play(MOTIONS[a["name"]]["frames"], MOTIONS[a["name"]]["label"])
                self._settle(timeout=40)
                return "done", False
            return f"unknown tool {name}", True
        except (RuntimeError, ValueError, KeyError) as e:
            return f"error: {e}", True

    def _top_down(self, x, y, z, roll, settle=True):
        """Gripper pointing down at (x, y, z) - the framework's top-down IK (what pick-and-place and the
        table calibration use), sent as joint targets. Raises if there is no such pose."""
        c = self.ctl
        sol = K.ik((x, y, z), dict(c.goal), roll=roll, gripper=c.goal.get("gripper", 70.0))
        if sol is None:
            raise RuntimeError(f"no gripper-down pose at reach {math.hypot(x, y) * 100:.0f} cm, height {z * 100:.0f} cm")
        c.set_joints({k: v for k, v in sol[0].items() if k != "gripper"}, source="ai")
        if settle:
            self._settle()
        return sol[0]

    def _pick_up(self, color):
        """Camera -> grasp, with the motion primitives of the framework's pick-and-place: carry height,
        jaws lined up with the object's edges, straight down to the grasp height, close, lift."""
        c, prof = self.ctl, self.ctl.prof
        col = vision.COLORS[color]
        seen = vision.find(self.frame, prof.name, (col,))
        if not seen:
            return f"no {color} object seen on the table", True
        o = seen[0]
        obj = next((v for v in prof["objects"].values() if v["color"] == col), {})
        m = prof["motion"]
        x, y = o["x"], o["y"]
        r = math.hypot(x, y)
        if not m["reach_min"] <= r <= m["reach_max"]:
            return f"the {o['what']} is at reach {r * 100:.0f} cm - outside {m['reach_min'] * 100:.0f}-{m['reach_max'] * 100:.0f} cm", True
        c.set_joints({"gripper": 90.0}, source="ai")
        self._settle()
        # wrist roll 0, as in the table calibration: its small offsets (the cube slides a little as the
        # jaws open) then lie along the closing direction, where the open jaws tolerate them
        roll = 0.0
        self._top_down(x, y, m["travel_z"], roll)
        self._top_down(x, y, m["approach_z"], roll)
        self._top_down(x, y, obj.get("grasp_z", m["grasp_z"]), roll)
        time.sleep(0.5)                                         # let the last millimetres settle
        c.set_joints({"gripper": 0.0}, source="ai")             # the stall guard stops it on the object
        self._settle()
        time.sleep(0.4)
        g = c.measured.get("gripper", 0.0)
        self._top_down(x, y, m["travel_z"], roll)
        held = g >= prof["gripper"]["empty_threshold"]
        where = f"reach {r * 100:.0f} cm, base {math.degrees(math.atan2(y, x)):.0f} deg"
        return (f"picked up the {o['what']} at {where} (gripper {g:.0f} %), now lifted" if held
                else f"closed on nothing at {where} (gripper {g:.0f} %) - missed it"), not held

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
