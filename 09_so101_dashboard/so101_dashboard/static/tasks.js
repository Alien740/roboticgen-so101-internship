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
  else if (t.state === "ended" && taskError(t)) head = `<span class="bad"><b>${escapeHtml(name)}</b> stopped: ${escapeHtml(taskError(t))}</span>`;
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

// the last "SomethingError: message" a procedure printed, in plain words
function taskError(t) {
  const line = [...(t.output || [])].reverse().find(l => /^\w*(Error|Exception): /.test(l));
  return line ? line.replace(/^\w*(Error|Exception): /, "").replace(/ - run `so101 doctor`$/, "").replace(/ \(\[.*\]\)/, "") : "";
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
  else if (b.classList.contains("draw-now")) window.drawNow && window.drawNow();
  else if (b.classList.contains("draw-level")) answer("level 80");
  else if (b.classList.contains("task-start")) startTask(b);
});
document.addEventListener("keydown", e => {
  if (e.key === "Enter" && e.target.classList?.contains("sess-text")) { answer(e.target.value); e.target.value = ""; }
});

// ------------------------------------------------------------------ Draw: one step at a time
// The drawing program (framework so101 draw) asks questions; each prompt is mapped to a step with
// plain instructions and only the buttons that make sense there. Answers are the program's own words.
const DRAW_STEPS = ["Set up", "Pen in", "Grip", "Place by hand", "Draw", "Pen back"];
let drawLeveled = false;
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
    B("go", "✓ I'm holding the arm – loosen it", "primary big") + B("auto", "Auto – no hands")];
  if (p.includes("pen over the drawing spot")) return [3, "Lower the pen onto the paper",
    `<p>The pen is over the drawing spot. Lower it in small steps until the tip <b>just touches</b> the paper (watch the
      side camera), then press <b>Touching now</b>.</p>`,
    B("down 5", "↓ 5 mm") + B("down 2", "↓ 2 mm") + B("down", "↓ 1 mm") + B("up", "↑ 1 mm") + B("up 5", "↑ 5 mm")
      + B("touch", "✓ Touching now", "primary big")];
  if (p.includes("near a corner")) {
    const n = (p.match(/\((\d) of 4/) || [])[1] || "1";
    return [3, `Touch corner ${n} of 4`,
      `<p>The arm is limp. Move it so the pen tip <b>touches the paper near a corner</b> of the sheet (about 2 cm in
        from the edges), pen as straight down as you can, and press the button. Any corner order is fine.
        <span class="muted">This maps the paper's height so the pen touches evenly everywhere.</span></p>`,
      B("go", `✓ Touching corner ${n}`, "primary big") + B("done", "Skip the corners")];
  }
  if (p.includes("too thin")) return [2, "The pen is too thin",
    `<p>The jaws closed almost completely, so they can't clamp this pen – it would wobble and overload the gripper.
      <b>Wrap tape around the pen</b> where the jaws hold it, until it is about 12 mm thick. Hold the pen and press the
      button – the gripper opens so you can put it back.</p>`,
    B("go", "✓ Holding it – open the gripper", "primary big") + B("ignore", "Draw anyway")];
  if (p.includes("taped pen across")) return [2, "Put the taped pen back",
    `<p>Lay the taped pen across the jaws (tip out 5–7 cm), hold it, then press the button.</p>`,
    B("go", "✓ Pen is in – grip it", "primary big")];
  if (p.includes("tilted about")) return [3, "Centre, pen tilted",
    `<p>Put the pen tip <b>on the centre of the sheet</b> with the pen <b>tilted about 25°</b> (lean it toward you or to the
      side). Hold it still and press the button. <span class="muted">With the next step this measures the pen's length,
      so the tip lands exactly where planned even when the pen leans.</span></p>`,
    B("go", "✓ Tip on the centre, pen tilted", "primary big") + B("skip", "Skip")];
  if (p.includes("same centre spot")) return [3, "Centre, pen straight",
    `<p>Keep the tip on <b>the same spot</b> and stand the pen <b>straight down</b>. Hold it still and press the button –
      the robot remembers this pose and holds it.</p>`,
    B("go", "✓ Straight down on the same spot", "primary big")];
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
  if (p.includes("'draw <shape>")) {
    const c = UI.sel.src === "picture" ? null : UI.drawChoice();
    const what = c ? escapeHtml(c.label) : "the drawing chosen below";
    return [4, "Ready to draw!",
      `<p>The pen is on the paper. Choose what to draw <b>below</b> (shape, sketch or picture) – it is checked and drawn
        straight away. Press <b>Finish</b> when you are done and the robot gives the pen back.</p>`,
      `<button class="btn primary big draw-now">✎ Draw ${what}</button>`
        + `<button class="btn draw-level" title="touch 3 corners first, for a sheet that isn't flat">⌖ Level paper</button>`
        + B("aside", "Move aside – see the paper") + B("place", "Place the pen again") + B("quit", "Finish – give the pen back", "warn")];
  }
  if (p.includes("release it")) return [5, "Take the pen back",
    `<p>Hold the pen, then press the button – the gripper opens.</p>`, B("go", "✓ I'm holding it", "primary big")];
  return null;
}

