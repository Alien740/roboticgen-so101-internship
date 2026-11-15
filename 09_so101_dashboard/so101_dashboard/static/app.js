// SO-101 dashboard UI. All arm commands go to the Python server (POST /api/...), which owns the arm;
// the live state arrives as Server-Sent Events (/api/events) about 20 times a second.
"use strict";

const $ = s => document.querySelector(s);
const $$ = s => [...document.querySelectorAll(s)];
const TOKEN = new URLSearchParams(location.search).get("t");
const HDR = TOKEN ? { "X-Token": TOKEN } : {};
const LABELS = { shoulder_pan: "Base pan", shoulder_lift: "Shoulder", elbow_flex: "Elbow",
  wrist_flex: "Wrist flex", wrist_roll: "Wrist roll", gripper: "Gripper" };
const JOG_PERIOD_MS = 50;

let INFO = null, S = null, twin = null;

// ------------------------------------------------------------------ server calls
async function api(cmd, body = {}) {
  try {
    const r = await fetch(`/api/${cmd}`, { method: "POST", headers: { "Content-Type": "application/json", "X-SO101": "1", ...HDR },
      body: JSON.stringify(body) });
    const j = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(j.error || `HTTP ${r.status}`);
    return j;
  } catch (e) { toast(e.message); throw e; }
}
let toastTimer = null;
function toast(msg) {
  const t = $("#toast"); t.textContent = msg; t.hidden = false;
  clearTimeout(toastTimer); toastTimer = setTimeout(() => (t.hidden = true), 3500);
}
// Confirm by clicking twice instead of a confirm() pop-up: pop-ups are blocked in some browser tabs
// (e.g. one an assistant's browser extension is driving), which silently cancelled the action.
// First click arms the button (shows `label`), a second click within ARM_MS goes ahead.
const ARM_MS = 6000;
function twoStep(el, label) {
  if (el.dataset.armed === "1") { clearTimeout(+el.dataset.armTimer); disarm(el); return true; }
  el.dataset.armed = "1"; el.dataset.label = el.innerHTML; el.innerHTML = label; el.classList.add("armed");
  el.dataset.armTimer = setTimeout(() => disarm(el), ARM_MS);
  return false;
}
function disarm(el) {
  if (el.dataset.armed !== "1") return;
  el.dataset.armed = ""; el.innerHTML = el.dataset.label; el.classList.remove("armed");
}
const fmt = (v, d = 1) => (v === null || v === undefined || Number.isNaN(v) ? "–" : Number(v).toFixed(d));
const ready = () => S && S.state === "ready" && !S.estop && S.torque;

// ------------------------------------------------------------------ build the UI from /api/info
async function init() {
  INFO = await (await fetch("/api/info", { headers: HDR })).json();
  buildSliders(); buildPoses(); buildSysInfo(); padHelp();
  twin = Twin.create($("#twin"), msg => ($("#twinMsg").textContent = msg));
  if (twin) {
    twin.load(`/api/twin_mesh${TOKEN ? "?t=" + TOKEN : ""}`, INFO.reach, HDR)
      .then(() => ($("#twinMsg").textContent = "Connect an arm (or the simulated one) to see it here."))
      .catch(e => ($("#twinMsg").textContent = e.message));
  }
  const es = new EventSource(`/api/events${TOKEN ? "?t=" + TOKEN : ""}`);
  es.onmessage = e => { S = JSON.parse(e.data); render(); $("#errorBar").hidden = S.state !== "error"; };
  es.onerror = () => { const b = $("#errorBar"); b.textContent = "Lost contact with the dashboard server – is so101-dashboard still running?"; b.hidden = false; };
  setInterval(jogTick, JOG_PERIOD_MS);
}

