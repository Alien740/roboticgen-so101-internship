// 3D twin: a tiny WebGL2 renderer (no libraries, works offline).
// Mesh parts are in their LINK frame; each frame the server sends every link's 4x4 pose
// (column-major, base frame: x forward, y left, z up, metres) and we draw part = pose * vertex.
"use strict";

const Twin = (() => {
  // ------------------------------------------------------------ mat4 helpers (column-major)
  const M = {
    mul(a, b) {
      const o = new Float32Array(16);
      for (let c = 0; c < 4; c++) for (let r = 0; r < 4; r++) {
        let s = 0;
        for (let k = 0; k < 4; k++) s += a[k * 4 + r] * b[c * 4 + k];
        o[c * 4 + r] = s;
      }
      return o;
    },
    persp(fovy, aspect, near, far) {
      const f = 1 / Math.tan(fovy / 2), nf = 1 / (near - far);
      return new Float32Array([f / aspect, 0, 0, 0, 0, f, 0, 0, 0, 0, (far + near) * nf, -1, 0, 0, 2 * far * near * nf, 0]);
    },
    lookAt(eye, at, up) {
      const sub = (a, b) => [a[0] - b[0], a[1] - b[1], a[2] - b[2]];
      const norm = v => { const l = Math.hypot(...v) || 1; return v.map(x => x / l); };
      const cross = (a, b) => [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];
      const dot = (a, b) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
      const z = norm(sub(eye, at)), x = norm(cross(up, z)), y = cross(z, x);
      return new Float32Array([x[0], y[0], z[0], 0, x[1], y[1], z[1], 0, x[2], y[2], z[2], 0,
        -dot(x, eye), -dot(y, eye), -dot(z, eye), 1]);
    },
    I: new Float32Array([1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]),
  };

  const VS = `#version 300 es
  in vec3 aPos; uniform mat4 uVP, uModel; out vec3 vW;
  void main() { vec4 w = uModel * vec4(aPos, 1.0); vW = w.xyz; gl_Position = uVP * w; }`;
  const FS = `#version 300 es
  precision highp float; in vec3 vW; uniform vec3 uColor, uEye; uniform float uLit, uAlpha; out vec4 o;
  void main() {
    if (uLit < 0.5) { o = vec4(uColor, uAlpha); return; }
    vec3 n = normalize(cross(dFdx(vW), dFdy(vW)));
    vec3 v = normalize(uEye - vW);
    if (dot(n, v) < 0.0) n = -n;
    float d = 0.55 * max(dot(n, normalize(vec3(0.4, 0.3, 1.0))), 0.0)
            + 0.30 * max(dot(n, normalize(vec3(-0.6, -0.8, 0.4))), 0.0);
    float rim = pow(1.0 - max(dot(n, v), 0.0), 3.0) * 0.15;
    o = vec4(uColor * (0.32 + d) + rim, uAlpha);
  }`;

  const VIEWS = { iso: [0.75, 0.40, 1.05], front: [0.0, 0.08, 1.0], side: [-1.5708, 0.08, 1.0], top: [0.0, 1.45, 1.1] };

  function create(canvas, onStatus) {
    const gl = canvas.getContext("webgl2", { antialias: true });
    if (!gl) { onStatus("Your browser has no WebGL2 – the 3D twin is unavailable (everything else works)."); return null; }
    const sh = (type, src) => {
      const s = gl.createShader(type); gl.shaderSource(s, src); gl.compileShader(s);
      if (!gl.getShaderParameter(s, gl.COMPILE_STATUS)) throw new Error(gl.getShaderInfoLog(s));
      return s;
    };
    const prog = gl.createProgram();
    gl.attachShader(prog, sh(gl.VERTEX_SHADER, VS)); gl.attachShader(prog, sh(gl.FRAGMENT_SHADER, FS));
    gl.linkProgram(prog);
    const U = n => gl.getUniformLocation(prog, n);
    const u = { vp: U("uVP"), model: U("uModel"), color: U("uColor"), eye: U("uEye"), lit: U("uLit"), alpha: U("uAlpha") };
    const aPos = gl.getAttribLocation(prog, "aPos");

    const parts = [];          // {link, vao, count, color}
    const lines = [];          // {vao, count, color}
    let frames = null, dirty = true;
    const cam = { yaw: VIEWS.iso[0], pitch: VIEWS.iso[1], dist: VIEWS.iso[2], at: [0.12, 0, 0.17] };
    const dark = matchMedia("(prefers-color-scheme: dark)").matches;
    const COLORS = {
      printed: [0.90, 0.91, 0.92],                 // white printed arm parts
      holder: [0.008, 0.184, 0.286],               // motor holders, #022F49
      motor: [0.07, 0.07, 0.08],                   // black STS3215 servos
      grid: dark ? [0.22, 0.23, 0.24] : [0.80, 0.81, 0.82],    // neutral greys
      reach: [0.129, 0.612, 0.737],                            // teal #219CBC
      axisX: [0.85, 0.25, 0.25], axisY: [0.2, 0.7, 0.3],
    };

    function vao(pos, idx) {
      const v = gl.createVertexArray(); gl.bindVertexArray(v);
      const b = gl.createBuffer(); gl.bindBuffer(gl.ARRAY_BUFFER, b); gl.bufferData(gl.ARRAY_BUFFER, pos, gl.STATIC_DRAW);
      gl.enableVertexAttribArray(aPos); gl.vertexAttribPointer(aPos, 3, gl.FLOAT, false, 0, 0);
      if (idx) { const e = gl.createBuffer(); gl.bindBuffer(gl.ELEMENT_ARRAY_BUFFER, e); gl.bufferData(gl.ELEMENT_ARRAY_BUFFER, idx, gl.STATIC_DRAW); }
      gl.bindVertexArray(null);
      return v;
    }
    const b64 = (s, T) => { const bin = atob(s); const u8 = new Uint8Array(bin.length);
      for (let i = 0; i < bin.length; i++) u8[i] = bin.charCodeAt(i); return new T(u8.buffer); };

    function addLines(pts, color) {
      lines.push({ vao: vao(new Float32Array(pts)), count: pts.length / 3, color });
    }
    // floor grid (5 cm) and reach rings from the profile
    function buildFloor(reach) {
      const g = [];
      for (let i = -8; i <= 8; i++) g.push(-0.15, i * 0.05, 0, 0.45, i * 0.05, 0);
      for (let i = -3; i <= 9; i++) g.push(i * 0.05, -0.4, 0, i * 0.05, 0.4, 0);
      addLines(g, COLORS.grid);
      for (const r of reach) {
        const c = [];
        for (let i = 0; i < 96; i++) {
          const a0 = -Math.PI * 0.75 + (i / 96) * Math.PI * 1.5, a1 = -Math.PI * 0.75 + ((i + 1) / 96) * Math.PI * 1.5;
          c.push(r * Math.cos(a0), r * Math.sin(a0), 0.0005, r * Math.cos(a1), r * Math.sin(a1), 0.0005);
        }
        addLines(c, COLORS.reach);
      }
      addLines([0, 0, 0.001, 0.08, 0, 0.001], COLORS.axisX);
      addLines([0, 0, 0.001, 0, 0.08, 0.001], COLORS.axisY);
    }

    async function load(meshUrl, reach, headers) {
      buildFloor(reach);
      const r = await fetch(meshUrl, { headers });
      if (!r.ok) throw new Error("3D model missing (run: python -m so101_dashboard.mesh <stl folder>)");
      const m = await r.json();
      for (const p of m.parts) {
        const pos = b64(p.pos, Float32Array), idx = b64(p.idx, Uint32Array);
        parts.push({ link: p.link, vao: vao(pos, idx), count: idx.length, color: partColor(p) });
      }
      dirty = true;
    }

    function partColor(p) {
      if (p.kind === "motor") return COLORS.motor;
      if (/motor_holder/.test(p.part)) return COLORS.holder;
      return COLORS.printed;
    }

    function setFrames(f) { frames = f; dirty = true; }

    // ghost preview: a see-through teal arm playing a motion's poses over the real one
    let ghost = null;            // {frames: [...], rate, t0, onEnd}
    const GHOST_COLOR = [0.129, 0.612, 0.737];
    function playGhost(list, rate, onEnd) { ghost = { frames: list, rate, t0: performance.now(), onEnd }; dirty = true; }
    function stopGhost() { if (ghost) { const cb = ghost.onEnd; ghost = null; dirty = true; cb && cb(); } }
    function ghostFrame() {
      if (!ghost) return null;
      const i = Math.floor((performance.now() - ghost.t0) / 1000 * ghost.rate);
      if (i >= ghost.frames.length) { stopGhost(); return null; }
      dirty = true;
      return ghost.frames[i];
    }
    function setView(name) { const v = VIEWS[name]; if (v) { [cam.yaw, cam.pitch, cam.dist] = v; dirty = true; } }

    function draw() {
      const dpr = Math.min(window.devicePixelRatio || 1, 2);
      const w = Math.round(canvas.clientWidth * dpr), h = Math.round(canvas.clientHeight * dpr);
      if (canvas.width !== w || canvas.height !== h) { canvas.width = w; canvas.height = h; dirty = true; }
      const gf = ghostFrame();
      if (!dirty) return;
      dirty = false;
      gl.viewport(0, 0, w, h);
      gl.clearColor(0, 0, 0, 0); gl.clear(gl.COLOR_BUFFER_BIT | gl.DEPTH_BUFFER_BIT);
      gl.enable(gl.DEPTH_TEST);
      gl.useProgram(prog);
      const cp = Math.cos(cam.pitch);
      // camera orbits the target: yaw 0 = standing in front of the robot (+x) looking back at it
      const eye = [cam.at[0] + cam.dist * cp * Math.cos(cam.yaw),
                   cam.at[1] + cam.dist * cp * Math.sin(cam.yaw),
                   cam.at[2] + cam.dist * Math.sin(cam.pitch)];
      const up = Math.abs(cam.pitch) > 1.4 ? [-Math.cos(cam.yaw), -Math.sin(cam.yaw), 0] : [0, 0, 1];
      const vp = M.mul(M.persp(0.75, w / h, 0.01, 10), M.lookAt(eye, cam.at, up));
      gl.uniformMatrix4fv(u.vp, false, vp);
      gl.uniform3fv(u.eye, eye);

      gl.uniform1f(u.lit, 0);
      gl.uniform1f(u.alpha, 1);
      gl.uniformMatrix4fv(u.model, false, M.I);
      for (const l of lines) { gl.uniform3fv(u.color, l.color); gl.bindVertexArray(l.vao); gl.drawArrays(gl.LINES, 0, l.count); }

      if (frames) {
        gl.uniform1f(u.lit, 1);
        for (const p of parts) {
          const T = frames[p.link];
          if (!T) continue;
          gl.uniformMatrix4fv(u.model, false, T);
          gl.uniform3fv(u.color, p.color);
          gl.bindVertexArray(p.vao);
          gl.drawElements(gl.TRIANGLES, p.count, gl.UNSIGNED_INT, 0);
        }
      }
      if (gf) {
        gl.uniform1f(u.lit, 1);
        gl.uniform1f(u.alpha, 0.38);
        gl.uniform3fv(u.color, GHOST_COLOR);
        gl.enable(gl.BLEND); gl.blendFunc(gl.SRC_ALPHA, gl.ONE_MINUS_SRC_ALPHA); gl.depthMask(false);
        for (const p of parts) {
          const T = gf[p.link];
          if (!T) continue;
          gl.uniformMatrix4fv(u.model, false, T);
          gl.bindVertexArray(p.vao);
          gl.drawElements(gl.TRIANGLES, p.count, gl.UNSIGNED_INT, 0);
        }
        gl.depthMask(true); gl.disable(gl.BLEND);
      }
      gl.bindVertexArray(null);
    }

    // orbit / zoom with mouse, touch or pen
    let drag = null;
    canvas.addEventListener("pointerdown", e => { drag = { x: e.clientX, y: e.clientY }; canvas.setPointerCapture(e.pointerId); });
    canvas.addEventListener("pointermove", e => {
      if (!drag) return;
      cam.yaw -= (e.clientX - drag.x) * 0.008;
      cam.pitch = Math.max(-0.2, Math.min(1.5, cam.pitch + (e.clientY - drag.y) * 0.008));
      drag = { x: e.clientX, y: e.clientY }; dirty = true;
    });
    canvas.addEventListener("pointerup", () => { drag = null; });
    canvas.addEventListener("wheel", e => {
      e.preventDefault(); cam.dist = Math.max(0.3, Math.min(2.5, cam.dist * (1 + Math.sign(e.deltaY) * 0.1))); dirty = true;
    }, { passive: false });

    (function loop() { draw(); requestAnimationFrame(loop); })();
    return { load, setFrames, setView, playGhost, stopGhost };
  }

  return { create };
})();
