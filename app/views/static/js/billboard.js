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

  // ----- update 89: "price cards" theme - three cards at a time, the page turns with the spotlight timer -----
  var cdPage = 0, cdTimer = null, CD_PER = 3;
  function star(id, c1, c2) {                          // starburst badge: 16 points, gradient top light -> bottom dark
    var pts = [], n = 32;
    for (var i = 0; i < n; i++) {
      var r = i % 2 ? 86 : 100, a = Math.PI * 2 * i / n - Math.PI / 2;
      pts.push((100 + r * Math.cos(a)).toFixed(1) + "," + (100 + r * Math.sin(a)).toFixed(1));
    }
    var s = svg("svg", { viewBox: "0 0 200 200" }), d = svg("defs", {}), g = svg("linearGradient", { id: id, x1: "0", y1: "0", x2: "0", y2: "1" });
    g.appendChild(svg("stop", { offset: "0", "stop-color": c1 })); g.appendChild(svg("stop", { offset: ".5", "stop-color": c1 }));
    g.appendChild(svg("stop", { offset: ".5", "stop-color": c2 })); g.appendChild(svg("stop", { offset: "1", "stop-color": c2 }));
    d.appendChild(g); s.appendChild(d);
    s.appendChild(svg("polygon", { points: pts.join(" "), fill: "url(#" + id + ")", stroke: "rgba(255,255,255,.7)", "stroke-width": "3" }));
    return s;
  }
  var CD_COL = [["#f472b6", "#ec4899"], ["#38bdf8", "#3b82f6"], ["#c084fc", "#9333ea"], ["#fdba74", "#f97316"], ["#4ade80", "#16a34a"]];
  function cdRow(ok, iconText, label, value, unit) {
    var r = el("div", "cd-row"), ic = el("i", "ic " + ok, iconText);
    r.appendChild(ic); r.appendChild(el("span", "", label));
    if (value !== undefined) { var b = el("b", "", value); if (unit) b.appendChild(el("small", "", unit)); r.appendChild(b); }
    return r;
  }
  function cdCard(item, k, changed) {
    var col = k % CD_COL.length, c = el("div", "cd-card cd-c" + col + (changed ? " flash" : "")), badge = el("div", "cd-badge");
    var main = priceOf(item, "s") > 0 && sides().indexOf("s") >= 0 ? "s" : "b";
    badge.appendChild(star("cdb" + k, CD_COL[col][0], CD_COL[col][1]));
    var bt = el("b", "", money(priceOf(item, main))); bt.appendChild(el("small", "", (main === "b" ? th("bbBuy") : th("bbSell")) + " · " + th("bbUnit")));
    badge.appendChild(bt); c.appendChild(badge);
    c.appendChild(el("h3", "", item.name)); c.appendChild(el("div", "en", item.name_en));
    var rows = el("div", "cd-rows");
    ["b", "s"].forEach(function (sd) {
      if (sides().indexOf(sd) < 0) return;
      var v = priceOf(item, sd), lbl = sd === "b" ? th("bbBuy") : th("bbSell");
      rows.appendChild(v > 0 ? cdRow("", "✓", lbl, money(v), th("bbUnit")) : cdRow("no", "✕", lbl, "-"));
    });
    var side = item[main], ch = changeText(side);
    rows.appendChild(cdRow(ch.cls === "down" ? "dn" : "", ch.arrow || "=", ch.txt + (ch.since ? " · " + ch.since : "")));
    if (item.note) rows.appendChild(cdRow("nt", "★", item.note));
    var stagger = rows.children;
    for (var i = 0; i < stagger.length; i++) stagger[i].style.transitionDelay = (0.25 + i * 0.12) + "s";
    c.appendChild(rows);
    var ser = side.series || [], sp = el("div", "cd-spark");
    if (ser.length > 1) {
      var lo = Math.min.apply(null, ser), hi = Math.max.apply(null, ser); if (hi - lo < 0.5) { lo -= 0.5; hi += 0.5; }
      var pts = ser.map(function (v, j) { return [j / (ser.length - 1) * 400, 62 - (v - lo) / (hi - lo) * 54]; });
      var d = pts.map(function (p, j) { return (j ? "L" : "M") + p[0].toFixed(1) + " " + p[1].toFixed(1); }).join(" ");
      var s2 = svg("svg", { viewBox: "0 0 400 70", preserveAspectRatio: "none" });
      s2.appendChild(svg("path", { "class": "ar", d: d + " L400 70 L0 70 Z" })); s2.appendChild(svg("path", { "class": "ln", d: d }));
      sp.appendChild(s2);
    }
    c.appendChild(sp);
    var btn = el("div", "cd-btn"); btn.innerHTML = ART[item.kind] || ART.rice; c.appendChild(btn);
    return c;
  }
  function cardsPage(changed, keep) {
    var host = document.getElementById("cd-wrap"), dots = document.getElementById("cd-dots"), list = items();
    if (!host) return;
    var pages = Math.max(1, Math.ceil(list.length / CD_PER));
    if (cdPage >= pages) cdPage = 0;
    var old = host.querySelectorAll(".cd-card");
    var put = function () {
      host.innerHTML = "";
      list.slice(cdPage * CD_PER, cdPage * CD_PER + CD_PER).forEach(function (item, k) {
        var c = cdCard(item, cdPage * CD_PER + k, changed && changed[item.code]);
        host.appendChild(c);
        setTimeout(function () { c.className += " in"; }, 60 + k * 160);
      });
      dots.innerHTML = "";
      if (pages > 1) for (var p = 0; p < pages; p++) dots.appendChild(el("i", p === cdPage ? "on" : ""));
    };
    if (old.length && !keep) { for (var i = 0; i < old.length; i++) old[i].className += " out"; setTimeout(put, 450); } else put();
    clearTimeout(cdTimer);
    cdTimer = setTimeout(function () { cdPage++; cardsPage(); }, Math.max(SPOT_MS, 6000) + 1500);
  }

  // ----- update 90: "ranking" theme - SKUs ranked by price with growing bars; the highlight walks down the list -----
  var rkOn = 0, rkTimer = null, RK_COL = ["#1d4ed8", "#0ea5e9", "#16a34a", "#f59e0b", "#e11d48", "#7c3aed", "#0891b2", "#ea580c"];
  // update 92 (Tim): buying AND selling - the ranking takes turns (selling list, then buying list, ...)
  var rkCur = null;
  function rkSides() {
    return ["s", "b"].filter(function (sd) { return sides().indexOf(sd) >= 0 && items().some(function (i) { return priceOf(i, sd) > 0; }); });
  }
  function rkSide() {
    var avail = rkSides();
    if (avail.indexOf(rkCur) < 0) rkCur = avail[0] || "s";
    return rkCur;
  }
  function ranking(changed) {
    var rows = document.getElementById("rk-rows"), signs = document.getElementById("rk-signs");
    if (!rows) return;
    var sd = rkSide(), other = sd === "s" ? "b" : "s", list = items().filter(function (i) { return priceOf(i, sd) > 0; })
      .sort(function (a, b) { return priceOf(b, sd) - priceOf(a, sd); }).slice(0, 10);
    var max = list.length ? priceOf(list[0], sd) : 1, sideName = sd === "b" ? th("bbBuy") : th("bbSell");
    document.getElementById("rk-title").textContent = th("rkTitle").replace("{side}", sideName) + " · Top " + list.length;
    document.getElementById("rk-sub").textContent = rkSides().length > 1 ? th("rkSub") : th("rkSub").split(" · ").slice(0, 2).join(" · ");
    document.getElementById("rk-wrap").className = "rk-wrap side-" + sd;
    rows.innerHTML = ""; signs.innerHTML = "";
    rows.className = "rk-rows" + (list.length <= 5 ? " few" : "");      // few rows: bigger rows
    list.forEach(function (item, k) {
      var r = el("div", "rk-row" + (changed && changed[item.code] ? " flash" : ""));
      r.appendChild(el("div", "no", String(k + 1)));
      var nm = el("div", "nm", item.name); nm.appendChild(el("small", "", item.name_en)); r.appendChild(nm);
      var bar = el("div", "rk-bar"), fill = el("i"); bar.appendChild(fill); r.appendChild(bar);
      var ch = changeText(item[sd]), val = el("div", "val", money(priceOf(item, sd)));
      var sub = priceOf(item, other) > 0 && sides().indexOf(other) >= 0
        ? th("rkOther").replace("{side}", other === "b" ? th("bbBuy") : th("bbSell")).replace("{v}", money(priceOf(item, other))) : (ch.arrow ? ch.arrow + " " + money(Math.abs(item[sd].change)) : "");
      val.appendChild(el("small", ch.cls, sub)); r.appendChild(val);
      rows.appendChild(r);
      setTimeout(function () { r.className += " in"; fill.style.width = Math.max(6, priceOf(item, sd) / max * 100).toFixed(1) + "%"; }, 120 + k * 110);
      var sg = el("div", "rk-sign", item.name), mark = el("i");
      mark.style.background = RK_COL[k % RK_COL.length]; sg.insertBefore(mark, sg.firstChild);
      sg.style.top = (k * (760 / Math.max(list.length, 1)) + 4).toFixed(0) + "px";
      sg.style.animationDelay = (k * 0.12) + "s, " + (-k * 0.7) + "s";
      sg.style.zIndex = 20 - k;
      signs.appendChild(sg);
    });
    rkMark();
  }
  function rkMark() {
    var rows = document.querySelectorAll("#rk-rows .rk-row"), signs = document.querySelectorAll("#rk-signs .rk-sign");
    if (!rows.length) return;
    if (rkOn >= rows.length) {                          // the list is done: the other side's ranking, if there is one
      rkOn = 0;
      var avail = rkSides();
      if (avail.length > 1) { rkCur = avail[(avail.indexOf(rkCur) + 1) % avail.length]; ranking(); return; }
    }
    for (var i = 0; i < rows.length; i++) {
      rows[i].className = rows[i].className.replace(/\s*on\b/g, "") + (i === rkOn ? " on" : "");
      if (signs[i]) signs[i].className = signs[i].className.replace(/\s*on\b/g, "") + (i === rkOn ? " on" : "");
    }
    clearTimeout(rkTimer);
    rkTimer = setTimeout(function () { rkOn++; rkMark(); }, Math.max(3000, SPOT_MS / 2));
  }

  var pcIndex = 0;
  function card(item, isNew) {
    var c = el("div", "bb-card enter pc" + (pcIndex++ % 6)), top = el("div", "bb-top"), art = el("div", "bb-art");   // update 88: poster colour
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
    if (old) { old.className = old.className.replace(" enter", "") + " leave"; setTimeout(function () { if (old.parentNode) old.parentNode.removeChild(old); }, 950); }
    var c = card(list[idx], isNew);
    stage.appendChild(c);
    setTimeout(function () { c.className = c.className.replace(" enter", ""); }, 30);
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
    ranking(changed);                                  // update 90
    if (first) cardsPage(changed, true);
    else if (firstChanged !== null) {                  // a new price: turn to the page with it
      var all = items();
      for (var q = 0; q < all.length; q++) if (all[q].code === firstChanged) cdPage = Math.floor(q / CD_PER);
      cardsPage(changed);
    }
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
      if (req.status === 200) { try { data = JSON.parse(req.responseText); SPOT_MS = (data.seconds || 7) * 1000; syncClock(); theme(); draw(false); lost(false); return; } catch (e) { /* keep */ } }
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

  // update 85: themes. The settings choose it (live); ?theme= on the link fixes it.
  var fixedTheme = root.getAttribute("data-theme");
  function theme() {
    var name = fixedTheme || data.theme || "default";
    document.body.className = document.body.className.replace(/\s*theme-[a-z]+/g, "") + " theme-" + name;
    music();
  }

  // update 87: soft Christmas music with the Christmas theme (settings: on / off, volume). The browser needs one tap
  // or key press first; the speaker button starts it, or mutes it on this TV (remembered on this TV).
  var musicBtn = document.getElementById("xm-music"), M = window.AOIXmasMusic;
  function muted() { try { return window.localStorage.getItem("aoi-bb-mute") === "1"; } catch (e) { return false; } }
  function wanted() { return !!M && (fixedTheme || data.theme) === "christmas" && data.music !== false && (data.volume || 0) > 0; }
  function music() {
    if (!musicBtn) return;
    musicBtn.hidden = !wanted();
    if (!wanted()) { if (M) M.stop(); return; }
    M.setVolume(data.volume);
    if (muted()) { M.stop(); musicBtn.innerHTML = musicBtn.getAttribute("data-mute"); musicBtn.className = "xm-music off"; return; }
    if (M.running()) { musicBtn.innerHTML = musicBtn.getAttribute("data-play"); musicBtn.className = "xm-music on"; }
    else { musicBtn.innerHTML = musicBtn.getAttribute("data-on"); musicBtn.className = "xm-music ask"; }
  }
  function userStart() {                               // the first tap / key: the browser now allows sound
    if (wanted() && !muted()) { M.start(); setTimeout(music, 300); }
  }
  if (musicBtn) musicBtn.onclick = function (e) {
    e.stopPropagation();
    var nowMuted = M.running();
    try { window.localStorage.setItem("aoi-bb-mute", nowMuted ? "1" : "0"); } catch (err) { /* private mode */ }
    if (nowMuted) M.stop(); else M.start();
    setTimeout(music, 300);
  };
  document.addEventListener("click", userStart);
  document.addEventListener("touchend", userStart);
  document.addEventListener("keydown", userStart);
  (function snow() {
    var host = document.getElementById("xm-snow");
    if (!host) return;
    for (var i = 0; i < 70; i++) {
      var f = document.createElement("i"), size = 3 + Math.random() * 7;
      f.style.left = (Math.random() * 100) + "%";
      f.style.width = f.style.height = size + "px";
      f.style.opacity = (0.4 + Math.random() * 0.6).toFixed(2);
      f.style.animationDuration = (8 + Math.random() * 12).toFixed(1) + "s";
      f.style.animationDelay = (-Math.random() * 20).toFixed(1) + "s";
      f.style.setProperty("--dx", (Math.random() * 160 - 80).toFixed(0) + "px");
      host.appendChild(f);
    }
  })();

  // update 86: gifts, trees and baubles drifting through a 3D room behind the board (CSS 3D, no library)
  (function room() {
    var host = document.getElementById("xm-3d");
    if (!host) return;
    var TREE = '<svg viewBox="0 0 100 135"><path class="tr" d="M50 8 L22 52 H36 L14 88 H32 L8 122 H92 L68 88 H86 L64 52 H78 Z"/>' +
      '<path class="tr2" d="M50 8 L36 30 L64 30 Z"/><rect class="trunk" x="43" y="122" width="14" height="12" rx="2"/>' +
      '<path class="star" d="M50 0l4 8 9 1-7 6 2 9-8-5-8 5 2-9-7-6 9-1z"/>' +
      '<circle class="o1" cx="40" cy="70" r="5"/><circle class="o2" cx="62" cy="62" r="5"/><circle class="o3" cx="50" cy="98" r="5"/>' +
      '<circle class="o4" cx="72" cy="106" r="5"/><circle class="o2" cx="28" cy="108" r="5"/><circle class="o1" cx="56" cy="40" r="4"/></svg>';
    var BOW = '<svg viewBox="0 0 100 50"><path d="M50 46 C30 10 4 8 8 30 C10 44 36 46 50 46 Z M50 46 C70 10 96 8 92 30 C90 44 64 46 50 46 Z" fill="var(--rb)" stroke="rgba(0,0,0,.2)" stroke-width="2"/><circle cx="50" cy="44" r="7" fill="var(--rb)"/></svg>';
    var BAUBLE = ["#ef4444", "#fde047", "#3b82f6", "#22c55e", "#ec4899", "#a855f7"];
    function rnd(a, b) { return a + Math.random() * (b - a); }
    function make(i) {
      var o = el("div", "o3"), sp = el("div", "s3"), kind = i % 3, size;
      if (kind === 0) {                                  // gift box
        size = rnd(70, 130);
        var g = el("div", "g3 gc" + Math.floor(rnd(0, 6)));
        ["fr", "bk", "lf", "rt", "tp", "bt"].forEach(function (f) { g.appendChild(el("div", "f " + f)); });
        var bow = el("div", "bow"), bow2 = el("div", "bow bow2"); bow.innerHTML = BOW; bow2.innerHTML = BOW;
        g.appendChild(bow); g.appendChild(bow2);
        g.style.setProperty("--s", size + "px"); sp.appendChild(g);
      } else if (kind === 1) {                           // tree
        size = rnd(110, 190);
        var t = el("div", "t3"); t.innerHTML = TREE + TREE; t.style.setProperty("--s", size + "px"); sp.appendChild(t);
      } else {                                           // bauble
        size = rnd(50, 90);
        var b = el("div", "b3"); b.style.setProperty("--s", size + "px"); b.style.setProperty("--bx", BAUBLE[Math.floor(rnd(0, BAUBLE.length))]);
        sp.appendChild(b);
      }
      var fromLeft = Math.random() < 0.5, y0 = rnd(80, 900), drift = rnd(-260, 260);
      o.style.setProperty("--x0", (fromLeft ? -300 : 2100) + "px"); o.style.setProperty("--x1", (fromLeft ? 2100 : -300) + "px");
      o.style.setProperty("--y0", y0 + "px"); o.style.setProperty("--y1", Math.max(40, Math.min(980, y0 + drift)) + "px");
      o.style.setProperty("--z0", rnd(-900, 150) + "px"); o.style.setProperty("--z1", rnd(-900, 150) + "px");
      var dur = rnd(22, 40);
      o.style.animationDuration = dur.toFixed(1) + "s"; o.style.animationDelay = (-rnd(0, dur)).toFixed(1) + "s";
      sp.style.animationDuration = rnd(6, 14).toFixed(1) + "s";
      if (Math.random() < 0.5) sp.style.animationDirection = "reverse";
      sp.style.setProperty("--tilt", rnd(-20, 25).toFixed(0) + "deg");
      o.appendChild(sp); host.appendChild(o);
    }
    for (var i = 0; i < 12; i++) make(i);
  })();

  (function drops() {                                   // update 102: Songkran water drops (shown only with the theme)
    var host = document.getElementById("sk-drops");
    if (!host) return;
    for (var i = 0; i < 60; i++) {
      var d = document.createElement("i"), size = 6 + Math.random() * 12;
      d.style.left = (Math.random() * 100) + "%";
      d.style.width = (size * 0.62).toFixed(1) + "px"; d.style.height = size.toFixed(1) + "px";
      d.style.animationDuration = (2.2 + Math.random() * 3).toFixed(2) + "s";
      d.style.animationDelay = (-Math.random() * 5).toFixed(2) + "s";
      d.style.setProperty("--dx", (Math.random() * 120 - 60).toFixed(0) + "px");
      host.appendChild(d);
    }
  })();

  window.addEventListener("resize", fit);
  fit(); syncClock(); clock(); theme(); draw(true);
  setInterval(clock, 1000);
  setInterval(function () { promoAt++; promo(); }, 6000);
  setInterval(refresh, 15000);
  setTimeout(function () { window.location.reload(); }, 6 * 3600 * 1000);
})();
