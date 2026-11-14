// Hand control: MediaPipe hand landmarks from this computer's camera -> POST /api/hand ~20x/s.
// The server does the IK and keeps every safety rule (limits, E-STOP, stall guard); this file only
// turns the hand into a target: pan / reach / height / pitch / roll / gripper, relative to a
// "neutral" hand pose. It works like a mouse with a clutch: when following starts (or the hand comes
// back into view) the hand must be steady for a moment, then the current hand pose is tied to where
// the arm IS - so engaging never makes the arm jump - and only hand movement from there moves it.
// Hand out of view = clutch released, the arm holds.
// Uses app.js globals: $, api, ready, toast.
const MP = "https://cdn.jsdelivr.net/npm/@mediapipe/tasks-vision@0.10.14";
const MODEL = "https://storage.googleapis.com/mediapipe-models/hand_landmarker/hand_landmarker/float16/1/hand_landmarker.task";
const SEND_MS = 50;
const ARM_NEUTRAL = { pan: 0, r: 0.22, z: 0.12, pitch: -40, roll: 0 };    // before the arm reports a pose
const STEADY_FRAMES = 12;                                     // ~0.4 s of visible hand before engaging
const BONES = [[0, 1], [1, 2], [2, 3], [3, 4], [0, 5], [5, 6], [6, 7], [7, 8], [5, 9], [9, 10], [10, 11], [11, 12],
  [9, 13], [13, 14], [14, 15], [15, 16], [13, 17], [0, 17], [17, 18], [18, 19], [19, 20]];

const video = $("#handVideo"), canvas = $("#handCanvas"), ctx = canvas.getContext("2d");
let landmarker = null, running = false, feat = null, neutral = null, lastSend = 0, busy = false;
let armRef = null, steady = 0;

const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));
const deg = r => (r * 180) / Math.PI;
const wrap = a => ((a + 540) % 360) - 180;
const val = id => +$(id).value;

// ------------------------------------------------------------------ camera + model
async function startCamera() {
  $("#handCam").disabled = true;
  try {
    $("#handMsg").textContent = "Loading hand model…";
    if (!landmarker) {
      const { FilesetResolver, HandLandmarker } = await import(`${MP}/vision_bundle.mjs`);
      const files = await FilesetResolver.forVisionTasks(`${MP}/wasm`);
      landmarker = await HandLandmarker.createFromOptions(files, {
        baseOptions: { modelAssetPath: MODEL, delegate: "GPU" }, runningMode: "VIDEO", numHands: 1,
        minHandDetectionConfidence: 0.6, minTrackingConfidence: 0.6 });
    }
    $("#handMsg").textContent = "Allow the camera in the browser prompt…";
    const dev = $("#handCamSel").value;
    video.srcObject = await navigator.mediaDevices.getUserMedia({
      video: { width: 640, height: 480, ...(dev ? { deviceId: { exact: dev } } : { facingMode: "user" }) } });
    await video.play();
    await listCameras();                                       // labels are only visible after permission
    running = true;
    $("#handCam").textContent = "Stop camera";
    $("#handNeutral").disabled = false;
    $("#handFollow").disabled = false;
    $("#handMsg").textContent = "";
    requestAnimationFrame(loop);
  } catch (e) {
    $("#handMsg").textContent = `Camera / model failed: ${e.message}`;
  } finally { $("#handCam").disabled = false; }
}

function stopCamera() {
  running = false;
  $("#handFollow").checked = false;
  $("#handFollow").disabled = true;
  $("#handNeutral").disabled = true;
  (video.srcObject?.getTracks() || []).forEach(t => t.stop());
  video.srcObject = null;
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  $("#handCam").textContent = "Start camera";
  $("#handMsg").textContent = "Camera off";
  feat = null;
}

