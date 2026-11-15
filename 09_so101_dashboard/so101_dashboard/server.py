"""Dashboard web server: Python standard library only (no installs), so it runs on any PC
the framework runs on.

    so101-dashboard [--sim] [--port 8000] [--lan] [--profile NAME]

GET  /                    the UI (static/)
GET  /api/info            profile, OS, ports, joint limits, poses, speed presets
GET  /api/state           one snapshot
GET  /api/events          Server-Sent Events: a snapshot every 50 ms (joints, telemetry, link frames)
GET  /api/twin_mesh       decimated URDF meshes for the 3D twin
GET  /api/task            the running framework task (draw, pick-place, calibrations): prompt, output
GET  /api/library         motions and saved recordings
GET  /api/ai              the AI tab's transcript (Claude plans moves with the controller's tools)
GET  /api/frame           the overhead camera's latest frame (JPEG; the page sends it: POST /api/frame)
GET  /api/pad             a USB gamepad the browser can't see (Xbox-360 protocol, e.g. Logitech F710 in X mode)
POST /api/<command>       connect, disconnect, estop, reset, speed, torque, jog, set, goto, hand, mimic,
                          motion, record, play, delete_recording, task, save_drawing, ai, ai_tool, doctor,
                          vectorize (picture -> strokes), draw_plan (dry run of a drawing for the 3D twin)

Every POST must carry the header `X-SO101: 1` (browsers cannot add custom headers cross-site
without a CORS preflight, which this server never grants, so a random web page cannot drive
the arm). With --lan the server listens on the network and also requires the token printed at
start-up (it is in the URL the server prints; the UI forwards it).
"""
from __future__ import annotations

import json
import mimetypes
import platform
import secrets
import socket
import subprocess
import sys
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from so101.hardware.motors import list_ports
from so101.profile import FRAMEWORK_ROOT
from . import drawing, mesh
from .controller import SPEEDS, Controller
from .ai import AIAgent
from .ai_draw import DrawAgent
from .xpad import XPad
from .motions import MOTIONS
from .tasks import TaskRunner

STATIC = Path(__file__).resolve().parent / "static"
EVENT_PERIOD_S = 0.05


def system_info(prof):
    try:
        from importlib.metadata import version
        lerobot_v = version("lerobot")
    except Exception:
        lerobot_v = "not installed"
    return {
        "profile": prof.name, "robot_id": prof["robot"]["id"],
        "os": platform.platform(), "os_family": platform.system(), "machine": platform.machine(),
        "python": platform.python_version(), "lerobot": lerobot_v, "host": socket.gethostname(),
        "ports": list_ports(),
    }


