// Draw / Tasks / Calibrate: run the framework's own procedures (server: tasks.py) and answer their
// prompts. Motions / Record: play built-in motions, record and replay movements (controller playback).
// Uses app.js globals: $, $$, api, toast, escapeHtml, HDR, S.
"use strict";

const TASK_POLL_MS = 700;
let TASK = null, taskTimer = null;

// ------------------------------------------------------------------ framework sessions
async function pollTask() {
  try {
    TASK = await (await fetch("/api/task", { headers: HDR })).json();
  } catch (_) { return; }
  renderSession();
  const live = ["starting", "running", "waiting"].includes(TASK.state);
  clearTimeout(taskTimer);
  taskTimer = setTimeout(pollTask, live ? TASK_POLL_MS : 3000);
}

function renderSession() {
  const t = TASK || {};
  const live = ["starting", "running", "waiting"].includes(t.state);
  const waiting = t.state === "waiting";
  const name = (t.task || []).join(" ");
  let head;
  if (t.state === "starting") head = "Parking the arm and starting…";
  else if (waiting) head = `<b>${escapeHtml(t.prompt || "")}</b>`;
  else if (t.state === "running") head = `Running <b>${escapeHtml(name)}</b>…`;
  else if (t.state === "ended") head = `Finished: <b>${escapeHtml(name)}</b>${t.exit_code ? ` (exit ${t.exit_code})` : ""}. Reconnect the arm at the top when you need it.`;
  else head = "No procedure running.";
  if (t.error) head += `<br><span class="bad">${escapeHtml(t.error)}</span>`;
  const opts = (t.options || []).filter(o => !o.includes("<"));
  const html = `
    <div class="prompt ${waiting ? "waiting" : ""}">${head}</div>
    <div class="row wrap">
      ${opts.map(o => `<button class="btn ${o === "go" ? "primary" : ""} sess-opt" data-a="${escapeHtml(o)}" ${waiting ? "" : "disabled"}>${escapeHtml(o)}</button>`).join("")}
      <input class="btn sess-text" placeholder="answer, e.g. 85 or roll 10" ${waiting ? "" : "disabled"} style="flex:1;min-width:140px">
      <button class="btn sess-send" ${waiting ? "" : "disabled"}>Send</button>
      <button class="btn warn sess-stop" ${live ? "" : "disabled"}>Stop</button>
    </div>
    ${(t.output || []).length ? `<pre class="console">${escapeHtml(t.output.join("\n"))}</pre>` : ""}`;
  $$(".session").forEach(el => {
    const typed = el.querySelector(".sess-text")?.value || "";
    if (el.dataset.html === html) return;                     // keep focus / typed text
    el.dataset.html = html;
    el.innerHTML = html;
    el.querySelector(".sess-text").value = typed;
    const pre = el.querySelector(".console"); if (pre) pre.scrollTop = pre.scrollHeight;
  });
  $$(".task-start").forEach(b => (b.disabled = live));
  $$(".drawpad button, .drawpad input").forEach(b => (b.disabled = !waiting || (TASK.task || [])[0] !== "draw"));
}

async function answer(text) {
  text = String(text).trim();
  if (!text) return;
  try { await api("task", { op: "answer", text }); } catch (_) { /* toast shown */ }
  setTimeout(pollTask, 300);
}

document.addEventListener("click", e => {
  const b = e.target.closest("button");
  if (!b) return;
  if (b.classList.contains("sess-opt") || b.classList.contains("ans")) answer(b.dataset.a);
  else if (b.classList.contains("sess-send")) {
    const inp = b.parentElement.querySelector(".sess-text"); answer(inp.value); inp.value = "";
  } else if (b.classList.contains("sess-stop")) api("task", { op: "stop" }).catch(() => {}).finally(() => setTimeout(pollTask, 300));
  else if (b.classList.contains("shape")) answer(`draw ${b.dataset.s} ${$("#drawSize").value}`);
  else if (b.classList.contains("task-start")) startTask(b);
});
document.addEventListener("keydown", e => {
  if (e.key === "Enter" && e.target.classList?.contains("sess-text")) { answer(e.target.value); e.target.value = ""; }
});
$("#drawLevel").onclick = () => answer(`level ${$("#drawSize").value}`);
$("#drawSize").oninput = e => (e.target.closest(".slider").querySelector("output").textContent = `${e.target.value} mm`);

async function startTask(b) {
  const task = b.dataset.task, options = {};
  if (b.dataset.trials) options["--trials"] = +$("#taskTrials").value || 1;
  if (b.dataset.hover) options["--hover"] = true;
  if (task === "calibrate-hsv") options["--color"] = $("#hsvColor").value;
  const moves = !["report", "demo"].includes(task);
  if (moves && S && S.mode === "real" && !confirm("The dashboard parks the arm and hands it to this procedure. Continue?")) return;
  try { await api("task", { op: "start", task, options }); toast(`Starting ${task}…`); } catch (_) { return; }
  setTimeout(pollTask, 300);
}

