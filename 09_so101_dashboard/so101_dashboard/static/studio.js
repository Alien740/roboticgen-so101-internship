// Cameras, "Draw a picture" and the 3D dry run (server: drawing.py).
//  - two live camera tiles (any cameras this browser sees: overhead webcam, laptop camera, phone)
//  - picture -> strokes (server vectorize) -> preview with stats
//  - Simulate: the framework plans the whole drawing without the arm; the twin plays it (faster
//    than real time) and the ink appears where the pen would draw
//  - Draw it: saves the strokes and runs them through the draw program (`drawfile picture <size>`),
//    starting the program first if needed; while it draws, the preview and the twin follow along
// Uses app.js / tasks.js globals: $, $$, api, toast, HDR, S, twin, twoStep, TASK, answer, pollTask.
"use strict";

const store = {
  get: k => { try { return localStorage.getItem(k); } catch (_) { return null; } },
  set: (k, v) => { try { localStorage.setItem(k, v); } catch (_) { /* private mode */ } },
};

// ------------------------------------------------------------------ cameras
const CAMS = $$(".cam");
async function listCams() {
  let devs = [];
  try { devs = (await navigator.mediaDevices.enumerateDevices()).filter(d => d.kind === "videoinput"); } catch (_) { return; }
  CAMS.forEach((fig, i) => {
    const sel = fig.querySelector(".cam-dev"), cur = sel.value || store.get(`cam${i}`) || "";
    sel.innerHTML = `<option value="">camera…</option>` + devs.map((d, n) =>
      `<option value="${d.deviceId}">${escapeHtml(d.label || `camera ${n + 1}`)}</option>`).join("");
    if (devs.some(d => d.deviceId === cur)) sel.value = cur;
    else if (!cur && devs.length) {                // first time: overhead webcam left, built-in camera right
      const builtIn = d => /facetime|built-in|macbook|integrated/i.test(d.label);
      const pick = i === 0 ? devs.find(d => !builtIn(d)) || devs[0] : devs.find(builtIn) || devs[1];
      if (pick) sel.value = pick.deviceId;
    }
  });
}
async function startCam(fig, i) {
  const sel = fig.querySelector(".cam-dev"), video = fig.querySelector("video");
  stopCam(fig);
  try {
    video.srcObject = await navigator.mediaDevices.getUserMedia({ audio: false, video: {
      width: { ideal: 1280 }, height: { ideal: 720 }, ...(sel.value ? { deviceId: { exact: sel.value } } : {}) } });
    await video.play();
    fig.classList.add("live");
    fig.querySelector(".cam-go").textContent = "Stop";
    store.set(`cam${i}`, sel.value); store.set(`cam${i}on`, "1");
    listCams();                                    // labels appear once a camera was allowed
  } catch (e) { toast(`Camera: ${e.message || e.name}`); }
}
function stopCam(fig) {
  const video = fig.querySelector("video");
  (video.srcObject?.getTracks() || []).forEach(t => t.stop());
  video.srcObject = null;
  fig.classList.remove("live");
  fig.querySelector(".cam-go").textContent = "Start";
}
CAMS.forEach((fig, i) => {
  fig.querySelector(".cam-go").onclick = () => {
    if (fig.classList.contains("live")) { stopCam(fig); store.set(`cam${i}on`, ""); } else startCam(fig, i);
  };
  fig.querySelector(".cam-dev").onchange = () => startCam(fig, i);
});
listCams().then(() => CAMS.forEach((fig, i) => { if (store.get(`cam${i}on`)) startCam(fig, i); }));
navigator.mediaDevices?.addEventListener?.("devicechange", listCams);

$("#btnShow").onclick = () => { document.body.classList.toggle("show"); store.set("show", document.body.classList.contains("show") ? "1" : ""); };
if (store.get("show")) document.body.classList.add("show");