function buildSliders() {
  const box = $("#sliders");
  for (const j of INFO.joints) {
    const [lo, hi] = INFO.limits[j];
    const row = document.createElement("div");
    row.className = "slider";
    row.innerHTML = `<label for="sl-${j}">${LABELS[j] || j}</label>
      <div class="track"><input type="range" id="sl-${j}" min="${lo}" max="${hi}" step="0.5" value="0"><span class="meas"></span></div>
      <output id="out-${j}">–</output>`;
    box.appendChild(row);
    const inp = row.querySelector("input");
    inp.dataset.joint = j;
    let last = 0;
    inp.addEventListener("pointerdown", () => (inp.dataset.drag = "1"));
    inp.addEventListener("pointerup", () => { delete inp.dataset.drag; send(); });
    inp.addEventListener("keydown", e => e.stopPropagation());     // slider arrows are not the tool jog
    inp.addEventListener("input", () => { if (Date.now() - last > 60) send(); });
    function send() { last = Date.now(); if (ready()) api("set", { joints: { [j]: +inp.value }, source: "slider" }).catch(() => {}); }
  }
}

function buildPoses() {
  const box = $("#poses");
  for (const name of Object.keys(INFO.poses)) {
    const b = document.createElement("button");
    b.className = "btn"; b.textContent = name.replace("_", " ");
    b.dataset.motion = "1";
    b.onclick = () => api("goto", { pose: name }).catch(() => {});
    box.appendChild(b);
  }
}

function buildSysInfo() {
  const s = INFO.system;
  const ports = s.ports.length ? s.ports.map(p => `${p.device} (${p.description})`).join("<br>") : "none found";
  $("#sysInfo").innerHTML = `
    <dt>Profile</dt><dd>${s.profile} (robot id ${s.robot_id})</dd>
    <dt>Computer</dt><dd>${s.host}</dd>
    <dt>OS</dt><dd>${s.os}</dd>
    <dt>Python</dt><dd>${s.python}</dd>
    <dt>lerobot</dt><dd>${s.lerobot}</dd>
    <dt>Serial ports</dt><dd>${ports}</dd>`;
}

// ------------------------------------------------------------------ render live state
function render() {
  const st = S.state, mode = S.mode;
  const badge = $("#modeBadge");
  const [txt, cls] = st === "ready" ? [mode === "sim" ? "SIMULATED" : "REAL ARM", mode === "sim" ? "sim" : "real"]
    : st === "connecting" ? ["CONNECTING…", "busy"] : st === "parking" ? ["PARKING…", "busy"]
    : st === "error" ? ["ERROR", "err"] : ["DISCONNECTED", "off"];
  badge.textContent = txt + (st === "ready" && S.port && mode === "real" ? ` · ${S.port}` : "");
  badge.className = `badge ${cls}`;
  $("#ownerPill").textContent = S.traj ? `${S.owner} ${Math.round(S.traj.progress * 100)}%` : S.owner;
  if (st === "error") $("#errorBar").textContent = S.error;

  const connected = st === "ready" || st === "parking";
  $("#btnSim").hidden = $("#btnReal").hidden = connected || st === "connecting";
  $("#btnDisc").hidden = !connected;
  $("#btnDisc").disabled = st === "parking";
  $("#btnEstop").classList.toggle("latched", S.estop);
  $("#btnReset").hidden = !S.estop;
  $("#torque").checked = S.torque; $("#torque").disabled = st !== "ready";
  $$(".speed button").forEach(b => b.classList.toggle("on", b.dataset.speed === S.speed));
  const can = ready();
  $$("[data-motion], .jog, #gripOpen, #gripClose, .slider input").forEach(el => (el.disabled = !can));

  // sliders follow the target unless the user is dragging; the green tick is the real position
  for (const j of INFO.joints) {
    const inp = $(`#sl-${j}`), m = S.measured[j];
    if (!inp.dataset.drag && S.target[j] !== undefined) inp.value = S.target[j];
    $(`#out-${j}`).textContent = m === undefined ? "–" : fmt(m) + (j === "gripper" ? "" : "°");
    const [lo, hi] = INFO.limits[j], tick = inp.parentElement.querySelector(".meas");
    tick.style.display = m === undefined ? "none" : "";
    // the range thumb (20 px) travels 10 px in from each end, so the dot does too
    if (m !== undefined) tick.style.left = `calc(10px + (100% - 20px) * ${Math.min(1, Math.max(0, (m - lo) / (hi - lo)))} - 5px)`;
  }

  if (S.tool) {
    const [x, y, z] = S.tool.xyz_mm;
    $("#toolXyz").textContent = `x ${fmt(x, 0)}  y ${fmt(y, 0)}  z ${fmt(z, 0)} mm`;
    $("#toolPitch").textContent = `${fmt(S.tool.pitch_deg, 0)}°`;
  } else { $("#toolXyz").textContent = $("#toolPitch").textContent = "–"; }
  $("#rate").textContent = connected ? `${fmt(S.rate_hz, 0)} Hz` : "–";
  if (twin && S.frames && !window.SIM_LOCK) { twin.setFrames(S.frames); $("#twinMsg").textContent = ""; }

  $("#warnings").innerHTML = S.warnings.slice().reverse().map(w =>
    `<li><time>${new Date(w.t * 1000).toLocaleTimeString()}</time>${escapeHtml(w.msg)}</li>`).join("");
  renderMotors();
}

