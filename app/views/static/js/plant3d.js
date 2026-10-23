/* Update 66: Automation > SCADA > 3D plant - the plant drawn in 3D from the live SCADA data (/automation/scada.json,
   every 2 seconds). No library: a small WebGL renderer (boxes, cylinders, tubes, lights) so it runs from the server's
   own files (CSP "self", no internet needed).

   Each machine is placed by its SCADA column / row and drawn by its type (silo, conveyor, intake bin, sorter, mill
   selector, main motor + mill, packer, AMR, other). Colour = status (running green, idle blue-grey, fault red blinking,
   maintenance amber, offline grey, not in use transparent). A machine with a device runs only on fresh telemetry:
   the motor coupling turns at its output Hz, the AMR drives, the belts move. Pipes follow FEEDS_TO; rice flows in them
   while the machine that feeds runs - from the selector only towards the motor it points at, and the selector's chute
   turns to that motor. Drag to turn, pinch / wheel to zoom, two fingers (or shift + drag) to move, tap a machine for
   its details. Commands stay on the SCADA screen. */
(function () {
  "use strict";
  var root = document.getElementById("plant3d");
  if (!root) return;
  var canvas = document.getElementById("p3-canvas"), wrap = document.getElementById("p3-wrap");
  var labelsBox = document.getElementById("p3-labels"), panel = document.getElementById("p3-panel");
  var T = JSON.parse(root.getAttribute("data-text"));
  var data = JSON.parse(root.getAttribute("data-initial"));
  var gl = canvas.getContext("webgl", { antialias: true, alpha: false }) || canvas.getContext("experimental-webgl");
  if (!gl) { wrap.classList.add("p3-nogl"); wrap.setAttribute("data-msg", T.noGL); return; }

  // ---------------- small matrix library (column-major, like WebGL) ----------------
  function ident() { return [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]; }
  function mul(a, b) {
    var o = new Array(16);
    for (var c = 0; c < 4; c++) for (var r = 0; r < 4; r++) {
      o[c * 4 + r] = a[r] * b[c * 4] + a[4 + r] * b[c * 4 + 1] + a[8 + r] * b[c * 4 + 2] + a[12 + r] * b[c * 4 + 3];
    }
    return o;
  }
  function trans(x, y, z) { var m = ident(); m[12] = x; m[13] = y; m[14] = z; return m; }
  function rotY(a) { var c = Math.cos(a), s = Math.sin(a); return [c, 0, -s, 0, 0, 1, 0, 0, s, 0, c, 0, 0, 0, 0, 1]; }
  function rotX(a) { var c = Math.cos(a), s = Math.sin(a); return [1, 0, 0, 0, 0, c, s, 0, 0, -s, c, 0, 0, 0, 0, 1]; }
  function rotZ(a) { var c = Math.cos(a), s = Math.sin(a); return [c, s, 0, 0, -s, c, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]; }
  function persp(fov, asp, n, f) {
    var t = 1 / Math.tan(fov / 2), nf = 1 / (n - f);
    return [t / asp, 0, 0, 0, 0, t, 0, 0, 0, 0, (f + n) * nf, -1, 0, 0, 2 * f * n * nf, 0];
  }
  function lookAt(e, c, u) {
    var z = norm([e[0] - c[0], e[1] - c[1], e[2] - c[2]]), x = norm(cross(u, z)), y = cross(z, x);
    return [x[0], y[0], z[0], 0, x[1], y[1], z[1], 0, x[2], y[2], z[2], 0, -dot(x, e), -dot(y, e), -dot(z, e), 1];
  }
  function cross(a, b) { return [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]]; }
  function dot(a, b) { return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]; }
  function norm(a) { var l = Math.sqrt(dot(a, a)) || 1; return [a[0] / l, a[1] / l, a[2] / l]; }
  function apply(m, p) {
    var x = p[0], y = p[1], z = p[2], w = m[3] * x + m[7] * y + m[11] * z + m[15];
    return [(m[0] * x + m[4] * y + m[8] * z + m[12]) / w, (m[1] * x + m[5] * y + m[9] * z + m[13]) / w, (m[2] * x + m[6] * y + m[10] * z + m[14]) / w, w];
  }

  // ---------------- geometry: arrays of [x, y, z, nx, ny, nz] ----------------
  function quad(out, a, b, c, d, n) { [a, b, c, a, c, d].forEach(function (p) { out.push(p[0], p[1], p[2], n[0], n[1], n[2]); }); }
  function box(w, h, d, cx, cy, cz) {
    var o = [], x0 = cx - w / 2, x1 = cx + w / 2, y0 = cy - h / 2, y1 = cy + h / 2, z0 = cz - d / 2, z1 = cz + d / 2;
    quad(o, [x0, y0, z1], [x1, y0, z1], [x1, y1, z1], [x0, y1, z1], [0, 0, 1]);
    quad(o, [x1, y0, z0], [x0, y0, z0], [x0, y1, z0], [x1, y1, z0], [0, 0, -1]);
    quad(o, [x1, y0, z1], [x1, y0, z0], [x1, y1, z0], [x1, y1, z1], [1, 0, 0]);
    quad(o, [x0, y0, z0], [x0, y0, z1], [x0, y1, z1], [x0, y1, z0], [-1, 0, 0]);
    quad(o, [x0, y1, z1], [x1, y1, z1], [x1, y1, z0], [x0, y1, z0], [0, 1, 0]);
    quad(o, [x0, y0, z0], [x1, y0, z0], [x1, y0, z1], [x0, y0, z1], [0, -1, 0]);
    return o;
  }
  // frustum along y: radius r0 at y0, r1 at y1, `seg` sides, with caps; `turn` rotates the sides (4 sides + PI/4 = square)
  function frustum(r0, r1, y0, y1, seg, cx, cz, turn) {
    var o = [], h = y1 - y0, slope = (r0 - r1) / (h || 1);
    seg = seg || 20; turn = turn || 0;
    for (var i = 0; i < seg; i++) {
      var a0 = turn + i / seg * Math.PI * 2, a1 = turn + (i + 1) / seg * Math.PI * 2, am = (a0 + a1) / 2;
      var c0 = Math.cos(a0), s0 = Math.sin(a0), c1 = Math.cos(a1), s1 = Math.sin(a1);
      var n = norm([Math.cos(am), slope, Math.sin(am)]);
      var flat = seg <= 8;
      var n0 = flat ? n : norm([c0, slope, s0]), n1 = flat ? n : norm([c1, slope, s1]);
      var p = [[cx + c0 * r0, y0, cz + s0 * r0], [cx + c1 * r0, y0, cz + s1 * r0], [cx + c1 * r1, y1, cz + s1 * r1], [cx + c0 * r1, y1, cz + s0 * r1]];
      [[p[0], n0], [p[3], n0], [p[2], n1], [p[0], n0], [p[2], n1], [p[1], n1]].forEach(function (v) {
        o.push(v[0][0], v[0][1], v[0][2], v[1][0], v[1][1], v[1][2]);
      });
      if (r1 > 0) o.push(cx, y1, cz, 0, 1, 0, cx + c1 * r1, y1, cz + s1 * r1, 0, 1, 0, cx + c0 * r1, y1, cz + s0 * r1, 0, 1, 0);
      if (r0 > 0) o.push(cx, y0, cz, 0, -1, 0, cx + c0 * r0, y0, cz + s0 * r0, 0, -1, 0, cx + c1 * r0, y0, cz + s1 * r0, 0, -1, 0);
    }
    return o;
  }
  function xf(g, m) {                                        // bake a transform into geometry
    var o = [];
    for (var i = 0; i < g.length; i += 6) {
      var p = apply(m, [g[i], g[i + 1], g[i + 2]]);
      var n = norm([m[0] * g[i + 3] + m[4] * g[i + 4] + m[8] * g[i + 5], m[1] * g[i + 3] + m[5] * g[i + 4] + m[9] * g[i + 5],
                    m[2] * g[i + 3] + m[6] * g[i + 4] + m[10] * g[i + 5]]);
      o.push(p[0], p[1], p[2], n[0], n[1], n[2]);
    }
    return o;
  }
  function join() { var o = []; for (var i = 0; i < arguments.length; i++) o = o.concat(arguments[i]); return o; }
  // horizontal cylinder along x, centred on (cx, cy, cz)
  function cylX(r, len, cx, cy, cz, seg) { return xf(frustum(r, r, -len / 2, len / 2, seg || 20, 0, 0), mul(trans(cx, cy, cz), rotZ(-Math.PI / 2))); }
  function tube(points, r) {                                  // a 6-sided tube through world points
    var o = [];
    for (var i = 0; i < points.length - 1; i++) {
      var a = points[i], b = points[i + 1], d = norm([b[0] - a[0], b[1] - a[1], b[2] - a[2]]);
      var up = Math.abs(d[1]) > 0.9 ? [1, 0, 0] : [0, 1, 0], u = norm(cross(d, up)), v = cross(u, d);
      for (var k = 0; k < 6; k++) {
        var t0 = k / 6 * Math.PI * 2, t1 = (k + 1) / 6 * Math.PI * 2;
        var n0 = [u[0] * Math.cos(t0) + v[0] * Math.sin(t0), u[1] * Math.cos(t0) + v[1] * Math.sin(t0), u[2] * Math.cos(t0) + v[2] * Math.sin(t0)];
        var n1 = [u[0] * Math.cos(t1) + v[0] * Math.sin(t1), u[1] * Math.cos(t1) + v[1] * Math.sin(t1), u[2] * Math.cos(t1) + v[2] * Math.sin(t1)];
        var p = function (c, n) { return [c[0] + n[0] * r, c[1] + n[1] * r, c[2] + n[2] * r]; };
        [[p(a, n0), n0], [p(b, n0), n0], [p(b, n1), n1], [p(a, n0), n0], [p(b, n1), n1], [p(a, n1), n1]].forEach(function (q) {
          o.push(q[0][0], q[0][1], q[0][2], q[1][0], q[1][1], q[1][2]);
        });
      }
    }
    return o;
  }

  // ---------------- WebGL ----------------
  var VS = "attribute vec3 p; attribute vec3 n; uniform mat4 uVP; uniform mat4 uM; varying vec3 vN; varying vec3 vW;" +
           "void main(){ vec4 w = uM * vec4(p, 1.0); vW = w.xyz; vN = mat3(uM) * n; gl_Position = uVP * w; }";
  var FS = "precision mediump float; uniform vec3 uC; uniform vec3 uE; uniform float uA; uniform vec3 uEye; uniform vec3 uFog;" +
           "varying vec3 vN; varying vec3 vW;" +
           "void main(){ vec3 N = normalize(vN); vec3 L = normalize(vec3(0.45, 1.0, 0.35));" +
           " float d = max(dot(N, L), 0.0); vec3 hemi = mix(vec3(0.22, 0.24, 0.30), vec3(0.62, 0.68, 0.80), N.y * 0.5 + 0.5);" +
           " vec3 H = normalize(L + normalize(uEye - vW)); float sp = pow(max(dot(N, H), 0.0), 32.0) * 0.25;" +
           " vec3 c = uC * (hemi * 0.75 + d * 0.65) + sp + uE;" +
           " float f = clamp((distance(uEye, vW) - 30.0) / 60.0, 0.0, 0.85); gl_FragColor = vec4(mix(c, uFog, f), uA); }";
  function shader(type, src) { var s = gl.createShader(type); gl.shaderSource(s, src); gl.compileShader(s); return s; }
  var prog = gl.createProgram();
  gl.attachShader(prog, shader(gl.VERTEX_SHADER, VS)); gl.attachShader(prog, shader(gl.FRAGMENT_SHADER, FS)); gl.linkProgram(prog);
  gl.useProgram(prog);
  var loc = {}; ["p", "n"].forEach(function (k) { loc[k] = gl.getAttribLocation(prog, k); });
  ["uVP", "uM", "uC", "uE", "uA", "uEye", "uFog"].forEach(function (k) { loc[k] = gl.getUniformLocation(prog, k); });
  var FOG = [0.055, 0.09, 0.16];
  gl.enable(gl.DEPTH_TEST); gl.enable(gl.BLEND); gl.blendFunc(gl.SRC_ALPHA, gl.ONE_MINUS_SRC_ALPHA);

  function mesh(g, lines) {
    var b = gl.createBuffer(); gl.bindBuffer(gl.ARRAY_BUFFER, b); gl.bufferData(gl.ARRAY_BUFFER, new Float32Array(g), gl.STATIC_DRAW);
    return { buf: b, n: g.length / 6, lines: !!lines };
  }
  function draw(m, model, color, alpha, emis) {
    gl.bindBuffer(gl.ARRAY_BUFFER, m.buf);
    gl.enableVertexAttribArray(loc.p); gl.vertexAttribPointer(loc.p, 3, gl.FLOAT, false, 24, 0);
    gl.enableVertexAttribArray(loc.n); gl.vertexAttribPointer(loc.n, 3, gl.FLOAT, false, 24, 12);
    gl.uniformMatrix4fv(loc.uM, false, new Float32Array(model));
    gl.uniform3fv(loc.uC, color); gl.uniform3fv(loc.uE, emis || [0, 0, 0]); gl.uniform1f(loc.uA, alpha == null ? 1 : alpha);
    gl.drawArrays(m.lines ? gl.LINES : gl.TRIANGLES, 0, m.n);
  }

  // ---------------- colours ----------------
  function hex(h) { return [parseInt(h.substr(1, 2), 16) / 255, parseInt(h.substr(3, 2), 16) / 255, parseInt(h.substr(5, 2), 16) / 255]; }
  var C = { run: hex("#22c55e"), idle: hex("#7f9cc4"), fault: hex("#ef4444"), maint: hex("#f59e0b"), off: hex("#4b5563"),
            dis: hex("#94a3b8"), metal: hex("#aab4c3"), dark: hex("#334155"), steel: hex("#cbd5e1"), belt: hex("#1f2937"),
            rice: hex("#fef3c7"), pipeOn: hex("#38bdf8"), pipeOff: hex("#475569"), floor: hex("#0f1b2d"), grid: hex("#1e3a5f"),
            bag: hex("#e8d9b5"), select: hex("#fbbf24") };

  // ---------------- machine models (local coordinates, y up, footprint about 3 x 3) ----------------
  // parts: {g: geometry, c: "body" (status colour) | colour, spin: {axis point, speed key}, move: "amr" | "belt"}
  var MODELS = {
    SILO: function () {
      var legs = [];
      [[-0.7, -0.7], [0.7, -0.7], [0.7, 0.7], [-0.7, 0.7]].forEach(function (q) { legs = legs.concat(box(0.12, 1.3, 0.12, q[0], 0.65, q[1])); });
      return { top: 4.4, parts: [{ g: frustum(1.05, 1.05, 1.3, 3.8, 24, 0, 0), c: "body" }, { g: frustum(0.25, 1.05, 0.55, 1.3, 24, 0, 0), c: "body" },
        { g: frustum(1.1, 0.12, 3.8, 4.35, 24, 0, 0), c: C.steel }, { g: legs, c: C.dark }, { g: frustum(0.16, 0.16, 0.1, 0.55, 10, 0, 0), c: C.dark }] };
    },
    CONVEYOR: function () {
      return { top: 1.25, parts: [{ g: box(3.4, 0.12, 0.8, 0, 1.0, 0), c: C.belt }, { g: join(box(3.5, 0.18, 0.08, 0, 1.08, 0.44), box(3.5, 0.18, 0.08, 0, 1.08, -0.44)), c: "body" },
        { g: join(box(0.12, 0.95, 0.7, -1.5, 0.47, 0), box(0.12, 0.95, 0.7, 1.5, 0.47, 0)), c: C.dark },
        { g: join(cylX(0.13, 0.95, -1.7, 1.0, 0, 12), cylX(0.13, 0.95, 1.7, 1.0, 0, 12)), c: C.steel, rollerZ: true },
        { g: box(0.32, 0.05, 0.62, 0, 1.085, 0), c: C.rice, belt: true }] };
    },
    INBOUND: function () {
      var legs = [];
      [[-0.85, -0.85], [0.85, -0.85], [0.85, 0.85], [-0.85, 0.85]].forEach(function (q) { legs = legs.concat(box(0.12, 1.9, 0.12, q[0], 0.95, q[1])); });
      return { top: 3.0, parts: [{ g: box(2.0, 0.9, 2.0, 0, 2.5, 0), c: "body" }, { g: frustum(0.3, 1.41, 1.05, 2.05, 4, 0, 0, Math.PI / 4), c: "body" },
        { g: legs, c: C.dark }, { g: box(2.06, 0.08, 2.06, 0, 2.97, 0), c: C.steel }] };
    },
    SORTER: function () {
      return { top: 2.3, parts: [{ g: box(1.9, 1.3, 1.4, 0, 0.75, 0), c: "body" }, { g: frustum(0.2, 0.55, 1.4, 2.25, 4, 0, 0, Math.PI / 4), c: C.steel },
        { g: box(2.0, 0.1, 1.5, 0, 0.05, 0), c: C.dark }, { g: box(0.5, 0.35, 0.05, 0.4, 0.9, 0.73), c: hex("#0ea5e9") }] };
    },
    MILL_SELECTOR: function () {
      return { top: 2.1, parts: [{ g: frustum(0.75, 0.6, 0, 0.45, 20, 0, 0), c: C.dark }, { g: frustum(0.2, 0.2, 0.45, 1.75, 14, 0, 0), c: "body" },
        { g: join(box(1.7, 0.22, 0.38, 0.85, 1.85, 0), box(0.4, 0.4, 0.4, 0, 1.85, 0)), c: C.select, chute: true }] };
    },
    MAIN_MOTOR: function () {
      return { top: 2.0, parts: [{ g: box(3.2, 0.3, 1.3, 0.3, 0.15, 0), c: C.dark },
        { g: cylX(0.58, 1.6, -0.5, 0.95, 0, 24), c: "body" }, { g: cylX(0.62, 0.12, -1.32, 0.95, 0, 24), c: C.steel },
        { g: join(box(0.7, 0.35, 0.5, -0.5, 1.6, 0)), c: C.steel },
        { g: cylX(0.1, 0.55, 0.55, 0.95, 0, 10), c: C.steel },
        { g: join(cylX(0.36, 0.14, 0.86, 0, 0, 20), box(0.16, 0.6, 0.12, 0.86, 0.16, 0)), c: C.select, spin: [0.86, 0.95, 0] },
        { g: join(box(1.1, 1.5, 1.1, 1.6, 1.05, 0), frustum(0.18, 0.5, 1.8, 2.1, 4, 1.6, 0, Math.PI / 4)), c: C.metal }] };
    },
    PACKER: function () {
      return { top: 2.4, parts: [{ g: box(1.5, 1.6, 1.3, 0, 0.8, 0), c: "body" }, { g: frustum(0.2, 0.6, 1.6, 2.35, 4, 0, 0, Math.PI / 4), c: C.steel },
        { g: box(1.6, 0.1, 0.7, 1.4, 0.55, 0), c: C.belt }, { g: box(0.08, 0.5, 0.6, 2.15, 0.3, 0), c: C.dark },
        { g: box(0.45, 0.6, 0.35, 0, 0.3, 0), c: C.bag, bag: true }] };
    },
    AMR: function () {
      return { top: 0.8, parts: [{ g: join(box(1.1, 0.38, 0.8, 0, 0.3, 0), box(0.9, 0.06, 0.7, 0, 0.52, 0)), c: "body", amr: true },
        { g: join(frustum(0.12, 0.12, 0.52, 0.72, 12, 0.3, 0)), c: C.dark, amr: true },
        { g: join(xf(frustum(0.14, 0.14, -0.06, 0.06, 12, 0, 0), mul(trans(-0.35, 0.14, 0.42), rotX(Math.PI / 2))),
                  xf(frustum(0.14, 0.14, -0.06, 0.06, 12, 0, 0), mul(trans(0.35, 0.14, 0.42), rotX(Math.PI / 2))),
                  xf(frustum(0.14, 0.14, -0.06, 0.06, 12, 0, 0), mul(trans(-0.35, 0.14, -0.42), rotX(Math.PI / 2))),
                  xf(frustum(0.14, 0.14, -0.06, 0.06, 12, 0, 0), mul(trans(0.35, 0.14, -0.42), rotX(Math.PI / 2)))), c: C.belt, amr: true },
        { g: box(0.9, 0.04, 2.8, 0, 0.01, 0), c: hex("#f59e0b"), alpha: 0.35 }] };
    },
    OTHER: function () {
      return { top: 1.8, parts: [{ g: box(1.5, 1.5, 1.5, 0, 0.75, 0), c: "body" }, { g: box(1.6, 0.1, 1.6, 0, 1.55, 0), c: C.steel }] };
    }
  };
  var meshCache = {};
  function modelOf(type) {
    var key = MODELS[type] ? type : "OTHER";
    if (!meshCache[key]) {
      var m = MODELS[key]();
      m.parts.forEach(function (p) { p.mesh = mesh(p.g); p.g = null; });
      meshCache[key] = m;
    }
    return meshCache[key];
  }
  var particle = mesh(box(0.2, 0.2, 0.2, 0, 0, 0));
  var ring = mesh(frustum(1.75, 1.75, 0, 0.03, 32, 0, 0));
  var shadowM = mesh(frustum(1.5, 1.5, 0, 0.01, 24, 0, 0));
  var floorM = mesh(box(80, 0.1, 80, 0, -0.05, 0));
  var gridM = (function () {
    var g = [];
    for (var i = -40; i <= 40; i += 2) { g.push(i, 0.01, -40, 0, 1, 0, i, 0.01, 40, 0, 1, 0, -40, 0.01, i, 0, 1, 0, 40, 0.01, i, 0, 1, 0); }
    return mesh(g, true);
  })();

  // ---------------- scene from the SCADA data ----------------
  var SPACE_X = 5.2, SPACE_Z = 4.8;
  var scene = { nodes: [], byId: {}, edges: [], key: "" };
  function running(n) {
    if (!n.active) return false;
    if (n.live) return !!(n.live.fresh && n.live.running && n.online);
    return n.status === "R";
  }
  function offline(n) { return n.active && !!n.live && !n.online; }
  function bodyColor(n, t) {
    if (!n.active) return C.dis;
    if (offline(n)) return C.off;
    return { R: C.run, I: C.idle, F: C.fault, M: C.maint, O: C.off }[n.status] || C.idle;
  }
  function build() {
    var placed = data.nodes.filter(function (n) { return n.col != null && n.row != null; });
    var key = placed.map(function (n) { return n.id + n.col + "," + n.row + n.type + n.feeds.join("+"); }).join("|");
    if (key === scene.key) { placed.forEach(function (n) { scene.byId[n.id].n = n; }); return; }
    var cols = placed.map(function (n) { return n.col; }), rows = placed.map(function (n) { return n.row; });
    var mc = (Math.min.apply(null, cols) + Math.max.apply(null, cols)) / 2, mr = (Math.min.apply(null, rows) + Math.max.apply(null, rows)) / 2;
    scene = { nodes: [], byId: {}, edges: [], key: key };
    placed.forEach(function (n) {
      var o = { n: n, x: (n.col - mc) * SPACE_X, z: (n.row - mr) * SPACE_Z, model: modelOf(n.type), chute: 0, spin: 0, amr: 0, belt: 0 };
      scene.nodes.push(o); scene.byId[n.id] = o;
    });
    scene.nodes.forEach(function (a) {
      a.n.feeds.forEach(function (id) {
        var b = scene.byId[id];
        if (!b) return;
        var p0 = [a.x, a.model.top - 0.2, a.z], p2 = [b.x, b.model.top + 0.1, b.z];
        var p1 = [(a.x + b.x) / 2, Math.max(p0[1], p2[1]) + 1.4, (a.z + b.z) / 2];
        var pts = [];
        for (var i = 0; i <= 18; i++) { var s = i / 18; pts.push(bez(p0, p1, p2, s)); }
        scene.edges.push({ a: a, b: b, p: [p0, p1, p2], tube: mesh(tube(pts, 0.09)) });
      });
    });
    if (!labelsBox.childElementCount || labelsBox.getAttribute("data-key") !== key) makeLabels(key);
  }
  function bez(a, b, c, s) {
    var u = 1 - s;
    return [u * u * a[0] + 2 * u * s * b[0] + s * s * c[0], u * u * a[1] + 2 * u * s * b[1] + s * s * c[1], u * u * a[2] + 2 * u * s * b[2] + s * s * c[2]];
  }
  function edgeActive(e) {
    var a = e.a.n, b = e.b.n;
    if (a.type === "MILL_SELECTOR") return a.select_to === b.id && running(b);
    if (b.type === "MILL_SELECTOR") return running(a) || data.nodes.some(function (m) { return m.id === b.select_to && running(m); });
    return running(a);
  }

  // ---------------- labels (HTML over the canvas) ----------------
  function makeLabels(key) {
    labelsBox.innerHTML = ""; labelsBox.setAttribute("data-key", key);
    scene.nodes.forEach(function (o) {
      var el = document.createElement("div"); el.className = "p3-label"; el.setAttribute("data-node", o.n.id);
      el.innerHTML = "<b></b><span></span>";
      labelsBox.appendChild(el); o.label = el;
    });
  }
  function labelText(o) {
    var n = o.n, st = !n.active ? "" : offline(n) ? T.offline : (T.status[n.status] || n.status);
    var extra = "";
    if (n.live && n.live.fresh && n.online && n.live.out_hz != null) extra = " · " + n.live.out_hz.toFixed(1) + " Hz" + (n.live.amp != null ? " · " + n.live.amp.toFixed(1) + " A" : "");
    if (n.type === "MILL_SELECTOR" && n.select_to) extra = " → " + ((scene.byId[n.select_to] || {}).n || {}).name;
    return [n.name, st + extra];
  }

  // ---------------- camera ----------------
  var cam = { yaw: -0.6, pitch: 0.62, dist: 26, tx: 0, ty: 1.2, tz: 0 }, HOME = JSON.parse(JSON.stringify(cam)), spinOn = false;
  var VP = ident(), eye = [0, 0, 0], W = 1, H = 1, dpr = 1;
  function camera() {
    cam.pitch = Math.max(0.12, Math.min(1.45, cam.pitch)); cam.dist = Math.max(6, Math.min(70, cam.dist));
    eye = [cam.tx + cam.dist * Math.cos(cam.pitch) * Math.sin(cam.yaw), cam.ty + cam.dist * Math.sin(cam.pitch), cam.tz + cam.dist * Math.cos(cam.pitch) * Math.cos(cam.yaw)];
    VP = mul(persp(0.75, W / H, 0.3, 200), lookAt(eye, [cam.tx, cam.ty, cam.tz], [0, 1, 0]));
  }
  function resize() {
    dpr = Math.min(window.devicePixelRatio || 1, 2);
    W = Math.max(1, wrap.clientWidth); H = Math.max(1, wrap.clientHeight);
    canvas.width = Math.round(W * dpr); canvas.height = Math.round(H * dpr);
    canvas.style.width = W + "px"; canvas.style.height = H + "px";
    gl.viewport(0, 0, canvas.width, canvas.height);
  }
  function toScreen(p) {
    var c = apply(VP, p);
    return { x: (c[0] * 0.5 + 0.5) * W, y: (0.5 - c[1] * 0.5) * H, front: c[3] > 0 && c[2] < 1 };
  }

  // ---------------- frame ----------------
  var last = performance.now(), t = 0;
  function frame(now) {
    var dt = Math.min(0.1, (now - last) / 1000); last = now; t += dt;
    if (spinOn) cam.yaw += dt * 0.15;
    camera();
    gl.clearColor(FOG[0], FOG[1], FOG[2], 1); gl.clear(gl.COLOR_BUFFER_BIT | gl.DEPTH_BUFFER_BIT);
    gl.uniformMatrix4fv(loc.uVP, false, new Float32Array(VP)); gl.uniform3fv(loc.uEye, eye); gl.uniform3fv(loc.uFog, FOG);
    draw(floorM, ident(), C.floor); draw(gridM, ident(), C.grid, 1, [0.03, 0.07, 0.13]);
    var blink = 0.5 + 0.5 * Math.sin(t * 6);
    var late = [];
    scene.nodes.forEach(function (o) {
      var n = o.n, run = running(n), base = trans(o.x, 0, o.z), body = bodyColor(n, t);
      var alpha = n.active ? 1 : 0.32;
      var emis = n.active && n.status === "F" && !offline(n) ? [0.55 * blink, 0.05, 0.05] : run ? [0.02, 0.09, 0.03] : null;
      draw(shadowM, mul(base, trans(0, 0.012, 0)), [0, 0, 0], 0.28);
      if (run) late.push(function () { draw(ring, mul(base, trans(0, 0.02, 0)), C.run, 0.18 + 0.12 * Math.sin(t * 3), [0.05, 0.25, 0.08]); });
      // motion
      var hz = n.live && n.live.fresh && n.live.out_hz != null ? n.live.out_hz : (run ? 50 : 0);
      if (run) { o.spin += dt * hz * 0.9; o.amr += dt; o.belt = (o.belt + dt * 0.9) % 3.0; }
      if (n.type === "MILL_SELECTOR") {
        var target = scene.byId[n.select_to];
        var want = target ? Math.atan2(-(target.z - o.z), target.x - o.x) : 0;
        var diff = Math.atan2(Math.sin(want - o.chute), Math.cos(want - o.chute));
        o.chute += diff * Math.min(1, dt * 2.5);
      }
      o.model.parts.forEach(function (p) {
        var m = base, color = p.c === "body" ? body : p.c, a = p.alpha != null ? p.alpha * alpha : alpha;
        if (p.chute) m = mul(base, rotY(o.chute));
        if (p.spin) m = mul(base, mul(trans(p.spin[0], p.spin[1], p.spin[2]), mul(rotX(o.spin), trans(-p.spin[0], -p.spin[1], -p.spin[2]))));
        if (p.amr) m = mul(base, trans(0, 0, Math.sin(o.amr * 0.6) * 1.05));
        if (p.belt) {
          if (!run) return;
          for (var k = 0; k < 4; k++) draw(p.mesh, mul(base, trans(((o.belt + k * 0.75) % 3.0) - 1.5, 0, 0)), color, a);
          return;
        }
        if (p.bag) { if (!run) return; m = mul(base, trans(0.5 + ((o.amr * 0.5) % 1.6), 0.6, 0)); }
        if (a < 1) late.push(function () { draw(p.mesh, m, color, a, p.c === "body" ? emis : null); });
        else draw(p.mesh, m, color, a, p.c === "body" ? emis : null);
      });
    });
    // pipes and the rice flowing in them
    scene.edges.forEach(function (e) {
      var on = edgeActive(e), dis = !e.a.n.active || !e.b.n.active;
      draw(e.tube, ident(), on ? C.pipeOn : C.pipeOff, dis ? 0.3 : 0.85, on ? [0.02, 0.12, 0.2] : null);
      if (!on) return;
      for (var k = 0; k < 7; k++) {
        var s = (t * 0.35 + k / 7) % 1, q = bez(e.p[0], e.p[1], e.p[2], s);
        draw(particle, mul(trans(q[0], q[1], q[2]), rotY(t * 3 + k)), C.rice, 1, [0.35, 0.3, 0.15]);
      }
    });
    gl.depthMask(false); late.forEach(function (f) { f(); }); gl.depthMask(true);
    // labels
    scene.nodes.forEach(function (o) {
      if (!o.label) return;
      var s = toScreen([o.x, o.model.top + 0.55, o.z]);
      o.label.hidden = !s.front || (!o.n.active && picked !== o);      // machines not in use: label only when tapped
      o.label.style.transform = "translate(" + Math.round(s.x) + "px," + Math.round(s.y) + "px) translate(-50%,-100%)";
      var txt = labelText(o), b = o.label.firstChild, sp = o.label.lastChild;
      if (b.textContent !== txt[0]) b.textContent = txt[0];
      if (sp.textContent !== txt[1]) sp.textContent = txt[1];
      o.label.className = "p3-label st-" + (!o.n.active ? "dis" : offline(o.n) ? "off" : o.n.status) + (picked === o ? " picked" : "");
    });
    requestAnimationFrame(frame);
  }

  // ---------------- touch / mouse ----------------
  var pointers = {}, startDist = 0, startCam = null, moved = 0, picked = null;
  canvas.addEventListener("pointerdown", function (e) {
    canvas.setPointerCapture(e.pointerId); pointers[e.pointerId] = { x: e.clientX, y: e.clientY }; moved = 0;
    var ids = Object.keys(pointers);
    if (ids.length === 2) { var a = pointers[ids[0]], b = pointers[ids[1]]; startDist = Math.hypot(a.x - b.x, a.y - b.y); startCam = { dist: cam.dist, mx: (a.x + b.x) / 2, my: (a.y + b.y) / 2 }; }
  });
  canvas.addEventListener("pointermove", function (e) {
    var p = pointers[e.pointerId]; if (!p) return;
    var dx = e.clientX - p.x, dy = e.clientY - p.y; moved += Math.abs(dx) + Math.abs(dy);
    var ids = Object.keys(pointers);
    if (ids.length === 1) {
      if (e.shiftKey || e.buttons === 2) pan(dx, dy);
      else { cam.yaw -= dx * 0.008; cam.pitch += dy * 0.006; }
    } else if (ids.length === 2 && startCam) {
      p.x = e.clientX; p.y = e.clientY;
      var a = pointers[ids[0]], b = pointers[ids[1]], d = Math.hypot(a.x - b.x, a.y - b.y);
      if (d > 0) cam.dist = startCam.dist * startDist / d;
      var mx = (a.x + b.x) / 2, my = (a.y + b.y) / 2; pan(mx - startCam.mx, my - startCam.my); startCam.mx = mx; startCam.my = my;
      return;
    }
    p.x = e.clientX; p.y = e.clientY;
  });
  function up(e) {
    var was = Object.keys(pointers).length;
    delete pointers[e.pointerId];
    if (was === 1 && moved < 8 && e.type === "pointerup") pick(e);
    if (Object.keys(pointers).length < 2) startCam = null;
  }
  canvas.addEventListener("pointerup", up); canvas.addEventListener("pointercancel", up);
  canvas.addEventListener("contextmenu", function (e) { e.preventDefault(); });
  canvas.addEventListener("wheel", function (e) { e.preventDefault(); cam.dist *= Math.exp(e.deltaY * 0.0012); }, { passive: false });
  function pan(dx, dy) {
    // grab the floor: right = (cos, -sin), forward = (-sin, -cos) seen from the camera
    var k = cam.dist * 0.0016, s = Math.sin(cam.yaw), c = Math.cos(cam.yaw);
    cam.tx += (-c * dx - s * dy) * k; cam.tz += (s * dx - c * dy) * k;
  }
  function pick(e) {
    var r = canvas.getBoundingClientRect(), x = e.clientX - r.left, y = e.clientY - r.top, best = null, bd = 60;
    scene.nodes.forEach(function (o) {
      [0.4, 0.7].forEach(function (f) {
        var s = toScreen([o.x, o.model.top * f, o.z]), d = Math.hypot(s.x - x, s.y - y);
        if (s.front && d < bd) { bd = d; best = o; }
      });
    });
    picked = best; showPanel();
  }
  labelsBox.addEventListener("click", function (e) {
    var l = e.target.closest(".p3-label"); if (!l) return;
    picked = scene.byId[l.getAttribute("data-node")] || null; showPanel();
  });

  // ---------------- details panel ----------------
  function row(k, v) { return v == null || v === "" ? "" : "<div class=\"p3-row\"><span>" + esc(k) + "</span><b>" + esc(v) + "</b></div>"; }
  function esc(s) { return String(s).replace(/[&<>"]/g, function (c) { return { "&": "&amp;", "<": "&lt;", ">": "&gt;", "\"": "&quot;" }[c]; }); }
  function showPanel() {
    if (!picked) { panel.innerHTML = "<p class=\"scada-hint\">" + esc(T.hint) + "</p>"; return; }
    var n = (scene.byId[picked.n.id] || picked).n, lv = n.live && n.live.fresh && n.online ? n.live : null;
    var st = !n.active ? T.status.DIS : offline(n) ? T.offline : (T.status[n.status] || n.status);
    var html = "<p class=\"p3-name\">" + esc(n.name) + "</p><p class=\"p3-id\">" + esc(n.id) + " · " + esc(T.types[n.type] || n.type) + "</p>" +
      "<p class=\"p3-st st-" + (!n.active ? "dis" : offline(n) ? "off" : n.status) + "\">" + esc(st) + "</p>";
    html += row(T.kw, n.kw ? n.kw + " kW" : null);
    if (lv) {
      html += row(T.outHz, lv.out_hz != null ? lv.out_hz.toFixed(1) + " Hz" : null) + row(T.setHz, lv.set_hz != null ? lv.set_hz.toFixed(1) + " Hz" : null) +
        row(T.amp, lv.amp != null ? lv.amp.toFixed(1) + " A" + (lv.rated_amp ? " / " + lv.rated_amp + " A" : "") : null) + row(T.rpm, lv.rpm != null ? Math.round(lv.rpm) : null) +
        row(T.fault, lv.fault || null);
    } else if (n.live) html += "<p class=\"p3-note\">" + esc(n.online ? T.noData : T.offline) + "</p>";
    if (n.type === "MILL_SELECTOR") html += row(T.selTo, n.select_to ? ((scene.byId[n.select_to] || {}).n || {}).name || n.select_to : T.selNone);
    var feeds = n.feeds.map(function (id) { return ((scene.byId[id] || {}).n || {}).name || id; }).join(", ");
    html += row(T.feeds, feeds || null);
    html += "<a class=\"scada-btn p3-open\" href=\"" + esc(root.getAttribute("data-scada-url")) + "\">" + esc(T.control) + " &rarr;</a>";
    panel.innerHTML = html;
  }

  // ---------------- buttons, KPIs, live data ----------------
  document.getElementById("p3-reset").addEventListener("click", function () { cam = JSON.parse(JSON.stringify(HOME)); fit(); });
  document.getElementById("p3-top").addEventListener("click", function () { cam.pitch = 1.45; cam.yaw = 0; });
  var spinBtn = document.getElementById("p3-spin");
  spinBtn.addEventListener("click", function () { spinOn = !spinOn; spinBtn.classList.toggle("on", spinOn); });
  var full = document.getElementById("p3-full");
  full.addEventListener("click", function () {
    var fs = document.fullscreenElement || document.webkitFullscreenElement;
    if (fs) { (document.exitFullscreen || document.webkitExitFullscreen).call(document); return; }
    var req = root.requestFullscreen || root.webkitRequestFullscreen;
    if (req) req.call(root); else root.classList.toggle("p3-pseudo-full");
  });
  ["fullscreenchange", "webkitfullscreenchange"].forEach(function (ev) { document.addEventListener(ev, function () { setTimeout(resize, 120); }); });
  window.addEventListener("resize", function () { setTimeout(resize, 60); });
  function fit() {
    var xs = scene.nodes.map(function (o) { return Math.abs(o.x); }), zs = scene.nodes.map(function (o) { return Math.abs(o.z); });
    var span = Math.max(8, Math.max.apply(null, xs.concat([0])) * 2, Math.max.apply(null, zs.concat([0])) * 2.4);
    cam.dist = HOME.dist = span * 1.3 + 6;
  }
  function kpis() {
    var k = data.kpi || {}, set = function (key, v) { var el = root.querySelector("[data-k=\"" + key + "\"]"); if (el) el.textContent = v; };
    var motor = data.nodes.filter(function (n) { return n.type === "MAIN_MOTOR" && running(n); }).map(function (n) { return n.name; });
    set("running", (k.running || 0) + " / " + (k.in_use || 0)); set("motor", motor.join(", ") || "-");
    set("queue", (k.queue || 0) + " · " + (k.in_process || 0)); set("paddy", Math.round(k.paddy_today || 0).toLocaleString() + " kg");
    set("energy", k.kwh_today != null ? Number(k.kwh_today).toFixed(1) + " kWh" : "-"); set("alarms", (data.alarms || []).length);
  }
  var linkDot = document.getElementById("p3-dot"), stamp = document.getElementById("p3-updated");
  function refresh() {
    fetch(root.getAttribute("data-url"), { credentials: "same-origin", cache: "no-store" })
      .then(function (r) { if (!r.ok) throw new Error(r.status); return r.json(); })
      .then(function (d) {
        data = d; build(); kpis(); if (picked) showPanel();
        linkDot.className = "scada-dot ok"; var now = new Date();
        stamp.textContent = T.updated + " " + [now.getHours(), now.getMinutes(), now.getSeconds()].map(function (x) { return ("0" + x).slice(-2); }).join(":");
      })
      .catch(function () { linkDot.className = "scada-dot bad"; stamp.textContent = T.lost; })
      .then(function () { setTimeout(refresh, 2000); });
  }
  resize(); build(); fit(); kpis(); showPanel();
  requestAnimationFrame(frame);
  setTimeout(refresh, 2000);
  window.__p3 = { scene: function () { return scene; }, cam: cam };          // for the automatic screen test
})();