function renderDraw() {
  const t = TASK || {};
  const mine = (t.task || [])[0] === "draw";
  const live = ["starting", "running", "waiting"].includes(t.state) && (mine || t.state === "starting");
  const menu = live && t.state === "waiting" && (t.prompt || "").includes("'draw <shape>");
  $("#drawSetup").hidden = live && !menu;                    // at the menu: choose the next drawing below
  $("#drawWizard").hidden = !live;
  $("#tab-draw").classList.toggle("at-menu", menu);
  if (!live) {
    drawLeveled = false;
    const end = ((t.events || []).find(e => e.type === "session_end") || {}).t;
    if (mine && t.state === "ended" && taskError(t) && end && renderDraw.seen !== end && Date.now() / 1000 - end < 600) {
      renderDraw.seen = end;
      $("#drawNote").innerHTML = `<span class="bad">The drawing program stopped: ${escapeHtml(taskError(t))}</span>`;
    }
    return;
  }
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
  }
  card.classList.toggle("moving", !st);
}

async function startTask(b) {
  const task = b.dataset.task, options = {};
  if (b.dataset.trials) options["--trials"] = +$("#taskTrials").value || 1;
  if (b.dataset.hover) options["--hover"] = true;
  if (task === "calibrate-hsv") options["--color"] = $("#hsvColor").value;
  const moves = !["report", "demo"].includes(task);
  if (moves && S && S.mode === "real" && !twoStep(b, "Parks the arm – click again to start")) return;
  try { await api("task", { op: "start", task, options }); toast(`Starting ${task}…`); } catch (_) { return; }
  setTimeout(pollTask, 300);
}

// ------------------------------------------------------------------ motions & recordings
async function loadLibrary() {
  let lib;
  try { lib = await (await fetch("/api/library", { headers: HDR })).json(); } catch (_) { return; }
  const ICON = { hello: "👋", yes: "🙂", no: "🙅", dance: "💃", bow: "🙇", chomp: "🦖" };
  $("#motionButtons").innerHTML = Object.entries(lib.motions)
    .map(([k, label]) => `<button class="btn motion" data-motion="1" data-m="${k}"><i>${ICON[k] || "✨"}</i>${escapeHtml(label)}</button>`).join("");
  $$("#motionButtons .motion").forEach(b => (b.onclick = () =>
    api("motion", { name: b.dataset.m, speed: +$("#motionSpeed").value }).catch(() => {})));
  $("#recList").innerHTML = lib.recordings.length
    ? lib.recordings.map(r => `<div class="item"><b>${escapeHtml(r.name)}</b><span>${r.seconds.toFixed(1)} s</span>
        <button class="btn primary rec-play" data-motion="1" data-n="${escapeHtml(r.name)}"><svg class="ic"><use href="#i-play"/></svg>Play</button>
        <button class="btn ghost rec-del" data-n="${escapeHtml(r.name)}">Delete</button></div>`).join("")
    : `<div class="empty">No recordings yet – press Record, move the arm, press Stop.</div>`;
  $$("#recList .rec-play").forEach(b => (b.onclick = () =>
    api("play", { name: b.dataset.n, speed: +$("#recSpeed").value }).catch(() => {})));
  $$("#recList .rec-del").forEach(b => (b.onclick = async () => {
    if (!twoStep(b, "Sure? Click again")) return;
    await api("delete_recording", { name: b.dataset.n }).catch(() => {}); loadLibrary();
  }));
  if (typeof render === "function" && S) render();               // grey out play buttons when the arm can't move
}