class App:
    def __init__(self, prof, token=None):
        self.prof = prof
        self.ctl = Controller(prof).start()
        self.tasks = TaskRunner(prof, self.ctl)
        self.ai = AIAgent(self.ctl)
        self.ai.draw = DrawAgent(self.ctl, self.tasks)
        self.ai.workdir = prof.run_dir / "ai-chat"
        self.rec_dir = prof.run_dir / "recordings"
        self.token = token
        self.frame = (0.0, None)              # (time, JPEG bytes): the overhead camera, sent by the page
        self.pad = XPad().start()             # X-mode USB gamepads the browser can't see (F710 on a Mac)
        self.ai.frame = lambda: self.frame
        self._doctor_lock = threading.Lock()

    def info(self):
        m = self.prof["motion"]
        return {
            "system": system_info(self.prof),
            "joints": list(self.ctl.limits), "limits": self.ctl.limits,
            "poses": {k: v for k, v in self.prof["poses"].items()},
            "speeds": SPEEDS, "tool_frame": "gripper_frame_link",
            "reach": [m["reach_min"], m["reach_max"]],
            "supply_voltage": self.prof["robot"].get("supply_voltage", 5.0),
        }

    # every command returns a small dict; errors become HTTP 400 with {"error": msg}
    def command(self, name, body):
        c = self.ctl
        if name == "connect":
            c.connect(body.get("mode", "sim"))
        elif name == "disconnect":
            c.disconnect()
        elif name == "estop":
            c.emergency_stop()
            if self.tasks.running():          # a framework procedure owns the arm: stop it too
                try:
                    self.tasks.stop()
                except Exception:
                    pass
        elif name == "reset":
            c.reset_estop()
        elif name == "speed":
            c.set_speed(body["level"])
        elif name == "torque":
            c.set_torque(bool(body["on"]))
        elif name == "jog":
            c.jog_input(body.get("joints"), body.get("cart"), body.get("boost", False), body.get("source", "manual"))
        elif name == "set":
            c.set_joints(body["joints"], body.get("source", "slider"))
        elif name == "goto":
            c.goto(body["pose"], body.get("duration"))
        elif name == "hand":
            return {"ok": True, **c.hand_input(body["pan"], body["r"], body["z"], body["pitch"],
                                               body.get("roll"), body.get("grip"))}
        elif name == "mimic":
            return {"ok": True, "target": c.mimic_input(body["joints"])}
        elif name == "motion":
            m = MOTIONS.get(body["name"])
            if m is None:
                raise KeyError(f"unknown motion '{body['name']}'")
            self._torque_on()
            c.play(m["frames"], m["label"], body.get("speed", 1.0))
        elif name == "record":
            if body["op"] == "start":
                c.record_start()
            else:
                frames = c.record_stop()
                moved = max(max(f[1][j] for f in frames) - min(f[1][j] for f in frames) for j in frames[0][1])
                if moved < 3.0:
                    raise RuntimeError(f"nothing was recorded: the arm moved only {moved:.1f} deg. To move it by hand, "
                                       "torque must be off ('Record by hand' does that) - with torque on the motors hold it still")
                name_ = self._rec_name(body.get("name") or time.strftime("rec-%H%M%S"))
                self.rec_dir.mkdir(parents=True, exist_ok=True)
                (self.rec_dir / f"{name_}.json").write_text(json.dumps({"name": name_, "frames": frames}))
                return {"ok": True, "name": name_, "seconds": frames[-1][0], "frames": len(frames), "moved_deg": round(moved, 1)}
        elif name == "play":
            f = self.rec_dir / f"{self._rec_name(body['name'])}.json"
            if not f.exists():
                raise KeyError(f"no recording '{body['name']}'")
            self._torque_on()
            c.play(json.loads(f.read_text())["frames"], body["name"], body.get("speed", 1.0))
        elif name == "delete_recording":
            (self.rec_dir / f"{self._rec_name(body['name'])}.json").unlink(missing_ok=True)
        elif name == "task":
            op = body["op"]
            if op == "start":
                return {"ok": True, **self.tasks.start(body["task"], body.get("options"))}
            if op == "answer":
                self.tasks.answer(body["text"])
            elif op == "stop":
                self.tasks.stop()
            else:
                raise ValueError("task op must be start | answer | stop")
        elif name == "save_drawing":                  # a sketch / picture from the Draw tab -> `drawfile <name>`
            strokes = [[[float(x), float(y)] for x, y in st] for st in body["strokes"] if len(st) >= 2]
            if not strokes or sum(map(len, strokes)) > 20000:
                raise ValueError("draw something first (and keep it under 20000 points)")
            name_ = self._rec_name(body.get("name") or "sketch")
            d = self.prof.run_dir / "drawings"
            d.mkdir(parents=True, exist_ok=True)
            out = {"strokes": strokes, **({"size_mm": float(body["size_mm"])} if body.get("size_mm") else {}),
                   **({"offset_mm": [float(v) for v in body["offset_mm"]]} if body.get("offset_mm") else {})}
            (d / f"{name_}.json").write_text(json.dumps(out))
            return {"ok": True, "name": name_}
        elif name == "vectorize":
            return {"ok": True, **drawing.vectorize(body, self.prof)}
        elif name == "draw_plan":
            return {"ok": True, "plan": drawing.plan(self.prof.name, body)}
        elif name == "ai_tool":                       # one robot tool, for `so101-robot` (Claude Code backend)
            out, err = self.ai._tool(body["name"], body.get("input") or {})
            return {"ok": True, "result": out, "is_error": err}
        elif name == "ai":
            op = body["op"]
            if op == "ask":
                self.ai.ask(body["text"])
            elif op == "stop":
                self.ai.stop()
            elif op == "reset":
                self.ai.reset()
            else:
                raise ValueError("ai op must be ask | stop | reset")
        elif name == "doctor":
            return self.doctor()
        else:
            raise KeyError(f"unknown command '{name}'")
        return {"ok": True}

    def _torque_on(self):
        """Replays need the motors on; after recording by hand they are off. Switching on holds the
        arm exactly where it is (goal = present position first), then playback eases from there."""
        c = self.ctl
        if c.torque:
            return
        c.set_torque(True)
        t0 = time.monotonic()
        while not c.torque and time.monotonic() - t0 < 3.0:
            time.sleep(0.05)
        if not c.torque:
            raise RuntimeError("could not switch the motors on")

    @staticmethod
    def _rec_name(n):
        n = "".join(ch for ch in str(n) if ch.isalnum() or ch in "-_ ").strip()[:40]
        if not n:
            raise ValueError("give the recording a name (letters, digits, - and _)")
        return n

    def library(self):
        recs = []
        for f in sorted(self.rec_dir.glob("*.json")) if self.rec_dir.exists() else []:
            try:
                d = json.loads(f.read_text())
                recs.append({"name": d["name"], "seconds": d["frames"][-1][0]})
            except Exception:
                continue
        return {"motions": {k: v["label"] for k, v in MOTIONS.items()}, "recordings": recs}

    def doctor(self):
        """Run `so101 doctor` (read-only) in a subprocess. Needs the serial port free."""
        if getattr(self.ctl.backend, "kind", None) == "real":
            raise RuntimeError("disconnect the arm first (doctor needs the serial port)")
        if not self._doctor_lock.acquire(blocking=False):
            raise RuntimeError("doctor is already running")
        try:
            r = subprocess.run([sys.executable, "-m", "so101", "--profile", self.prof.name, "doctor"],
                               cwd=FRAMEWORK_ROOT, capture_output=True, text=True, timeout=180,
                               encoding="utf-8", errors="replace")
            return {"ok": r.returncode == 0, "output": (r.stdout + r.stderr)[-20000:]}
        finally:
            self._doctor_lock.release()


