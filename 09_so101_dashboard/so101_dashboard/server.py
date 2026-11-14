"""Dashboard web server: Python standard library only (no installs), so it runs on any PC
the framework runs on.

    so101-dashboard [--sim] [--port 8000] [--lan] [--profile NAME]

GET  /                    the UI (static/)
GET  /api/info            profile, OS, ports, joint limits, poses, speed presets
GET  /api/state           one snapshot
GET  /api/events          Server-Sent Events: a snapshot every 50 ms (joints, telemetry, link frames)
GET  /api/twin_mesh       decimated URDF meshes for the 3D twin
POST /api/<command>       connect, disconnect, estop, reset, speed, torque, jog, set, goto, hand, mimic, doctor

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
from . import mesh
from .controller import SPEEDS, Controller

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
        self.token = token
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
        elif name == "doctor":
            return self.doctor()
        else:
            raise KeyError(f"unknown command '{name}'")
        return {"ok": True}

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


