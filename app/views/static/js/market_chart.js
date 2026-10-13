/* Market Price graph: THB/kg of every SKU over 3, 6, 12 or 36 months.
   Data: <div id="mkh-chart" data-series='{"skus": [{code, name, variety, kind, points: [[day, kg]]}]}'>.
   Colour = rice variety (Jasmine, White, Glutinous, bran), line style = kind (paddy solid, rice dashed, broken dotted),
   so a line is never told apart by colour alone. One y-axis (all prices are THB/kg). Hover/tap: crosshair + tooltip. */
(function () {
  "use strict";
  var host = document.getElementById("mkh-chart");
  if (!host) return;
  var data = JSON.parse(host.getAttribute("data-series"));
  var text = JSON.parse(host.getAttribute("data-text"));
  var DAY = 86400000, MONTHS = { "3M": 3, "6M": 6, "12M": 12, "36M": 36 };
  var DASH = { paddy: "", rice: "7 4", broken: "2 4", bran: "", husk: "" };
  var NS = "http://www.w3.org/2000/svg";
  var periodBtns = document.querySelectorAll("[data-mkh-period]"), skuBoxes = document.querySelectorAll("[data-mkh-sku]");
  var tip = document.getElementById("mkh-tip"), empty = document.getElementById("mkh-empty");
  var period = host.getAttribute("data-period") || "12M";
  var W = 760, H = 320, M = { l: 46, r: 56, t: 14, b: 30 };
  function size() {                       // drawn at the real width, so the text stays readable on a phone
    W = Math.max(300, Math.round(host.clientWidth || 760));
    H = Math.round(Math.min(340, Math.max(230, W * 0.48)));
  }

  function toTime(day) { return Date.parse(day + "T00:00:00Z"); }
  function fmtDay(t) { var d = new Date(t); return ("0" + d.getUTCDate()).slice(-2) + "/" + ("0" + (d.getUTCMonth() + 1)).slice(-2) + "/" + d.getUTCFullYear(); }
  function el(name, attrs) { var n = document.createElementNS(NS, name); for (var k in attrs) n.setAttribute(k, attrs[k]); return n; }
  function colour(s) { return "var(--mkh-" + (s.variety || "bran") + ")"; }
  function chosen() {
    var on = {};
    skuBoxes.forEach(function (b) { if (b.checked) on[b.getAttribute("data-mkh-sku")] = true; });
    return on;
  }
  function niceStep(span) {
    var raw = span / 5, pow = Math.pow(10, Math.floor(Math.log(raw) / Math.LN10)), n = raw / pow;
    return (n < 1.5 ? 1 : n < 3 ? 2 : n < 7 ? 5 : 10) * pow;
  }

  function draw() {
    size();
    var end = toTime(data.end), start = end - Math.round(MONTHS[period] * 30.44) * DAY, on = chosen();
    var shown = data.skus.filter(function (s) { return on[s.code]; }).map(function (s) {
      return { s: s, pts: s.points.filter(function (p) { return toTime(p[0]) >= start; }).map(function (p) { return [toTime(p[0]), p[1]]; }) };
    }).filter(function (x) { return x.pts.length; });
    host.textContent = "";
    empty.hidden = shown.length > 0;
    if (!shown.length) return;
    var lo = Infinity, hi = -Infinity, first = Infinity;
    shown.forEach(function (x) { x.pts.forEach(function (p) { lo = Math.min(lo, p[1]); hi = Math.max(hi, p[1]); first = Math.min(first, p[0]); }); });
    var x0 = Math.max(start, first - DAY), step = niceStep(Math.max(1, hi - lo));
    lo = Math.max(0, Math.floor(lo / step) * step - (hi === lo ? step : 0)); hi = Math.ceil(hi / step) * step + (hi === lo ? step : 0);
    var X = function (t) { return M.l + (t - x0) / Math.max(DAY, end - x0) * (W - M.l - M.r); };
    var Y = function (v) { return M.t + (hi - v) / Math.max(1e-9, hi - lo) * (H - M.t - M.b); };
    var svg = el("svg", { viewBox: "0 0 " + W + " " + H, role: "img", "aria-label": text.title, "class": "mkh-svg" });
    for (var v = lo; v <= hi + 1e-9; v += step) {             // recessive grid + y labels
      svg.appendChild(el("line", { x1: M.l, x2: W - M.r, y1: Y(v), y2: Y(v), "class": "mkh-grid" }));
      var yl = el("text", { x: M.l - 6, y: Y(v) + 4, "class": "mkh-axis", "text-anchor": "end" }); yl.textContent = v.toFixed(step < 1 ? 1 : 0); svg.appendChild(yl);
    }
    var months = period === "36M" ? 6 : period === "12M" ? 2 : 1, d = new Date(x0); d.setUTCDate(1); d.setUTCMonth(d.getUTCMonth() + 1);
    for (; d.getTime() <= end; d.setUTCMonth(d.getUTCMonth() + months)) {     // x labels on month starts
      var xl = el("text", { x: X(d.getTime()), y: H - 8, "class": "mkh-axis", "text-anchor": "middle" });
      xl.textContent = ("0" + (d.getUTCMonth() + 1)).slice(-2) + "/" + String(d.getUTCFullYear()).slice(-2); svg.appendChild(xl);
    }
    var labels = [];
    shown.forEach(function (x) {
      var path = x.pts.map(function (p, i) { return (i ? "L" : "M") + X(p[0]).toFixed(1) + "," + Y(p[1]).toFixed(1); }).join(" ");
      var line = el("path", { d: path, fill: "none", stroke: colour(x.s), "stroke-width": 2, "stroke-linejoin": "round", "stroke-linecap": "round" });
      if (DASH[x.s.kind]) line.setAttribute("stroke-dasharray", DASH[x.s.kind]);
      svg.appendChild(line);
      var lastP = x.pts[x.pts.length - 1];
      svg.appendChild(el("circle", { cx: X(lastP[0]), cy: Y(lastP[1]), r: 4, fill: colour(x.s), stroke: "var(--surface)", "stroke-width": 2 }));
      labels.push({ y: Y(lastP[1]), x: X(lastP[0]), t: lastP[1].toFixed(2), s: x.s });
    });
    if (shown.length <= 4) {                                   // direct end labels, nudged apart
      labels.sort(function (a, b) { return a.y - b.y; });
      for (var i = 1; i < labels.length; i++) if (labels[i].y - labels[i - 1].y < 13) labels[i].y = labels[i - 1].y + 13;
      labels.forEach(function (l) {
        var tl = el("text", { x: W - M.r + 8, y: l.y + 4, "class": "mkh-end" }); tl.textContent = l.t; svg.appendChild(tl);
      });
    }
    var cross = el("line", { y1: M.t, y2: H - M.b, "class": "mkh-cross", visibility: "hidden" });
    svg.appendChild(cross);
    var hit = el("rect", { x: M.l, y: M.t, width: W - M.l - M.r, height: H - M.t - M.b, fill: "transparent" });
    svg.appendChild(hit);
    function move(evt) {
      var box = svg.getBoundingClientRect(), px = ((evt.touches ? evt.touches[0].clientX : evt.clientX) - box.left) * W / box.width;
      var t = x0 + (px - M.l) / (W - M.l - M.r) * Math.max(DAY, end - x0);
      cross.setAttribute("x1", px); cross.setAttribute("x2", px); cross.setAttribute("visibility", "visible");
      tip.textContent = "";
      var head = document.createElement("b"); head.textContent = fmtDay(t); tip.appendChild(head);
      shown.forEach(function (x) {
        var best = null;
        x.pts.forEach(function (p) { if (p[0] <= t + DAY / 2) best = p; });
        if (!best) return;
        var row = document.createElement("div"), sw = document.createElement("i");
        sw.className = "mkh-sw mkh-k-" + x.s.kind; sw.style.borderColor = colour(x.s);
        row.appendChild(sw); row.appendChild(document.createTextNode(x.s.name + ": " + best[1].toFixed(2) + " " + text.unit));
        tip.appendChild(row);
      });
      tip.hidden = false;
      var left = (px / W) * box.width;
      tip.style.left = Math.min(Math.max(0, left + 12), box.width - tip.offsetWidth) + "px";
    }
    function leave() { cross.setAttribute("visibility", "hidden"); tip.hidden = true; }
    hit.addEventListener("mousemove", move); hit.addEventListener("touchstart", move, { passive: true });
    hit.addEventListener("touchmove", move, { passive: true }); hit.addEventListener("mouseleave", leave);
    host.appendChild(svg);
  }
  periodBtns.forEach(function (b) {
    b.addEventListener("click", function () {
      period = b.getAttribute("data-mkh-period");
      periodBtns.forEach(function (o) { o.classList.toggle("active", o === b); o.setAttribute("aria-pressed", o === b ? "true" : "false"); });
      document.querySelectorAll("[data-mkh-row]").forEach(function (r) { r.hidden = r.getAttribute("data-mkh-period") !== period; });
      draw();
    });
  });
  skuBoxes.forEach(function (b) { b.addEventListener("change", draw); });
  var allBtn = document.getElementById("mkh-all"), noneBtn = document.getElementById("mkh-none");
  if (allBtn) allBtn.addEventListener("click", function () { skuBoxes.forEach(function (b) { b.checked = true; }); draw(); });
  if (noneBtn) noneBtn.addEventListener("click", function () { skuBoxes.forEach(function (b) { b.checked = false; }); draw(); });
  var resizeTimer = null;
  window.addEventListener("resize", function () { clearTimeout(resizeTimer); resizeTimer = setTimeout(draw, 150); });
  draw();
})();
