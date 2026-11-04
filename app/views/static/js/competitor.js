/* Update 109: competitor analysis chart - our price (solid step line) against each competitor's sightings (dashed
   lines with dots), THB/kg over the chosen period. Each competitor also has its own dot shape, so colour is not the only cue. */
(function () {
  "use strict";
  var host = document.getElementById("co-chart");
  if (!host) return;
  var data = JSON.parse(host.getAttribute("data-series")), NS = "http://www.w3.org/2000/svg", DAY = 86400000;
  var COLORS = ["#ea580c", "#7c3aed", "#0891b2", "#db2777", "#65a30d", "#ca8a04"], SHAPES = ["circle", "square", "diamond"];
  function t(day) { return Date.parse(day + "T00:00:00Z"); }
  function el(name, attrs, text) { var n = document.createElementNS(NS, name); for (var k in attrs) n.setAttribute(k, attrs[k]); if (text !== undefined) n.textContent = text; return n; }
  function fmt(ms) { var d = new Date(ms); return ("0" + d.getUTCDate()).slice(-2) + "/" + ("0" + (d.getUTCMonth() + 1)).slice(-2); }
  function mark(svg, shape, x, y, color, title) {
    var m = shape === "square" ? el("rect", { x: x - 4.5, y: y - 4.5, width: 9, height: 9 })
      : shape === "diamond" ? el("path", { d: "M" + x + " " + (y - 6) + "l6 6-6 6-6-6z" }) : el("circle", { cx: x, cy: y, r: 5 });
    m.setAttribute("fill", color); m.setAttribute("stroke", "#fff"); m.setAttribute("stroke-width", "1.5");
    m.appendChild(el("title", {}, title)); svg.appendChild(m);
  }
  function draw() {
    host.innerHTML = "";
    var all = [];
    data.ours.forEach(function (p) { all.push(p[1]); });
    data.comps.forEach(function (c) { c.points.forEach(function (p) { all.push(p[1]); }); });
    document.getElementById("co-chart-empty").hidden = all.length > 0;
    if (!all.length) return;
    var W = Math.max(300, host.clientWidth || 760), H = Math.round(Math.min(320, Math.max(220, W * 0.42))), M = { l: 46, r: 14, t: 12, b: 28 };
    var x0 = t(data.start), x1 = t(data.end) + DAY / 2, lo = Math.min.apply(null, all), hi = Math.max.apply(null, all);
    var pad = Math.max(0.5, (hi - lo) * 0.15); lo = Math.max(0, lo - pad); hi += pad;
    function X(ms) { return M.l + (ms - x0) / (x1 - x0) * (W - M.l - M.r); }
    function Y(v) { return M.t + (hi - v) / (hi - lo) * (H - M.t - M.b); }
    var svg = el("svg", { viewBox: "0 0 " + W + " " + H, width: W, height: H, "class": "co-svg", role: "img", "aria-label": data.name });
    for (var i = 0; i <= 4; i++) {
      var v = lo + (hi - lo) * i / 4, y = Y(v);
      svg.appendChild(el("line", { x1: M.l, x2: W - M.r, y1: y, y2: y, "class": "co-grid" }));
      svg.appendChild(el("text", { x: M.l - 6, y: y + 4, "text-anchor": "end", "class": "co-axis" }, v.toFixed(2)));
    }
    for (var k = 0; k <= 4; k++) {
      var ms = x0 + (x1 - x0) * k / 4;
      svg.appendChild(el("text", { x: X(ms), y: H - 8, "text-anchor": k === 0 ? "start" : (k === 4 ? "end" : "middle"), "class": "co-axis" }, fmt(ms)));
    }
    if (data.ours.length) {                        // our price: a step line (it holds until the next change)
      var d = "", last = null;
      data.ours.forEach(function (p, n) {
        var x = X(t(p[0])), y = Y(p[1]);
        d += n === 0 ? "M" + x + " " + y : "H" + x + "V" + y; last = p;
      });
      d += "H" + X(t(data.end));
      svg.appendChild(el("path", { d: d, "class": "co-ours" }));
    }
    var legend = document.createElement("div"); legend.className = "co-legend";
    legend.innerHTML = '<span><i class="co-lg-ours"></i>' + host.getAttribute("data-ours") + "</span>";
    data.comps.forEach(function (c, n) {
      var color = COLORS[n % COLORS.length], shape = SHAPES[n % SHAPES.length];
      if (c.points.length > 1) {
        var d = c.points.map(function (p, j) { return (j ? "L" : "M") + X(t(p[0])) + " " + Y(p[1]); }).join("");
        svg.appendChild(el("path", { d: d, fill: "none", stroke: color, "stroke-width": 2, "stroke-dasharray": "6 4" }));
      }
      c.points.forEach(function (p) { mark(svg, shape, X(t(p[0])), Y(p[1]), color, c.name + " " + p[0] + ": " + Number(p[1]).toFixed(2)); });
      var s = document.createElement("span"), i = document.createElement("i");
      i.className = "co-lg-dot " + shape; i.style.background = color; s.appendChild(i); s.appendChild(document.createTextNode(c.name)); legend.appendChild(s);
    });
    host.appendChild(svg); host.appendChild(legend);
  }
  draw();
  var timer; window.addEventListener("resize", function () { clearTimeout(timer); timer = setTimeout(draw, 200); });
})();

/* update 116: while the AI summary runs in the background, ask every 4 s and reload when it is finished */
(function () {
  "use strict";
  var box = document.getElementById("co-ai-run");
  if (!box) return;
  var url = box.getAttribute("data-poll"), tries = 0;
  function poll() {
    tries++;
    fetch(url, { credentials: "same-origin", headers: { "Accept": "application/json" } })
      .then(function (r) { return r.json(); })
      .then(function (d) {
        if (d.state === "running" && tries < 200) { setTimeout(poll, 4000); return; }
        window.location.hash = "co-ai"; window.location.reload();
      })
      .catch(function () { if (tries < 200) setTimeout(poll, 6000); });
  }
  setTimeout(poll, 4000);
})();
