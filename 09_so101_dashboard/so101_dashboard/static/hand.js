// Hand / arm control from this computer's camera (MediaPipe, in the browser). Three modes:
//
// "Side view" (default): you sit/stand side-on to the camera, which sees your arm in profile (2D,
//   no depth guessing). Direct joint control in one vertical plane, base fixed by a slider:
//   your upper arm -> robot shoulder, your elbow -> robot elbow, your hand's angle -> robot wrist,
//   your finger curl -> gripper. Clutch: when following starts, your arm's angles are tied to the
//   robot's current joints, then your angle CHANGES move it (SIDE_JOINTS) - so it never jumps.
//   Joint angles are One-Euro filtered, clamped to safe ranges and sent at a steady 20 Hz to
//   POST /api/mimic (server: noise hold, smooth accel/brake, table guard).
//
// "My whole arm" (default): body tracking finds where your hand is relative to your shoulder, as a
//   fraction of your arm's length, and puts the gripper at the matching spot of the robot's picking
//   workspace (ARM_SPACE); your hand's direction gives the tool pitch, its turn the wrist roll.
//   Same posture = same gripper position. Sent to POST /api/hand, so the server's IK, reach edge,
//   noise hold-zone and table limit apply. Following eases in from where the robot is (RAMP_S).
// "My hand": the hand is a 3D cursor -> POST /api/hand (the server does the IK). Works like a mouse
//   with a clutch: when the hand is steady, its pose is tied to where the arm IS, and only hand
//   movement from there moves it. Hand out of view = clutch released.
//
// Both: One-Euro smoothing, fist / open hand (or pinch) for the gripper, out of view = robot holds.
// Every limit, the E-STOP and the gripper stall guard are on the server. Uses app.js globals:
// $, $$, api, ready, toast, escapeHtml, S.
const MP = "https://cdn.jsdelivr.net/npm/@mediapipe/tasks-vision@0.10.14";
const HAND_MODEL = "https://storage.googleapis.com/mediapipe-models/hand_landmarker/hand_landmarker/float16/1/hand_landmarker.task";
const POSE_MODEL = "https://storage.googleapis.com/mediapipe-models/pose_landmarker/pose_landmarker_lite/float16/1/pose_landmarker_lite.task";
const SEND_MS = 50;
const ARM_NEUTRAL = { pan: 0, r: 0.22, z: 0.12, pitch: -40, roll: 0 };    // before the arm reports a pose
const STEADY_FRAMES = 12;                                     // ~0.4 s of steady tracking before engaging
const RAMP_S = 1.5;                                           // arm mode: ease into the mapped pose
// your hand relative to your shoulder (in arm lengths) -> robot workspace (m / deg). The robot side is
// its picking workspace: 15-33 cm out, from the table to 25 cm up.
const ARM_SPACE = {
  reach:  { from: [0.2, 1.0],  to: [0.15, 0.33] },           // hand close to the body -> arm straight out
  height: { from: [-0.9, 0.2], to: [0.02, 0.25] },           // hand at the hip -> a bit above the shoulder
  pan:    { from: [-75, 75],   to: [-75, 75] },              // hand to your left / right (deg)
  roll:   { from: [-70, 70],   to: [-60, 60] },              // palm down = 0
};
const HAND_BONES = [[0, 1], [1, 2], [2, 3], [3, 4], [0, 5], [5, 6], [6, 7], [7, 8], [5, 9], [9, 10], [10, 11], [11, 12],
  [9, 13], [13, 14], [14, 15], [15, 16], [13, 17], [0, 17], [17, 18], [18, 19], [19, 20]];
// your angle change -> robot joint change (sign) and the robot's safe range for this mode (deg)
const SIDE_JOINTS = {
  shoulder_lift: { from: "lift",  sign: +1, range: [-95, 60] },  // raise your upper arm forward = lean out
  elbow_flex:    { from: "elbow", sign: -1, range: [-60, 97] },  // bend your elbow (forearm up) = forearm up
  wrist_flex:    { from: "wrist", sign: -1, range: [-40, 95] },  // hand down = gripper down
};
// finger curl (mean bend of the 4 fingers over their 3 joints, deg): open hand ~30, fist ~200+
const CURL_OPEN = 70, CURL_CLOSED = 150;
const SIDES = { right: { sh: 12, el: 14, wr: 16, idx: 20 }, left: { sh: 11, el: 13, wr: 15, idx: 19 } };

