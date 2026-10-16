/* SCADA (update 34: the "control room" design Tim approved): glowing machine tiles on lit platforms over a circuit
   board, glowing links where material flows, a details panel with a speed gauge and ON / OFF / speed / reset, and
   three monitors (output Hz and motor current of the last 10 minutes, alarms). Refreshed every 2 seconds from
   /automation/scada.json.

   Layout from the data: every machine has a column and row (SCADA_COL, SCADA_ROW) and the machines it feeds
   (FEEDS_TO). A machine fed from / feeding several rows of the next columns grows to cover them (milling inbound,
   packing); links inside one column are vertical (sorting -> inbound, packing -> AMR).
   The page's security policy allows no inline style attributes: positions are set through element.style only. */
(function () {
  "use strict";
  var root = document.getElementById("scada");
  if (!root) return;
  var NS = "http://www.w3.org/2000/svg";
  var text = JSON.parse(root.getAttribute("data-text"));
  var canCommand = !!root.getAttribute("data-can-command");
  var wrap = document.getElementById("scada-wrap"), canvas = document.getElementById("scada-canvas");
  var panel = document.getElementById("scada-panel");
  var data = JSON.parse(root.getAttribute("data-initial"));
  var selected = null, lastOk = Date.now(), panelSig = null;

  var PADX = 24, TOP = 64, ROWH = 160, GAP = 72, CARD_H = 112, SLIM_H = 72;
  var SLIM = { CONVEYOR: 1, SORTER: 1 };

  // ---------- small helpers ----------
  function h(tag, cls, parent, txt) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (txt !== undefined && txt !== null) e.textContent = txt;
    if (parent) parent.appendChild(e);
    return e;
  }
  function s(tag, attrs, parent) {
    var e = document.createElementNS(NS, tag);
    for (var k in attrs) e.setAttribute(k, attrs[k]);
    if (parent) parent.appendChild(e);
    return e;
  }
  function place(e, x, y, w, ht) { e.style.left = x + "px"; e.style.top = y + "px"; if (w) e.style.width = w + "px"; if (ht) e.style.height = ht + "px"; }
  function fmt(v, d) { return v === null || v === undefined ? "-" : Number(v).toLocaleString(undefined, { minimumFractionDigits: d, maximumFractionDigits: d }); }
  function state(n) {
    if (!n.active) return "dis";
    if (n.status === "F" || (n.live && n.live.fault)) return "fault";
    if (n.status === "R") return "run";
    if (n.status === "M") return "maint";
    if (n.status === "O" || (n.live && !n.live.fresh)) return "off";
    return "idle";
  }
  function statusText(n) { return n.active ? (text.status[n.status] || n.status) : text.status.DIS; }

  // ---------- icons (24 x 24 line drawings) ----------
  var ICON = {
    SILO: "M6 3h12v11l-4 5h-4l-4-5z M6 8h12 M10 19v2h4v-2",
    CONVEYOR: "M5 10h14a3 3 0 0 1 0 6H5a3 3 0 0 1 0-6z M5.5 13h.01 M18.5 13h.01 M7 7h4 M13 7h4",
    INBOUND: "M3 4h18l-6 8v7h-6v-7z M7 8h10",
    SORTER: "M6 4h12a2 2 0 0 1 2 2v4a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2z M7 12l-2 8 M12 12v8 M17 12l2 8",
    MAIN_MOTOR: "M4 7h10a2 2 0 0 1 2 2v7a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V9a2 2 0 0 1 2-2z M16 11h3v3h-3 M19 12.5h3",
    PACKER: "M4 8l8-4 8 4v8l-8 4-8-4z M4 8l8 4 8-4 M12 12v8",
    AMR: "M6 9h12a3 3 0 0 1 3 3v2a3 3 0 0 1-3 3H6a3 3 0 0 1-3-3v-2a3 3 0 0 1 3-3z M9 5h6v4 M12 3v2",
    OTHER: "M6 6h12v12H6z M9 12h6"
  };
  function icon(type, parent, size) {
    var svg = s("svg", { width: size, height: size, viewBox: "0 0 24 24", fill: "none", stroke: "currentColor", "stroke-width": 1.6,
                         "stroke-linecap": "round", "stroke-linejoin": "round", "aria-hidden": "true", "class": "sc-ico" }, parent);
    s("path", { d: ICON[type] || ICON.OTHER }, svg);
    if (type === "MAIN_MOTOR") {
      var fan = s("g", { "class": "sc-fan" }, s("g", { transform: "translate(9 12.5)" }, svg));
      s("circle", { cx: 0, cy: 0, r: 3 }, fan); s("path", { d: "M0 -3v6M-3 0h6" }, fan);
    }
    return svg;
  }

  // ---------- layout from the data ----------
  function layout(nodes) {
    var placed = nodes.filter(function (n) { return n.col !== null && n.row !== null; });
    var byId = {}, cols = {};
    placed.forEach(function (n) { byId[n.id] = n; (cols[n.col] = cols[n.col] || []).push(n); });
    var maxCol = Math.max.apply(null, placed.map(function (n) { return n.col; }).concat([4]));
    var colW = [], colX = [], x = PADX;
    for (var c = 0; c <= maxCol; c++) {
      colW[c] = Math.max.apply(null, (cols[c] || []).map(function (n) { return n.type === "MAIN_MOTOR" ? 220 : 160; }).concat([160]));
      colX[c] = x; x += colW[c] + GAP;
    }
    var feedsIn = {};
    placed.forEach(function (n) { n.feeds.forEach(function (to) { (feedsIn[to] = feedsIn[to] || []).push(n.id); }); });
    var geo = {};
    placed.forEach(function (n) {           // rows covered: its own + single-row neighbours in the next / previous column
      var rows = [n.row];
      (feedsIn[n.id] || []).forEach(function (id) { var o = byId[id]; if (o && o.col === n.col - 1) rows.push(o.row); });
      n.feeds.forEach(function (id) { var o = byId[id]; if (o && o.col === n.col + 1) rows.push(o.row); });
      var lo = Math.min.apply(null, rows), hi = Math.max.apply(null, rows);
      (cols[n.col] || []).forEach(function (o) { if (o !== n && o.row > n.row && o.row <= hi) hi = o.row - 1; if (o !== n && o.row < n.row && o.row >= lo) lo = o.row + 1; });
      var w = n.type === "MAIN_MOTOR" ? 220 : 160;
      var left = colX[n.col] + (colW[n.col] - w) / 2, top, ht;
      if (hi > lo) { top = TOP + lo * ROWH; ht = (hi - lo) * ROWH + CARD_H; }
      else if (SLIM[n.type]) { top = TOP + n.row * ROWH + (CARD_H - SLIM_H) / 2; ht = SLIM_H; }
      else { top = TOP + n.row * ROWH; ht = CARD_H; }
      geo[n.id] = { x: left, y: top, w: w, h: ht, span: hi > lo, cx: left + w / 2, cy: top + ht / 2, rowY: TOP + n.row * ROWH + CARD_H / 2 };
    });
    var maxRow = Math.max.apply(null, placed.map(function (n) { return n.row; }).concat([2]));
    return { placed: placed, byId: byId, geo: geo, colX: colX, colW: colW, W: x - GAP + PADX, H: TOP + maxRow * ROWH + CARD_H + 40, maxCol: maxCol };
  }

  // ---------- draw the plant ----------
  function draw() {
    var L = layout(data.nodes), g = L.geo;
    canvas.textContent = "";
    canvas.style.width = L.W + "px"; canvas.style.height = L.H + "px";
    // stage titles
    for (var c = 0; c <= L.maxCol; c++) {
      var st = h("div", "sc-stage" + (c === 3 ? " hot" : ""), canvas, text.stages[c] || "");
      place(st, L.colX[c] - 20, 18, L.colW[c] + 40);
    }
    // the milling frame around the main motors
    var motors = L.placed.filter(function (n) { return n.type === "MAIN_MOTOR"; });
    if (motors.length) {
      var x0 = Math.min.apply(null, motors.map(function (n) { return g[n.id].x; })) - 16;
      var y0 = Math.min.apply(null, motors.map(function (n) { return g[n.id].y; })) - 24;
      var x1 = Math.max.apply(null, motors.map(function (n) { return g[n.id].x + g[n.id].w; })) + 16;
      var y1 = Math.max.apply(null, motors.map(function (n) { return g[n.id].y + g[n.id].h; })) + 24;
      place(h("div", "sc-group", canvas), x0, y0, x1 - x0, y1 - y0);
    }
    // glowing platforms
    L.placed.forEach(function (n) {
      var p = g[n.id], plat = h("div", "sc-plat st-" + state(n), canvas);
      place(plat, p.x - 14, p.y + p.h - 16, p.w + 28, 32);
    });
    // links
    var svg = s("svg", { "class": "sc-links", width: L.W, height: L.H, viewBox: "0 0 " + L.W + " " + L.H, "aria-hidden": "true" }, canvas);
    var defs = s("defs", {}, svg);
    [["a-dim", "dim"], ["a-hot", "hot"]].forEach(function (m) {
      var mk = s("marker", { id: "sc-" + m[0], viewBox: "0 0 10 10", refX: 8, refY: 5, markerWidth: 6, markerHeight: 6, orient: "auto" }, defs);
      s("path", { d: "M0 0L10 5L0 10z", "class": "sc-head " + m[1] }, mk);
    });
    L.placed.forEach(function (n) {
      n.feeds.forEach(function (to) {
        var t = L.byId[to];
        if (!t) return;
        var a = g[n.id], b = g[t.id], d;
        if (t.col === n.col) {
          var up = b.y < a.y;
          d = "M" + a.cx + " " + (up ? a.y : a.y + a.h) + " V" + (up ? b.y + b.h + 4 : b.y - 4);
        } else {
          var y1 = a.span && !b.span ? b.cy : a.cy, y2 = b.span && !a.span ? a.cy : b.cy;
          if (a.span && b.span) { y1 = y2 = (Math.max(a.y, b.y) + Math.min(a.y + a.h, b.y + b.h)) / 2; }
          var xa = a.x + a.w, xb = b.x - 4, xm = (xa + xb) / 2;
          d = y1 === y2 ? "M" + xa + " " + y1 + " H" + xb : "M" + xa + " " + y1 + " H" + xm + " V" + y2 + " H" + xb;
        }
        var hot = state(n) === "run";
        var path = s("path", { d: d, "class": "sc-link" + (hot ? " hot" : ""), "marker-end": "url(#sc-" + (hot ? "a-hot" : "a-dim") + ")" }, svg);
        if (hot) s("path", { d: d, "class": "sc-pulse" }, svg);
        return path;
      });
    });
    // machines
    L.placed.forEach(function (n) {
      var p = g[n.id], st = state(n);
      var b = h("button", "sc-node st-" + st + (n.type === "MAIN_MOTOR" ? " motor" : "") + (selected === n.id ? " selected" : "") + (p.span ? " tall" : ""), canvas);
      b.type = "button";
      b.setAttribute("aria-pressed", selected === n.id ? "true" : "false");
      b.setAttribute("aria-label", n.name + ": " + statusText(n));
      place(b, p.x, p.y, p.w, p.h);
      icon(n.type, b, n.type === "MAIN_MOTOR" ? 38 : (SLIM[n.type] && !p.span ? 28 : 34));
      var tx = h("span", "sc-txt", b);
      h("b", "sc-name", tx, n.name);
      var line = n.active ? statusText(n) : text.disShort;          // the long "Disabled (planned)" is in the panel
      if (n.live && n.live.fresh && n.status === "R") line += " · " + fmt(n.live.out_hz, 1) + " Hz";
      if (n.live && n.live.fault) line = "⚠ " + n.live.fault;
      h("span", "sc-state", tx, line);
      if (n.live && n.live.fresh) h("span", "sc-val", tx, fmt(n.live.amp, 1) + " A · " + fmt(n.live.rpm, 0) + " rpm");
      else if (n.type === "MAIN_MOTOR" && n.kw) h("span", "sc-val", tx, n.kw + " kW");
      b.addEventListener("click", function () { select(n.id); });
      if (n.shift) {
        var chip = h("span", "sc-shift-chip" + (n.on_shift ? " now" : ""), canvas, (text.shift[n.shift] || n.shift) + (n.on_shift ? " · " + text.now : ""));
        place(chip, p.x + p.w - 116, p.y - 11, 112);
      }
    });
    fit(L);
  }
  // the plant scales down to the width it has (down to 60 %), below that it scrolls sideways
  function fit(L) {
    var k = Math.max(0.6, Math.min(1.25, (wrap.clientWidth - 2) / L.W));
    canvas.style.transform = "scale(" + k + ")";
    wrap.style.height = Math.ceil(L.H * k) + "px";
    canvas.dataset.k = k;
    wrap.classList.toggle("scroll", k * L.W > wrap.clientWidth + 2);
    if (wrap.classList.contains("scroll")) wrap.style.height = Math.ceil(L.H * k) + 16 + "px";
  }

  // ---------- details panel ----------
  function hidden(form, name, value) { var i = h("input", null, form); i.type = "hidden"; i.name = name; i.value = value; }
  function commandForm(n, cmd, label, cls, confirmText, extra) {
    var f = h("form", "scada-cmd");
    f.method = "post"; f.action = root.getAttribute("data-command-url"); f.setAttribute("data-ajax", "");
    hidden(f, "csrf_token", root.getAttribute("data-csrf")); hidden(f, "action", "command"); hidden(f, "back", "scada");
    hidden(f, "MACHINE_ID", n.id); hidden(f, "CMD", cmd);
    if (extra) extra(f);
    var b = h("button", "sc-btn " + cls, f, label);
    b.type = "submit";
    if (confirmText) b.setAttribute("data-confirm", confirmText.replace("{name}", n.name));
    b.disabled = !canCommand || !n.control || !!(n.live && n.live.busy);
    return f;
  }
  function signature(n) {
    var lv = n.live || {};
    return [n.id, n.status, n.active, n.control, canCommand, lv.busy, lv.fresh ? lv.running : "", lv.fault, lv.min_hz, lv.max_hz].join("|");
  }
  function gauge(parent, n) {
    var lv = n.live || {}, frac = lv.fresh ? Math.max(0, Math.min(1, (lv.out_hz || 0) / (lv.max_hz || 50))) : 0, C = 2 * Math.PI * 46;
    var svg = s("svg", { width: 116, height: 116, viewBox: "0 0 112 112", role: "img", "class": "sc-gauge st-" + state(n),
                         "aria-label": text.outHz + " " + fmt(lv.out_hz, 1) + " Hz" }, parent);
    s("circle", { cx: 56, cy: 56, r: 46, "class": "g-bg" }, svg);
    s("circle", { cx: 56, cy: 56, r: 46, "class": "g-val", "stroke-dasharray": (C * frac).toFixed(1) + " " + C.toFixed(1), transform: "rotate(-90 56 56)" }, svg);
    var t1 = s("text", { x: 56, y: 58, "text-anchor": "middle", "class": "g-num", "data-g": "num" }, svg); t1.textContent = lv.fresh ? fmt(lv.out_hz, 1) : "-";
    var t2 = s("text", { x: 56, y: 78, "text-anchor": "middle", "class": "g-unit" }, svg); t2.textContent = "Hz";
  }
  function fillInfo(box, n) {
    box.textContent = "";
    var top = h("div", "sc-info-top", box);
    if (n.live) gauge(top, n);
    var col = h("div", "sc-info-col", top);
    h("span", "sc-pill st-" + state(n), col, statusText(n));
    var lv = n.live;
    if (lv) {
      var r1 = h("span", "sc-kv", col, text.amp + " "); h("b", null, r1, fmt(lv.amp, 1)); r1.appendChild(document.createTextNode(lv.rated_amp ? " / " + lv.rated_amp + " A" : " A"));
      var r2 = h("span", "sc-kv", col, text.rpm + " "); h("b", null, r2, fmt(lv.rpm, 0));
      var r3 = h("span", "sc-kv", col, text.setHz + " "); h("b", null, r3, fmt(lv.set_hz, 1)); r3.appendChild(document.createTextNode(" Hz"));
      if (!lv.fresh) h("span", "sc-note", col, text.noData);
    } else {
      if (n.kw) { var rk = h("span", "sc-kv", col, text.kw + " "); h("b", null, rk, n.kw + " kW"); }
      if (n.feeds.length) h("span", "sc-kv", col, text.feeds + " " + n.feeds.join(", "));
    }
    if (!n.active) h("p", "sc-note", box, text.planned);
    else if (!n.live) h("p", "sc-note", box, text.noDevice);
    if (lv && lv.fault) h("p", "sc-warn", box, "⚠ " + text.fault + ": " + lv.fault);
    if (lv && lv.amp && lv.rated_amp && lv.amp > lv.rated_amp) h("p", "sc-warn", box, "⚠ " + text.overload);
    if (lv && lv.busy) h("p", "sc-note", box, text.busy.replace("{cmd}", lv.busy));
  }
  function details() {
    var n = data.nodes.filter(function (x) { return x.id === selected; })[0];
    if (!n) return;
    panel.textContent = "";
    panelSig = signature(n);
    var head = h("div", "sc-panel-head", panel);
    h("span", "sc-eyebrow", head, text.selected);
    h("span", "sc-panel-name", head, n.name);
    h("span", "sc-panel-sub", head, n.id + " · " + (text.types[n.type] || n.type) + (n.shift ? " · " + text.shift[n.shift] : ""));
    var info = h("div", "sc-info", panel); info.id = "scada-info";
    fillInfo(info, n);
    if (n.active && n.live) {
      var lv = n.live, running = lv.fresh ? lv.running : n.status === "R";
      var pair = h("div", "sc-pair", panel);
      var on = commandForm(n, "START", text.on, "on", text.confirmOn), off = commandForm(n, "STOP", text.off, "off", text.confirmOff);
      if (running || n.status === "F") on.querySelector("button").disabled = true;
      if (!running) off.querySelector("button").disabled = true;
      pair.appendChild(on); pair.appendChild(off);
      var sp = h("div", "sc-speed", panel);
      var lab = h("label", "sc-speed-label", sp, text.speedRange.replace("{low}", lv.min_hz).replace("{high}", lv.max_hz));
      lab.setAttribute("for", "sc-speed-range");
      var row = commandForm(n, "SPEED", text.setTo.replace("{hz}", fmt(lv.set_hz, 1)), "set", null, function (f) {
        var r = h("input", "sc-range", f); r.type = "range"; r.id = "sc-speed-range"; r.name = "CMD_VALUE";
        r.min = lv.min_hz; r.max = lv.max_hz; r.step = "0.5"; r.value = lv.set_hz;
        r.addEventListener("input", function () { f.querySelector("button").textContent = text.setTo.replace("{hz}", Number(r.value).toFixed(1)); });
      });
      sp.appendChild(row);
      if (n.status === "F") panel.appendChild(commandForm(n, "RESET", "↻ " + text.reset, "set", null));
      if (!canCommand || !n.control) h("p", "sc-note", panel, !canCommand ? text.noRight : text.offline);
    }
    var a = h("a", "sc-link-hw", panel, text.openHw + " →");
    a.href = root.getAttribute("data-hardware-url");
  }
  function select(id) { selected = id; draw(); details(); monitors(); }
  function refreshPanel() {
    var n = data.nodes.filter(function (x) { return x.id === selected; })[0];
    if (!n) return;
    var overlay = document.getElementById("app-modal-overlay");
    var busy = (overlay && overlay.classList.contains("show")) || panel.contains(document.activeElement);
    if (!busy && signature(n) !== panelSig) { details(); return; }
    var box = document.getElementById("scada-info");
    if (box) fillInfo(box, n);           // numbers only: the buttons stay (a pop-up may be asking about one)
  }

  // ---------- monitors ----------
  function motorShown() {
    var pick = data.nodes.filter(function (n) { return n.id === selected && n.live; })[0];
    return pick || data.nodes.filter(function (n) { return n.type === "MAIN_MOTOR" && n.active && n.live; })
      .sort(function (a, b) { return (b.on_shift ? 1 : 0) - (a.on_shift ? 1 : 0); })[0];
  }
  function trend(host, samples, field, cls, ceiling, ratedLine) {
    host.textContent = "";
    var W = 448, H = 96, pts = samples.filter(function (p) { return p[field] !== null && p[field] !== undefined; });
    var svg = s("svg", { viewBox: "0 0 " + W + " " + H, preserveAspectRatio: "none", "class": "sc-trend-svg", role: "img",
                         "aria-label": field === "hz" ? text.outHz : text.amp }, host);
    [8, 48, 88].forEach(function (y) { s("path", { d: "M0 " + y + " H" + W, "class": "t-grid" }, svg); });
    if (pts.length < 2) { var nt = s("text", { x: W / 2, y: 54, "text-anchor": "middle", "class": "t-empty" }, svg); nt.textContent = text.noData; return; }
    var top = Math.max(ceiling || 0, Math.max.apply(null, pts.map(function (p) { return p[field]; })) * 1.1) || 1;
    var t0 = Date.parse(pts[0].t), t1 = Date.parse(pts[pts.length - 1].t) || t0 + 1;
    function X(t) { return (Date.parse(t) - t0) / Math.max(1, t1 - t0) * W; }
    function Y(v) { return 88 - v / top * 80; }
    if (ratedLine) s("path", { d: "M0 " + Y(ratedLine) + " H" + W, "class": "t-rated" }, svg);
    s("polyline", { points: pts.map(function (p) { return X(p.t).toFixed(1) + "," + Y(p[field]).toFixed(1); }).join(" "), "class": "t-line " + cls }, svg);
    var last = pts[pts.length - 1][field];
    var lt = s("text", { x: W - 4, y: Math.max(14, Y(last) - 6), "text-anchor": "end", "class": "t-last " + cls }, svg); lt.textContent = fmt(last, 1);
  }
  function monitors() {
    var m = motorShown(), samples = m && m.live ? m.live.samples : [];
    trend(document.getElementById("scada-trend-hz"), samples, "hz", "hz", m && m.live ? m.live.max_hz : 50, null);
    var rated = m && m.live ? m.live.rated_amp : null;
    trend(document.getElementById("scada-trend-amp"), samples, "amp", "amp", rated ? rated * 1.2 : 0, rated);
    document.getElementById("scada-rated").textContent = rated ? text.rated.replace("{a}", rated) : "";
  }

  // ---------- top: figures, shift, clock, alarms ----------
  function figures() {
    var k = data.kpi;
    function set(name, main, sub) {
      var e = root.querySelector('[data-k="' + name + '"]');
      if (!e) return;
      e.textContent = main;
      if (sub) h("small", null, e, " " + sub);
    }
    set("running", k.running + " / " + k.in_use, k.planned ? text.kpiPlanned.replace("{n}", k.planned) : "");
    var m = motorShown();
    if (m && m.live && m.live.fresh) set("motor", fmt(m.live.out_hz, 1) + " Hz", "· " + fmt(m.live.amp, 1) + " A"); else set("motor", text.noData);
    set("queue", String(k.queue), "· " + fmt(k.queue_kg, 0) + " kg / " + k.in_process);
    set("paddy", fmt(k.paddy_today, 0), "kg");
    set("energy", fmt(k.kwh_today, 1), "kWh");
    set("alarms", String(data.alarms.length + k.faults));
    var shift = document.getElementById("scada-shift");
    shift.className = "scada-shift " + (data.shift === "DAY" ? "day" : "night");
    shift.textContent = "";
    var ic = s("svg", { width: 16, height: 16, viewBox: "0 0 24 24", fill: "none", stroke: "currentColor", "stroke-width": 1.8, "stroke-linecap": "round", "aria-hidden": "true" }, shift);
    if (data.shift === "DAY") { s("circle", { cx: 12, cy: 12, r: 4 }, ic); s("path", { d: "M12 2v2M12 20v2M2 12h2M20 12h2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4" }, ic); }
    else s("path", { d: "M20 14.5A8 8 0 0 1 9.5 4a8 8 0 1 0 10.5 10.5z" }, ic);
    shift.appendChild(document.createTextNode(text.shift[data.shift] || data.shift));
    var list = document.getElementById("scada-alarm-list");
    list.textContent = "";
    if (!data.alarms.length) h("li", "sc-ok", list, text.noAlarms);
    data.alarms.forEach(function (a) {
      var li = h("li", "sc-alarm", list);
      h("i", null, li);
      h("b", null, li, new Date(a.time).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }));
      li.appendChild(document.createTextNode(" " + a.id + (a.note ? " · " + a.note : "")));
    });
    data.nodes.forEach(function (n) {
      var cell = document.querySelector('[data-row="' + n.id + '"] [data-cell="status"]');
      if (cell) cell.textContent = statusText(n);
    });
  }
  function clock() {
    var stale = (Date.now() - lastOk) / 1000 > 10;
    document.getElementById("scada-clock").textContent = new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
    var dot = document.getElementById("scada-link-dot");
    dot.className = "scada-dot" + (stale ? " lost" : "");
    dot.title = stale ? text.lost : "";
  }
  // update 40: commands go by fetch, so the page (and full screen) stays; a short message shows the answer
  var toast = null, toastTimer = null;
  function say(msg, bad) {
    if (!toast) { toast = h("div", "scada-toast", root); toast.setAttribute("role", "status"); }
    toast.textContent = msg; toast.className = "scada-toast show" + (bad ? " bad" : "");
    clearTimeout(toastTimer); toastTimer = setTimeout(function () { toast.className = "scada-toast"; }, 4000);
  }
  document.addEventListener("submit", function (e) {
    var f = e.target;
    if (!f.classList || !f.classList.contains("scada-cmd") || !window.fetch) return;
    e.preventDefault();
    var body = new FormData(f);
    if (e.submitter && e.submitter.name) body.append(e.submitter.name, e.submitter.value);
    f.querySelectorAll("button").forEach(function (b) { b.disabled = true; });
    // getAttribute: the form has an input named "action", so f.action is that input, not the address
    fetch(f.getAttribute("action"), { method: "POST", body: body, credentials: "same-origin", headers: { "X-AOI-Fetch": "1" } })
      .then(function (r) { return r.json().catch(function () { return { ok: false, message: text.lost }; }); })
      .catch(function () { return { ok: false, message: text.lost }; })
      .then(function (res) {
        say(res.message || "", !res.ok);
        if (f.parentElement === document.body) f.remove();      // the copy kept alive during the question
        panelSig = null;                                       // draw the buttons again with the new state
        refreshNow();
      });
  });
  function refreshNow() {
    fetch(root.getAttribute("data-url"), { credentials: "same-origin" }).then(function (r) { return r.json(); }).then(function (d) {
      if (!d.ok) return;
      data = d; canCommand = !!d.can_command; lastOk = Date.now();
      draw(); figures(); monitors();
      if (selected) refreshPanel();
    }).catch(function () {});
  }

  function refresh() {
    fetch(root.getAttribute("data-url"), { credentials: "same-origin" }).then(function (r) { return r.json(); }).then(function (d) {
      if (!d.ok) return;
      data = d; canCommand = !!d.can_command; lastOk = Date.now();
      draw(); figures(); monitors();
      if (selected) refreshPanel();
    }).catch(function () {}).then(function () { setTimeout(refresh, 2000); });
  }

  document.getElementById("scada-full").addEventListener("click", function () {
    if (document.fullscreenElement) document.exitFullscreen();
    else if (root.requestFullscreen) root.requestFullscreen();
  });
  var resizeTimer = null;
  window.addEventListener("resize", function () { clearTimeout(resizeTimer); resizeTimer = setTimeout(draw, 150); });
  document.addEventListener("fullscreenchange", function () { setTimeout(draw, 100); });
  draw(); figures(); clock(); monitors();
  setInterval(clock, 1000);
  var first = data.nodes.filter(function (n) { return n.type === "MAIN_MOTOR" && n.active; })[0];
  if (first) select(first.id);
  setTimeout(refresh, 2000);
})();
