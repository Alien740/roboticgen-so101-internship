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
  renderDraw();
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
  else if (b.classList.contains("draw-level")) answer(`level ${$("#drawSize").value}`);
  else if (b.classList.contains("task-start")) startTask(b);
});
document.addEventListener("keydown", e => {
  if (e.key === "Enter" && e.target.classList?.contains("sess-text")) { answer(e.target.value); e.target.value = ""; }
});
document.addEventListener("input", e => {
  if (e.target.id === "drawSize") { drawSize = +e.target.value; e.target.closest(".slider").querySelector("output").textContent = `${drawSize} mm`; }
});

// ------------------------------------------------------------------ Draw: one step at a time
// The drawing program (framework so101 draw) asks questions; each prompt is mapped to a step with
// plain instructions and only the buttons that make sense there. Answers are the program's own words.
const DRAW_STEPS = ["Set up", "Pen in", "Grip", "Place by hand", "Draw", "Pen back"];
let sketch = [];                                               // the sketch pad's strokes (canvas px)
let drawSize = 80, drawLeveled = false;
const B = (a, label, cls = "") => `<button class="btn ${cls} ans" data-a="${escapeHtml(a)}">${label}</button>`;

function drawStep(prompt) {
  const p = (prompt || "").toLowerCase();
  if (p.includes("across the jaws")) return [1, "Put the pen in the gripper",
    `<p>The gripper is open. Lay the pen <b>across the jaws, like a cigarette</b> – deep between the pads, with the
      <b>tip sticking out 5–7 cm</b>. Hold it there, then press the button: the gripper closes on it.</p>`,
    B("go", "✓ Pen is in – grip it", "primary big") + B("roll -10", "turn gripper ↺") + B("roll 10", "turn gripper ↻")];
  if (p.includes("pen gripped")) return [2, "Check the grip",
    `<p>Let go of the pen and give it a gentle tug. It should not slip.</p>`,
    B("go", "✓ It's firm", "primary big")];
  if (p.includes("hold the arm")) return [3, "Get ready to place the pen",
    `<p>With one hand, <b>hold the robot's forearm</b>. When you press the button the arm goes <b>limp</b>
      (the gripper keeps holding the pen) – don't let it drop.</p>`,
    B("go", "✓ I'm holding the arm – loosen it", "primary big")];
  if (p.includes("move the pen by hand")) return [3, "Place the pen on the paper",
    `<p>Move the arm by hand so the pen points <b>straight down</b> with its <b>tip touching the paper</b> where you
      want the drawing (best near the middle of the sheet). Hold it still and press the button – the robot
      remembers this spot and holds it.</p>`,
    B("go", "✓ Tip is on the paper – keep it here", "primary big")];
  if (p.includes("you can let go of the arm")) return [3, "Got it",
    `<p>The robot is holding the pen now. Let go of the arm, then press the button – the pen lifts off the paper.</p>`,
    B("go", "✓ Let go – lift the pen", "primary big")];
  if (p.includes("straight down") && p.includes("now")) return [3, "Straight down now?",
    `<p>Look at the pen from the front and from the side.</p>`,
    B("go", "✓ Yes, straight down", "primary big") + B("roll -10", "turn ↺ 10°") + B("roll 10", "turn ↻ 10°") + B("roll -3", "↺ 3°") + B("roll 3", "↻ 3°")];
  if (p.includes("straight down")) return [3, "Is the pen pointing straight down?",
    `<p>The pen is now over the drawing spot. Check that the <b>tip</b> (not the cap end) points
      <b>straight down</b> at the paper – look from the front and from the side.</p>`,
    B("go", "✓ Yes, straight down", "primary big") + B("roll -10", "turn ↺ 10°") + B("roll 10", "turn ↻ 10°")
      + B("flip", "The cap end is down – flip it")];
  if (p.includes("gripper opens so you can turn")) return [3, "Flip the pen (1/3)",
    `<p>Hold the pen with your fingers, then press the button – the gripper will open.</p>`, B("go", "✓ I'm holding it", "primary big")];
  if (p.includes("put the pen back")) return [3, "Flip the pen (2/3)",
    `<p>Turn the pen around so the <b>tip</b> is on the other side, put it back across the jaws, then press the button.</p>`,
    B("go", "✓ Pen is back – grip it", "primary big")];
  if (p.includes("let go of the pen")) return [3, "Flip the pen (3/3)",
    `<p>Let go of the pen.</p>`, B("go", "✓ Done", "primary big")];
  if (p.includes("how high")) return [3, "How high is the tip above the paper?",
    `<p>Measure with a ruler (or estimate) the gap between the <b>pen tip</b> and the paper. A rough number is fine –
      next you lower it the last bit by hand.</p>`,
    [40, 60, 80, 100, 120].map(v => B(String(v), `${v} mm`, v === 80 ? "primary" : "")).join("")
      + `<input class="btn sess-text" placeholder="or type mm" style="width:110px"><button class="btn sess-send">Send</button>`];
  if (p.includes("'touch'")) return [drawLeveled ? 4 : 3, drawLeveled ? "Touch the paper (corner)" : "Lower the pen until it just touches",
    `<p>Lower the pen in small steps. Stop when the tip <b>just touches</b> the paper (it may bend very slightly),
      then press <b>Touching now</b>.</p>`,
    B("down 5", "↓ 5 mm") + B("down 3", "↓ 3 mm") + B("down", "↓ 1 mm") + B("up", "↑ 1 mm") + B("up 5", "↑ 5 mm")
      + B("touch", "✓ Touching now", "primary big")];
  if (p.includes("'draw <shape>")) return [4, "Draw!",
    `<p>Pick a shape, or <b>sketch anything</b> in the box and press <b>Draw my sketch</b> – the robot copies it onto
      the paper at the size below. <span class="muted">(Optional: <b>Level paper</b> touches 3 corners first, for a
      sheet that isn't flat.)</span></p>
     <div class="slider"><label for="drawSize">Size</label><div class="track"><input type="range" id="drawSize" min="40" max="120" step="10" value="${drawSize}"></div><output>${drawSize} mm</output></div>
     <div class="sketchbox"><canvas id="sketch" width="300" height="300"></canvas>
       <div class="row wrap"><button class="btn primary sketch-draw">✎ Draw my sketch</button>
         <button class="btn sketch-undo">Undo</button><button class="btn sketch-clear">Clear</button></div>
       <p class="muted">The top of the box is away from the robot.</p></div>`,
    [["circle", "○ Circle"], ["square", "□ Square"], ["star", "☆ Star"], ["smiley", "☺ Smiley"], ["house", "⌂ House"], ["spiral", "@ Spiral"]]
      .map(([k, l]) => `<button class="btn shape" data-s="${k}">${l}</button>`).join("")
      + `<button class="btn draw-level">⌖ Level paper</button>` + B("place", "Place again") + B("quit", "Finish – give the pen back", "warn")];
  if (p.includes("release it")) return [5, "Take the pen back",
    `<p>Hold the pen, then press the button – the gripper opens.</p>`, B("go", "✓ I'm holding it", "primary big")];
  return null;
}