// ------------------------------------------------------------------ picture -> strokes
let PIC = null, VEC = null, PLAN = null, CHECKED = false;
// the drawing waiting for the program's menu; kept across a page reload so it is never lost
let PENDING = (() => { try { return JSON.parse(store.get("drawPending") || "null"); } catch (_) { return null; } })();
const setPending = d => { PENDING = d && { ...d, at: Date.now() }; store.set("drawPending", d ? JSON.stringify(PENDING) : ""); };
try { const v = JSON.parse(store.get("picVec") || "null"); if (v && v.uv) VEC = v; } catch (_) { /* none saved */ }
const picImg = new Image();
let picMode = "lines", vecTimer = null;

function setPicture(dataUrl) {
  PIC = dataUrl; PLAN = null; picImg.src = dataUrl;
  if (UI.sel.src !== "picture") UI.setSrc("picture");
  stopSim(); clearOverlays();
  vectorizeSoon(0);
}
$("#picFile").onchange = e => { const f = e.target.files[0]; if (f) readFile(f); e.target.value = ""; };
function readFile(f) {
  if (!f.type.startsWith("image/")) { toast("That is not a picture"); return; }
  const r = new FileReader(); r.onload = () => setPicture(r.result); r.readAsDataURL(f);
}
const drop = $("#picDrop");
drop.addEventListener("dragover", e => { e.preventDefault(); drop.classList.add("over"); });
drop.addEventListener("dragleave", () => drop.classList.remove("over"));
drop.addEventListener("drop", e => { e.preventDefault(); drop.classList.remove("over"); const f = e.dataTransfer.files[0]; if (f) readFile(f); });
document.addEventListener("paste", e => {
  if (e.target.matches?.("input, textarea")) return;
  const it = [...(e.clipboardData?.items || [])].find(x => x.type.startsWith("image/"));
  if (it) { readFile(it.getAsFile()); $('.tab[data-tab="draw"]')?.click(); }
});
$$("#picMode button").forEach(b => (b.onclick = () => {
  $$("#picMode button").forEach(x => x.classList.toggle("on", x === b)); picMode = b.dataset.m; vectorizeSoon(0);
}));
$("#picDetail").oninput = () => vectorizeSoon(350);
$("#picSize").oninput = () => { $("#picSizeOut").textContent = `${$("#picSize").value} mm`; PLAN = null; vectorizeSoon(350); };
const fill = () => $("#picFill").checked;
const sizeNow = () => (fill() ? (PLAN && PLAN.size_mm) || 160 : +$("#picSize").value);
$("#picFill").onchange = () => {
  $("#picSize").disabled = fill();
  $("#picSizeOut").textContent = fill() ? "auto" : `${$("#picSize").value} mm`;
  PLAN = null; vectorizeSoon(0);
};
$("#picShowImg").onchange = () => paintPic();
$$("#picSpeed button").forEach(b => (b.onclick = () => {
  $$("#picSpeed button").forEach(x => x.classList.toggle("on", x === b)); if (SIM) SIM.x = +b.dataset.x;
}));

