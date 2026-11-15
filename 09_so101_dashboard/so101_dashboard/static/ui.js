// Look-and-feel layer: keyboard and gamepad maps (2D art, live), the
// "connect the arm" bar, segmented speed pickers and the Draw chooser (shape / sketch / picture).
// Uses app.js globals: $, $$, api, toast, S, INFO, twin, LABELS, fmt, ready, padMode.
"use strict";

const UI = (() => {
  const keep = {
    get: k => { try { return localStorage.getItem(k); } catch (_) { return null; } },
    set: (k, v) => { try { localStorage.setItem(k, v); } catch (_) { /* private mode */ } },
  };
  const tint = (hex, a) => {                                   // hex colour mixed with white
    const n = parseInt(hex.slice(1), 16), c = [n >> 16, (n >> 8) & 255, n & 255].map(v => Math.round(255 - (255 - v) * a));
    return `rgb(${c.join(",")})`;
  };
  const C = { pan: "#219CBC", lift: "#54AFE7", elbow: "#43B268", wflex: "#E8A60C", roll: "#8B6AD8", grip: "#E87A55",
    tool: "#022F49", speed: "#5A6B7A", stop: "#E5484D" };

  // ------------------------------------------------------------------ tabs: remember the last one
  $$(".rail .tab").forEach(t => t.addEventListener("click", () => { keep.set("tab", t.dataset.tab); needArm(); }));
  const lastTab = keep.get("tab");
  if (lastTab && lastTab !== "manual") setTimeout(() => $(`.rail .tab[data-tab="${lastTab}"]`)?.click(), 0);

  // ------------------------------------------------------------------ keyboard map
  // key: [id, label, x, y, w (key units), group]
  const KB = [
    ["Escape", "Esc", 0, 0, 1, "stop"],
    ["Backquote", "`", 0, 1.1, 1], ["Digit1", "1", 1, 1.1, 1, "speed"], ["Digit2", "2", 2, 1.1, 1, "speed"], ["Digit3", "3", 3, 1.1, 1, "speed"],
    ["Digit4", "4", 4, 1.1, 1], ["Digit5", "5", 5, 1.1, 1], ["Digit6", "6", 6, 1.1, 1], ["Digit7", "7", 7, 1.1, 1],
    ["Tab", "Tab", 0, 2.1, 1.5], ["KeyQ", "Q", 1.5, 2.1, 1, "pan"], ["KeyW", "W", 2.5, 2.1, 1, "lift"], ["KeyE", "E", 3.5, 2.1, 1, "pan"],
    ["KeyR", "R", 4.5, 2.1, 1, "roll"], ["KeyT", "T", 5.5, 2.1, 1, "grip"], ["KeyY", "Y", 6.5, 2.1, 1],
    ["CapsLock", "Caps", 0, 3.1, 1.75], ["KeyA", "A", 1.75, 3.1, 1, "elbow"], ["KeyS", "S", 2.75, 3.1, 1, "lift"], ["KeyD", "D", 3.75, 3.1, 1, "elbow"],
    ["KeyF", "F", 4.75, 3.1, 1, "roll"], ["KeyG", "G", 5.75, 3.1, 1, "grip"], ["KeyH", "H", 6.75, 3.1, 1],
    ["ShiftLeft", "Shift", 0, 4.1, 2.25, "speed"], ["KeyZ", "Z", 2.25, 4.1, 1, "wflex"], ["KeyX", "X", 3.25, 4.1, 1, "wflex"],
    ["KeyC", "C", 4.25, 4.1, 1], ["KeyV", "V", 5.25, 4.1, 1], ["KeyB", "B", 6.25, 4.1, 1],
    ["ControlLeft", "Ctrl", 0, 5.1, 1.25], ["MetaLeft", "Win", 1.25, 5.1, 1.25], ["AltLeft", "Alt", 2.5, 5.1, 1.25],
    ["Space", "Space · E-STOP", 3.75, 5.1, 4.25, "stop"],
    ["Insert", "Ins", 8.9, 1.1, 1], ["Home", "Home", 9.9, 1.1, 1], ["PageUp", "PgUp", 10.9, 1.1, 1, "tool"],
    ["Delete", "Del", 8.9, 2.1, 1], ["End", "End", 9.9, 2.1, 1], ["PageDown", "PgDn", 10.9, 2.1, 1, "tool"],
    ["ArrowUp", "↑", 9.9, 4.1, 1, "tool"], ["ArrowLeft", "←", 8.9, 5.1, 1, "tool"], ["ArrowDown", "↓", 9.9, 5.1, 1, "tool"], ["ArrowRight", "→", 10.9, 5.1, 1, "tool"],
  ];
  const KB_LEGEND = [
    ["pan", "Q  E", "Base turns left / right"], ["lift", "W  S", "Shoulder up / down"],
    ["elbow", "A  D", "Elbow in / out"], ["wflex", "Z  X", "Wrist bends down / up"],
    ["roll", "R  F", "Wrist rolls ↺ / ↻"], ["grip", "T  G", "Gripper close / open"],
    ["tool", "↑ ↓ ← →", "Tool forward / back / left / right"], ["tool", "PgUp PgDn", "Tool up / down"],
    ["speed", "1  2  3", "Slow / normal / fast · Shift = boost"], ["stop", "Space  Esc", "E-STOP (always on)"],
  ];
  function buildKeyboard() {
    const U = 46, G = 4, W = 12 * U, H = 6.1 * U;
    const key = ([id, label, x, y, w, grp]) => {
      const col = grp ? C[grp] : null, kx = x * U + G / 2, ky = y * U + G / 2, kw = w * U - G, kh = (y === 0 ? 0.85 : 1) * U - G;
      const fs = label.length > 2 ? (label.length > 6 ? 12 : 13) : 19;
      return `<g class="kk${grp ? " on" : ""}" data-code="${id}" style="--c:${col || "#8696a3"}">
        <rect class="kb" x="${kx}" y="${ky + 3}" width="${kw}" height="${kh}" rx="8"/>
        <rect class="kt" x="${kx}" y="${ky}" width="${kw}" height="${kh}" rx="8" fill="${col ? tint(col, 0.2) : "#fff"}" stroke="${col || "#d3dde4"}"/>
        <text x="${kx + kw / 2}" y="${ky + kh / 2 + fs * 0.36}" font-size="${fs}" fill="${col || "#9aa8b4"}">${label}</text></g>`;
    };
    // the left part of a keyboard (fading out where it continues) and the arrow / page block
    $("#kbdWrap").innerHTML = `<svg class="kbd-art" viewBox="-10 -10 ${W + 20} ${H + 20}" role="img" aria-label="Keyboard map">
      <defs><linearGradient id="kbFade" x1="0" x2="1"><stop offset="0" stop-color="#fff" stop-opacity="0"/><stop offset="1" stop-color="#fff"/></linearGradient></defs>
      <rect x="-10" y="-10" width="${8.6 * U + 10}" height="${H + 20}" rx="18" fill="#E9EFF3"/>
      ${KB.filter(k => k[2] < 8).map(key).join("")}
      <rect x="${7.5 * U}" y="-10" width="${1.1 * U}" height="${H + 20}" fill="url(#kbFade)"/>
      <rect x="${8.75 * U}" y="-10" width="${3.25 * U + 10}" height="${H + 20}" rx="18" fill="#E9EFF3"/>
      ${KB.filter(k => k[2] >= 8).map(key).join("")}
    </svg>`;
    $("#kbdLegend").innerHTML = KB_LEGEND.map(([g, k, what]) =>
      `<div data-g="${g}" style="--c:${C[g]}"><i></i><b>${k}</b><span>${what}</span></div>`).join("");
  }
  const kbKey = e => (e.code === "ShiftRight" ? "ShiftLeft" : e.code);
  function kbLight(e, down) {
    $$(`#kbdWrap [data-code="${kbKey(e)}"]`).forEach(g => g.classList.toggle("down", down));
  }
  window.addEventListener("keydown", e => kbLight(e, true));
  window.addEventListener("keyup", e => kbLight(e, false));
  window.addEventListener("blur", () => $$("#kbdWrap .down").forEach(g => g.classList.remove("down")));

  // ------------------------------------------------------------------ gamepad map
  // standard mapping: buttons 0 A, 1 B, 2 X, 3 Y, 4 LB, 5 RB, 6 LT, 7 RT, 8 View, 9 Menu, 16 Home
  const PADMAP = {
    joint: { ls: ["Left stick", "↔ base pan · ↕ shoulder", C.pan], rs: ["Right stick", "↔ wrist roll · ↕ wrist flex", C.roll],
      lb: ["LB / RB", "elbow − / +", C.elbow] },
    cart: { ls: ["Left stick", "↕ tool forward · ↔ sideways", C.tool], rs: ["Right stick", "↕ tool up/down · ↔ wrist roll", C.roll],
      lb: ["LB / RB", "wrist flex − / +", C.wflex] },
    common: { lt: ["LT / RT", "gripper close / open", C.grip], a: ["A", "go to the home pose", C.elbow],
      y: ["Y (hold)", "boost ×2", C.lift], view: ["View", "switch joint ↔ tool mode", C.speed], b: ["B", "E-STOP (always)", C.stop] },
  };
  function buildPad() {
    const body = "M180 100C140 100 110 112 95 150C70 215 50 300 62 345C72 380 120 392 150 362C175 336 195 302 228 292L372 292"
      + "C405 302 425 336 450 362C480 392 528 380 538 345C550 300 530 215 505 150C490 112 460 100 420 100C380 97 350 106 300 106C250 106 220 97 180 100Z";
    const btn = (id, x, y, r, label, fill) => `<g class="pb" data-b="${id}"><circle cx="${x}" cy="${y}" r="${r}" fill="${fill}"/>
      <circle class="ring" cx="${x}" cy="${y}" r="${r + 4}"/><text x="${x}" y="${y + 6}" font-size="17">${label}</text></g>`;
    $("#padWrap").innerHTML = `<svg class="pad-art" viewBox="40 6 520 398" role="img" aria-label="Gamepad map">
      <defs><linearGradient id="gpBody" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#1D4762"/><stop offset="1" stop-color="#0B2536"/></linearGradient>
        <radialGradient id="gpStick" cx=".4" cy=".35"><stop offset="0" stop-color="#3a5568"/><stop offset="1" stop-color="#0d1f2b"/></radialGradient></defs>
      <g class="pb trig" data-b="6"><rect class="ring" x="148" y="14" width="86" height="50" rx="16"/><rect x="152" y="18" width="78" height="42" rx="13" fill="#234b63"/>
        <rect class="fillv" x="152" y="60" width="78" height="0" rx="6"/><text x="191" y="45" font-size="16">LT</text></g>
      <g class="pb trig" data-b="7"><rect class="ring" x="366" y="14" width="86" height="50" rx="16"/><rect x="370" y="18" width="78" height="42" rx="13" fill="#234b63"/>
        <rect class="fillv" x="370" y="60" width="78" height="0" rx="6"/><text x="409" y="45" font-size="16">RT</text></g>
      <g class="pb bump" data-b="4"><rect class="ring" x="122" y="68" width="124" height="30" rx="13"/><rect x="126" y="72" width="116" height="22" rx="10" fill="#2a5a77"/><text x="184" y="88" font-size="13">LB</text></g>
      <g class="pb bump" data-b="5"><rect class="ring" x="354" y="68" width="124" height="30" rx="13"/><rect x="358" y="72" width="116" height="22" rx="10" fill="#2a5a77"/><text x="416" y="88" font-size="13">RB</text></g>
      <path d="${body}" fill="url(#gpBody)" stroke="#2f6585" stroke-width="3"/>
      <path d="M150 118C200 108 250 112 300 112C350 112 400 108 450 118" fill="none" stroke="rgba(255,255,255,.12)" stroke-width="3"/>
      <g class="stick" data-s="ls"><circle class="ring" cx="190" cy="172" r="40"/><circle cx="190" cy="172" r="36" fill="#0a1c28"/>
        <circle class="cap" cx="190" cy="172" r="26" fill="url(#gpStick)"/><circle class="dot" cx="190" cy="172" r="6"/></g>
      <g class="stick" data-s="rs"><circle class="ring" cx="362" cy="242" r="40"/><circle cx="362" cy="242" r="36" fill="#0a1c28"/>
        <circle class="cap" cx="362" cy="242" r="26" fill="url(#gpStick)"/><circle class="dot" cx="362" cy="242" r="6"/></g>
      <g class="dpad" fill="#0f2a3b"><rect x="226" y="222" width="26" height="76" rx="5"/><rect x="201" y="247" width="76" height="26" rx="5"/></g>
      ${btn(3, 430, 136, 17, "Y", "#c99312")}${btn(2, 396, 170, 17, "X", "#2c6fb4")}${btn(1, 464, 170, 17, "B", "#c23c40")}${btn(0, 430, 204, 17, "A", "#3a9a5b")}
      <g class="pb" data-b="8"><circle cx="262" cy="172" r="12" fill="#0f2a3b"/><circle class="ring" cx="262" cy="172" r="15"/><path d="M256 168h8v6h-8zM259 171h8v6" fill="none" stroke="#9fc3d6" stroke-width="1.6"/></g>
      <g class="pb" data-b="9"><circle cx="338" cy="172" r="12" fill="#0f2a3b"/><path d="M333 168h10M333 172h10M333 176h10" stroke="#9fc3d6" stroke-width="1.6"/></g>
      <g class="pb" data-b="16"><circle cx="300" cy="132" r="16" fill="#0f2a3b" stroke="#2f6585" stroke-width="2"/><circle cx="300" cy="132" r="8" fill="none" stroke="#9fc3d6" stroke-width="2"/></g>
    </svg>`;
    padArt(typeof padMode === "string" ? padMode : "joint");
  }
  // colours on the art + the legend table follow the mode
  const PAD_PARTS = { ls: ['[data-s="ls"]'], rs: ['[data-s="rs"]'], lb: ['[data-b="4"]', '[data-b="5"]'],
    lt: ['[data-b="6"]', '[data-b="7"]'], a: ['[data-b="0"]'], y: ['[data-b="3"]'], view: ['[data-b="8"]'], b: ['[data-b="1"]'] };
  function padArt(mode) {
    const m = { ...PADMAP[mode], ...PADMAP.common };
    for (const [k, sels] of Object.entries(PAD_PARTS))
      sels.forEach(s => { const el = $(`#padWrap ${s}`); if (el) { el.style.setProperty("--c", m[k][2]); el.classList.add("mapped"); } });
    $("#padHelp").innerHTML = Object.entries(m).map(([k, [ctl, what, col]]) =>
      `<tr data-k="${k}" style="--c:${col}"><td><i class="sw"></i>${ctl}</td><td>${what}</td></tr>`).join("");
  }
  function padLive() {
    requestAnimationFrame(padLive);
    if ($("#tab-pad").hidden) return;
    const gp = [...(navigator.getGamepads ? navigator.getGamepads() : [])].find(g => g && g.connected);
    const ax = i => (gp ? gp.axes[i] || 0 : 0), pr = i => !!(gp && gp.buttons[i] && gp.buttons[i].pressed);
    [["ls", 0, 1, 190, 172], ["rs", 2, 3, 362, 242]].forEach(([s, ix, iy, cx, cy]) => {
      const g = $(`#padWrap [data-s="${s}"]`); if (!g) return;
      const x = cx + ax(ix) * 18, y = cy + ax(iy) * 18;
      g.querySelector(".cap").setAttribute("cx", x); g.querySelector(".cap").setAttribute("cy", y);
      g.querySelector(".dot").setAttribute("cx", x); g.querySelector(".dot").setAttribute("cy", y);
      const live = Math.hypot(ax(ix), ax(iy)) > 0.15;
      g.classList.toggle("down", live);
      $(`#padHelp tr[data-k="${s}"]`)?.classList.toggle("down", live);
    });
    $$("#padWrap .pb").forEach(g => g.classList.toggle("down", pr(+g.dataset.b)));
    [6, 7].forEach(i => {
      const v = gp && gp.buttons[i] ? gp.buttons[i].value : 0, r = $(`#padWrap [data-b="${i}"] .fillv`);
      if (r) { r.setAttribute("height", 42 * v); r.setAttribute("y", 60 - 42 * v); }
    });
    const rows = { lb: pr(4) || pr(5), lt: pr(6) || pr(7), a: pr(0), y: pr(3), view: pr(8), b: pr(1) };
    for (const [k, on] of Object.entries(rows)) $(`#padHelp tr[data-k="${k}"]`)?.classList.toggle("down", on);
  }

  // ------------------------------------------------------------------ "needs the arm" bar
  const NEEDS = { manual: 1, keys: 1, pad: 1, hand: 1, motions: 1, record: 1, ai: 1 };
  function needArm() {
    const bar = $("#needArm"), tab = $(".rail .tab.on")?.dataset.tab;
    let html = "";
    if (S && NEEDS[tab]) {
      const busy = typeof TASK !== "undefined" && TASK && ["starting", "running", "waiting"].includes(TASK.state);
      if (busy && S.state !== "ready") html = `<svg class="ic"><use href="#i-info"/></svg><span>A procedure (${(TASK.task || [])[0]}) is using the arm. Stop it to drive the arm here.</span>`;
      else if (S.state === "disconnected" || S.state === "error")
        html = `<svg class="ic"><use href="#i-plug"/></svg><span>Connect an arm to use this.</span>
          <button class="btn" data-go="sim">Simulated</button><button class="btn primary" data-go="real">Real arm</button>`;
      else if (S.state === "connecting") html = `<svg class="ic"><use href="#i-plug"/></svg><span>Connecting…</span>`;
      else if (S.estop) html = `<svg class="ic"><use href="#i-info"/></svg><span>E-STOP is on – the arm won't move.</span><button class="btn" data-go="reset">Reset E-STOP</button>`;
      else if (!S.torque && tab !== "record") html = `<svg class="ic"><use href="#i-info"/></svg><span>Torque is off – the arm is limp.</span><button class="btn" data-go="torque">Torque on</button>`;
    }
    if (bar.dataset.html !== html) { bar.dataset.html = html; bar.innerHTML = html; }
    bar.hidden = !html;
    $(".panes").classList.toggle("needs", !!html);
    // the rail dot: the arm is not ready, so the arm tabs can't do anything yet
    const off = !S || S.state !== "ready";
    $$(".rail .tab").forEach(t => {
      let d = t.querySelector(".dotb");
      if (NEEDS[t.dataset.tab] && off) { if (!d) { d = document.createElement("i"); d.className = "dotb"; t.appendChild(d); } }
      else if (d) d.remove();
    });
  }
  $("#needArm").addEventListener("click", e => {
    const go = e.target.closest("button")?.dataset.go;
    if (go === "sim" || go === "real") api("connect", { mode: go }).catch(() => {});
    else if (go === "reset") api("reset").catch(() => {});
    else if (go === "torque") api("torque", { on: true }).catch(() => {});
  });
  setInterval(needArm, 300);

  // ------------------------------------------------------------------ segmented pickers -> hidden selects
  function segSelect(seg, sel) {
    $$(`${seg} button`).forEach(b => (b.onclick = () => {
      $$(`${seg} button`).forEach(x => x.classList.toggle("on", x === b)); $(sel).value = b.dataset.v;
    }));
  }
  segSelect("#motionSpeedSeg", "#motionSpeed");
  segSelect("#recSpeedSeg", "#recSpeed");
  // sliders that show their value next to them
  [["#shapeSize", " mm"], ["#sketchSize", " mm"], ["#planePan", "°"], ["#hgSmooth", ""], ["#hgPan", ""], ["#hgZ", ""], ["#hgR", ""]].forEach(([id, unit]) => {
    const inp = $(id); if (!inp) return;
    const out = inp.closest(".slider")?.querySelector("output");
    const show = () => out && (out.textContent = inp.value + unit);
    inp.addEventListener("input", show); show();
  });

  // ------------------------------------------------------------------ Draw: what to draw
  // Every choice becomes strokes in the drawing frame (u = left, v = away from the robot, any unit;
  // the program scales them to the size), so shapes, sketches and pictures all take the same path:
  // plan + 3D check (draw_plan), then save_drawing + `drawfile <name> <size>`.
  const sel = {
    src: keep.get("drawSrc") || "shape",
    shape: keep.get("drawShape") || "star",
    sketch: (() => { try { return JSON.parse(keep.get("sketch") || "[]"); } catch (_) { return []; } })(),
  };
  if ($("#shapeSize") && keep.get("shapeSize")) $("#shapeSize").value = keep.get("shapeSize");
  if ($("#sketchSize") && keep.get("sketchSize")) $("#sketchSize").value = keep.get("sketchSize");
  ["#shapeSize", "#sketchSize"].forEach(id => $(id).dispatchEvent(new Event("input")));
  const changed = () => window.onDrawChange && window.onDrawChange();

  function shapeStrokes(name, size = 100) {                  // same shapes as the draw program (draw.py shape())
    const r = size / 2, lin = (a, b, n) => Array.from({ length: n }, (_, i) => a + (b - a) * i / (n - 1));
    const circ = (rad, n = 90, a0 = 0, a1 = 2 * Math.PI) => lin(a0, a1, n).map(t => [rad * Math.cos(t), rad * Math.sin(t)]);
    switch (name) {
      case "square": return [[[-r, -r], [r, -r], [r, r], [-r, r], [-r, -r]]];
      case "circle": return [circ(r)];
      case "star": return [[0, 1, 2, 3, 4, 5].map(k => [r * Math.cos(Math.PI / 2 + k * 4 * Math.PI / 5), r * Math.sin(Math.PI / 2 + k * 4 * Math.PI / 5)])];
      case "spiral": { const T = 6 * Math.PI; return [lin(0, T, 300).map(t => [r * t / T * Math.cos(t), r * t / T * Math.sin(t)])]; }
      case "smiley": return [circ(r), circ(0.08 * size, 24).map(([x, y]) => [x - 0.35 * r, y + 0.3 * r]),
        circ(0.08 * size, 24).map(([x, y]) => [x + 0.35 * r, y + 0.3 * r]),
        lin(Math.PI * 1.15, Math.PI * 1.85, 40).map(t => [0.55 * r * Math.cos(t), 0.55 * r * Math.sin(t) - 0.05 * r])];
      case "house": return [[[-r, -r], [r, -r], [r, 0.2 * r], [-r, 0.2 * r], [-r, -r]], [[-r, 0.2 * r], [0, r], [r, 0.2 * r]],
        [[-0.25 * r, -r], [-0.25 * r, -0.35 * r], [0.15 * r, -0.35 * r], [0.15 * r, -r]]];
    }
    return [];
  }
  // shape frame (+u left, +v away) -> card icon (screen x right, y down): x = -u, y = -v
  function shapeIcons() {
    $$("#shapeGrid .shape-card").forEach(b => {
      const st = shapeStrokes(b.dataset.s, 80);
      const d = st.map(s => s.map(([u, v], i) => `${i ? "L" : "M"}${(-u).toFixed(1)} ${(-v).toFixed(1)}`).join("")).join("");
      b.querySelector("span").outerHTML = `<svg viewBox="-48 -48 96 96" class="shape-ic"><path d="${d}"/></svg>`;
      b.classList.toggle("on", b.dataset.s === sel.shape);
      b.onclick = () => { sel.shape = b.dataset.s; keep.set("drawShape", sel.shape); $$("#shapeGrid .shape-card").forEach(x => x.classList.toggle("on", x === b)); changed(); };
    });
  }
  function setSrc(src) {
    sel.src = src; keep.set("drawSrc", src);
    $$("#drawSrc button").forEach(b => b.classList.toggle("on", b.dataset.src === src));
    ["shape", "sketch", "picture"].forEach(s => ($(`#src-${s}`).hidden = s !== src));
    if (src === "sketch") paintSketch();
    changed();
  }
  $$("#drawSrc button").forEach(b => (b.onclick = () => setSrc(b.dataset.src)));
  $("#shapeSize").addEventListener("change", () => { keep.set("shapeSize", $("#shapeSize").value); changed(); });
  $("#sketchSize").addEventListener("change", () => { keep.set("sketchSize", $("#sketchSize").value); changed(); });

  // sketch pad: 420 x 300 canvas, the top is away from the robot
  const pad = $("#padSketch");
  function paintSketch() {
    const g = pad.getContext("2d");
    g.fillStyle = "#fff"; g.fillRect(0, 0, pad.width, pad.height);
    g.strokeStyle = "#eef3f6"; g.lineWidth = 1;
    for (let x = 30; x < pad.width; x += 30) { g.beginPath(); g.moveTo(x, 0); g.lineTo(x, pad.height); g.stroke(); }
    for (let y = 30; y < pad.height; y += 30) { g.beginPath(); g.moveTo(0, y); g.lineTo(pad.width, y); g.stroke(); }
    g.fillStyle = "#9fb0bd"; g.font = "600 12px Barlow, sans-serif"; g.textAlign = "center";
    g.fillText("▲ away from the robot", pad.width / 2, 16);
    if (!sel.sketch.length) { g.fillStyle = "#b9c6cf"; g.font = "600 16px Barlow, sans-serif"; g.fillText("Draw here with the mouse or a finger", pad.width / 2, pad.height / 2 + 6); }
    g.strokeStyle = "#022F49"; g.lineWidth = 3.5; g.lineCap = g.lineJoin = "round";
    for (const st of sel.sketch) { g.beginPath(); st.forEach(([x, y], i) => (i ? g.lineTo(x, y) : g.moveTo(x, y))); g.stroke(); }
  }
  let pen = null;
  const at = e => { const r = pad.getBoundingClientRect(), k = pad.width / r.width; return [(e.clientX - r.left) * k, (e.clientY - r.top) * k]; };
  pad.addEventListener("pointerdown", e => { e.preventDefault(); pad.setPointerCapture(e.pointerId); pen = [at(e)]; sel.sketch.push(pen); paintSketch(); });
  pad.addEventListener("pointermove", e => {
    if (!pen) return;
    const p = at(e), q = pen[pen.length - 1];
    if (Math.hypot(p[0] - q[0], p[1] - q[1]) >= 2) { pen.push(p); paintSketch(); }
  });
  const penUp = () => {
    if (!pen) return;
    pen = null; sel.sketch = sel.sketch.filter(st => st.length >= 2);
    keep.set("sketch", JSON.stringify(sel.sketch)); paintSketch(); changed();
  };
  pad.addEventListener("pointerup", penUp); pad.addEventListener("pointercancel", penUp);
  $("#sketchUndo").onclick = () => { sel.sketch.pop(); keep.set("sketch", JSON.stringify(sel.sketch)); paintSketch(); changed(); };
  $("#sketchClear").onclick = () => { sel.sketch = []; keep.set("sketch", "[]"); paintSketch(); changed(); };

  // the current choice as strokes; the picture's come from studio.js (VEC)
  function drawChoice() {
    if (sel.src === "shape") {
      const size = +$("#shapeSize").value;
      return { name: `shape-${sel.shape}`, label: `${sel.shape} · ${size} mm`, uv: shapeStrokes(sel.shape, 100), size, fill: false };
    }
    if (sel.src === "sketch") {
      if (!sel.sketch.length) return null;
      const size = +$("#sketchSize").value;
      return { name: "sketch", label: `your sketch · ${size} mm`, size, fill: false,
        uv: sel.sketch.map(st => st.map(([x, y]) => [-(x - pad.width / 2), -(y - pad.height / 2)])) };
    }
    return null;                                               // picture: studio.js fills it in
  }

  buildKeyboard(); buildPad(); shapeIcons(); setSrc(sel.src);
  requestAnimationFrame(padLive);
  return { padArt, drawChoice, setSrc, sel, keep };
})();