function renderMotors() {
  const tel = S.telemetry, rows = [];
  const supply = INFO.supply_voltage || 5.0;
  for (const j of INFO.joints) {
    const t = tel ? tel[j] : null;
    const faults = t && t.faults.length ? t.faults.join(", ") : (t ? "ok" : "–");
    const hot = t && t.temp_c >= 50, lowV = t && t.voltage !== null && t.voltage < supply * 0.9;
    rows.push(`<tr><td>${t ? t.id : ""}</td><td>${LABELS[j]}</td><td>${fmt(S.measured[j])}</td><td>${fmt(S.goal[j])}</td>
      <td class="${hot ? "hot" : ""}">${t ? fmt(t.temp_c, 0) : "–"}</td><td class="${lowV ? "hot" : ""}">${t ? fmt(t.voltage, 1) : "–"}</td>
      <td>${t && t.load !== null ? (t.load / 10).toFixed(0) + "%" : "–"}</td><td>${t && t.torque_on !== null ? (t.torque_on ? "on" : "off") : "–"}</td>
      <td class="${t && t.faults.length ? "bad" : ""}">${faults}</td></tr>`);
  }
  $("#motorRows").innerHTML = rows.join("");
  $("#motorNote").textContent = S.mode === "sim" ? "Simulated arm: these readings are made up."
    : S.mode === "real" ? "Readings refresh about every 0.6 s (one register per control tick)." : "Not connected.";
}
const escapeHtml = s => s.replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

// ------------------------------------------------------------------ top bar
$("#btnSim").onclick = () => api("connect", { mode: "sim" }).catch(() => {});
$("#btnReal").onclick = () => api("connect", { mode: "real" }).catch(() => {});
$("#btnDisc").onclick = () => api("disconnect").catch(() => {});
$("#btnEstop").onclick = () => estop();
$("#btnReset").onclick = () => api("reset").catch(() => {});
$$(".speed button").forEach(b => (b.onclick = () => api("speed", { level: b.dataset.speed }).catch(() => {})));
let torqueArmed = 0;
$("#torque").onchange = e => {
  const on = e.target.checked;
  if (!on && Date.now() - torqueArmed > ARM_MS) {           // first untick: warn, second within 6 s: go
    e.target.checked = true; torqueArmed = Date.now();
    toast("Torque OFF makes the arm go limp. HOLD THE ARM, then untick Torque again (within 6 s).");
    return;
  }
  torqueArmed = 0;
  api("torque", { on }).catch(() => {});
};
function estop() { api("estop").catch(() => {}); }

