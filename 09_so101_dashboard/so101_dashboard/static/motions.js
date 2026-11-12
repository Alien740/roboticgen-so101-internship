// Motions (built-in hello / scare / dance + your recordings) and the Record tab.
// Uses app.js globals: api, toast, S (live state), HDR/TOKEN, twin, ready, $, $$.
"use strict";

let MOTIONS = [], playSpeed = 1, previewing = null, recCountdown = null;
const qs = o => Object.entries({ ...o, ...(TOKEN ? { t: TOKEN } : {}) }).map(([k, v]) => `${k}=${encodeURIComponent(v)}`).join("&");

async function loadMotions() {
  try {
    const r = await fetch("/api/motions", { headers: HDR });
    MOTIONS = (await r.json()).motions;
  } catch (_) { MOTIONS = []; }
  renderMotionList();
}

function motionRow(m) {
  const li = document.createElement("li");
  li.className = "motion"; li.dataset.name = m.name;
  const meta = [m.description, `${m.duration_s} s at 1×`].filter(Boolean).join(" · ");
  li.innerHTML = `<div class="info"><div class="name"></div><div class="meta"></div>
    ${m.builtin ? "" : '<div class="tools"><button class="link" data-act="rename">Rename</button><button class="link danger" data-act="delete">Delete</button></div>'}</div>
    <button class="btn" data-act="preview">Preview</button>
    <button class="btn primary" data-act="play">Play</button>`;
  li.querySelector(".name").textContent = m.name;
  li.querySelector(".meta").textContent = meta;
  li.querySelector('[data-act="preview"]').onclick = () => preview(m.name);
  li.querySelector('[data-act="play"]').onclick = () => api("motion_play", { name: m.name, speed: playSpeed }).catch(() => {});
  if (!m.builtin) {
    li.querySelector('[data-act="rename"]').onclick = async () => {
      const n = prompt(`New name for "${m.name}":`, m.name);
      if (n && n.trim() && n.trim() !== m.name) { await api("motion_rename", { name: m.name, new: n.trim() }).catch(() => {}); loadMotions(); }
    };
    li.querySelector('[data-act="delete"]').onclick = async () => {
      if (confirm(`Delete the recording "${m.name}"? This cannot be undone.`)) {
        await api("motion_delete", { name: m.name }).catch(() => {}); loadMotions();
      }
    };
  }
  return li;
}

function renderMotionList() {
  const b = $("#builtinList"), s = $("#savedList");
  b.innerHTML = ""; s.innerHTML = "";
  for (const m of MOTIONS) (m.builtin ? b : s).appendChild(motionRow(m));
  $("#savedEmpty").hidden = MOTIONS.some(m => !m.builtin);
  renderMotions();
}

async function preview(name) {
  if (!twin) return toast("The 3D twin is not available in this browser.");
  if (previewing === name) { twin.stopGhost(); return; }
  if (previewing) twin.stopGhost();
  try {
    const r = await fetch(`/api/motion_preview?${qs({ name, speed: playSpeed })}`, { headers: HDR });
    const j = await r.json();
    if (!r.ok) throw new Error(j.error || `HTTP ${r.status}`);
    if (j.problems.length) toast(`Preview only – it can't play like this: ${j.problems[0]}`);
    previewing = name;
    twin.playGhost(j.frames, j.rate_hz, () => { previewing = null; renderMotions(); });
    renderMotions();
  } catch (e) { toast(e.message); }
}

// called by app.js on every live update
function renderMotions() {
  if (!S) return;
  const tr = S.traj, motionNames = new Set(MOTIONS.map(m => m.name));
  let playingName = null, text = "";
  if (tr && tr.kind === "play") { playingName = tr.name; text = `Playing “${tr.name}” · ${Math.round(tr.progress * 100)}%`; }
  else if (tr && tr.next && tr.next.length && motionNames.has(tr.next[0])) {
    playingName = tr.next[0]; text = `Moving to the start of “${tr.next[0]}”…`;
  }
  $("#playing").hidden = !playingName;
  $("#playingText").textContent = text;
  const can = ready();
  $$(".motion").forEach(li => {
    li.classList.toggle("active", li.dataset.name === playingName);
    li.querySelector('[data-act="play"]').disabled = !can;
    const pv = li.querySelector('[data-act="preview"]');
    pv.textContent = previewing === li.dataset.name ? "Stop preview" : "Preview";
  });

  // recording
  const rec = S.recording;
  const connected = S.state === "ready";
  $("#btnRecStart").hidden = !!rec || recCountdown !== null;
  $("#btnRecStart").disabled = !connected || S.estop;
  $("#btnRecStop").hidden = $("#btnRecDiscard").hidden = !rec;
  $("#recHand").disabled = !!rec;
  if (recCountdown !== null) {
    $("#recStatus").hidden = false; $("#recText").textContent = `Starting in ${recCountdown}… hold the arm`;
  } else if (rec) {
    $("#recStatus").hidden = false;
    const full = rec.elapsed_s >= rec.max_s;
    $("#recText").textContent = `${full ? "Full" : "Recording"} · ${rec.elapsed_s.toFixed(1)} s` +
      (rec.hand_guide ? " · torque off: move the arm by hand" : " · drive it with sliders, keys or gamepad");
  } else { $("#recStatus").hidden = true; }
}

$$("#playSpeed button").forEach(b => (b.onclick = () => {
  playSpeed = +b.dataset.speed;
  $$("#playSpeed button").forEach(x => x.classList.toggle("on", x === b));
}));
$("#btnStopMotion").onclick = () => api("motion_stop").catch(() => {});

$("#btnRecStart").onclick = async () => {
  const hand = $("#recHand").checked;
  if (hand) {
    if (!confirm("The arm will go limp and fall unless you hold it.\nHold the arm, then press OK (recording starts after 3 s).")) return;
    for (recCountdown = 3; recCountdown > 0; recCountdown--) { renderMotions(); await new Promise(r => setTimeout(r, 1000)); }
    recCountdown = null;
  }
  api("record_start", { hand_guide: hand }).catch(() => {}).finally(renderMotions);
};
$("#btnRecStop").onclick = async () => {
  try {
    const r = await api("record_stop", { name: $("#recName").value.trim() });
    toast(`Saved “${r.name}” (${r.duration_s} s) – it's in the Motions tab.`);
    $("#recName").value = "";
    loadMotions();
  } catch (_) { /* toast shown; still recording, fix the name and try again */ }
};
$("#btnRecDiscard").onclick = () => {
  if (confirm("Throw this recording away?")) api("record_discard").catch(() => {});
};

loadMotions();