function vectorizeSoon(ms) {
  if (!PIC) return;
  clearTimeout(vecTimer);
  vecTimer = setTimeout(async () => {
    $("#picStats").textContent = "finding the lines…";
    try {
      VEC = await api("vectorize", { image: PIC, mode: picMode, detail: +$("#picDetail").value, size_mm: sizeNow() });
    } catch (_) { $("#picStats").textContent = ""; return; }
    PLAN = null; CHECKED = false; stopSim(); clearOverlays();
    try { store.set("picVec", JSON.stringify(VEC)); } catch (_) { /* too big to keep: fine */ }
    $("#picView").hidden = false;
    paintPic();
    const st = VEC.stats, mmss = s => `${Math.floor(s / 60)}:${String(Math.round(s % 60)).padStart(2, "0")}`;
    $("#picStats").innerHTML = `<b>${st.strokes}</b> strokes · <b>${st.ink_mm}</b> mm of ink · ${st.travel_mm} mm pen-up travel · `
      + `about <b>${mmss(st.est_s)}</b> to draw at ${sizeNow()} mm` + (fill() ? " (the size is set to fill the sheet when planned)" : "")
      + (st.strokes > 120 ? ` <span class="bad">– a lot of strokes: less detail or "Sketch" is quicker</span>` : "");
    note("Press <b>Check in 3D</b> to see every stroke the way the robot will draw it.");
    drawFlow();
  }, ms);
}
const note = html => ($("#drawNote").innerHTML = html);
// what the Draw tab would draw now: a shape or sketch (ui.js) or the picture
function current() {
  if (UI.sel.src !== "picture") return UI.drawChoice();
  if (!VEC) return null;
  return { name: "picture", label: "your picture", uv: VEC.uv, size: +$("#picSize").value, fill: fill(), picture: true };
}
window.onDrawChange = () => {
  PLAN = null; CHECKED = false; stopSim(); clearOverlays();
  const c = current();
  note(c ? `Ready: <b>${escapeHtml(c.label)}</b>. Check it in 3D, or draw it straight away.`
    : UI.sel.src === "sketch" ? "Sketch something in the box first." : "Choose a picture first.");
  drawFlow();
};
// the 1-2-3 strip above the Draw tab
function drawFlow() {
  const t = TASK || {}, live = ["starting", "running", "waiting"].includes(t.state) && (t.task || [])[0] === "draw";
  const step = live ? 3 : CHECKED ? 3 : current() ? 2 : 1;
  $$("#drawFlow li").forEach(li => { const f = +li.dataset.f; li.className = f < step ? "done" : f === step ? "now" : ""; });
  if (!$("#picSim .spin")) $("#picSim").disabled = !current();      // not while it is planning
  $("#picDraw").disabled = !current();
}

// progress: strokes before `done` are drawn, `cur` is being drawn (0-based)
let PROG = { done: 0, cur: -1 };
function paintPic() {
  if (!VEC || UI.sel.src !== "picture") return;
  const c = $("#picCanvas"), g = c.getContext("2d"), k = 600 / Math.max(VEC.width, VEC.height);
  c.width = Math.round(VEC.width * k); c.height = Math.round(VEC.height * k);
  g.fillStyle = "#fff"; g.fillRect(0, 0, c.width, c.height);
  if ($("#picShowImg").checked && picImg.complete) { g.globalAlpha = 0.18; g.drawImage(picImg, 0, 0, c.width, c.height); g.globalAlpha = 1; }
  g.lineCap = g.lineJoin = "round";
  const started = PROG.cur >= 0 || PROG.done > 0;
  VEC.strokes.forEach((st, i) => {
    g.strokeStyle = !started ? "#022F49" : i < PROG.done ? "#022F49" : i === PROG.cur ? "#d92d20" : "#c9ccd1";
    g.lineWidth = i === PROG.cur ? 3.5 : 2;
    g.beginPath(); st.forEach(([x, y], j) => (j ? g.lineTo(x * k, y * k) : g.moveTo(x * k, y * k))); g.stroke();
  });
}

// ------------------------------------------------------------------ 3D dry run
let SIM = null;
const INK = [0.01, 0.12, 0.29], PLANNED = [0.55, 0.78, 0.86], PAPER = [0.92, 0.92, 0.92], AREA = [0.85, 0.65, 0.1], PEN = [0.99, 0.72, 0.07];
function clearOverlays() { if (twin) ["plan", "ink", "paper", "area", "pen"].forEach(n => twin.setOverlay(n, null)); }
function showSheet(p) { twin.setOverlay("paper", p.paper, PAPER); twin.setOverlay("area", p.areaLines, AREA); twin.setOverlay("plan", p.segs, PLANNED); }
// the pen (not in the 3D model): a vertical bundle of lines from the tip up, drawn thick
function penAt(x, y, z) {
  const out = [], L = 0.11;
  for (const [dx, dy] of [[0, 0], [0.0015, 0], [-0.0015, 0], [0, 0.0015], [0, -0.0015], [0.001, 0.001], [-0.001, -0.001]])
    out.push(x + dx, y + dy, z, x + dx, y + dy, z + L);
  return out;
}
function tipAt(p, t) {
  let lo = 0, hi = p.tips.length - 1;
  while (lo < hi) { const m = (lo + hi + 1) >> 1; if (p.tips[m][0] <= t) lo = m; else hi = m - 1; }
  const a = p.tips[lo], b = p.tips[Math.min(lo + 1, p.tips.length - 1)];
  const s = b[0] > a[0] ? Math.min(1, (t - a[0]) / (b[0] - a[0])) : 0;
  return [2, 3, 4].map(i => a[i] + (b[i] - a[i]) * s);
}