function renderDraw() {
  const t = TASK || {};
  const mine = (t.task || [])[0] === "draw";
  const live = ["starting", "running", "waiting"].includes(t.state) && (mine || t.state === "starting");
  $("#drawSetup").hidden = live;
  $("#drawWizard").hidden = !live;
  if (!live) { drawLeveled = false; return; }
  if ((t.events || []).some(e => e.type === "plane")) drawLeveled = true;
  const st = t.state === "waiting" ? drawStep(t.prompt) : null;
  let idx, html;
  if (st) {
    const [i, title, body, buttons] = st;
    idx = i;
    html = `<h4>${title}</h4>${body}<div class="row wrap">${buttons}</div>`;
  } else {
    idx = Math.max(1, ...[...$$("#drawStepper li.now")].map(li => +li.dataset.i));
    const lastEv = (t.events || []).slice(-1)[0];
    const what = t.state === "starting" ? "Parking the arm and starting the drawing program…"
      : lastEv && lastEv.type === "stroke_done" ? `Drawing… stroke ${lastEv.index} of ${lastEv.of}`
      : t.state === "waiting" ? escapeHtml(t.prompt || "") : "The robot is moving – keep your hands clear.";
    html = `<h4>${t.state === "waiting" ? "Answer below" : "Please wait"}</h4><p>${what}</p>`
      + (t.state === "waiting" ? `<div class="row wrap">${B("go", "go", "primary")}<input class="btn sess-text" placeholder="answer"><button class="btn sess-send">Send</button></div>` : "");
  }
  html += `<div class="row"><button class="btn warn sess-stop">■ Stop</button></div>`;
  $("#drawStepper").innerHTML = DRAW_STEPS.map((n, i) =>
    `<li data-i="${i}" class="${i < idx ? "done" : i === idx ? "now" : ""}">${n}</li>`).join("");
  const card = $("#drawCard");
  if (card.dataset.html !== html) {
    const typed = card.querySelector(".sess-text")?.value || "";
    card.dataset.html = html; card.innerHTML = html;
    const inp = card.querySelector(".sess-text"); if (inp) inp.value = typed;
    paintSketch();
  }
  card.classList.toggle("moving", !st);
}

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