const video = $("#handVideo"), canvas = $("#handCanvas"), ctx = canvas.getContext("2d");
let handLm = null, poseLm = null, running = false, feat = null, neutral = null, lastSend = 0, busy = false;
let armRef = null, steady = 0, mode = "side", engage = null, armLen = 0, facing = 1;

const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));
const deg = r => (r * 180) / Math.PI;
const wrap = a => ((a + 540) % 360) - 180;
const val = id => +$(id).value;
const sub = (a, b) => [a.x - b.x, a.y - b.y, a.z - b.z];
const dot = (a, b) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
const cross = (a, b) => [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];
const norm = a => { const n = Math.hypot(...a) || 1; return a.map(v => v / n); };
const minus = (a, b) => a.map((v, i) => v - b[i]);
const scale = (a, k) => a.map(v => v * k);
const angle = (a, b) => deg(Math.acos(clamp(dot(norm(a), norm(b)), -1, 1)));

// ------------------------------------------------------------------ camera + models
async function vision() {
  const m = await import(`${MP}/vision_bundle.mjs`);
  return { m, files: await m.FilesetResolver.forVisionTasks(`${MP}/wasm`) };
}

async function startCamera() {
  $("#handCam").disabled = true;
  try {
    $("#handMsg").textContent = "Loading tracking models…";
    if (!handLm || !poseLm) {
      const { m, files } = await vision();
      handLm = handLm || await m.HandLandmarker.createFromOptions(files, {
        baseOptions: { modelAssetPath: HAND_MODEL, delegate: "GPU" }, runningMode: "VIDEO", numHands: 2,
        minHandDetectionConfidence: 0.6, minTrackingConfidence: 0.6 });
      poseLm = poseLm || await m.PoseLandmarker.createFromOptions(files, {
        baseOptions: { modelAssetPath: POSE_MODEL, delegate: "GPU" }, runningMode: "VIDEO", numPoses: 1,
        minPoseDetectionConfidence: 0.6, minTrackingConfidence: 0.6 });
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
  release();
}

function release() {                                           // clutch out: the robot holds
  feat = null; neutral = null; engage = null; steady = 0; armLen = 0;
  for (const k in euro) delete euro[k];
}

// ------------------------------------------------------------------ hand -> features
function handFeatures(lm, world) {
  const ar = video.videoWidth / video.videoHeight;            // landmarks are 0..1 on both axes
  const P = i => [lm[i].x * ar, lm[i].y];
  const palm = [0, 5, 9, 17];
  const cx = palm.reduce((s, i) => s + lm[i].x, 0) / 4, cy = palm.reduce((s, i) => s + lm[i].y, 0) / 4;
  const [w0, m9, i5, p17] = [P(0), P(9), P(5), P(17)];
  const size = Math.hypot(m9[0] - w0[0], m9[1] - w0[1]);
  const v = sub(world[9], world[0]);
  const flex = deg(Math.atan2(-v[2], -v[1]));                  // 0 = fingers up, + = tilted toward camera
  const roll = deg(Math.atan2(p17[1] - i5[1], p17[0] - i5[0]));
  return { x: 1 - cx, y: cy, size, flex, roll, ...gripFeatures(world) };  // x mirrored: your right = picture right
}

function gripFeatures(world) {
  const d3 = (a, b) => Math.hypot(...sub(world[a], world[b]));
  const palm3 = Math.max(d3(0, 9), 1e-6);
  // curl = bend at the knuckle, middle and end joint of each finger: 3D angles, so it reads the
  // same whichever way the hand faces the camera (fingertip distances do not, seen edge-on)
  const curl = [[5, 6, 7, 8], [9, 10, 11, 12], [13, 14, 15, 16], [17, 18, 19, 20]].reduce((s, [m, p, d, t]) =>
    s + angle(sub(world[m], world[0]), sub(world[p], world[m])) + angle(sub(world[p], world[m]), sub(world[d], world[p]))
      + angle(sub(world[d], world[p]), sub(world[t], world[d])), 0) / 4;
  return { pinch: d3(4, 8) / palm3, curl };
}

function gripValue(f) {
  const g = $("#hgGrip").value === "pinch"
    ? clamp((f.pinch - 0.25) / (0.9 - 0.25), 0, 1) * 95
    : clamp((CURL_CLOSED - f.curl) / (CURL_CLOSED - CURL_OPEN), 0, 1) * 95;   // fist = closed
  return g < 12 ? 0 : g;                                       // a closing hand snaps fully shut
}

// ------------------------------------------------------------------ body -> arm angles
// Angles are measured in a frame attached to your torso (shoulders + hips), so turning toward the
// camera or leaning does not move the robot. MediaPipe world landmarks are metres, camera axes.
function armFeatures(W, side, hand, prev, hipsSeen = true) {
  const s = SIDES[side];
  const mid = (a, b) => [(W[a].x + W[b].x) / 2, (W[a].y + W[b].y) / 2, (W[a].z + W[b].z) / 2];
  // torso up from hips to shoulders; sitting at a desk the hips are often out of view: then
  // use the camera's vertical (world y points down)
  const up = hipsSeen ? norm(minus(mid(11, 12), mid(23, 24))) : [0, -1, 0];
  let across = sub(W[12], W[11]);                              // toward your right shoulder
  across = norm(minus(across, scale(up, dot(across, up))));
  let fwd = norm(cross(up, across));
  if (fwd[2] > 0) fwd = scale(fwd, -1);                        // forward = toward the camera
  const upper = sub(W[s.el], W[s.sh]), fore = sub(W[s.wr], W[s.el]);
  const L = Math.hypot(...upper) + Math.hypot(...fore);        // your arm length: the scale
  const w = sub(W[s.wr], W[s.sh]);
  const [a, u, f] = [dot(w, across), dot(w, up), dot(w, fwd)];
  const flat = Math.hypot(a, f);
  const handDir = hand ? sub(hand[9], hand[0]) : sub(W[s.idx], W[s.wr]);
  const ft = {
    reach: flat / L,
    height: u / L,
    // hand close to the body's vertical line: its left/right direction is noise, keep the last
    pan: flat > 0.25 * L ? deg(Math.atan2(a, f)) : prev ? prev.pan : 0,
    // hand pointing down = -90, level = 0
    pitch: deg(Math.atan2(dot(handDir, up), Math.hypot(dot(handDir, across), dot(handDir, fwd)))),
  };
  if (hand) {
    // palm line (index -> pinky knuckles) around the forearm axis; palm facing down = 0
    const ax = norm(fore), pv = sub(hand[17], hand[5]);
    const p = minus(pv, scale(ax, dot(pv, ax))), ref = minus(up, scale(ax, dot(up, ax)));
    ft.roll = wrap(deg(Math.atan2(dot(cross(ref, p), ax), dot(ref, p))) - 90);
    Object.assign(ft, gripFeatures(hand));
  } else if (prev) {
    for (const k of ["roll", "pinch", "curl"]) if (k in prev) ft[k] = prev[k];
  }
  return ft;
}

function armTarget(f) {
  const map = (k, v) => {
    const { from: [a, b], to: [c, d] } = ARM_SPACE[k];
    return c + (d - c) * clamp((v - a) / (b - a), 0, 1);
  };
  return {
    pan: map("pan", ($("#hgMirror").checked ? -1 : 1) * -f.pan),  // your right = robot's right = -y
    r: map("reach", f.reach),
    z: map("height", f.height),
    pitch: clamp(f.pitch, -90, 0),
    roll: "roll" in f && !$("#hgRollOff").checked ? map("roll", f.roll) : null,
    grip: "curl" in f ? gripValue(f) : null,
  };
}

// the hand landmarker sees up to 2 hands: take the one at the tracked arm's wrist
function handAt(hres, wristPx, image = false) {
  let best = null, bd = 0.12;
  (hres.landmarks || []).forEach((lm, i) => {
    const d = Math.hypot(lm[0].x - wristPx.x, lm[0].y - wristPx.y);
    if (d < bd) { bd = d; best = image ? { lm, world: hres.worldLandmarks[i] } : hres.worldLandmarks[i]; }
  });
  return best;
}

// ------------------------------------------------------------------ side view: arm in profile
// Image coordinates (x scaled by the aspect ratio so angles are true). dirAngle: forward = 0,
// up = +90, down = -90, whichever way you face.
function sideFeatures(P, s, hand, prev) {
  const ar = video.videoWidth / video.videoHeight;
  const pt = p => [p.x * ar, p.y];
  const [sh, el, wr] = [pt(P[s.sh]), pt(P[s.el]), pt(P[s.wr])];
  const nose = pt(P[0]);                                      // the nose is in front of the shoulder
  if (Math.abs(nose[0] - sh[0]) > 0.02) facing = Math.sign(nose[0] - sh[0]);
  const dirAngle = v => deg(Math.atan2(-v[1], facing * v[0]));
  const upper = [el[0] - sh[0], el[1] - sh[1]], fore = [wr[0] - el[0], wr[1] - el[1]];
  const hd = hand ? [(hand.lm[9].x - hand.lm[0].x) * ar, hand.lm[9].y - hand.lm[0].y]
    : [pt(P[s.idx])[0] - wr[0], pt(P[s.idx])[1] - wr[1]];
  // side-on check: seen from the front, the shoulders are far apart compared with the upper arm
  const shoulderGap = Math.abs(pt(P[11])[0] - pt(P[12])[0]);
  const f = {
    lift: dirAngle(upper) + 90,                                // 0 hanging, 90 forward, 180 up
    elbow: wrap(dirAngle(fore) - dirAngle(upper)),             // 0 straight, +90 forearm bent up
    wrist: wrap(dirAngle(hd) - dirAngle(fore)),                // + hand bent up, - bent down
    frontal: shoulderGap / Math.max(Math.hypot(...upper), 1e-3),
  };
  if (hand) Object.assign(f, gripFeatures(hand.world));
  else if (prev) for (const k of ["pinch", "curl"]) if (k in prev) f[k] = prev[k];
  return f;
}

// robot joints = joints when following started + your angle changes since then
function sideTarget(f, ref) {
  const t = { shoulder_pan: val("#planePan") };
  for (const [j, m] of Object.entries(SIDE_JOINTS)) {
    const base = ref ? ref.robot[j] : 0, d = ref ? wrap(f[m.from] - ref.human[m.from]) : 0;
    t[j] = clamp(base + m.sign * d, ...m.range);
  }
  if ("curl" in f || "pinch" in f) t.gripper = gripValue(f);
  return t;
}

// side-on, the arm nearer the camera is the better-seen one
function nearerSide(P) {
  const score = s => Math.min(...[s.sh, s.el, s.wr].map(i => P[i].visibility ?? 1));
  return score(SIDES.right) >= score(SIDES.left) ? "right" : "left";
}

// ------------------------------------------------------------------ smoothing
// One-Euro filter per feature: heavy smoothing while still (kills camera jitter), light while moving
// fast (no lag). SCALE = a "noticeable" change of each feature, so one beta fits all.
const SCALE = { x: 0.02, y: 0.02, size: 0.01, flex: 4, roll: 4, pinch: 0.08, open: 0.08,
  reach: 0.02, height: 0.02, pitch: 4, pan: 3, lift: 3, elbow: 3, wrist: 4, frontal: 0.1, curl: 10 };
const euro = {};
let lastT = 0;
const alphaFor = (cutoff, dt) => 1 / (1 + 1 / (2 * Math.PI * cutoff * dt));
function smooth(f) {
  const now = performance.now() / 1000, dt = Math.min(Math.max(now - lastT, 1 / 120), 0.2);
  lastT = now;
  const s = val("#hgSmooth") / 100;                             // 0 = raw, 1 = very smooth
  const minCut = 4.0 * (1 - s) + 0.15, beta = 0.6;               // Hz, Hz per (scale/s)
  const out = {};
  for (const k of Object.keys(f)) {
    const e = euro[k];
    if (!e) { euro[k] = { x: f[k], dx: 0 }; out[k] = f[k]; continue; }
    const d = ["roll", "pan", "elbow", "wrist"].includes(k) ? wrap(f[k] - e.x) : f[k] - e.x;
    e.dx += alphaFor(1.0, dt) * (d / dt - e.dx);                 // smoothed speed
    const cut = minCut + beta * Math.abs(e.dx) / SCALE[k];
    e.x += alphaFor(cut, dt) * d;
    out[k] = e.x;
  }
  return out;
}

// ------------------------------------------------------------------ hand mode mapping
function armPose() {                                           // where the tool is now, hand-mode coordinates
  if (!S || !S.tool) return { ...ARM_NEUTRAL };
  const [x, y, z] = S.tool.xyz_mm.map(v => v / 1000);
  return { pan: deg(Math.atan2(y, x)), r: Math.hypot(x, y), z, pitch: S.tool.pitch_deg, roll: S.measured.wrist_roll };
}

function handTarget(f, n, a = ARM_NEUTRAL) {
  const side = $("#hgMirror").checked ? 1 : -1;               // default: you stand behind the robot
  return {
    pan: clamp(a.pan + side * (f.x - n.x) * val("#hgPan"), -90, 90),
    z: clamp(a.z + (n.y - f.y) * val("#hgZ") / 100, 0.02, 0.30),
    r: clamp(a.r + (f.size / n.size - 1) * val("#hgR") / 100, 0.12, 0.33),
    pitch: $("#hgPitchOff").checked ? clamp(a.pitch, -90, 30) : clamp(a.pitch - (f.flex - n.flex), -90, 30),
    roll: $("#hgRollOff").checked ? null : a.roll - wrap(f.roll - n.roll),
    grip: gripValue(f),
  };
}

// ------------------------------------------------------------------ per frame
let lastVideoTime = -1;
function loop() {
  if (!running) return;
  if (video.currentTime !== lastVideoTime && video.videoWidth) {
    lastVideoTime = video.currentTime;
    canvas.width = video.videoWidth; canvas.height = video.videoHeight;
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    const ts = performance.now();
    if (mode === "side") sideFrame(ts); else if (mode === "arm") armFrame(ts); else handFrame(ts);
  }
  requestAnimationFrame(loop);
}

function handFrame(ts) {
  const res = handLm.detectForVideo(video, ts);
  const lm = res.landmarks?.[0], world = res.worldLandmarks?.[0];
  const follow = $("#handFollow").checked;
  if (!(lm && world)) {
    release();
    status(follow ? "Raise your hand into view (palm to the camera)" : "Show your hand to the camera",
      "no hand – robot holds still");
    return;
  }
  drawHand(lm);
  feat = smooth(handFeatures(lm, world));
  steady++;
  if (!neutral && steady >= STEADY_FRAMES) {                   // engage: tie this hand pose to the arm's pose
    neutral = { ...feat };
    armRef = follow ? armPose() : null;
  }
  if (!neutral) { status(follow ? "Hold your hand steady…" : "", "hand – hold steady"); return; }
  const t = handTarget(feat, neutral, armRef || ARM_NEUTRAL);
  showHand(t);
  status(follow ? "" : "Tick “Follow me” to move the robot", follow ? "hand – following" : "hand");
  if (follow) send("hand", { pan: t.pan, r: t.r, z: t.z, pitch: t.pitch, grip: t.grip, ...(t.roll === null ? {} : { roll: t.roll }) });
}

function armFrame(ts) {
  const pres = poseLm.detectForVideo(video, ts);
  const P = pres.landmarks?.[0], W = pres.worldLandmarks?.[0];
  const side = $("#armSide").value, s = SIDES[side];
  const follow = $("#handFollow").checked;
  const vis = i => (P[i].visibility ?? 1) > 0.5;
  const seen = P && W && [11, 12, s.sh, s.el, s.wr].every(vis);
  if (!seen) {
    release();
    status(follow ? `Step back: the camera must see your shoulders and ${side} arm` : `Show your shoulders and ${side} arm`,
      "no arm – robot holds still");
    return;
  }
  const hres = handLm.detectForVideo(video, ts);
  const hand = handAt(hres, P[s.wr]);
  drawArm(P, s);
  feat = smooth(armFeatures(W, side, hand, feat, vis(23) && vis(24)));
  const t = armTarget(feat);
  showArm(t, !!hand);
  steady++;
  if (!follow || steady < STEADY_FRAMES) {
    status(follow ? "Hold still…" : "Tick “Follow me” to move the robot", follow ? "arm – hold still" : "arm");
    return;
  }
  if (!engage) engage = { t0: performance.now(), from: armPose() };
  const u = clamp((performance.now() - engage.t0) / 1000 / RAMP_S, 0, 1);
  const e = engage.from, mix = (a, b) => a + (b - a) * u;      // ease from where the gripper is
  const body = { pan: mix(e.pan, t.pan), r: mix(e.r, t.r), z: mix(Math.max(e.z, 0.02), t.z), pitch: mix(clamp(e.pitch, -90, 0), t.pitch) };
  if (t.roll !== null) body.roll = mix(e.roll, t.roll);
  if (t.grip !== null) body.grip = t.grip;
  status("", u < 1 ? "arm – easing in" : "arm – following");
  send("hand", body);
}

function sideFrame(ts) {
  const pres = poseLm.detectForVideo(video, ts);
  const P = pres.landmarks?.[0];
  const follow = $("#handFollow").checked;
  const side = P ? nearerSide(P) : "right", s = SIDES[side];
  const seen = P && [s.sh, s.el, s.wr].every(i => (P[i].visibility ?? 1) > 0.5);
  if (!seen) {
    release();
    status(follow ? "Turn sideways: the camera must see your arm in profile" : "Show your arm side-on to the camera",
      "no arm – robot holds still");
    return;
  }
  const hand = handAt(handLm.detectForVideo(video, ts), P[s.wr], true);
  drawArm(P, s, false);
  if (hand) drawHand(hand.lm);
  feat = smooth(sideFeatures(P, s, hand, feat));
  if (feat.frontal > 0.8) {                                    // facing the camera: angles are meaningless
    release();
    status("Turn SIDEWAYS – the camera should see your arm from the side", "facing the camera – robot holds still");
    return;
  }
  steady++;
  if (follow && steady >= STEADY_FRAMES && !engage && S && S.measured)
    engage = { human: { ...feat }, robot: { ...S.measured } };  // clutch in: your pose = robot's pose now
  const t = sideTarget(feat, engage);
  showSide(t, !!hand);
  if (!follow || !engage) {
    status(follow ? "Hold still…" : "Tick “Follow me” to move the robot", follow ? `${side} arm – hold still` : `${side} arm`);
    return;
  }
  status("", `${side} arm – following`);
  send("mimic", { joints: t });
}

function showSide(t, hand) {
  $("#hrPan").textContent = `${t.shoulder_pan.toFixed(0)}°`;
  $("#hrR").textContent = `shoulder ${t.shoulder_lift.toFixed(0)}°`;
  $("#hrZ").textContent = `elbow ${t.elbow_flex.toFixed(0)}°`;
  $("#hrPitch").textContent = `wrist ${t.wrist_flex.toFixed(0)}°`;
  $("#hrRoll").textContent = "fixed";
  $("#hrGrip").textContent = "gripper" in t
    ? `${t.gripper.toFixed(0)} %  (finger curl ${feat.curl !== undefined ? feat.curl.toFixed(0) + "°" : "–"})${hand ? "" : " – hand not seen"}`
    : "– (show your hand)";
}

function status(onVideo, line) {
  const m = $("#handMsg");
  if (m.textContent !== onVideo) m.textContent = onVideo;
  $("#hrSeen").textContent = line;
}

function drawHand(lm) {
  const W = canvas.width, H = canvas.height;
  ctx.lineWidth = 3; ctx.strokeStyle = "#219CBC"; ctx.fillStyle = "#FDB713";
  for (const [a, b] of HAND_BONES) {
    ctx.beginPath(); ctx.moveTo(lm[a].x * W, lm[a].y * H); ctx.lineTo(lm[b].x * W, lm[b].y * H); ctx.stroke();
  }
  for (const p of lm) { ctx.beginPath(); ctx.arc(p.x * W, p.y * H, 4, 0, 7); ctx.fill(); }
}

function drawArm(P, s, shoulders = true) {
  const W = canvas.width, H = canvas.height;
  ctx.lineWidth = 5; ctx.strokeStyle = "#219CBC"; ctx.fillStyle = "#FDB713";
  for (const [a, b] of [...(shoulders ? [[11, 12]] : []), [s.sh, s.el], [s.el, s.wr], [s.wr, s.idx]]) {
    ctx.beginPath(); ctx.moveTo(P[a].x * W, P[a].y * H); ctx.lineTo(P[b].x * W, P[b].y * H); ctx.stroke();
  }
  for (const i of [...(shoulders ? [11, 12] : [s.sh]), s.el, s.wr]) { ctx.beginPath(); ctx.arc(P[i].x * W, P[i].y * H, 7, 0, 7); ctx.fill(); }
}


function showHand(t) {
  $("#hrPan").textContent = `${t.pan.toFixed(0)}°`;
  $("#hrR").textContent = `${(t.r * 100).toFixed(1)} cm`;
  $("#hrZ").textContent = `${(t.z * 100).toFixed(1)} cm`;
  $("#hrPitch").textContent = `${t.pitch.toFixed(0)}°`;
  $("#hrRoll").textContent = t.roll === null ? "locked" : `${t.roll.toFixed(0)}°`;
  $("#hrGrip").textContent = `${t.grip.toFixed(0)} %`;
}

function showArm(t, hand) {
  showHand({ ...t, grip: t.grip ?? 0 });
  if (t.grip === null) $("#hrGrip").textContent = "– (show your hand)";
  else if (!hand) $("#hrGrip").textContent += " (hand not seen)";
}

async function send(cmd, body) {
  const now = performance.now();
  if (busy || now - lastSend < SEND_MS || !ready()) return;
  busy = true; lastSend = now;
  try {
    await api(cmd, body);
  } catch (_) {
    $("#handFollow").checked = false;                          // stop on any refusal (E-STOP, torque off…)
    release();
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

function setMode(m) {
  mode = m;
  $$("#followMode button").forEach(b => b.classList.toggle("on", b.dataset.mode === m));
  $("#armHelp").hidden = m !== "arm";
  $("#sideHelp").hidden = m !== "side";
  $("#handHelp").hidden = m !== "hand";
  $("#handNeutral").hidden = m !== "hand";
  release();
}

listCameras().catch(() => {});
setMode("side");
$$("#followMode button").forEach(b => (b.onclick = () => setMode(b.dataset.mode)));
$("#armSide").onchange = () => release();
$("#handCamSel").onchange = () => { if (running) { stopCamera(); startCamera(); } };
$("#handCam").onclick = () => (running ? stopCamera() : startCamera());
$("#handNeutral").onclick = () => {
  if (!feat) { toast("Show your hand to the camera first"); return; }
  neutral = { ...feat }; armRef = $("#handFollow").checked ? armPose() : null;
  toast("Neutral set: this hand pose = where the arm is now");
};
$("#handFollow").onchange = e => {
  e.target.blur();
  release();                                                   // (re)engage on the next steady frames
  if (!e.target.checked) return;
  if (!ready()) { e.target.checked = false; toast("Connect the arm (or the simulated one) first"); return; }
  if (mode === "side" && S && S.measured) {                   // work in the plane the robot already faces
    const p = Math.round(clamp(S.measured.shoulder_pan, -75, 75));
    $("#planePan").value = p; $("#planePan").closest(".slider").querySelector("output").textContent = `${p}°`;
  }
  toast(mode !== "hand" ? "Following: hold still a moment – the robot eases into your arm's pose"
    : "Following: hold your hand steady in view to start – the arm starts from where it is");
};
$("#planePan").oninput = e => (e.target.closest(".slider").querySelector("output").textContent = `${e.target.value}°`);
for (const id of ["#hgPan", "#hgZ", "#hgR", "#hgSmooth"]) {
  const inp = $(id); inp.oninput = () => (inp.closest(".slider").querySelector("output").textContent = inp.value);
}