function prepPlan(p) {
  // ink segments (pen down, same stroke) in time order, and the planned drawing
  const segs = [], segT = [];
  for (let i = 1; i < p.tips.length; i++) {
    const a = p.tips[i - 1], b = p.tips[i];
    if (a[1] && b[1] && a[5] === b[5]) { segs.push(a[2], a[3], a[4] + 0.0006, b[2], b[3], b[4] + 0.0006); segT.push(b[0]); }
  }
  p.segs = segs; p.segT = segT;
  // the A4 sheet and the drawing's area, as the server placed them (lifted a little above the paper)
  const loop = C => C.flatMap((q, i) => [q[0], q[1], q[2] + 0.0003, C[(i + 1) % 4][0], C[(i + 1) % 4][1], C[(i + 1) % 4][2] + 0.0003]);
  p.paper = loop(p.sheet); p.areaLines = loop(p.area);
  return p;
}
function inkUntil(p, t) {
  let lo = 0, hi = p.segT.length;                  // number of segments with time <= t
  while (lo < hi) { const m = (lo + hi) >> 1; if (p.segT[m] <= t) lo = m + 1; else hi = m; }
  return p.segs.slice(0, lo * 6);
}
function framesAt(p, t) {
  const K = p.keys;
  let lo = 0, hi = K.length - 1;
  if (t <= K[0].t) return K[0].f;
  if (t >= K[hi].t) return K[hi].f;
  while (hi - lo > 1) { const m = (lo + hi) >> 1; if (K[m].t <= t) lo = m; else hi = m; }
  const a = K[lo], b = K[hi], s = (t - a.t) / Math.max(1e-6, b.t - a.t), out = {};
  for (const n in a.f) { const A = a.f[n], B = b.f[n] || A; out[n] = A.map((v, i) => v + (B[i] - v) * s); }
  return out;
}
function strokeAt(p, t) {
  let lo = 0, hi = p.tips.length - 1;
  while (lo < hi) { const m = (lo + hi + 1) >> 1; if (p.tips[m][0] <= t) lo = m; else hi = m - 1; }
  return p.tips[lo] ? p.tips[lo][5] : 0;
}
const mmss = s => `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, "0")}`;
function hud(html, real) {
  const el = $("#simHud");
  el.hidden = !html; el.classList.toggle("real", !!real);
  if (html) el.innerHTML = `<span class="dot"></span>${html}`;
}

async function getPlan() {
  if (PLAN) return PLAN;
  const c = current();
  if (!c) throw new Error("nothing chosen");
  note(c.fill ? "Finding the biggest size that fits the sheet with the pen upright, then planning every stroke (≈10 s)…"
    : "Planning every stroke the way the robot will move (a few seconds)…");
  const r = await api("draw_plan", { strokes: c.uv, size_mm: c.size, fill: c.fill });
  PLAN = prepPlan(r.plan);
  PLAN.choice = c;
  if (c.fill) $("#picSizeOut").textContent = `${PLAN.drawn_mm[0]} × ${PLAN.drawn_mm[1]} mm`;
  note(`<b>${escapeHtml(c.label)}</b>: ${PLAN.drawn_mm[0]} × ${PLAN.drawn_mm[1]} mm on the A4 sheet. `
    + (PLAN.ok ? `✓ Every stroke is reachable – about <b>${mmss(PLAN.seconds)}</b> of drawing.`
    : `<span class="bad">✗ ${escapeHtml(PLAN.error)}</span> – make it smaller.`)
    + (PLAN.placed ? "" : " <span class=\"muted\">(Planned at the default spot: after you place the pen by hand it uses that spot.)</span>"));
  return PLAN;
}

