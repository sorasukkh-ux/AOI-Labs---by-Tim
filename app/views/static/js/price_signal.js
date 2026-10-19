/* AI > Price analysis chart (update 50). One y-axis, THB/kg.
   data-chart: {start, end, base, sources: {NABC: [[day, kg]], ...}, lines: {sma_short, sma_long, band_up, band_low},
                marks: [[day, "B"|"S", kg]], levels: {cost, tp, sl}}.
   The base source is the thick line (the signals are worked out on it); the other sources are thin lines in their
   own fixed colour; the averages are neutral dashed lines, the bands a light area; B / S are the average crosses;
   cost, take profit and stop loss are labelled horizontal lines. Hover / tap: crosshair + tooltip.
   Update 63: drawn again when the live refresh brings a new chart ("aoi:live"). */
(function () {
  "use strict";
  var current = null, timer = null;
  window.addEventListener("resize", function () { clearTimeout(timer); timer = setTimeout(function () { if (current) current(); }, 150); });
  document.addEventListener("aoi:live", function (e) { if (!e.detail || e.detail.indexOf("ps-chart") >= 0) init(); });
  init();
  function init() {
  var host = document.getElementById("ps-svg");
  if (!host) return;
  var data = JSON.parse(host.getAttribute("data-chart"));
  var text = JSON.parse(host.getAttribute("data-text"));
  var tip = document.getElementById("ps-tip"), empty = document.getElementById("ps-empty");
  var NS = "http://www.w3.org/2000/svg", DAY = 86400000;
  var ORDER = ["NABC", "TRMA", "TREA", "APPLIED", "BUY", "SELL"];
  var boxes = document.querySelectorAll("[data-ps-line]");
  var W = 760, H = 340, M = { l: 46, r: 64, t: 14, b: 30 };

  function el(name, attrs) { var n = document.createElementNS(NS, name); for (var k in attrs) n.setAttribute(k, attrs[k]); return n; }
  function T(day) { return Date.parse(day + "T00:00:00Z"); }
  function fmtDay(t) { var d = new Date(t); return ("0" + d.getUTCDate()).slice(-2) + "/" + ("0" + (d.getUTCMonth() + 1)).slice(-2) + "/" + d.getUTCFullYear(); }
  function on(name) { var b = document.querySelector('[data-ps-line="' + name + '"]'); return !b || b.checked; }
  function niceStep(span) {
    var raw = span / 5, pow = Math.pow(10, Math.floor(Math.log(raw) / Math.LN10)), n = raw / pow;
    return (n < 1.5 ? 1 : n < 3 ? 2 : n < 7 ? 5 : 10) * pow;
  }
  function pts(list) { return (list || []).map(function (p) { return [T(p[0]), p[1]]; }); }

  function draw() {
    W = Math.max(300, Math.round(host.clientWidth || 760));
    H = Math.round(Math.min(360, Math.max(240, W * 0.5)));
    var start = T(data.start), end = T(data.end), series = [];
    ORDER.forEach(function (k) { if (data.sources[k] && data.sources[k].length && on(k)) series.push({ k: k, p: pts(data.sources[k]), base: k === data.base }); });
    var L = data.lines || {}, extra = [];
    if (on("sma")) { extra.push({ k: "sma_short", p: pts(L.sma_short), cls: "ps-l-smas" }); extra.push({ k: "sma_long", p: pts(L.sma_long), cls: "ps-l-smal" }); }
    var band = on("band") && L.band_up && L.band_up.length ? { up: pts(L.band_up), low: pts(L.band_low) } : null;
    var levels = on("levels") && data.levels ? data.levels : null;
    host.textContent = "";
    empty.hidden = series.length > 0;
    if (!series.length) return;
    var lo = Infinity, hi = -Infinity, first = Infinity;
    function take(list) { list.forEach(function (p) { lo = Math.min(lo, p[1]); hi = Math.max(hi, p[1]); first = Math.min(first, p[0]); }); }
    series.forEach(function (s) { take(s.p); });
    extra.forEach(function (s) { take(s.p); });
    if (band) { take(band.up); take(band.low); }
    if (levels) ["cost", "tp", "sl"].forEach(function (k) { if (levels[k] != null) { lo = Math.min(lo, levels[k]); hi = Math.max(hi, levels[k]); } });
    var x0 = Math.max(start, first - DAY), step = niceStep(Math.max(0.5, hi - lo));
    lo = Math.max(0, Math.floor(lo / step) * step); hi = Math.ceil(hi / step) * step + (hi === lo ? step : 0);
    var X = function (t) { return M.l + (t - x0) / Math.max(DAY, end - x0) * (W - M.l - M.r); };
    var Y = function (v) { return M.t + (hi - v) / Math.max(1e-9, hi - lo) * (H - M.t - M.b); };
    var svg = el("svg", { viewBox: "0 0 " + W + " " + H, role: "img", "class": "mkh-svg ps-svg" });
    for (var v = lo; v <= hi + 1e-9; v += step) {
      svg.appendChild(el("line", { x1: M.l, x2: W - M.r, y1: Y(v), y2: Y(v), "class": "mkh-grid" }));
      var yl = el("text", { x: M.l - 6, y: Y(v) + 4, "class": "mkh-axis", "text-anchor": "end" }); yl.textContent = v.toFixed(step < 1 ? 1 : 0); svg.appendChild(yl);
    }
    var span = (end - x0) / DAY, months = span > 700 ? 6 : span > 300 ? 2 : 1, d = new Date(x0); d.setUTCDate(1); d.setUTCMonth(d.getUTCMonth() + 1);
    for (; d.getTime() <= end; d.setUTCMonth(d.getUTCMonth() + months)) {
      var xl = el("text", { x: X(d.getTime()), y: H - 8, "class": "mkh-axis", "text-anchor": "middle" });
      xl.textContent = ("0" + (d.getUTCMonth() + 1)).slice(-2) + "/" + String(d.getUTCFullYear()).slice(-2); svg.appendChild(xl);
    }
    function path(list) { return list.map(function (p, i) { return (i ? "L" : "M") + X(p[0]).toFixed(1) + "," + Y(p[1]).toFixed(1); }).join(" "); }
    if (band) {
      var area = path(band.up) + " " + band.low.slice().reverse().map(function (p) { return "L" + X(p[0]).toFixed(1) + "," + Y(p[1]).toFixed(1); }).join(" ") + " Z";
      svg.appendChild(el("path", { d: area, "class": "ps-band" }));
    }
    if (levels) {
      [["sl", text.sl], ["cost", text.cost], ["tp", text.tp]].forEach(function (lv) {
        var val = levels[lv[0]]; if (val == null) return;
        svg.appendChild(el("line", { x1: M.l, x2: W - M.r, y1: Y(val), y2: Y(val), "class": "ps-lv ps-lv-" + lv[0] }));
        var lt = el("text", { x: W - M.r + 6, y: Y(val) + 4, "class": "ps-lv-t" }); lt.textContent = lv[1] + " " + val.toFixed(2); svg.appendChild(lt);
      });
    }
    extra.forEach(function (s) { if (s.p.length) svg.appendChild(el("path", { d: path(s.p), fill: "none", "class": s.cls })); });
    series.slice().sort(function (a, b) { return a.base - b.base; }).forEach(function (s) {
      var line = el("path", { d: path(s.p), fill: "none", "class": "ps-src ps-s-" + s.k + (s.base ? " base" : "") });
      svg.appendChild(line);
      if (s.p.length < 40) s.p.forEach(function (p) { svg.appendChild(el("circle", { cx: X(p[0]), cy: Y(p[1]), r: s.base ? 3.5 : 2.5, "class": "ps-dot ps-s-" + s.k })); });
      if (s.base) {
        var lp = s.p[s.p.length - 1];
        svg.appendChild(el("circle", { cx: X(lp[0]), cy: Y(lp[1]), r: 5, "class": "ps-dot ps-s-" + s.k + " last" }));
        if (!levels) { var et = el("text", { x: W - M.r + 6, y: Y(lp[1]) + 4, "class": "mkh-end" }); et.textContent = lp[1].toFixed(2); svg.appendChild(et); }
      }
    });
    if (on("marks")) (data.marks || []).forEach(function (m) {
      var x = X(T(m[0])), y = Y(m[2]), buy = m[1] === "B";
      var tri = buy ? "M" + x + "," + (y + 6) + " l-6,10 h12 z" : "M" + x + "," + (y - 6) + " l-6,-10 h12 z";
      svg.appendChild(el("path", { d: tri, "class": buy ? "ps-mk-b" : "ps-mk-s" }));
      var mt = el("text", { x: x, y: buy ? y + 28 : y - 20, "class": "ps-mk-t", "text-anchor": "middle" }); mt.textContent = m[1]; svg.appendChild(mt);
    });
    var cross = el("line", { y1: M.t, y2: H - M.b, "class": "mkh-cross", visibility: "hidden" });
    svg.appendChild(cross);
    var hit = el("rect", { x: M.l, y: M.t, width: W - M.l - M.r, height: H - M.t - M.b, fill: "transparent" });
    svg.appendChild(hit);
    function at(list, t) { var best = null; list.forEach(function (p) { if (p[0] <= t + DAY / 2) best = p; }); return best; }
    function move(evt) {
      var box = svg.getBoundingClientRect(), px = ((evt.touches ? evt.touches[0].clientX : evt.clientX) - box.left) * W / box.width;
      var t = x0 + (px - M.l) / (W - M.l - M.r) * Math.max(DAY, end - x0);
      cross.setAttribute("x1", px); cross.setAttribute("x2", px); cross.setAttribute("visibility", "visible");
      tip.textContent = "";
      var head = document.createElement("b"); head.textContent = fmtDay(t); tip.appendChild(head);
      function row(cls, label, val) {
        if (!val) return;
        var r = document.createElement("div"), sw = document.createElement("i");
        sw.className = "mkh-sw " + cls; r.appendChild(sw);
        r.appendChild(document.createTextNode(label + ": " + val[1].toFixed(2) + " " + text.unit)); tip.appendChild(r);
      }
      series.forEach(function (s) { row("ps-c-" + s.k, text.names[s.k] || s.k, at(s.p, t)); });
      extra.forEach(function (s) { row(s.cls === "ps-l-smas" ? "ps-c-sma" : "ps-c-smal", s.k === "sma_short" ? "SMA S" : "SMA L", at(s.p, t)); });
      tip.hidden = false;
      var left = (px / W) * box.width;
      tip.style.left = Math.min(Math.max(0, left + 12), box.width - tip.offsetWidth) + "px";
    }
    function leave() { cross.setAttribute("visibility", "hidden"); tip.hidden = true; }
    hit.addEventListener("mousemove", move); hit.addEventListener("touchstart", move, { passive: true });
    hit.addEventListener("touchmove", move, { passive: true }); hit.addEventListener("mouseleave", leave);
    host.appendChild(svg);
  }
  boxes.forEach(function (b) { b.addEventListener("change", draw); });
  current = draw;
  draw();
  }
})();
