/* Financial > Investment & Break-even (draft).
   1. Budget bars: width from data-pct (set here because inline styles are not allowed by the page's CSP).
   2. Investment item form: the useful life placeholder follows the chosen category.
   3. Break-even chart: kg paddy per month (x) against THB per month (y). Revenue = revenue/kg x kg,
      total cost = fixed + variable/kg x kg, fixed cost dashed. Shaded loss zone before the break-even point,
      profit zone after it. Planned volume and capacity as reference lines. Hover/tap: crosshair + tooltip. */
(function () {
  "use strict";
  document.querySelectorAll("[data-pct]").forEach(function (el) {
    var pct = Math.max(0, Math.min(100, parseFloat(el.getAttribute("data-pct")) || 0));
    if (el.classList.contains("iv-bar-mark")) el.style.left = "calc(" + pct + "% - 1px)";
    else el.style.width = pct + "%";
  });

  var cat = document.getElementById("iv-cat"), life = document.getElementById("iv-life");
  function lifeHint() {
    if (!cat || !life) return;
    var opt = cat.options[cat.selectedIndex];
    life.placeholder = opt ? opt.getAttribute("data-life") : "";
  }
  if (cat) { cat.addEventListener("change", lifeHint); lifeHint(); }

  var host = document.getElementById("iv-be-chart");
  if (!host) return;
  var c = JSON.parse(host.getAttribute("data-chart")), text = c.text, tip = document.getElementById("iv-be-tip");
  var NS = "http://www.w3.org/2000/svg", W = 760, H = 320, M = { l: 64, r: 20, t: 16, b: 40 };
  function el(name, attrs) { var n = document.createElementNS(NS, name); for (var k in attrs) n.setAttribute(k, attrs[k]); return n; }
  function fmt(v) { return Math.round(v).toLocaleString(); }
  function short(v) { return Math.abs(v) >= 1e6 ? (v / 1e6).toFixed(1) + "M" : Math.abs(v) >= 1e3 ? Math.round(v / 1e3) + "k" : String(Math.round(v)); }
  function niceStep(span) {
    var raw = span / 5, pow = Math.pow(10, Math.floor(Math.log(raw) / Math.LN10)), n = raw / pow;
    return (n < 1.5 ? 1 : n < 3 ? 2 : n < 7 ? 5 : 10) * pow;
  }
  function rev(q) { return c.rev_kg * q; }
  function cost(q) { return c.fixed + c.var_kg * q; }

  function draw() {
    W = Math.max(300, Math.round(host.clientWidth || 760));
    H = Math.round(Math.min(340, Math.max(240, W * 0.5)));
    M.l = W < 420 ? 48 : 64;
    host.textContent = "";
    var xMax = c.x_max, yMax = Math.max(rev(xMax), cost(xMax), c.fixed * 1.2, 1);
    var yStep = niceStep(yMax); yMax = Math.ceil(yMax / yStep) * yStep;
    var X = function (q) { return M.l + q / xMax * (W - M.l - M.r); };
    var Y = function (v) { return M.t + (yMax - v) / yMax * (H - M.t - M.b); };
    var svg = el("svg", { viewBox: "0 0 " + W + " " + H, role: "img", "aria-label": text.rev + " / " + text.cost });
    for (var v = 0; v <= yMax + 1e-9; v += yStep) {
      svg.appendChild(el("line", { x1: M.l, x2: W - M.r, y1: Y(v), y2: Y(v), "class": "mkh-grid" }));
      var yl = el("text", { x: M.l - 6, y: Y(v) + 4, "class": "mkh-axis", "text-anchor": "end" }); yl.textContent = short(v); svg.appendChild(yl);
    }
    var xStep = niceStep(xMax);
    for (var q = 0; q <= xMax + 1e-9; q += xStep) {
      var xl = el("text", { x: X(q), y: H - M.b + 16, "class": "mkh-axis", "text-anchor": "middle" }); xl.textContent = short(q); svg.appendChild(xl);
    }
    var xt = el("text", { x: (M.l + W - M.r) / 2, y: H - 6, "class": "mkh-axis", "text-anchor": "middle" }); xt.textContent = text.x; svg.appendChild(xt);
    var be = c.be;
    if (be && be > 0 && be < xMax) {        // loss zone (cost above revenue) and profit zone
      svg.appendChild(el("polygon", { "class": "iv-zone-loss", points: [X(0), Y(cost(0)), X(be), Y(cost(be)), X(be), Y(rev(be)), X(0), Y(0)].join(" ") }));
      svg.appendChild(el("polygon", { "class": "iv-zone-profit", points: [X(be), Y(rev(be)), X(xMax), Y(rev(xMax)), X(xMax), Y(cost(xMax))].join(" ") }));
    }
    svg.appendChild(el("line", { x1: X(0), x2: X(xMax), y1: Y(c.fixed), y2: Y(c.fixed), "class": "iv-line-fixed", "stroke-width": 1.5 }));
    svg.appendChild(el("line", { x1: X(0), x2: X(xMax), y1: Y(cost(0)), y2: Y(cost(xMax)), "class": "iv-line-cost", "stroke-width": 2, "stroke-linecap": "round" }));
    svg.appendChild(el("line", { x1: X(0), x2: X(xMax), y1: Y(0), y2: Y(rev(xMax)), "class": "iv-line-rev", "stroke-width": 2, "stroke-linecap": "round" }));
    [["planned", c.planned], ["capacity", c.capacity]].forEach(function (r, i) {
      if (!r[1] || r[1] > xMax) return;
      svg.appendChild(el("line", { x1: X(r[1]), x2: X(r[1]), y1: M.t, y2: H - M.b, "class": "iv-ref" }));
      var tl = el("text", { x: X(r[1]) - 4, y: M.t + 12 + i * 14, "class": "iv-ref-label", "text-anchor": "end" }); tl.textContent = text[r[0]]; svg.appendChild(tl);
    });
    if (be && be > 0 && be <= xMax) {
      svg.appendChild(el("circle", { cx: X(be), cy: Y(rev(be)), r: 5, "class": "iv-be-dot" }));
      var bl = el("text", { x: X(be) + (X(be) > W * 0.7 ? -8 : 8), y: Y(rev(be)) - 10, "class": "iv-be-label", "text-anchor": X(be) > W * 0.7 ? "end" : "start" });
      bl.textContent = text.be + " " + fmt(be) + " kg"; svg.appendChild(bl);
    }
    var cross = el("line", { y1: M.t, y2: H - M.b, "class": "mkh-cross", visibility: "hidden" });
    svg.appendChild(cross);
    var hit = el("rect", { x: M.l, y: M.t, width: W - M.l - M.r, height: H - M.t - M.b, fill: "transparent" });
    svg.appendChild(hit);
    function move(evt) {
      var box = svg.getBoundingClientRect(), px = ((evt.touches ? evt.touches[0].clientX : evt.clientX) - box.left) * W / box.width;
      var qq = Math.max(0, Math.min(xMax, (px - M.l) / (W - M.l - M.r) * xMax));
      cross.setAttribute("x1", X(qq)); cross.setAttribute("x2", X(qq)); cross.setAttribute("visibility", "visible");
      tip.textContent = "";
      var head = document.createElement("b"); head.textContent = fmt(qq) + " " + text.kg; tip.appendChild(head);
      [[text.rev, rev(qq), "rev"], [text.cost, cost(qq), "cost"], [text.fixed, c.fixed, "fixed"]].forEach(function (r) {
        var row = document.createElement("div"), sw = document.createElement("i");
        sw.className = "iv-sw " + r[2]; row.appendChild(sw); row.appendChild(document.createTextNode(r[0] + ": " + fmt(r[1]))); tip.appendChild(row);
      });
      var p = rev(qq) - cost(qq), res = document.createElement("div");
      res.className = p >= 0 ? "pos" : "neg"; res.textContent = (p >= 0 ? text.profit : text.loss) + ": " + fmt(Math.abs(p)); tip.appendChild(res);
      tip.hidden = false;
      var left = X(qq) / W * box.width;
      tip.style.left = Math.min(Math.max(0, left + 12), box.width - tip.offsetWidth) + "px";
    }
    function leave() { cross.setAttribute("visibility", "hidden"); tip.hidden = true; }
    hit.addEventListener("mousemove", move); hit.addEventListener("touchstart", move, { passive: true });
    hit.addEventListener("touchmove", move, { passive: true }); hit.addEventListener("mouseleave", leave);
    host.appendChild(svg);
  }
  var timer = null;
  window.addEventListener("resize", function () { clearTimeout(timer); timer = setTimeout(draw, 150); });
  draw();
})();