async function simulate() {
  if (SIM) { stopSim(); return; }
  if (!current() || !twin) return;
  let p;
  const btn = $("#picSim");
  if (!PLAN) { btn.disabled = true; btn.innerHTML = `<span class="spin"></span>Planning…`; }
  try { p = await getPlan(); } catch (_) { return; }
  finally { btn.disabled = false; btn.innerHTML = `<svg class="ic"><use href="#i-play"/></svg>Check in 3D`; }
  const n = p.choice.uv.length;
  if (!p.keys.length) return;
  window.SIM_LOCK = true;
  $("#twinMsg").textContent = "";
  twin.focus([p.center[0], p.center[1], p.paper_z + 0.05], 0.55);
  showSheet(p);
  SIM = { t0: performance.now(), t: 0, x: +$("#picSpeed button.on").dataset.x, last: performance.now() };
  $("#picSim").innerHTML = `<svg class="ic"><use href="#i-stop"/></svg>Stop`;
  const step = now => {
    if (!SIM) return;
    SIM.t += (now - SIM.last) / 1000 * SIM.x; SIM.last = now;
    const t = Math.min(SIM.t, p.seconds);
    twin.setFrames(framesAt(p, t));
    twin.setOverlay("ink", inkUntil(p, t), INK);
    twin.setOverlay("pen", penAt(...tipAt(p, t)), PEN);
    const k = strokeAt(p, t);
    if (k !== PROG.cur) { PROG = { done: k, cur: k }; paintPic(); }
    hud(`3D CHECK ${SIM.x}× · stroke ${k + 1}/${n} · ${mmss(t)} / ${mmss(p.seconds)}`);
    if (t >= p.seconds) { PROG = { done: n, cur: -1 }; paintPic(); CHECKED = p.ok; drawFlow(); finishSim(); return; }
    requestAnimationFrame(step);
  };
  requestAnimationFrame(step);
}
function finishSim() {
  SIM = null;
  $("#picSim").innerHTML = `<svg class="ic"><use href="#i-play"/></svg>Check in 3D`;
  hud(PLAN && PLAN.ok && CHECKED ? "3D CHECK done ✓ – ready to draw" : "");
  setTimeout(() => { if (!SIM) { window.SIM_LOCK = false; hud(""); twin && (twin.setOverlay("pen", null), twin.focus(null)); } }, 2500);
}
function stopSim() {
  if (!SIM) return;
  finishSim(); window.SIM_LOCK = false; hud(""); PROG = { done: 0, cur: -1 }; paintPic();
}
$("#picSim").onclick = simulate;