$$(".tab:not(.soon)").forEach(t => (t.onclick = () => {
  $$(".tab").forEach(x => x.classList.toggle("on", x === t));
  $$(".tabpane").forEach(p => (p.hidden = p.id !== `tab-${t.dataset.tab}`));
}));
$$("#views button").forEach(b => (b.onclick = () => {
  $$("#views button").forEach(x => x.classList.toggle("on", x === b)); twin && twin.setView(b.dataset.view);
}));
$("#gripOpen").onclick = () => api("set", { joints: { gripper: INFO.limits.gripper[1] }, source: "button" }).catch(() => {});
$("#gripClose").onclick = () => api("set", { joints: { gripper: INFO.limits.gripper[0] }, source: "button" }).catch(() => {});
$("#btnDoctor").onclick = async () => {
  const out = $("#doctorOut"); out.hidden = false; out.textContent = "Running so101 doctor… (up to a minute)";
  $("#btnDoctor").disabled = true;
  try { const r = await api("doctor"); out.textContent = r.output; } catch (e) { out.textContent = e.message; }
  $("#btnDoctor").disabled = false;
};

// ------------------------------------------------------------------ jog inputs: pad, keyboard, gamepad
// Every 50 ms the active inputs are merged into one velocity command. The server stops the arm if
// these stop arriving (lost tab, unplugged gamepad), and we send one all-zero command on release.
const held = { pad: {}, keys: new Set() };
let jogWasActive = false, jogBusy = false;

$$(".jog").forEach(b => {
  const [axis, sign] = b.dataset.cart.split(":");
  const on = e => { e.preventDefault(); b.setPointerCapture?.(e.pointerId); held.pad[axis] = +sign; b.classList.add("active"); };
  const off = () => { delete held.pad[axis]; b.classList.remove("active"); };
  b.addEventListener("pointerdown", on);
  ["pointerup", "pointercancel", "lostpointercapture"].forEach(ev => b.addEventListener(ev, off));
});

const KEY_JOINTS = { q: ["shoulder_pan", -1], e: ["shoulder_pan", 1], w: ["shoulder_lift", 1], s: ["shoulder_lift", -1],
  a: ["elbow_flex", -1], d: ["elbow_flex", 1], z: ["wrist_flex", -1], x: ["wrist_flex", 1],
  r: ["wrist_roll", -1], f: ["wrist_roll", 1], t: ["gripper", -1], g: ["gripper", 1] };
const KEY_CART = { ArrowUp: ["x", 1], ArrowDown: ["x", -1], ArrowLeft: ["y", 1], ArrowRight: ["y", -1],
  PageUp: ["z", 1], PageDown: ["z", -1] };
const typing = e => e.target.matches && e.target.matches("input[type=text], input[type=number], textarea");

window.addEventListener("keydown", e => {
  if (typing(e)) return;
  if (e.key === " " || e.key === "Escape") { e.preventDefault(); if (!e.repeat) estop(); return; }
  if (!$("#armKeys").checked) return;
  if (["1", "2", "3"].includes(e.key)) { api("speed", { level: ["slow", "normal", "fast"][+e.key - 1] }).catch(() => {}); return; }
  const k = e.key.length === 1 ? e.key.toLowerCase() : e.key;
  if (KEY_JOINTS[k] || KEY_CART[k]) { e.preventDefault(); held.keys.add(k); }
});
window.addEventListener("keyup", e => {
  const k = e.key.length === 1 ? e.key.toLowerCase() : e.key; held.keys.delete(k);
});
window.addEventListener("blur", () => held.keys.clear());
$("#armKeys").onchange = e => {
  if (!e.target.checked) held.keys.clear();
  $("#kbdWrap").classList.toggle("off", !e.target.checked); e.target.blur();
};
$("#kbdWrap").classList.add("off");

// gamepad (standard mapping): joint mode keeps the layout of the old gamepad_control.py
let padMode = "joint", padPrev = [], trig = { lt: false, rt: false };
function padHelp() {                                   // the gamepad map and its table (ui.js)
  if (typeof UI !== "undefined") UI.padArt(padMode);
  $$("#padMode button").forEach(b => b.classList.toggle("on", b.dataset.mode === padMode));
}
$$("#padMode button").forEach(b => (b.onclick = () => { padMode = b.dataset.mode; padHelp(); }));
$("#armPad").onchange = e => e.target.blur();
const dz = v => (Math.abs(v) < 0.15 ? 0 : (v - Math.sign(v) * 0.15) / 0.85);