$("#motionStop").onclick = () => {                             // stop = hold where it is now
  if (S && S.measured) api("set", { joints: S.measured, source: "stop" }).catch(() => {});
};
let recByHand = false;
$("#recBtn").onclick = async () => {
  if (S && S.recording !== null && S.recording !== undefined) {
    try {
      const r = await api("record", { op: "stop", name: $("#recName").value });
      toast(`Saved "${r.name}" (${r.seconds.toFixed(1)} s, moved ${r.moved_deg}°)`); $("#recName").value = ""; loadLibrary();
    } catch (_) { /* toast shown */ }
    if (recByHand && S && !S.torque) api("torque", { on: true }).catch(() => {});   // hold where it was left
    recByHand = false;
  } else {
    if (!S || S.state !== "ready") { toast("Connect the arm first"); return; }
    recByHand = $("#recByHand").checked;
    if (recByHand) {
      if (!twoStep($("#recBtn"), "Hold the arm, click again")) return;
      try { if (S.torque) await api("torque", { on: false }); } catch (_) { return; }
    }
    api("record", { op: "start" }).catch(() => {});
  }
};

// live bits from the 20 Hz state (S): recording timer, what is playing
setInterval(() => {
  if (!S) return;
  const rec = S.recording !== null && S.recording !== undefined;
  if ($("#recBtn").dataset.armed !== "1") {                  // leave the "click again" prompt alone
    $("#recBtn").innerHTML = rec ? "■<br>Stop &amp; save" : "●<br>Record";
    $("#recBtn").classList.toggle("rec", rec);
  }
  const st = $("#recState");
  st.textContent = rec ? `● Recording… ${S.recording.toFixed(1)} s` : S.state === "ready" ? "Ready to record." : "Connect the arm to record.";
  st.classList.toggle("on", rec);
  const prog = S.playing ? S.playing.progress : S.traj ? S.traj.progress : 0;
  $("#playBar").style.width = `${Math.round((prog || 0) * 100)}%`;
  $("#playState").textContent = S.playing ? `Playing ${S.playing.name} – ${Math.round(S.playing.progress * 100)} %`
    : S.traj ? `Moving: ${S.traj.name}` : "Nothing playing.";
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
  // a framework procedure (the AI's own pen drawing) has the arm while it runs: the chat still talks to it
  const procedure = TASK && ["starting", "running", "waiting"].includes(TASK.state);
  if ((!S || S.state !== "ready") && !procedure) { toast("Connect the arm (or the simulated one) first"); return; }
  try { await api("ai", { op: "ask", text }); $("#aiText").value = ""; } catch (_) { return; }
  pollAI();
}
$("#aiSend").onclick = () => aiSend();
$("#aiText").addEventListener("keydown", e => { if (e.key === "Enter") aiSend(); });
$("#aiStop").onclick = () => api("ai", { op: "stop" }).catch(() => {});
$("#aiReset").onclick = () => api("ai", { op: "reset" }).then(pollAI).catch(() => {});
$$("#aiExamples .ai-ex").forEach(b => (b.onclick = () => aiSend(b.textContent)));
$('.tab[data-tab="ai"]').addEventListener("click", pollAI);