// ------------------------------------------------------------------ hand -> features
function features(lm, world) {
  const ar = video.videoWidth / video.videoHeight;            // landmarks are 0..1 on both axes
  const P = i => [lm[i].x * ar, lm[i].y];
  const palm = [0, 5, 9, 17];
  const cx = palm.reduce((s, i) => s + lm[i].x, 0) / 4, cy = palm.reduce((s, i) => s + lm[i].y, 0) / 4;
  const [w0, m9, i5, p17] = [P(0), P(9), P(5), P(17)];
  const size = Math.hypot(m9[0] - w0[0], m9[1] - w0[1]);
  const W = i => world[i];
  const v = [W(9).x - W(0).x, W(9).y - W(0).y, W(9).z - W(0).z];
  const flex = deg(Math.atan2(-v[2], -v[1]));                  // 0 = fingers up, + = tilted toward camera
  const roll = deg(Math.atan2(p17[1] - i5[1], p17[0] - i5[0]));
  const d3 = (a, b) => Math.hypot(W(a).x - W(b).x, W(a).y - W(b).y, W(a).z - W(b).z);
  const palm3 = Math.max(d3(0, 9), 1e-6);
  const pinch = d3(4, 8) / palm3;
  const open = [8, 12, 16, 20].reduce((s, i) => s + d3(0, i), 0) / 4 / palm3;   // ~1.9 open hand, ~0.9 fist
  return { x: 1 - cx, y: cy, size, flex, roll, pinch, open };  // x mirrored: your right = picture right
}

// One-Euro filter per feature: heavy smoothing while the hand is still (kills camera jitter), light
// while it moves fast (no lag). SCALE = a "noticeable" change of each feature, so one beta fits all.
const SCALE = { x: 0.02, y: 0.02, size: 0.01, flex: 4, roll: 4, pinch: 0.08, open: 0.08 };
const euro = {};
let lastT = 0;
const alphaFor = (cutoff, dt) => 1 / (1 + 1 / (2 * Math.PI * cutoff * dt));
function smooth(f) {
  const now = performance.now() / 1000, dt = Math.min(Math.max(now - lastT, 1 / 120), 0.2);
  lastT = now;
  if (!feat) { for (const k in f) euro[k] = { x: f[k], dx: 0 }; return { ...f }; }
  const s = val("#hgSmooth") / 100;                             // 0 = raw, 1 = very smooth
  const minCut = 4.0 * (1 - s) + 0.15, beta = 0.6;               // Hz, Hz per (scale/s)
  const out = {};
  for (const k of Object.keys(f)) {
    const e = euro[k] || (euro[k] = { x: f[k], dx: 0 });
    const d = k === "roll" ? wrap(f[k] - e.x) : f[k] - e.x;
    e.dx += alphaFor(1.0, dt) * (d / dt - e.dx);                 // smoothed speed
    const cut = minCut + beta * Math.abs(e.dx) / SCALE[k];
    e.x += alphaFor(cut, dt) * d;
    out[k] = e.x;
  }
  return out;
}

// where the arm is now, in the hand-control coordinates (from the live state of app.js)
function armPose() {
  if (!S || !S.tool) return { ...ARM_NEUTRAL };
  const [x, y, z] = S.tool.xyz_mm.map(v => v / 1000);
  return { pan: deg(Math.atan2(y, x)), r: Math.hypot(x, y), z, pitch: S.tool.pitch_deg, roll: S.measured.wrist_roll };
}

function target(f, n, a = ARM_NEUTRAL) {
  const side = $("#hgMirror").checked ? 1 : -1;               // default: you stand behind the robot
  return {
    pan: clamp(a.pan + side * (f.x - n.x) * val("#hgPan"), -90, 90),
    z: clamp(a.z + (n.y - f.y) * val("#hgZ") / 100, 0.02, 0.30),
    r: clamp(a.r + (f.size / n.size - 1) * val("#hgR") / 100, 0.12, 0.33),
    pitch: $("#hgPitchOff").checked ? clamp(a.pitch, -90, 30) : clamp(a.pitch - (f.flex - n.flex), -90, 30),
    roll: $("#hgRollOff").checked ? null : a.roll - wrap(f.roll - n.roll),
    grip: $("#hgGrip").value === "pinch"
      ? clamp((f.pinch - 0.25) / (0.9 - 0.25), 0, 1) * 95
      : clamp((f.open - 1.05) / (1.75 - 1.05), 0, 1) * 95,  // fist = closed, open hand = open
  };
}