// ------------------------------------------------------------------ sketch pad -> robot drawing
function paintSketch() {
  const c = $("#sketch"); if (!c) return;
  const g = c.getContext("2d");
  g.fillStyle = "#fff"; g.fillRect(0, 0, c.width, c.height);
  g.strokeStyle = "#e3e5e8"; g.lineWidth = 1; g.strokeRect(0.5, 0.5, c.width - 1, c.height - 1);
  g.strokeStyle = "#022F49"; g.lineWidth = 3; g.lineCap = g.lineJoin = "round";
  for (const st of sketch) {
    g.beginPath(); st.forEach(([x, y], i) => (i ? g.lineTo(x, y) : g.moveTo(x, y))); g.stroke();
  }
}
let penDown = null;
document.addEventListener("pointerdown", e => {
  if (e.target.id !== "sketch") return;
  e.preventDefault(); e.target.setPointerCapture(e.pointerId);
  const r = e.target.getBoundingClientRect(), k = e.target.width / r.width;
  penDown = [[(e.clientX - r.left) * k, (e.clientY - r.top) * k]]; sketch.push(penDown); paintSketch();
});
document.addEventListener("pointermove", e => {
  if (!penDown || e.target.id !== "sketch") return;
  const r = e.target.getBoundingClientRect(), k = e.target.width / r.width;
  const p = [(e.clientX - r.left) * k, (e.clientY - r.top) * k], q = penDown[penDown.length - 1];
  if (Math.hypot(p[0] - q[0], p[1] - q[1]) >= 2) { penDown.push(p); paintSketch(); }
});
document.addEventListener("pointerup", () => { penDown = null; });
document.addEventListener("click", async e => {
  const b = e.target.closest("button"); if (!b) return;
  if (b.classList.contains("sketch-clear")) { sketch = []; paintSketch(); }
  else if (b.classList.contains("sketch-undo")) { sketch.pop(); paintSketch(); }
  else if (b.classList.contains("sketch-draw")) {
    const strokes = sketch.filter(st => st.length >= 2)
      .map(st => st.map(([x, y]) => [-(x - 150), -(y - 150)]));     // canvas -> u (left), v (away)
    if (!strokes.length) { toast("Sketch something in the box first"); return; }
    try {
      const r = await api("save_drawing", { name: "sketch", strokes });
      answer(`drawfile ${r.name} ${drawSize}`);
    } catch (_) { /* toast shown */ }
  }
});