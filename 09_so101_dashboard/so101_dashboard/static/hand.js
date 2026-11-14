// Hand control: MediaPipe hand landmarks from this computer's camera -> POST /api/hand ~20x/s.
// The server does the IK and keeps every safety rule (limits, E-STOP, stall guard); this file only
// turns the hand into a target: pan / reach / height / pitch / roll / gripper, relative to a
// "neutral" hand pose captured when following starts (or with Set neutral).
// Uses app.js globals: $, api, ready, toast.
const MP = "https://cdn.jsdelivr.net/npm/@mediapipe/tasks-vision@0.10.14";
const MODEL = "https://storage.googleapis.com/mediapipe-models/hand_landmarker/hand_landmarker/float16/1/hand_landmarker.task";
const SEND_MS = 50;
const ARM_NEUTRAL = { pan: 0, r: 0.22, z: 0.12, pitch: -40, roll: 0 };
const BONES = [[0, 1], [1, 2], [2, 3], [3, 4], [0, 5], [5, 6], [6, 7], [7, 8], [5, 9], [9, 10], [10, 11], [11, 12],
  [9, 13], [13, 14], [14, 15], [15, 16], [13, 17], [0, 17], [17, 18], [18, 19], [19, 20]];

const video = $("#handVideo"), canvas = $("#handCanvas"), ctx = canvas.getContext("2d");
let landmarker = null, running = false, feat = null, neutral = null, lastSend = 0, busy = false;

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
  const pinch = d3(4, 8) / Math.max(d3(0, 9), 1e-6);
  return { x: 1 - cx, y: cy, size, flex, roll, pinch };       // x mirrored: your right = picture right
}

function smooth(f) {
  if (!feat) return f;
  const a = 1 - val("#hgSmooth") / 100;
  const out = {};
  for (const k of Object.keys(f)) out[k] = k === "roll" ? feat[k] + a * wrap(f[k] - feat[k]) : feat[k] + a * (f[k] - feat[k]);
  return out;
}

function target(f, n) {
  const side = $("#hgMirror").checked ? 1 : -1;               // default: you stand behind the robot
  return {
    pan: clamp(side * (f.x - n.x) * val("#hgPan") + ARM_NEUTRAL.pan, -90, 90),
    z: clamp(ARM_NEUTRAL.z + (n.y - f.y) * val("#hgZ") / 100, 0.02, 0.30),
    r: clamp(ARM_NEUTRAL.r + (f.size / n.size - 1) * val("#hgR") / 100, 0.12, 0.33),
    pitch: $("#hgPitchOff").checked ? ARM_NEUTRAL.pitch : clamp(ARM_NEUTRAL.pitch - (f.flex - n.flex), -90, 30),
    roll: $("#hgRollOff").checked ? null : ARM_NEUTRAL.roll - wrap(f.roll - n.roll),
    grip: clamp((f.pinch - 0.25) / (0.9 - 0.25), 0, 1) * 100,
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
    if (lm && world) {
      draw(lm);
      feat = smooth(features(lm, world));
      if (!neutral) neutral = { ...feat };
      $("#hrSeen").textContent = "visible";
      const t = target(feat, neutral);
      show(t);
      if ($("#handFollow").checked) send(t);
    } else {
      feat = null;                                             // arm holds (server timeout)
      $("#hrSeen").textContent = "no hand – arm holds still";
    }
  }
  requestAnimationFrame(loop);
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
$("#handNeutral").onclick = () => { if (feat) { neutral = { ...feat }; toast("Neutral set: this hand pose = arm's middle pose"); } };
$("#handFollow").onchange = e => {
  e.target.blur();
  if (!e.target.checked) return;
  if (!ready()) { e.target.checked = false; toast("Connect the arm (or the simulated one) first"); return; }
  if (!feat) { e.target.checked = false; toast("Show your hand to the camera first"); return; }
  neutral = { ...feat };
  toast("Following – your current hand pose is the arm's middle pose");
};
for (const id of ["#hgPan", "#hgZ", "#hgR", "#hgSmooth"]) {
  const inp = $(id); inp.oninput = () => (inp.closest(".slider").querySelector("output").textContent = inp.value);
}
