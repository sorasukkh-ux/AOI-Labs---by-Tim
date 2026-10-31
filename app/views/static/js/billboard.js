/* Price billboard (/tv/<branch>/prices), update 83: the TV theme on a 1920x1080 stage. One SKU at a time in the
   spotlight (every 7 s, slide + rolling digits + 30-day trend drawn in), the board on the right follows it, a ticker
   runs along the bottom, and a price that changed since the last refresh flashes and gets the spotlight. Plain ES5. */
(function () {
  "use strict";
  var root = document.getElementById("tvb");
  if (!root) return;
  var text = JSON.parse(root.getAttribute("data-text"));
  var data = JSON.parse(root.getAttribute("data-board"));
  var url = root.getAttribute("data-url"), show = root.getAttribute("data-show") || "both";
  var SPOT_MS = (data.seconds || 7) * 1000, idx = -1, last = {}, spotTimer = null;
  var NS = "http://www.w3.org/2000/svg";

  function th(k) { return (text[k] && text[k].th) || ""; }
  function en(k) { return (text[k] && text[k].en) || ""; }
  function el(tag, cls, content) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (content !== undefined && content !== null) n.textContent = content;
    return n;
  }
  function svg(tag, attrs) { var n = document.createElementNS(NS, tag); for (var k in attrs) n.setAttribute(k, attrs[k]); return n; }
  function money(v) { return Number(v || 0).toFixed(2); }
  function sides() { return show === "both" ? ["b", "s"] : (show === "buy" ? ["b"] : ["s"]); }
  function priceOf(item, side) { return side === "b" ? item.buy : item.sell; }
  function items() {                       // only the SKUs that have a price on the sides shown
    return (data.items || []).filter(function (i) { return sides().some(function (s) { return priceOf(i, s) > 0; }); });
  }

  function fit() {
    var s = Math.min(window.innerWidth / 1920, window.innerHeight / 1080);
    root.style.transform = "scale(" + s + ")";
    root.style.left = ((window.innerWidth - 1920 * s) / 2) + "px";
    root.style.top = ((window.innerHeight - 1080 * s) / 2) + "px";
  }
  var skew = 0;
  function syncClock() { if (data.now_ms) skew = data.now_ms - Date.now(); }
  function clock() {
    var d = new Date(Date.now() + skew + (data.tz_min || 0) * 60000), pad = function (n) { return (n < 10 ? "0" : "") + n; };
    document.getElementById("tvb-time").textContent = pad(d.getUTCHours()) + ":" + pad(d.getUTCMinutes());
    document.getElementById("tvb-date").textContent = text.days[(d.getUTCDay() + 6) % 7] + " " + d.getUTCDate() + " " +
      text.months[d.getUTCMonth()] + " " + (d.getUTCFullYear() + 543);
  }

  // ----- pictures (simple drawings: paddy ear, rice bowl, broken grains, bran) -----
  var ART = {
    paddy: '<svg viewBox="0 0 100 100" fill="none" stroke="#fbbf24" stroke-width="3" stroke-linecap="round"><path d="M50 92V30"/>' +
      '<g fill="#fbbf24" stroke="none"><ellipse cx="50" cy="22" rx="6" ry="11"/><ellipse cx="38" cy="36" rx="5.5" ry="10" transform="rotate(-30 38 36)"/>' +
      '<ellipse cx="62" cy="36" rx="5.5" ry="10" transform="rotate(30 62 36)"/><ellipse cx="37" cy="54" rx="5.5" ry="10" transform="rotate(-35 37 54)"/>' +
      '<ellipse cx="63" cy="54" rx="5.5" ry="10" transform="rotate(35 63 54)"/><ellipse cx="38" cy="72" rx="5" ry="9" transform="rotate(-40 38 72)"/>' +
      '<ellipse cx="62" cy="72" rx="5" ry="9" transform="rotate(40 62 72)"/></g></svg>',
    rice: '<svg viewBox="0 0 100 100"><g fill="#f8fafc"><ellipse cx="34" cy="44" rx="5" ry="9" transform="rotate(-25 34 44)"/><ellipse cx="48" cy="38" rx="5" ry="9"/>' +
      '<ellipse cx="62" cy="43" rx="5" ry="9" transform="rotate(25 62 43)"/><ellipse cx="42" cy="50" rx="5" ry="9" transform="rotate(60 42 50)"/>' +
      '<ellipse cx="57" cy="51" rx="5" ry="9" transform="rotate(-60 57 51)"/><ellipse cx="70" cy="52" rx="5" ry="9" transform="rotate(80 70 52)"/>' +
      '<ellipse cx="28" cy="53" rx="5" ry="9" transform="rotate(-80 28 53)"/></g><path d="M14 56h72c0 18-16 30-36 30S14 74 14 56z" fill="#38bdf8"/>' +
      '<path d="M30 88h40" stroke="#7dd3fc" stroke-width="4" stroke-linecap="round"/></svg>',
    broken: '<svg viewBox="0 0 100 100" fill="#e2e8f0"><path d="M26 40l12-4 4 9-12 4z"/><path d="M52 30l10 2-2 10-10-2z"/><path d="M66 50l11 5-4 9-11-5z"/>' +
      '<path d="M34 62l10-2 3 10-10 2z"/><path d="M52 66l8 4-4 8-8-4z"/><path d="M44 46l9 1-1 8-9-1z"/><path d="M20 70l8 2-2 8-8-2z"/><path d="M70 30l7 3-3 7-7-3z"/></svg>',
    bran: '<svg viewBox="0 0 100 100"><path d="M22 34c0-6 12-10 28-10s28 4 28 10l6 46c0 6-15 10-34 10S16 86 16 80z" fill="#b45309"/>' +
      '<path d="M24 34c4 4 14 6 26 6s22-2 26-6" fill="none" stroke="#fcd34d" stroke-width="3"/>' +
      '<g fill="#fcd34d"><circle cx="40" cy="58" r="2.5"/><circle cx="52" cy="66" r="2.5"/><circle cx="60" cy="54" r="2.5"/><circle cx="46" cy="74" r="2.5"/><circle cx="34" cy="70" r="2.5"/><circle cx="64" cy="72" r="2.5"/></g></svg>'
  };

  // ----- rolling digits -----
  function digits(host, value, from) {
    var s = money(value), f = from === undefined ? null : money(from);
    host.innerHTML = "";
    var strips = [];
    for (var i = 0; i < s.length; i++) {
      var ch = s.charAt(i);
      if (ch === ".") { host.appendChild(el("span", "pt", ".")); continue; }
      var dg = el("span", "dg"), strip = el("span", "strip");
      for (var n = 0; n <= 9; n++) strip.appendChild(el("span", "", String(n)));
      dg.appendChild(strip); host.appendChild(dg);
      var start = f && f.length === s.length && f.charAt(i) !== "." ? Number(f.charAt(i)) : (Number(ch) + 5 + i) % 10;
      strip.style.transform = "translateY(" + (-start) + "em)";
      strips.push([strip, Number(ch)]);
    }
    setTimeout(function () { strips.forEach(function (x) { x[0].style.transform = "translateY(" + (-x[1]) + "em)"; }); }, 60);
  }

  function changeText(side) {
    if (!side.prev || !side.change) return { cls: "", txt: th("bbSame") };
    var up = side.change > 0;
    return { cls: up ? "up" : "down", arrow: up ? "▲" : "▼",
             txt: (up ? th("bbUp") : th("bbDown")) + " " + money(Math.abs(side.change)) + " (" + (up ? "+" : "") + side.pct + "%)",
             since: th("bbSince").replace("{v}", money(side.prev)).replace(" ({d})", side.since ? " (" + side.since + ")" : "").replace("{d}", side.since || "") };
  }

  function spark(host, item) {
    var main = sides().filter(function (sd) { return priceOf(item, sd) > 0; })[0] || sides()[0], series = item[main].series || [];
    var cap = el("div", "cap"); cap.appendChild(el("span", "", th("bbDays") + " · " + en("bbDays")));
    cap.appendChild(el("span", "", (main === "b" ? th("bbBuy") : th("bbSell")) + " " + th("bbUnit")));
    host.appendChild(cap);
    if (series.length < 2) return;
    var W = 1000, H = 170, lo = Math.min.apply(null, series), hi = Math.max.apply(null, series);
    if (hi - lo < 0.5) { lo -= 0.5; hi += 0.5; }
    var color = main === "b" ? "#6ee7b7" : "#38bdf8", pts = [];
    for (var i = 0; i < series.length; i++) pts.push([i / (series.length - 1) * W, H - 14 - (series[i] - lo) / (hi - lo) * (H - 34)]);
    var d = pts.map(function (p, k) { return (k ? "L" : "M") + p[0].toFixed(1) + " " + p[1].toFixed(1); }).join(" ");
    var s = svg("svg", { viewBox: "0 0 " + W + " " + H, preserveAspectRatio: "none" });
    var grad = svg("linearGradient", { id: "bbg", x1: "0", y1: "0", x2: "0", y2: "1" });
    grad.appendChild(svg("stop", { offset: "0", "stop-color": color })); grad.appendChild(svg("stop", { offset: "1", "stop-color": color, "stop-opacity": "0" }));
    var defs = svg("defs", {}); defs.appendChild(grad); s.appendChild(defs);
    s.appendChild(svg("path", { "class": "area", d: d + " L" + W + " " + H + " L0 " + H + " Z", fill: "url(#bbg)" }));
    s.appendChild(svg("path", { "class": "ln", d: d, stroke: color }));
    var lp = pts[pts.length - 1];
    s.appendChild(svg("circle", { "class": "dot", cx: lp[0], cy: lp[1], r: 9, fill: color }));
    host.appendChild(s);
  }

  function card(item, isNew) {
    var c = el("div", "bb-card enter"), top = el("div", "bb-top"), art = el("div", "bb-art");
    art.innerHTML = ART[item.kind] || ART.rice;
    var name = el("div", "bb-name");
    if (isNew) name.appendChild(el("div", "bb-new", th("bbNew") + " · " + en("bbNew")));
    name.appendChild(el("h2", "", item.name)); name.appendChild(el("p", "", item.name_en));
    top.appendChild(art); top.appendChild(name); c.appendChild(top);
    if (item.note) c.appendChild(el("div", "bb-note-ribbon", "🏷 " + item.note));   // update 84: the SKU's promotion note
    var row = el("div", "bb-prices");
    sides().forEach(function (sd) {
      var value = priceOf(item, sd);
      if (!(value > 0)) return;
      var box = el("div", "bb-price " + sd), lbl = el("div", "lbl");
      lbl.appendChild(el("span", "", sd === "b" ? th("bbBuy") : th("bbSell")));
      lbl.appendChild(el("small", "", sd === "b" ? en("bbBuy") : en("bbSell")));
      box.appendChild(lbl);
      var val = el("div", "val"), num = el("span", "bb-num");
      val.appendChild(num); val.appendChild(el("span", "unit", th("bbUnit"))); box.appendChild(val);
      var ch = changeText(item[sd]), chg = el("div", "bb-chg " + ch.cls);
      if (ch.arrow) chg.appendChild(el("b", "", ch.arrow));
      chg.appendChild(el("span", "", ch.txt));
      box.appendChild(chg);
      if (ch.since) box.appendChild(el("div", "bb-since", ch.since));
      row.appendChild(box);
      digits(num, value, isNew && item[sd].prev ? item[sd].prev : undefined);
    });
    if (row.children.length === 1) row.className += " one";      // one price for this SKU: shown bigger
    c.appendChild(row);
    var sp = el("div", "bb-spark"); spark(sp, item); c.appendChild(sp);
    return c;
  }

  function spotlight(i, isNew) {
    var list = items(), stage = document.getElementById("bb-stage");
    if (!list.length) { stage.innerHTML = ""; stage.appendChild(el("div", "bb-empty", "-")); return; }
    idx = (i + list.length) % list.length;
    var old = stage.querySelector(".bb-card:not(.leave)");
    if (old) { old.className = "bb-card leave"; setTimeout(function () { if (old.parentNode) old.parentNode.removeChild(old); }, 950); }
    var c = card(list[idx], isNew);
    stage.appendChild(c);
    setTimeout(function () { c.className = "bb-card"; }, 30);
    var rows = document.querySelectorAll("#bb-rows .bb-row");
    for (var r = 0; r < rows.length; r++) rows[r].className = rows[r].className.replace(/\s*on\b/g, "") + (r === idx ? " on" : "");
    var bar = document.getElementById("bb-timer");
    bar.className = ""; bar.style.transitionDuration = "0ms"; bar.style.width = "0";
    setTimeout(function () { bar.style.transitionDuration = SPOT_MS + "ms"; bar.className = "run"; bar.style.width = "100%"; }, 50);
    clearTimeout(spotTimer);
    spotTimer = setTimeout(function () { spotlight(idx + 1); }, SPOT_MS);
  }

  function board(changed) {
    var host = document.getElementById("bb-rows"), list = items();
    host.innerHTML = "";
    list.forEach(function (item, k) {
      var r = el("div", "bb-row" + (changed[item.code] ? " flash" : ""));
      r.appendChild(el("i", "vd v-" + (item.variety || "")));
      var nm = el("div", "nm", item.name); nm.appendChild(el("small", "", item.name_en)); r.appendChild(nm);
      ["b", "s"].forEach(function (sd) {
        var has = priceOf(item, sd) > 0, pv = el("div", "pv " + sd, has ? money(priceOf(item, sd)) : ""), ch = changeText(item[sd]);
        if (has) pv.appendChild(el("small", ch.cls, ch.arrow ? ch.arrow + " " + money(Math.abs(item[sd].change)) : "—"));
        r.appendChild(pv);
      });
      host.appendChild(r);
      setTimeout(function () { r.className += " in"; }, 80 * k + 40);     // rows slide in one by one
    });
  }

  function ticker() {
    var tape = document.getElementById("bb-tape"), list = items();
    tape.innerHTML = "";
    if (!list.length) return;
    for (var copy = 0; copy < 2; copy++) {                  // two copies: the tape loops without a gap
      list.forEach(function (item) {
        var it = el("span", "it"); it.appendChild(el("span", "", item.name));
        sides().forEach(function (sd) {
          if (!(priceOf(item, sd) > 0)) return;
          it.appendChild(el("em", "", sd === "b" ? th("bbBuy") : th("bbSell")));
          it.appendChild(el("b", sd, money(priceOf(item, sd))));
          var ch = changeText(item[sd]);
          if (ch.arrow) it.appendChild(el("em", ch.cls, ch.arrow + " " + money(Math.abs(item[sd].change))));
        });
        if (item.note) it.appendChild(el("em", "nt", "🏷 " + item.note));
        tape.appendChild(it);
      });
      (data.messages || []).forEach(function (m) { tape.appendChild(el("span", "it msg", "📢 " + m)); });
    }
    var px = tape.scrollWidth / 2;
    tape.style.animationDuration = Math.max(20, px / 90) + "s";       // about 90 px a second
  }

  var promoAt = 0;
  function promoLines() {
    var lines = [];
    if (data.discount > 0) lines.push("🎉 " + th("bbDiscount").replace("{pct}", data.discount));
    (data.messages || []).forEach(function (m) { lines.push("📢 " + m); });
    return lines;
  }
  function promo() {                                    // update 84: the discount and the messages take turns
    var p = document.getElementById("bb-promo"), lines = promoLines();
    p.hidden = !lines.length;
    if (!lines.length) return;
    promoAt = promoAt % lines.length;
    p.textContent = lines[promoAt];
  }

  function snapshot() {
    var s = {};
    (data.items || []).forEach(function (i) { s[i.code] = i.buy + "|" + i.sell; });
    return s;
  }

  function draw(first) {
    var now = snapshot(), changed = {}, firstChanged = null;
    if (!first) for (var k in now) if (last[k] !== undefined && last[k] !== now[k]) { changed[k] = true; if (firstChanged === null) firstChanged = k; }
    last = now;
    board(changed); ticker(); promo();
    var list = items(), at = -1;
    for (var i = 0; i < list.length; i++) if (list[i].code === firstChanged) at = i;
    if (first) spotlight(0);
    else if (at >= 0) spotlight(at, true);                // a new price takes the spotlight at once
    else {
      var rows = document.querySelectorAll("#bb-rows .bb-row");
      if (rows[idx]) rows[idx].className += " on";
    }
  }

  function lost(yes) {
    document.getElementById("tvb-offline").hidden = !yes;
    document.getElementById("tvb-live").className = yes ? "tvb-live lost" : "tvb-live";
  }
  function refresh() {
    var req = new XMLHttpRequest();
    req.open("GET", url, true);
    req.timeout = 10000;
    req.onload = function () {
      if (req.status === 200) { try { data = JSON.parse(req.responseText); SPOT_MS = (data.seconds || 7) * 1000; syncClock(); draw(false); lost(false); return; } catch (e) { /* keep */ } }
      lost(true);
    };
    req.onerror = req.ontimeout = function () { lost(true); };
    req.send();
  }

  // full screen: button (shown while the pointer moves), double-click / double-tap, or the F key
  var fullBtn = document.getElementById("tvb-full"), idle = null, de = document.documentElement;
  function isFull() { return !!(document.fullscreenElement || document.webkitFullscreenElement); }
  function toggleFull() {
    try {
      if (isFull()) { (document.exitFullscreen || document.webkitExitFullscreen).call(document); }
      else if (de.requestFullscreen) { de.requestFullscreen(); } else if (de.webkitRequestFullscreen) { de.webkitRequestFullscreen(); }
    } catch (e) { /* not allowed */ }
  }
  function wake() {
    document.body.className = document.body.className.replace(/\s*awake\b/g, "") + " awake";
    clearTimeout(idle);
    idle = setTimeout(function () { document.body.className = document.body.className.replace(/\s*awake\b/g, ""); }, 4000);
  }
  if (fullBtn) {
    if (!(de.requestFullscreen || de.webkitRequestFullscreen)) fullBtn.style.display = "none";
    fullBtn.onclick = function (e) { e.stopPropagation(); toggleFull(); };
  }
  function fullLabel() { if (fullBtn) fullBtn.lastChild.textContent = fullBtn.getAttribute(isFull() ? "data-off" : "data-on"); }
  document.addEventListener("dblclick", toggleFull);
  document.addEventListener("mousemove", wake);
  document.addEventListener("touchstart", wake);
  document.addEventListener("keydown", function (e) {
    wake();
    if (e.key === "f" || e.key === "F") toggleFull();
    if (e.key === "ArrowRight") spotlight(idx + 1);
    if (e.key === "ArrowLeft") spotlight(idx - 1);
  });
  document.addEventListener("fullscreenchange", function () { fullLabel(); fit(); });
  document.addEventListener("webkitfullscreenchange", function () { fullLabel(); fit(); });

  window.addEventListener("resize", fit);
  fit(); syncClock(); clock(); draw(true);
  setInterval(clock, 1000);
  setInterval(function () { promoAt++; promo(); }, 6000);
  setInterval(refresh, 15000);
  setTimeout(function () { window.location.reload(); }, 6 * 3600 * 1000);
})();