function readGamepad() {
  const gp = [...(navigator.getGamepads ? navigator.getGamepads() : [])].find(g => g && g.connected);
  if (!gp) { $("#padStatus").textContent = "No gamepad – plug one in and press any button."; return null; }
  $("#padStatus").textContent = `Connected: ${gp.id.slice(0, 60)}`;
  const btn = i => !!(gp.buttons[i] && gp.buttons[i].pressed);
  const edge = i => btn(i) && !padPrev[i];
  if (edge(1)) estop();                                                    // B: always
  if (edge(8)) { padMode = padMode === "joint" ? "cart" : "joint"; padHelp(); }
  const armed = $("#armPad").checked;
  if (armed && edge(0) && ready()) api("goto", { pose: "home" }).catch(() => {});
  padPrev = gp.buttons.map(b => b.pressed);
  if (!armed) return null;

  const lt = gp.buttons[6] ? gp.buttons[6].value : 0, rt = gp.buttons[7] ? gp.buttons[7].value : 0;
  trig.lt = trig.lt ? lt > 0.3 : lt > 0.5; trig.rt = trig.rt ? rt > 0.3 : rt > 0.5;     // hysteresis
  const [lx, ly, rx, ry] = [0, 1, 2, 3].map(i => dz(gp.axes[i] || 0));
  const joints = {}, cart = {};
  if (padMode === "joint") {
    joints.shoulder_pan = lx; joints.shoulder_lift = -ly; joints.wrist_roll = rx; joints.wrist_flex = -ry;
    joints.elbow_flex = (btn(5) ? 1 : 0) - (btn(4) ? 1 : 0);
  } else {
    cart.x = -ly; cart.y = -lx; cart.z = -ry; joints.wrist_roll = rx;
    joints.wrist_flex = (btn(5) ? 1 : 0) - (btn(4) ? 1 : 0);
  }
  joints.gripper = (trig.rt ? 1 : 0) - (trig.lt ? 1 : 0);
  return { joints, cart, boost: btn(3) };
}

async function jogTick() {
  const pad = readGamepad();
  if (!ready()) { jogWasActive = false; return; }
  const joints = {}, cart = { ...held.pad };
  let boost = false, source = Object.keys(held.pad).length ? "tool pad" : "";
  if ($("#armKeys").checked && held.keys.size) {
    for (const k of held.keys) {
      if (KEY_JOINTS[k]) { const [j, s] = KEY_JOINTS[k]; joints[j] = (joints[j] || 0) + s; }
      if (KEY_CART[k]) { const [a, s] = KEY_CART[k]; cart[a] = (cart[a] || 0) + s; }
    }
    boost = held.keys.has("Shift");
    source = "keyboard";
  }
  if (pad) {
    for (const [k, v] of Object.entries(pad.joints)) if (v) { joints[k] = (joints[k] || 0) + v; source = "gamepad"; }
    for (const [k, v] of Object.entries(pad.cart)) if (v) { cart[k] = (cart[k] || 0) + v; source = "gamepad"; }
    boost = boost || pad.boost;
  }
  const active = Object.values(joints).some(v => v) || Object.values(cart).some(v => v);
  if (!active && !jogWasActive) return;
  if (jogBusy) return;                             // never queue up stale commands
  jogBusy = true;
  try { await api("jog", { joints, cart, boost, source: source || "manual" }); } catch (_) { /* toast shown */ }
  finally { jogBusy = false; jogWasActive = active; }
}
window.addEventListener("keydown", e => { if (e.key === "Shift") held.keys.add("Shift"); });

init().catch(e => toast(`Dashboard failed to start: ${e.message}`));
