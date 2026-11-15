"""Overhead camera: always-fresh frames, auto index selection, "camera moved?" check.

Lessons encoded here:
- Windows re-numbers cameras on replug (index 1 became 0 and returned black frames).
- Grabbing on demand returned frames SECONDS old (DSHOW ignores CAP_PROP_BUFFERSIZE);
  a reader thread drains the driver and read() waits for a frame newer than the call.
- The driver sometimes freezes (identical frames) -> detected and the device reopened.
- A table shake moved the camera 38 px mid-calibration; phase correlation against the
  calibration reference catches that before a vision task trusts the mapping.
"""
from __future__ import annotations

import platform
import threading
import time

import cv2
import numpy as np


def _backend():
    sysname = platform.system()
    if sysname == "Windows":
        return cv2.CAP_DSHOW
    if sysname == "Darwin":
        return cv2.CAP_AVFOUNDATION
    return cv2.CAP_V4L2


def probe_index(i, width=640, height=480, frames=10):
    """Return a dict describing camera index i, or None if it does not open.
    'static': two frames 0.2 s apart are pixel-identical -> virtual camera (OBS) or frozen;
    real sensors always differ by noise."""
    cap = cv2.VideoCapture(i, _backend())
    if not cap.isOpened():
        return None
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, width); cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
    f = None
    for _ in range(frames):
        ok, f = cap.read()
    time.sleep(0.2)
    ok2, f2 = cap.read()
    cap.release()
    if f is None:
        return None
    mean = float(f.mean())
    return {"index": i, "mean": round(mean, 1), "black": mean < 3,
            "static": bool(ok2 and f2 is not None and np.array_equal(f, f2)), "frame": f}


def find_camera(width=640, height=480, max_index=5, prefer=None):
    """First live, non-black, non-static camera; `prefer` (e.g. last known index) tried first.
    Returns (index or None, list of probe results without frames)."""
    order = ([prefer] if isinstance(prefer, int) else []) + [i for i in range(max_index) if i != prefer]
    found = []
    for i in order:
        c = probe_index(i, width, height)
        if c:
            found.append(c)
    pick = next((c["index"] for c in found if not c["black"] and not c["static"]), None)
    return pick, [{k: v for k, v in c.items() if k != "frame"} for c in found]


class Camera:
    def __init__(self, index=0, width=640, height=480, settle_s=0.15):
        self.index, self.width, self.height, self.settle_s = index, width, height, settle_s
        self._open()
        self._lock = threading.Lock()
        self._frame, self._t, self._changed = None, 0.0, time.time()
        self._stop = False
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        t0 = time.time()
        while self._frame is None and time.time() - t0 < 5:
            time.sleep(0.05)
        f = self.read()
        if f.mean() < 3:
            self.release()
            raise RuntimeError(f"camera {index} gives black frames (wrong index? lens covered?) - run `so101 doctor`")

    @classmethod
    def from_profile(cls, prof):
        c = prof["camera"]
        idx = c.get("index", "auto")
        if idx == "dashboard":
            return DashboardCamera(c.get("settle_s", 0.15))
        if idx == "auto":
            idx, _ = find_camera(c["width"], c["height"], prefer=prof.get("measured", {}).get("camera", {}).get("index"))
            if idx is None:
                raise RuntimeError("no usable camera found - run `so101 doctor`")
        return cls(int(idx), c["width"], c["height"], c.get("settle_s", 0.15))

    def _open(self):
        self.cap = cv2.VideoCapture(self.index, _backend())
        self.cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.width)
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.height)
        if not self.cap.isOpened():
            raise RuntimeError(f"could not open camera {self.index}")

    def _loop(self):
        prev = None
        while not self._stop:
            ok, f = self.cap.read()
            now = time.time()
            if not ok:
                time.sleep(0.01); continue
            if prev is None or not np.array_equal(f, prev):
                with self._lock:
                    self._frame, self._t, self._changed = f, now, now
                prev = f
            elif now - self._changed > 2.0:          # frozen driver: reopen
                self.cap.release(); self._open(); self._changed = time.time()

    def read(self, timeout=3.0):
        """First frame captured at least settle_s after this call."""
        t_req, t0 = time.time() + self.settle_s, time.time()
        while time.time() - t0 < timeout:
            with self._lock:
                if self._frame is not None and self._t >= t_req:
                    return self._frame.copy()
            time.sleep(0.01)
        raise RuntimeError("camera: no fresh frame (stream stalled)")

    def release(self):
        self._stop = True
        self._thread.join(timeout=1.0)
        self.cap.release()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.release()


class DashboardCamera:
    """Frames from the dashboard page: the browser has the camera (on a Mac, apps started from a
    tool often get no camera permission), and the page sends the overhead camera's frames to the
    dashboard server (POST /api/frame). Same interface as Camera."""

    def __init__(self, settle_s=0.15, url=None):
        import os
        self.url = (url or os.environ.get("SO101_DASHBOARD", "http://127.0.0.1:8000")) + "/api/frame"
        self.settle_s = settle_s
        self.read(timeout=5.0)                                # fail early with a clear message

    def _get(self):
        import urllib.error
        import urllib.request
        try:
            with urllib.request.urlopen(urllib.request.Request(self.url, headers={"X-SO101": "1"}), timeout=3) as r:
                return float(r.headers["X-Frame-Time"]), r.read()
        except (urllib.error.URLError, OSError):
            return 0.0, None

    def read(self, timeout=3.0):
        """First frame the page captured at least settle_s after this call."""
        t_req, t0 = time.time() + self.settle_s, time.time()
        while time.time() - t0 < timeout:
            t, data = self._get()
            if data and t >= t_req:
                f = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
                if f is not None:
                    return f
            time.sleep(0.05)
        raise RuntimeError("no fresh camera frame from the dashboard - open the dashboard page in the browser "
                           "with the overhead camera (Camera 1) switched on")

    def release(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        pass


def camera_shift(ref, frame) -> float:
    """Global image shift in px (phase correlation): moving objects barely affect it,
    a bumped camera shifts everything."""
    a = cv2.cvtColor(ref, cv2.COLOR_BGR2GRAY).astype(np.float32)
    b = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY).astype(np.float32)
    if a.shape != b.shape:
        return float("inf")
    win = cv2.createHanningWindow(a.shape[::-1], cv2.CV_32F)
    (dx, dy), _ = cv2.phaseCorrelate(a, b, win)
    return float(np.hypot(dx, dy))


def check_camera_still(prof, frame):
    """Raise if the camera moved since table calibration."""
    ref = prof.path(prof["calibration"]["reference_image"])
    if not ref.exists():
        raise RuntimeError("no calibration reference image - run `so101 calibrate table`")
    s = camera_shift(cv2.imread(str(ref)), frame)
    if s > prof["camera"].get("max_shift_px", 3.0):
        raise RuntimeError(f"camera moved {s:.1f} px since calibration - put it back or run `so101 calibrate table`")
    return s