def make_handler(app: App):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, fmt, *args):          # keep the console readable
            pass

        def _send(self, code, body, ctype="application/json"):
            data = body if isinstance(body, bytes) else json.dumps(body, default=float).encode()
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def _authorized(self):
            if app.token is None:
                return True
            q = parse_qs(urlparse(self.path).query)
            return secrets.compare_digest(self.headers.get("X-Token") or (q.get("t") or [""])[0], app.token)

        # ------------------------------------------------------------ GET
        def do_GET(self):
            path = urlparse(self.path).path
            if path.startswith("/api/") and not self._authorized():
                return self._send(403, {"error": "missing or wrong token"})
            if path == "/api/info":
                return self._send(200, app.info())
            if path == "/api/state":
                return self._send(200, app.ctl.snapshot())
            if path == "/api/twin_mesh":
                m = mesh.load()
                return self._send(200, m) if m else self._send(404, {"error": "twin mesh not built"})
            if path == "/api/events":
                return self._events()
            if path == "/api/task":
                return self._send(200, app.tasks.state())
            if path == "/api/library":
                return self._send(200, app.library())
            if path == "/api/frame":                  # the overhead camera, for the framework's vision
                t, data = app.frame
                if data is None or time.time() - t > 5.0:
                    return self._send(404, {"error": "no camera frames - open the dashboard page with Camera 1 on"})
                self.send_response(200)
                self.send_header("Content-Type", "image/jpeg")
                self.send_header("Content-Length", str(len(data)))
                self.send_header("X-Frame-Time", repr(t))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                return self.wfile.write(data)
            if path == "/api/pad":
                return self._send(200, {**app.pad.snapshot(), "error": app.pad.error})
            if path == "/api/ai":
                return self._send(200, app.ai.state())
            return self._static(path)

        def _static(self, path):
            rel = "index.html" if path in ("/", "") else path.lstrip("/")
            f = (STATIC / rel).resolve()
            if STATIC not in f.parents or not f.is_file():
                return self._send(404, {"error": "not found"})
            ctype = mimetypes.guess_type(f.name)[0] or "application/octet-stream"
            if ctype.startswith("text/") or ctype.endswith("javascript"):
                ctype += "; charset=utf-8"
            self._send(200, f.read_bytes(), ctype)

        def _events(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Connection", "keep-alive")
            self.end_headers()
            try:
                while True:
                    t0 = time.monotonic()
                    data = json.dumps(app.ctl.snapshot(), default=float)
                    self.wfile.write(f"data: {data}\n\n".encode())
                    self.wfile.flush()
                    time.sleep(max(0.0, EVENT_PERIOD_S - (time.monotonic() - t0)))
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, OSError):
                return

        # ------------------------------------------------------------ POST
        def do_POST(self):
            path = urlparse(self.path).path
            if self.headers.get("X-SO101") != "1":
                return self._send(403, {"error": "missing X-SO101 header"})
            if not self._authorized():
                return self._send(403, {"error": "missing or wrong token"})
            if not path.startswith("/api/"):
                return self._send(404, {"error": "not found"})
            try:
                n = int(self.headers.get("Content-Length") or 0)
                if path == "/api/frame":              # JPEG from the page's overhead camera
                    if not 0 < n <= 2_000_000:
                        raise ValueError("frame must be a JPEG under 2 MB")
                    app.frame = (time.time(), self.rfile.read(n))
                    return self._send(200, {"ok": True})
                body = json.loads(self.rfile.read(n) or b"{}") if n else {}
                return self._send(200, app.command(path[len("/api/"):], body))
            except (KeyError, ValueError, RuntimeError, TypeError) as e:
                return self._send(400, {"error": str(e).strip("'\"")})
            except Exception as e:
                traceback.print_exc()
                return self._send(500, {"error": f"{type(e).__name__}: {e}"})

    return Handler


def lan_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.255.255.255", 1))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except OSError:
        return "127.0.0.1"


def serve(prof, port=8000, lan=False, sim=False, open_browser=True):
    token = secrets.token_urlsafe(8) if lan else None
    app = App(prof, token)
    host = "0.0.0.0" if lan else "127.0.0.1"
    httpd = ThreadingHTTPServer((host, port), make_handler(app))
    httpd.daemon_threads = True
    url = f"http://127.0.0.1:{port}/" + (f"?t={token}" if token else "")
    print(f"SO-101 dashboard  ->  {url}", flush=True)
    if lan:
        print(f"  on your network  ->  http://{lan_ip()}:{port}/?t={token}", flush=True)
        print("  Anyone with this link can move the arm. Share it only with people in the room.", flush=True)
    print("  Ctrl+C stops the server (the arm parks at home first).", flush=True)
    if sim:
        app.ctl.connect("sim")
    if open_browser:
        import webbrowser
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever(poll_interval=0.2)
    except KeyboardInterrupt:
        print("\nstopping: parking the arm ...", flush=True)
    finally:
        app.ctl.shutdown(park=True)
        httpd.server_close()
    return 0