// ------------------------------------------------------------------ draw it for real
const atMenu = t => t && t.state === "waiting" && (t.task || [])[0] === "draw" && (t.prompt || "").includes("'draw <shape>");
let SENT = "", sending = false;
async function sendDrawing(c) {
  if (sending) return;
  sending = true;
  try {
    PLAN = null;                                   // plan again: the pen was just placed (new spot, paper map)
    const keepSrc = UI.sel.src;
    const r = await api("draw_plan", { strokes: c.uv, size_mm: c.size, fill: c.fill });
    const p = prepPlan(r.plan); p.choice = c;
    if (UI.sel.src === keepSrc) PLAN = p;
    if (!p.ok) { setPending(null); toast("Not drawing: " + p.error); note(`<span class="bad">✗ ${escapeHtml(p.error)}</span> – make it smaller.`); return; }
    const s = await api("save_drawing", { name: c.name, strokes: c.uv, size_mm: p.size_mm, offset_mm: p.offset_mm });
    if (twin) { showSheet(p); twin.setOverlay("ink", null); }
    SENT = s.name; DRAWPLAN = p;
    setPending(null);
    answer(`drawfile ${s.name} ${p.size_mm}`);
    toast(`Drawing ${c.label} – watch the cameras`);
  } catch (_) { setPending(null); }
  finally { sending = false; }
}
window.drawNow = () => $("#picDraw").click();
$("#picDraw").onclick = async () => {
  const c = current();
  if (!c) { toast(UI.sel.src === "sketch" ? "Sketch something first" : "Choose a picture first"); return; }
  stopSim();
  const t = TASK || {};
  const live = ["starting", "running", "waiting"].includes(t.state);
  if (atMenu(t)) { await sendDrawing(c); return; }
  if (live && (t.task || [])[0] !== "draw") { toast("Another procedure is running – stop it first"); return; }
  if (!live && S && S.mode === "sim") {
    note("Drawing on paper needs the <b>real arm</b> (plugged in by USB). The simulated arm can only <b>Check in 3D</b>.");
    toast("Connect the real arm to draw on paper"); return;
  }
  if (!live && S && S.mode === "real" && !twoStep($("#picDraw"), "Parks the arm and starts drawing – click again")) return;
  setPending(c);
  try {
    if (!live) { await api("task", { op: "start", task: "draw" }); setTimeout(pollTask, 300); }
  } catch (_) { setPending(null); return; }
  note(`<b>Next:</b> follow the steps – pen in, grip, place it on the paper. When the robot is ready to draw, <b>${escapeHtml(c.label)}</b> starts by itself.`);
  drawFlow();
};

// follow the real drawing: send the pending picture at the menu; highlight strokes; HUD; ink in 3D.
// The task reports its last events only, so "which drawing is running" is remembered here.
let drawT0 = 0, drawingMine = false, lastEvT = 0, DRAWPLAN = null;
setInterval(() => {
  const t = TASK;
  drawFlow();
  if (PENDING && atMenu(t)) sendDrawing(PENDING);
  // the program stopped before it reached the menu (only once it has run since the click)
  if (PENDING && t && ["starting", "running", "waiting"].includes(t.state)) PENDING.live = true;
  if (PENDING && t && ["ended", "idle"].includes(t.state) && (PENDING.live || Date.now() - (PENDING.at || 0) > 60000)) setPending(null);
  if (!t) return;
  const fresh = (t.events || []).filter(e => (e.t || 0) > lastEvT);
  if (fresh.length) lastEvT = Math.max(...fresh.map(e => e.t || 0));
  let st = null;
  for (const e of fresh) {
    if (e.type === "drawing") { drawingMine = !!SENT && e.shape === SENT; drawT0 = Date.now(); PROG = { done: 0, cur: -1 }; }
    else if (e.type === "drawn") drawingMine = false;
    else if (e.type === "stroke_start" || e.type === "stroke_done") st = e;
  }
  if (!["running", "waiting"].includes(t.state)) drawingMine = false;
  if (!drawingMine || !DRAWPLAN) { if (drawT0) { drawT0 = 0; hud(""); } return; }
  const n = DRAWPLAN.choice.uv.length;
  if (st) {
    PROG = st.type === "stroke_start" ? { done: st.index - 1, cur: st.index - 1 } : { done: st.index, cur: -1 };
    if (DRAWPLAN.choice.picture) paintPic();
    if (twin) {                                      // ink so far, from the plan: the finished strokes
      const tEnd = DRAWPLAN.tips.filter(p => p[5] < PROG.done).reduce((m, p) => Math.max(m, p[0]), 0);
      showSheet(DRAWPLAN);
      twin.setOverlay("ink", inkUntil(DRAWPLAN, tEnd), INK);
    }
  }
  const k = Math.min(Math.max(PROG.cur, PROG.done - 1) + 1, n);
  hud(`LIVE · drawing stroke ${Math.max(k, 1)}/${n} · ${mmss((Date.now() - drawT0) / 1000)}`, true);
}, 500);

// a picture from before the reload: show its lines again
if (VEC) { $("#picView").hidden = false; paintPic(); }
window.onDrawChange();
