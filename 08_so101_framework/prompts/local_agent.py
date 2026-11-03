"""Connect a local LLM (any OpenAI-compatible server: Ollama, LM Studio, llama.cpp, vLLM) to the robot.

    python prompts/local_agent.py --base-url http://localhost:11434/v1 --model qwen2.5:14b

The model gets prompts/system_prompt.md and the tools in prompts/tools.json. SAFETY GATE: any tool
that can move the arm (agent_start, agent_answer) is shown to YOU and runs only if you type 'y',
whatever the model decided. `stop` always runs. Standard library only (no extra installs).
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GATED = {"so101_agent_start", "so101_agent_answer"}


def so101(*args, timeout=900):
    r = subprocess.run([sys.executable, "-m", "so101", *args], cwd=ROOT, capture_output=True, text=True, timeout=timeout)
    out = (r.stdout + r.stderr).strip()
    return out[-6000:]                         # keep the model's context small


def run_tool(name, args):
    if name == "so101_doctor":
        return so101("doctor")
    if name == "so101_detect":
        return so101("detect", *(args.get("colors") or ["orange"]))
    if name == "so101_report":
        return so101("report")
    if name == "so101_agent_start":
        return so101("agent", "start", *args["args"])
    if name == "so101_agent_wait":
        return so101("agent", "wait", "--timeout", str(args.get("timeout_s", 300)))
    if name == "so101_agent_status":
        return so101("agent", "status")
    if name == "so101_agent_answer":
        return so101("agent", "answer", args["text"])
    if name == "so101_agent_stop":
        return so101("agent", "stop")
    if name == "read_doc":
        p = (ROOT / args["path"]).resolve()
        if ROOT not in p.parents and p != ROOT:
            return "refused: path outside the framework folder"
        return p.read_text(encoding="utf-8", errors="replace")[:12000] if p.is_file() else f"not found: {args['path']}"
    return f"unknown tool {name}"


def chat(base_url, model, messages, tools, api_key):
    body = json.dumps({"model": model, "messages": messages, "tools": tools, "temperature": 0.2}).encode()
    req = urllib.request.Request(base_url.rstrip("/") + "/chat/completions", data=body,
                                 headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"})
    with urllib.request.urlopen(req, timeout=600) as r:
        return json.loads(r.read())["choices"][0]["message"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", default="http://localhost:11434/v1")
    ap.add_argument("--model", required=True)
    ap.add_argument("--api-key", default="local")
    a = ap.parse_args()
    tools = json.loads((ROOT / "prompts" / "tools.json").read_text())
    messages = [{"role": "system", "content": (ROOT / "prompts" / "system_prompt.md").read_text(encoding="utf-8")}]
    print("SO-101 local agent. Type your request ('quit' to exit). Arm-moving actions need your 'y'.\n")
    while True:
        user = input("you> ").strip()
        if user.lower() in ("quit", "exit"):
            break
        messages.append({"role": "user", "content": user})
        for _ in range(25):                       # tool-call rounds per user turn
            msg = chat(a.base_url, a.model, messages, tools, a.api_key)
            messages.append(msg)
            calls = msg.get("tool_calls") or []
            if not calls:
                print(f"\nassistant> {msg.get('content', '')}\n")
                break
            for c in calls:
                name = c["function"]["name"]
                try:
                    args = json.loads(c["function"].get("arguments") or "{}")
                except json.JSONDecodeError:
                    args = {}
                if name in GATED:
                    print(f"\n[model wants to run] {name} {json.dumps(args)}")
                    if input("  allow? this can MOVE THE ARM (y/N) ").strip().lower() != "y":
                        result = "DENIED by the human operator. Ask them what they want instead."
                    else:
                        result = run_tool(name, args)
                else:
                    result = run_tool(name, args)
                print(f"[{name}] {result[:300]}{'...' if len(result) > 300 else ''}")
                messages.append({"role": "tool", "tool_call_id": c.get("id", name), "content": result})


if __name__ == "__main__":
    main()
