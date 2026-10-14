/* SCADA (update 31): the plant diagram with live machine states, refreshed every 2 seconds.

   Drawn from /automation/scada.json: every machine has a place (SCADA_COL, SCADA_ROW) and the machines it feeds
   (FEEDS_TO). Design follows "high-performance HMI" ideas: grey equipment, colour only for state (green running,
   red fault), flow arrows move only where material moves, planned (disabled) machines drawn dashed and dimmed.
   Click / tap a machine for its details and, when it can be controlled, ON / OFF / speed / reset. */
(function () {
  "use strict";
  var root = document.getElementById("scada");
  if (!root) return;
  var NS = "http://www.w3.org/2000/svg";
  var text = JSON.parse(root.getAttribute("data-text"));
  var canCommand = !!root.getAttribute("data-can-command");
  var canvas = document.getElementById("scada-canvas"), panel = document.getElementById("scada-panel");
  var data = JSON.parse(root.getAttribute("data-initial"));
  var selected = null, lastOk = Date.now();

  var COLW = 236, ROWH = 150, NODEW = 184, NODEH = 108, PADX = 26, PADY = 56;

  function el(name, attrs, parent) {
    var n = document.createElementNS(NS, name);
    for (var k in attrs) n.setAttribute(k, attrs[k]);
    if (parent) parent.appendChild(n);
    return n;
  }
  function txt(parent, x, y, value, cls, anchor) {
    var t = el("text", { x: x, y: y, "class": cls || "", "text-anchor": anchor || "start" }, parent);
    t.textContent = value;
    return t;
  }
  function fmt(v, d) { return v === null || v === undefined ? "-" : Number(v).toFixed(d); }
  function state(n) {
    if (!n.active) return "dis";
    if (n.status === "F" || (n.live && n.live.fault)) return "fault";
    if (n.status === "R") return "run";
    if (n.status === "M") return "maint";
    if (n.status === "O" || (n.live && !n.live.fresh)) return "off";
    return "idle";
  }

  // ---------- equipment icons (40 x 40, drawn at x, y) ----------
  var ICONS = {
    SILO: function (g) { el("path", { d: "M8 6h24v20l-8 10h-8l-8-10z", "class": "ic" }, g); el("path", { d: "M8 14h24", "class": "ic-l" }, g); },
    CONVEYOR: function (g) {
      el("rect", { x: 2, y: 16, width: 36, height: 10, rx: 5, "class": "ic" }, g);
      el("circle", { cx: 8, cy: 21, r: 3, "class": "ic-l roll" }, g); el("circle", { cx: 32, cy: 21, r: 3, "class": "ic-l roll" }, g);
      el("path", { d: "M10 12h6M20 12h6", "class": "ic-l" }, g);
    },
    INBOUND: function (g) { el("path", { d: "M4 6h32l-10 16v12h-12v-12z", "class": "ic" }, g); el("path", { d: "M12 14h16", "class": "ic-l" }, g); },
    SORTER: function (g) {
      el("rect", { x: 4, y: 8, width: 32, height: 16, rx: 3, "class": "ic" }, g);
      el("path", { d: "M10 24l-4 10M20 24v10M30 24l4 10", "class": "ic-l" }, g);
    },
    MAIN_MOTOR: function (g) {
      el("rect", { x: 4, y: 10, width: 26, height: 22, rx: 4, "class": "ic" }, g);
      el("rect", { x: 30, y: 18, width: 8, height: 6, rx: 1, "class": "ic" }, g);
      var fan = el("g", { "class": "fan" }, el("g", { transform: "translate(17 21)" }, g));
      el("path", { d: "M0 -7 L2 0 L0 7 L-2 0Z M-7 0 L0 -2 L7 0 L0 2Z", "class": "ic-l fill" }, fan);
      el("path", { d: "M8 34h18", "class": "ic-l" }, g);
    },
    PACKER: function (g) { el("rect", { x: 6, y: 12, width: 28, height: 22, rx: 2, "class": "ic" }, g); el("path", { d: "M6 18h28M20 12v6", "class": "ic-l" }, g); },
    AMR: function (g) {
      el("rect", { x: 4, y: 12, width: 32, height: 16, rx: 6, "class": "ic" }, g);
      el("circle", { cx: 11, cy: 31, r: 4, "class": "ic" }, g); el("circle", { cx: 29, cy: 31, r: 4, "class": "ic" }, g);
      el("path", { d: "M14 8h12v4h-12z", "class": "ic-l" }, g);
    },
    OTHER: function (g) { el("rect", { x: 6, y: 8, width: 28, height: 26, rx: 4, "class": "ic" }, g); el("path", { d: "M12 21h16", "class": "ic-l" }, g); }
  };

  function wrap(name, max) {
    if (name.length <= max) return [name];
    var cut = name.lastIndexOf(" ", max);
    if (cut < 8) cut = max;
    var rest = name.slice(cut).trim();
    return [name.slice(0, cut).trim(), rest.length > max ? rest.slice(0, max - 1) + "…" : rest];
  }
  function pos(n) { return { x: PADX + n.col * COLW, y: PADY + n.row * ROWH }; }

  // ---------- draw ----------
  function draw() {
    var nodes = data.nodes.filter(function (n) { return n.col !== null && n.row !== null; });
    var byId = {};
    nodes.forEach(function (n) { byId[n.id] = n; });
    var cols = Math.max(5, 1 + Math.max.apply(null, nodes.map(function (n) { return n.col; }).concat([0])));
    var rows = Math.max(3, 1 + Math.max.apply(null, nodes.map(function (n) { return n.row; }).concat([0])));
    var W = PADX * 2 + (cols - 1) * COLW + NODEW, H = PADY + (rows - 1) * ROWH + NODEH + 24;
    canvas.textContent = "";
    var svg = el("svg", { viewBox: "0 0 " + W + " " + H, "class": "scada-svg" }, canvas);
    var defs = el("defs", {}, svg);
    var m = el("marker", { id: "sc-arrow", viewBox: "0 0 10 10", refX: 9, refY: 5, markerWidth: 7, markerHeight: 7, orient: "auto-start-reverse" }, defs);
    el("path", { d: "M0 0L10 5L0 10z", "class": "sc-arrowhead" }, m);
    var hatch = el("pattern", { id: "sc-hatch", width: 8, height: 8, patternUnits: "userSpaceOnUse", patternTransform: "rotate(45)" }, defs);
    el("line", { x1: 0, y1: 0, x2: 0, y2: 8, "class": "sc-hatch-line" }, hatch);

    // stage headers and the milling group frame
    for (var c = 0; c < cols; c++) {
      txt(svg, PADX + c * COLW + NODEW / 2, 30, text.stages[c] || "", "sc-stage", "middle");
    }
    var mill = nodes.filter(function (n) { return n.type === "MAIN_MOTOR"; });
    if (mill.length > 1) {
      var top = Math.min.apply(null, mill.map(function (n) { return pos(n).y; })), bottom = Math.max.apply(null, mill.map(function (n) { return pos(n).y; }));
      var mx = Math.min.apply(null, mill.map(function (n) { return pos(n).x; }));
      el("rect", { x: mx - 10, y: top - 12, width: NODEW + 20, height: bottom - top + NODEH + 24, rx: 16, "class": "sc-group" }, svg);
    }

    // links first (under the machines)
    nodes.forEach(function (n) {
      n.feeds.forEach(function (to) {
        var t = byId[to];
        if (!t) return;
        var a = pos(n), b = pos(t), d;
        if (t.col === n.col) {                                     // same column: vertical (e.g. sorting -> inbound)
          var x = a.x + NODEW / 2, up = b.y < a.y;
          d = "M" + x + " " + (up ? a.y : a.y + NODEH) + " V" + (up ? b.y + NODEH + 6 : b.y - 6);
        } else {
          var x1 = a.x + NODEW, y1 = a.y + NODEH / 2, x2 = b.x - 6, y2 = b.y + NODEH / 2, xm = (x1 + x2) / 2;
          d = y1 === y2 ? "M" + x1 + " " + y1 + " H" + x2 : "M" + x1 + " " + y1 + " H" + xm + " V" + y2 + " H" + x2;
        }
        var live = state(n) === "run" && t.active;
        el("path", { d: d, "class": "sc-link" + (live ? " flow" : "") + (n.active && t.active ? "" : " planned"), "marker-end": "url(#sc-arrow)" }, svg);
      });
    });

    // machines
    nodes.forEach(function (n) {
      var p = pos(n), st = state(n);
      var g = el("g", { "class": "sc-node st-" + st + (selected === n.id ? " selected" : "") + (n.on_shift ? " on-shift" : ""),
                         transform: "translate(" + p.x + " " + p.y + ")", tabindex: 0, role: "button",
                         "aria-label": n.name + ": " + (text.status[n.active ? n.status : "DIS"] || "") }, svg);
      el("rect", { x: 0, y: 0, width: NODEW, height: NODEH, rx: 12, "class": "sc-box" }, g);
      if (!n.active) el("rect", { x: 0, y: 0, width: NODEW, height: NODEH, rx: 12, fill: "url(#sc-hatch)", "class": "sc-hatch" }, g);
      el("rect", { x: 0, y: 0, width: 6, height: NODEH, rx: 3, "class": "sc-state-bar" }, g);
      var icon = el("g", { transform: "translate(14 12)", "class": "sc-icon" }, g);
      (ICONS[n.type] || ICONS.OTHER)(icon);
      var lines = wrap(n.name, 17);
      txt(g, 62, 26, lines[0], "sc-name");
      if (lines[1]) txt(g, 62, 42, lines[1], "sc-name");
      txt(g, 62, lines[1] ? 58 : 44, n.id, "sc-id");
      var label = n.active ? (text.status[n.status] || "") : text.status.DIS;
      if (n.live && n.live.fresh && n.status === "R") label += " · " + fmt(n.live.out_hz, 1) + " Hz";
      if (n.live && n.live.fault) label = "⚠ " + n.live.fault;
      txt(g, 14, NODEH - 30, label, "sc-state");
      if (n.live && n.live.fresh) {
        txt(g, 14, NODEH - 12, fmt(n.live.amp, 1) + " A · " + fmt(n.live.rpm, 0) + " rpm", "sc-val");
        var frac = Math.max(0, Math.min(1, (n.live.out_hz || 0) / (n.live.max_hz || 50)));
        el("rect", { x: NODEW - 18, y: 14, width: 6, height: NODEH - 28, rx: 3, "class": "sc-gauge-bg" }, g);
        el("rect", { x: NODEW - 18, y: 14 + (NODEH - 28) * (1 - frac), width: 6, height: (NODEH - 28) * frac, rx: 3, "class": "sc-gauge" }, g);
      } else if (n.active && n.type === "MAIN_MOTOR" && !n.live) {
        txt(g, 14, NODEH - 12, text.noDevice, "sc-val");
      }
      if (n.shift) {
        var chip = el("g", { transform: "translate(" + (NODEW - 76) + " " + (-11) + ")" }, g);
        el("rect", { x: 0, y: 0, width: 72, height: 20, rx: 10, "class": "sc-shift" + (n.on_shift ? " now" : "") }, chip);
        txt(chip, 36, 14, text.shift[n.shift] || n.shift, "sc-shift-t", "middle");
      }
      g.addEventListener("click", function () { select(n.id); });
      g.addEventListener("keydown", function (e) { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); select(n.id); } });
    });
  }

  // ---------- details panel ----------
  function hidden(form, name, value) { var i = document.createElement("input"); i.type = "hidden"; i.name = name; i.value = value; form.appendChild(i); }
  function commandForm(n, cmd, label, cls, confirmText, extra) {
    var f = document.createElement("form");
    f.method = "post"; f.action = root.getAttribute("data-command-url"); f.className = "scada-cmd";
    hidden(f, "csrf_token", root.getAttribute("data-csrf")); hidden(f, "action", "command"); hidden(f, "back", "scada");
    hidden(f, "MACHINE_ID", n.id); hidden(f, "CMD", cmd);
    if (extra) extra(f);
    var b = document.createElement("button");
    b.type = "submit"; b.className = "action " + cls; b.textContent = label;
    if (confirmText) b.setAttribute("data-confirm", confirmText.replace("{name}", n.name));
    var busy = n.live && n.live.busy;
    b.disabled = !canCommand || !n.control || !!busy;
    f.appendChild(b);
    return f;
  }
  function row(dl, k, v) {
    var dt = document.createElement("dt"); dt.textContent = k; var dd = document.createElement("dd"); dd.textContent = v;
    dl.appendChild(dt); dl.appendChild(dd);
  }
  function select(id) { selected = id; draw(); details(); }
  function details() {
    var n = data.nodes.filter(function (x) { return x.id === selected; })[0];
    if (!n) return;
    panel.textContent = "";
    var h = document.createElement("p"); h.className = "scada-h"; h.textContent = n.name; panel.appendChild(h);
    var sub = document.createElement("p"); sub.className = "scada-sub";
    sub.textContent = n.id + " · " + (text.types[n.type] || n.type) + (n.shift ? " · " + text.shift[n.shift] + (n.on_shift ? " (" + text.onShift + ")" : "") : "");
    panel.appendChild(sub);
    var pill = document.createElement("span"); pill.className = "scada-pill st-" + state(n);
    pill.textContent = n.active ? (text.status[n.status] || n.status) : text.status.DIS; panel.appendChild(pill);
    var dl = document.createElement("dl"); dl.className = "scada-dl"; panel.appendChild(dl);
    if (n.kw) row(dl, text.kw, n.kw + " kW");
    if (n.feeds.length) row(dl, text.feeds, n.feeds.join(", "));
    if (!n.active) {
      var note = document.createElement("p"); note.className = "scada-note"; note.textContent = text.planned; panel.appendChild(note);
    } else if (n.live) {
      var lv = n.live;
      if (!lv.fresh) row(dl, "", text.noData);
      row(dl, text.outHz, fmt(lv.out_hz, 1) + " Hz"); row(dl, text.amp, fmt(lv.amp, 1) + " A" + (lv.rated_amp ? " / " + lv.rated_amp + " A" : ""));
      row(dl, text.rpm, fmt(lv.rpm, 0)); row(dl, text.setHz, fmt(lv.set_hz, 1) + " Hz");
      if (lv.fault) row(dl, text.fault, lv.fault);
      if (lv.amp && lv.rated_amp && lv.amp > lv.rated_amp) { var w = document.createElement("p"); w.className = "scada-warn"; w.textContent = "⚠ " + text.overload; panel.appendChild(w); }
      if (lv.busy) { var b = document.createElement("p"); b.className = "scada-note"; b.textContent = text.busy.replace("{cmd}", lv.busy); panel.appendChild(b); }
      var pair = document.createElement("div"); pair.className = "scada-pair";
      var running = lv.fresh ? lv.running : n.status === "R";
      var on = commandForm(n, "START", "▶ " + text.on, "primary", text.confirmOn), off = commandForm(n, "STOP", "■ " + text.off, "danger", text.confirmOff);
      if (running || n.status === "F") on.querySelector("button").disabled = true;
      if (!running) off.querySelector("button").disabled = true;
      pair.appendChild(on); pair.appendChild(off); panel.appendChild(pair);
      panel.appendChild(commandForm(n, "SPEED", text.speed, "", null, function (f) {
        var i = document.createElement("input"); i.type = "number"; i.name = "CMD_VALUE"; i.min = lv.min_hz; i.max = lv.max_hz; i.step = "0.5";
        i.value = lv.set_hz; i.required = true; i.setAttribute("aria-label", "Hz"); f.appendChild(i);
      }));
      if (n.status === "F") panel.appendChild(commandForm(n, "RESET", "↻ " + text.reset, "", null));
    } else {
      var nd = document.createElement("p"); nd.className = "scada-note"; nd.textContent = text.noDevice; panel.appendChild(nd);
    }
    var a = document.createElement("a"); a.href = root.getAttribute("data-hardware-url"); a.className = "scada-link"; a.textContent = text.openHw + " →";
    panel.appendChild(a);
  }

  // ---------- figures, alarms, list ----------
  function figures() {
    var k = data.kpi;
    function set(name, value) { var e = root.querySelector('[data-k="' + name + '"]'); if (e) e.textContent = value; }
    set("running", k.running + " / " + k.in_use + (k.planned ? "  (+" + k.planned + ")" : ""));
    var motor = data.nodes.filter(function (n) { return n.type === "MAIN_MOTOR" && n.active && n.live; })
      .sort(function (a, b) { return (b.on_shift ? 1 : 0) - (a.on_shift ? 1 : 0); })[0];
    set("motor", motor && motor.live.fresh ? fmt(motor.live.out_hz, 1) + " Hz · " + fmt(motor.live.amp, 1) + " A" : text.noData);
    set("queue", k.queue + " · " + Number(k.queue_kg).toLocaleString() + " kg / " + k.in_process);
    set("paddy", Number(k.paddy_today).toLocaleString() + " kg");
    set("energy", fmt(k.kwh_today, 1) + " kWh");
    set("alarms", String(data.alarms.length + k.faults));
    document.getElementById("scada-shift").textContent = text.shift[data.shift] || data.shift;
    document.getElementById("scada-shift").className = "scada-shift " + (data.shift === "DAY" ? "day" : "night");
    var list = document.getElementById("scada-alarm-list");
    list.textContent = "";
    if (!data.alarms.length) { var li = document.createElement("li"); li.className = "scada-sub"; li.textContent = text.noAlarms; list.appendChild(li); }
    data.alarms.forEach(function (a) {
      var li = document.createElement("li"), t = document.createElement("b");
      t.textContent = new Date(a.time).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }) + "  " + a.id;
      li.appendChild(t); li.appendChild(document.createTextNode("  " + (a.note || ""))); list.appendChild(li);
    });
    data.nodes.forEach(function (n) {
      var cell = document.querySelector('[data-row="' + n.id + '"] [data-cell="status"]');
      if (cell) cell.textContent = n.active ? (text.status[n.status] || n.status) : text.status.DIS;
    });
  }
  function clock() {
    var c = document.getElementById("scada-clock");
    var stale = (Date.now() - lastOk) / 1000 > 10;
    c.textContent = " " + new Date().toLocaleTimeString() + (stale ? " · " + text.lost : "");
    document.getElementById("scada-link-dot").className = "scada-dot" + (stale ? " lost" : "");
  }
  function refresh() {
    fetch(root.getAttribute("data-url"), { credentials: "same-origin" }).then(function (r) { return r.json(); }).then(function (d) {
      if (!d.ok) return;
      data = d; canCommand = !!d.can_command; lastOk = Date.now();
      draw(); figures();
      var focused = panel.contains(document.activeElement);
      if (selected && !focused) details();
    }).catch(function () {}).then(function () { setTimeout(refresh, 2000); });
  }

  document.getElementById("scada-full").addEventListener("click", function () {
    if (document.fullscreenElement) document.exitFullscreen();
    else if (root.requestFullscreen) root.requestFullscreen();
  });
  draw(); figures(); clock();
  setInterval(clock, 1000);
  var motor = data.nodes.filter(function (n) { return n.type === "MAIN_MOTOR" && n.active; })[0];
  if (motor) select(motor.id);
  setTimeout(refresh, 2000);
})();
