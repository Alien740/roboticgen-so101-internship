"""`so101 agent ...`: drive a task from ANY AI that can run shell commands.

    so101 agent start basket --trials 3     run a task in the background (agent mode)
    so101 agent wait [--timeout 600]        block until it asks something or ends; prints JSON
    so101 agent status                      current prompt + recent events (JSON)
    so101 agent answer "go"                 reply to the current prompt
    so101 agent stop                        halt motion now (same as answering 'stop')

Protocol (also in AGENTS.md): every `wait`/`status` returns
  {"state": "waiting"|"running"|"ended"|"not_started", "prompt": str|null, "options": [...],
   "events": [last events], "exit_code": int|null}
When state == "waiting", decide or ask the human, then `answer`. Never answer a safety
prompt ("hold the arm", "is the tip pointing down?") without the human's confirmation.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time

from .session import stdout_utf8


def _paths(prof):
    d = prof.run_dir
    return d / "session.log", d / "cmd.txt", d / "agent.json", d / "agent.out"


def _alive(pid):
    if pid is None:
        return False
    if os.name == "nt":
        out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"], capture_output=True, text=True).stdout
        return str(pid) in out
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _state(prof, n_events=8):
    log, cmd, meta_f, out = _paths(prof)
    meta = json.loads(meta_f.read_text()) if meta_f.exists() else {}
    if not meta:
        return {"state": "not_started", "prompt": None, "options": [], "events": [], "exit_code": None}
    lines = log.read_text(encoding="utf-8", errors="replace").splitlines() if log.exists() else []
    start = max((i for i, l in enumerate(lines) if '"session_start"' in l and l.startswith("@@EVENT")), default=0)
    lines = lines[start:]
    events = [json.loads(l[8:]) for l in lines if l.startswith("@@EVENT ")]
    last_wait = max((i for i, l in enumerate(lines) if l.startswith("@@WAIT ")), default=-1)
    answered = any(l.startswith("> ") for l in lines[last_wait + 1:]) if last_wait >= 0 else True
    alive = _alive(meta.get("pid"))
    rc = meta.get("exit_code")
    ended = (not alive) or any(e.get("type") == "session_end" for e in events[-1:])
    if ended:
        st = "ended"
    elif last_wait >= 0 and not answered and not cmd.exists():
        st = "waiting"
    else:
        st = "running"
    w = json.loads(lines[last_wait][7:]) if (st == "waiting") else {"prompt": None, "options": []}
    return {"state": st, "prompt": w["prompt"], "options": w["options"], "events": events[-n_events:],
            "exit_code": rc, "task": meta.get("argv"), "output_file": str(out)}


def main(argv, prof_name=None):
    stdout_utf8()
    from . import profile as P
    prof = P.load(prof_name)
    log, cmd, meta_f, out = _paths(prof)
    if not argv:
        print(__doc__); return 2
    op, rest = argv[0], argv[1:]
    if op == "start":
        st = _state(prof)
        if st["state"] in ("waiting", "running"):
            print(json.dumps({"error": "a task is already running", **st})); return 1
        cmd.unlink(missing_ok=True)
        args = [sys.executable, "-m", "so101"] + (["--profile", prof.name]) + ["--agent"] + rest
        flags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
        with open(out, "w", encoding="utf-8") as fh:
            p = subprocess.Popen(args, stdout=fh, stderr=subprocess.STDOUT, creationflags=flags,
                                 env={**os.environ, "SO101_AGENT": "1", "PYTHONUNBUFFERED": "1"})
        meta_f.write_text(json.dumps({"pid": p.pid, "argv": rest, "started": time.time()}))
        time.sleep(1.0)
        print(json.dumps({"started": rest, "pid": p.pid, **_state(prof)}, default=str)); return 0
    if op == "status":
        print(json.dumps(_state(prof), default=str)); return 0
    if op == "wait":
        timeout = float(rest[rest.index("--timeout") + 1]) if "--timeout" in rest else 600.0
        t0 = time.time()
        while time.time() - t0 < timeout:
            st = _state(prof)
            if st["state"] in ("waiting", "ended", "not_started"):
                print(json.dumps(st, default=str)); return 0
            time.sleep(0.5)
        print(json.dumps({**_state(prof), "timeout": True}, default=str)); return 0
    if op in ("answer", "stop"):
        text = "stop" if op == "stop" else " ".join(rest)
        cmd.write_text(text, encoding="utf-8")
        time.sleep(0.8)
        print(json.dumps({"sent": text, **_state(prof)}, default=str)); return 0
    print(f"unknown agent op '{op}'\n{__doc__}"); return 2
