/* Lucky wheel of the campaign's special gift (update 93). Slices are sized by the customer's points (tickets).
   "Spin" asks the server for the winner (picked once, kept), then the wheel spins ~7 s with ticking and stops on
   the winner's slice; confetti and the winner card follow. After the draw the button replays the same result. */
(function () {
  "use strict";
  var stage = document.getElementById("cp-stage");
  if (!stage) return;
  var NS = "http://www.w3.org/2000/svg";
  var people = JSON.parse(stage.getAttribute("data-entrants") || "[]"), text = JSON.parse(stage.getAttribute("data-text"));
  var done = JSON.parse(stage.getAttribute("data-done") || "null");
  var wheel = document.getElementById("cp-wheel"), btn = document.getElementById("cp-spin");
  if (!wheel || !people.length) return;
  var COLORS = ["#dc2626", "#16a34a", "#f59e0b", "#2563eb", "#db2777", "#0d9488", "#7c3aed", "#ea580c"];
  var total = people.reduce(function (s, p) { return s + p.tickets; }, 0), slices = [], a = 0, rot = 0, busy = false;

  function svg(tag, attrs) { var n = document.createElementNS(NS, tag); for (var k in attrs) n.setAttribute(k, attrs[k]); return n; }
  function pt(r, deg) { var rad = (deg - 90) * Math.PI / 180; return [r * Math.cos(rad), r * Math.sin(rad)]; }
  function short(name) { var p = (name || "").split(/\s+/); return p.length > 1 ? p[0] + " " + p[1].charAt(0) + "." : p[0]; }

  // draw the slices
  people.forEach(function (p, i) {
    var span = p.tickets / total * 360, s = pt(200, a), e = pt(200, a + span), large = span > 180 ? 1 : 0;
    var d = people.length === 1 ? "M0 -200 A200 200 0 1 1 -0.01 -200 Z" : "M0 0 L" + s[0] + " " + s[1] + " A200 200 0 " + large + " 1 " + e[0] + " " + e[1] + " Z";
    wheel.appendChild(svg("path", { d: d, fill: COLORS[i % COLORS.length], stroke: "#fff", "stroke-width": "2" }));
    if (span > 7) {
      var mid = a + span / 2, tp = pt(118, mid), label = svg("text", { x: tp[0], y: tp[1], "text-anchor": "middle", "dominant-baseline": "middle",
        transform: "rotate(" + (mid - 90) + " " + tp[0] + " " + tp[1] + ")", "class": "cp-lbl" });
      label.textContent = short(p.name);
      wheel.appendChild(label);
    }
    slices.push({ cust: p.cust, from: a, to: a + span });
    a += span;
  });
  for (var i = 0; i < 24; i++) { var b = pt(204, i * 15); wheel.appendChild(svg("circle", { cx: b[0], cy: b[1], r: 4, "class": "cp-bulb" + (i % 2 ? " alt" : "") })); }

  // a soft tick for each slice that passes the pointer
  var ctx = null;
  function tick() {
    try {
      ctx = ctx || new (window.AudioContext || window.webkitAudioContext)();
      var o = ctx.createOscillator(), g = ctx.createGain();
      o.frequency.value = 1400; g.gain.setValueAtTime(0.08, ctx.currentTime); g.gain.exponentialRampToValueAtTime(0.0001, ctx.currentTime + 0.05);
      o.connect(g); g.connect(ctx.destination); o.start(); o.stop(ctx.currentTime + 0.06);
    } catch (e) { /* no sound */ }
  }
  function fanfare() {
    try {
      [523, 659, 784, 1047].forEach(function (f, k) {
        var o = ctx.createOscillator(), g = ctx.createGain(), t0 = ctx.currentTime + k * 0.16;
        o.type = "triangle"; o.frequency.value = f; g.gain.setValueAtTime(0.0001, t0); g.gain.exponentialRampToValueAtTime(0.15, t0 + 0.02);
        g.gain.exponentialRampToValueAtTime(0.0001, t0 + 0.6); o.connect(g); g.connect(ctx.destination); o.start(t0); o.stop(t0 + 0.7);
      });
    } catch (e) { /* no sound */ }
  }

  function confetti() {
    var host = document.getElementById("cp-confetti");
    host.innerHTML = "";
    for (var i = 0; i < 120; i++) {
      var c = document.createElement("i");
      c.style.left = (Math.random() * 100) + "%";
      c.style.background = COLORS[i % COLORS.length];
      c.style.animationDelay = (Math.random() * 0.8).toFixed(2) + "s";
      c.style.animationDuration = (2.4 + Math.random() * 2).toFixed(2) + "s";
      c.style.setProperty("--dx", (Math.random() * 300 - 150).toFixed(0) + "px");
      c.style.setProperty("--rot", (Math.random() * 1080).toFixed(0) + "deg");
      host.appendChild(c);
    }
    setTimeout(function () { host.innerHTML = ""; }, 6000);
  }

  function show(r) {
    document.getElementById("cp-win-name").textContent = r.name;
    document.getElementById("cp-win-code").textContent = r.winner;
    document.getElementById("cp-win-gift").textContent = r.gift;
    document.getElementById("cp-win-tickets").textContent = text.cpTickets.replace("{n}", r.tickets).replace("{total}", r.total);
    document.getElementById("cp-result").hidden = false;
    confetti(); fanfare();
  }

  function spinTo(r) {
    var s = slices.filter(function (x) { return x.cust === r.winner; })[0] || slices[0];
    var span = s.to - s.from, inside = s.from + span * (0.2 + Math.random() * 0.6);       // somewhere inside the slice
    var target = 360 * 8 + (360 - inside);                                                 // the pointer is at the top
    rot = rot - (rot % 360) + target;
    wheel.style.transition = "transform 7s cubic-bezier(.12,.67,.12,1)";
    wheel.style.transform = "rotate(" + rot + "deg)";
    btn.textContent = text.cpSpinning; btn.disabled = true;
    // ticks: follow the angle while it turns
    var last = null, started = Date.now();
    (function loop() {
      var m = window.getComputedStyle(wheel).transform, deg = 0;
      if (m && m !== "none") { var v = m.split("(")[1].split(")")[0].split(","); deg = Math.atan2(+v[1], +v[0]) * 180 / Math.PI; }
      var at = (360 - ((deg % 360) + 360) % 360) % 360, idx = 0;
      for (var k = 0; k < slices.length; k++) if (at >= slices[k].from && at < slices[k].to) idx = k;
      if (idx !== last) { if (last !== null) tick(); last = idx; }
      if (Date.now() - started < 7100) requestAnimationFrame(loop);
      else { busy = false; btn.disabled = false; btn.textContent = text.cpReplay; show(r); }
    })();
  }

  btn.addEventListener("click", function () {
    if (busy) return;
    busy = true;
    if (done) { spinTo(done); return; }
    var req = new XMLHttpRequest();
    req.open("POST", stage.getAttribute("data-url"), true);
    req.setRequestHeader("X-CSRF-Token", stage.getAttribute("data-csrf"));
    req.setRequestHeader("Accept", "application/json");
    req.onload = function () {
      var r = null;
      try { r = JSON.parse(req.responseText); } catch (e) { r = null; }
      if (!r || !r.ok) { busy = false; window.AOIModal.alert((r && r.error) || text.errServer); return; }
      done = r; spinTo(r);
    };
    req.onerror = function () { busy = false; window.AOIModal.alert(text.errServer); };
    req.send();
  });
  document.getElementById("cp-close").addEventListener("click", function () { document.getElementById("cp-result").hidden = true; });
})();