// ------------------------------------------------------------------ motions & recordings
async function loadLibrary() {
  let lib;
  try { lib = await (await fetch("/api/library", { headers: HDR })).json(); } catch (_) { return; }
  $("#motionButtons").innerHTML = Object.entries(lib.motions)
    .map(([k, label]) => `<button class="btn motion" data-m="${k}">${escapeHtml(label)}</button>`).join("");
  $$("#motionButtons .motion").forEach(b => (b.onclick = () =>
    api("motion", { name: b.dataset.m, speed: +$("#motionSpeed").value }).catch(() => {})));
  $("#recList").innerHTML = lib.recordings.length
    ? lib.recordings.map(r => `<tr><td>${escapeHtml(r.name)}</td><td>${r.seconds.toFixed(1)} s</td>
        <td><button class="btn primary rec-play" data-n="${escapeHtml(r.name)}">▶ Play</button>
        <button class="btn rec-del" data-n="${escapeHtml(r.name)}">Delete</button></td></tr>`).join("")
    : `<tr><td class="muted">No recordings yet.</td></tr>`;
  $$("#recList .rec-play").forEach(b => (b.onclick = () =>
    api("play", { name: b.dataset.n, speed: +$("#recSpeed").value }).catch(() => {})));
  $$("#recList .rec-del").forEach(b => (b.onclick = async () => {
    if (!confirm(`Delete recording "${b.dataset.n}"?`)) return;
    await api("delete_recording", { name: b.dataset.n }).catch(() => {}); loadLibrary();
  }));
}

$("#motionStop").onclick = () => {                             // stop = hold where it is now
  if (S && S.measured) api("set", { joints: S.measured, source: "stop" }).catch(() => {});
};
$("#recBtn").onclick = async () => {
  if (S && S.recording !== null && S.recording !== undefined) {
    try {
      const r = await api("record", { op: "stop", name: $("#recName").value });
      toast(`Saved "${r.name}" (${r.seconds.toFixed(1)} s)`); $("#recName").value = ""; loadLibrary();
    } catch (_) { /* toast shown */ }
  } else {
    api("record", { op: "start" }).catch(() => {});
  }
};

// live bits from the 20 Hz state (S): recording timer, what is playing
setInterval(() => {
  if (!S) return;
  const rec = S.recording !== null && S.recording !== undefined;
  $("#recBtn").textContent = rec ? "■ Stop & save" : "● Record";
  $("#recBtn").classList.toggle("warn", rec);
  $("#recState").textContent = rec ? `recording… ${S.recording.toFixed(1)} s` : "";
  $("#playState").textContent = S.playing ? `playing ${S.playing.name} – ${Math.round(S.playing.progress * 100)} %`
    : S.traj ? `moving: ${S.traj.name}` : "";
}, 200);

$$('.tab[data-tab="motions"], .tab[data-tab="record"]').forEach(t => t.addEventListener("click", loadLibrary));
$$('.tab[data-tab="draw"], .tab[data-tab="tasks"], .tab[data-tab="calibrate"]').forEach(t => t.addEventListener("click", pollTask));
loadLibrary();
pollTask();

// ------------------------------------------------------------------ AI (Claude plans with the controller's tools)
let aiTimer = null, aiSeen = "";
async function pollAI() {
  let st;
  try { st = await (await fetch("/api/ai", { headers: HDR })).json(); } catch (_) { return; }
  $("#aiProblem").hidden = false;
  $("#aiProblem").textContent = st.problem || (st.backend === "claude-code"
    ? "Using Claude Code with your claude.ai login (it may only run the robot command)."
    : "Using the Anthropic API key.");
  const html = st.log.map(m => `<div class="m ${m.role}">${escapeHtml(m.text)}</div>`).join("")
    + (st.busy ? `<div class="m busy">working…</div>` : "");
  if (html !== aiSeen) { aiSeen = html; $("#aiLog").innerHTML = html || `<div class="m busy">Say what the arm should do.</div>`; $("#aiLog").scrollTop = 1e9; }
  $("#aiSend").disabled = st.busy; $("#aiStop").disabled = !st.busy;
  clearTimeout(aiTimer);
  if (!$("#tab-ai").hidden || st.busy) aiTimer = setTimeout(pollAI, st.busy ? 600 : 2500);
}
async function aiSend(text) {
  text = (text ?? $("#aiText").value).trim();
  if (!text) return;
  if (!S || S.state !== "ready") { toast("Connect the arm (or the simulated one) first"); return; }
  try { await api("ai", { op: "ask", text }); $("#aiText").value = ""; } catch (_) { return; }
  pollAI();
}
$("#aiSend").onclick = () => aiSend();
$("#aiText").addEventListener("keydown", e => { if (e.key === "Enter") aiSend(); });
$("#aiStop").onclick = () => api("ai", { op: "stop" }).catch(() => {});
$("#aiReset").onclick = () => api("ai", { op: "reset" }).then(pollAI).catch(() => {});
$$("#aiExamples .ai-ex").forEach(b => (b.onclick = () => aiSend(b.textContent)));
$('.tab[data-tab="ai"]').addEventListener("click", pollAI);
