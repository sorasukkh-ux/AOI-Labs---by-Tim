/* Hardware Control (update 30): live values of the main motor from its inverter, every 2 seconds.
   Output Hz, current, rpm, speed set, fault; two small charts of the last 10 minutes (Hz and A, one axis each);
   the ON / OFF / speed / reset buttons follow the live state, so the page does not need to reload. */
(function () {
  "use strict";
  var list = document.getElementById("machine-list");
  if (!list || !list.querySelector("[data-live]")) return;
  var url = list.getAttribute("data-live-url"), text = JSON.parse(list.getAttribute("data-text"));
  var NS = "http://www.w3.org/2000/svg";

  // the slider and the number box show the same speed
  var touched = {};                                   // a speed the operator is typing is not overwritten
  document.querySelectorAll(".mc-speed-range").forEach(function (range) {
    var box = document.getElementById(range.getAttribute("data-pair"));
    range.addEventListener("input", function () { box.value = range.value; touched[box.id] = true; });
    box.addEventListener("input", function () { range.value = box.value; touched[box.id] = true; });
  });

  function fmt(v, digits) { return v === null || v === undefined ? "-" : Number(v).toFixed(digits); }
  function spark(host, samples, field, cls) {
    var W = Math.max(160, host.clientWidth || 240), H = 56, pad = 4;
    var pts = samples.filter(function (s) { return s[field] !== null && s[field] !== undefined; });
    host.textContent = "";
    if (pts.length < 2) { host.textContent = "-"; return; }
    var t0 = Date.parse(pts[0].t), t1 = Date.parse(pts[pts.length - 1].t) || t0 + 1;
    var hi = Math.max.apply(null, pts.map(function (s) { return s[field]; })) || 1;
    var svg = document.createElementNS(NS, "svg");
    svg.setAttribute("viewBox", "0 0 " + W + " " + H); svg.setAttribute("aria-hidden", "true");
    var line = document.createElementNS(NS, "polyline");
    line.setAttribute("points", pts.map(function (s) {
      var x = pad + (Date.parse(s.t) - t0) / Math.max(1, t1 - t0) * (W - 2 * pad);
      var y = H - pad - (s[field] / hi) * (H - 2 * pad);
      return x.toFixed(1) + "," + y.toFixed(1);
    }).join(" "));
    line.setAttribute("class", "mc-spark-line " + cls);
    svg.appendChild(line);
    var label = document.createElementNS(NS, "text");
    label.setAttribute("x", W - pad); label.setAttribute("y", 12); label.setAttribute("text-anchor", "end");
    label.setAttribute("class", "mc-spark-max"); label.textContent = "max " + hi.toFixed(1);
    svg.appendChild(label);
    host.appendChild(svg);
  }
  function set(card, name, value) { var el = card.querySelector('[data-f="' + name + '"]'); if (el) el.textContent = value; return el; }
  function enable(id, on) { var el = document.getElementById(id); if (el) el.disabled = !on; }

  function apply(m, canCommand) {
    var card = list.querySelector('[data-machine="' + m.id + '"]');
    if (!card) return;
    set(card, "out_hz", fmt(m.out_hz, 1)); set(card, "amp", fmt(m.amp, 1)); set(card, "rpm", fmt(m.rpm, 0));
    set(card, "set_hz", fmt(m.set_hz, 1));
    var fault = card.querySelector('[data-f="fault"]');
    if (fault) { fault.hidden = !m.fault; fault.querySelector("b").textContent = m.fault || ""; }
    var over = card.querySelector('[data-f="overload"]');
    if (over) over.hidden = !(m.amp !== null && m.rated_amp && m.amp > m.rated_amp);
    var age = m.updated ? Math.round((Date.now() - Date.parse(m.updated)) / 1000) : null;
    set(card, "updated", !m.fresh ? text.noData : text.updated.replace("{s}", Math.max(0, age)) + (m.busy ? " · " + text.busy.replace("{cmd}", m.busy) : ""));
    var badge = card.querySelector(".mc-status");
    if (badge && text.status[m.status]) {
      badge.className = "badge " + text.badge[m.status] + " mc-status";
      badge.textContent = (card.classList.contains("main-motor") ? (m.status === "R" ? "ON · " : "OFF · ") : "") + text.status[m.status];
    }
    var ok = canCommand && m.online && !m.busy;
    enable("cmd-on-" + m.id, ok && !m.running && m.status !== "F");
    enable("cmd-off-" + m.id, ok && m.running);
    enable("cmd-speed-" + m.id, ok);
    enable("cmd-reset-" + m.id, ok);
    var reset = card.querySelector('[data-f="reset-form"]');
    if (reset) reset.hidden = m.status !== "F";
    var box = document.getElementById("hz-" + m.id);
    if (box && !touched[box.id] && document.activeElement !== box) {
      box.value = m.set_hz;
      var range = card.querySelector(".mc-speed-range");
      if (range) range.value = m.set_hz;
    }
    spark(card.querySelector('[data-spark="hz"]'), m.samples, "hz", "hz");
    spark(card.querySelector('[data-spark="amp"]'), m.samples, "amp", "amp");
  }
  function poll() {
    fetch(url, { credentials: "same-origin" }).then(function (r) { return r.json(); }).then(function (data) {
      (data.machines || []).forEach(function (m) { apply(m, data.can_command); });
    }).catch(function () { /* next poll */ }).then(function () { setTimeout(poll, 2000); });
  }
  poll();
})();