// ------------------------------------------------------------------ per frame
let lastVideoTime = -1;
function loop() {
  if (!running) return;
  if (video.currentTime !== lastVideoTime && video.videoWidth) {
    lastVideoTime = video.currentTime;
    const res = landmarker.detectForVideo(video, performance.now());
    canvas.width = video.videoWidth; canvas.height = video.videoHeight;
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    const lm = res.landmarks?.[0], world = res.worldLandmarks?.[0];
    const follow = $("#handFollow").checked;
    if (lm && world) {
      draw(lm);
      feat = smooth(features(lm, world));
      steady++;
      if (!neutral && steady >= STEADY_FRAMES) {               // engage: tie this hand pose to the arm's pose
        neutral = { ...feat };
        armRef = follow ? armPose() : null;
      }
      if (!neutral) {
        status(follow ? "Hold your hand steady…" : "", "visible – hold steady");
      } else {
        const t = target(feat, neutral, armRef || ARM_NEUTRAL);
        show(t);
        status(follow ? "" : "Tick “Follow my hand” to move the arm", follow ? "following" : "visible");
        if (follow) send(t);
      }
    } else {
      feat = null; steady = 0;                                 // clutch released: arm holds (server timeout)
      if (follow) neutral = null;                              // next time the hand appears, re-engage
      for (const k in euro) delete euro[k];
      status(follow ? "Raise your hand into view (palm to the camera)" : "Show your hand to the camera",
        "no hand – arm holds still");
    }
  }
  requestAnimationFrame(loop);
}

function status(onVideo, line) {
  const m = $("#handMsg");
  if (m.textContent !== onVideo) m.textContent = onVideo;
  $("#hrSeen").textContent = line;
}

function draw(lm) {
  const W = canvas.width, H = canvas.height;
  ctx.lineWidth = 3; ctx.strokeStyle = "#219CBC"; ctx.fillStyle = "#FDB713";
  for (const [a, b] of BONES) {
    ctx.beginPath(); ctx.moveTo(lm[a].x * W, lm[a].y * H); ctx.lineTo(lm[b].x * W, lm[b].y * H); ctx.stroke();
  }
  for (const p of lm) { ctx.beginPath(); ctx.arc(p.x * W, p.y * H, 4, 0, 7); ctx.fill(); }
}

function show(t) {
  $("#hrPan").textContent = `${t.pan.toFixed(0)}°`;
  $("#hrR").textContent = `${(t.r * 100).toFixed(1)} cm`;
  $("#hrZ").textContent = `${(t.z * 100).toFixed(1)} cm`;
  $("#hrPitch").textContent = `${t.pitch.toFixed(0)}°`;
  $("#hrRoll").textContent = t.roll === null ? "locked" : `${t.roll.toFixed(0)}°`;
  $("#hrGrip").textContent = `${t.grip.toFixed(0)} %`;
}

async function send(t) {
  const now = performance.now();
  if (busy || now - lastSend < SEND_MS || !ready()) return;
  busy = true; lastSend = now;
  try {
    const body = { pan: t.pan, r: t.r, z: t.z, pitch: t.pitch, grip: t.grip };
    if (t.roll !== null) body.roll = t.roll;
    await api("hand", body);
  } catch (_) {
    $("#handFollow").checked = false;                          // stop on any refusal (E-STOP, torque off…)
  } finally { busy = false; }
}

// ------------------------------------------------------------------ controls
async function listCameras() {
  const sel = $("#handCamSel"), keep = sel.value || video.srcObject?.getVideoTracks()[0]?.getSettings().deviceId || "";
  const cams = (await navigator.mediaDevices.enumerateDevices()).filter(d => d.kind === "videoinput");
  sel.innerHTML = `<option value="">Default camera</option>` +
    cams.map((d, i) => `<option value="${d.deviceId}">${escapeHtml(d.label || `Camera ${i + 1}`)}</option>`).join("");
  if (cams.some(d => d.deviceId === keep)) sel.value = keep;
}
listCameras().catch(() => {});
$("#handCamSel").onchange = () => { if (running) { stopCamera(); startCamera(); } };
$("#handCam").onclick = () => (running ? stopCamera() : startCamera());
$("#handNeutral").onclick = () => {
  if (!feat) { toast("Show your hand to the camera first"); return; }
  neutral = { ...feat }; armRef = $("#handFollow").checked ? armPose() : null;
  toast("Neutral set: this hand pose = where the arm is now");
};
$("#handFollow").onchange = e => {
  e.target.blur();
  neutral = null; steady = 0;                                  // engage on the next steady hand
  if (!e.target.checked) return;
  if (!ready()) { e.target.checked = false; toast("Connect the arm (or the simulated one) first"); return; }
  toast("Following: hold your hand steady in view to start – the arm starts from where it is");
};
for (const id of ["#hgPan", "#hgZ", "#hgR", "#hgSmooth"]) {
  const inp = $(id); inp.oninput = () => (inp.closest(".slider").querySelector("output").textContent = inp.value);
}
