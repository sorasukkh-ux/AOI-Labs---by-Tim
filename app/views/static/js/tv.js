/* TV display: scales the 1920x1080 stage to the screen, shows the clock, refreshes the data every 15 s and
   turns the pages of a long column every 10 s. Plain ES5 so that the browsers of smart TVs can run it. */
(function () {
  "use strict";
  var root = document.getElementById("tvb");
  if (!root) return;
  var text = JSON.parse(root.getAttribute("data-text"));
  var data = JSON.parse(root.getAttribute("data-board"));
  var url = root.getAttribute("data-url");
  var PER_PAGE = { ready: 4, milling: 3, queue: 4 }, PRICE_PAGE = 4, page = { ready: 0, milling: 0, queue: 0, prices: 0 };

  function th(key) { return (text[key] && text[key].th) || ""; }
  function el(tag, cls, content) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (content !== undefined && content !== null) n.textContent = content;
    return n;
  }
  function money(v) { return Number(v || 0).toFixed(2); }

  function fit() {
    var s = Math.min(window.innerWidth / 1920, window.innerHeight / 1080);
    root.style.transform = "scale(" + s + ")";
    root.style.left = ((window.innerWidth - 1920 * s) / 2) + "px";
    root.style.top = ((window.innerHeight - 1080 * s) / 2) + "px";
  }

  // the clock follows the server (mill time zone), not the TV's own setting, which is often wrong
  var skew = 0;
  function syncClock() { if (data.now_ms) skew = data.now_ms - Date.now(); }
  function clock() {
    var d = new Date(Date.now() + skew + (data.tz_min || 0) * 60000), pad = function (n) { return (n < 10 ? "0" : "") + n; };
    document.getElementById("tvb-time").textContent = pad(d.getUTCHours()) + ":" + pad(d.getUTCMinutes());
    document.getElementById("tvb-date").textContent = text.days[(d.getUTCDay() + 6) % 7] + " " + d.getUTCDate() + " " +
      text.months[d.getUTCMonth()] + " " + (d.getUTCFullYear() + 543);
  }

  function detail(r) {
    var parts = [];
    if (r.delivery) parts.push("🚚 " + th("tvbDeliver"));
    parts.push(Math.round(r.kg) + " " + th("tvbKg"));
    if (text["var"][r.variety]) parts.push(text["var"][r.variety]);
    return parts.join(" · ");
  }

  function row(kind, r) {
    var box = el("div", "tvb-row" + (r["new"] ? " new" : "")), top = box;
    if (kind === "milling") { top = el("div", "top"); box.appendChild(top); }
    top.appendChild(el("div", "no", "#" + r.no));
    var who = el("div", "who", r.name || "-");
    who.appendChild(el("small", "", detail(r)));
    top.appendChild(who);
    var tag = el("div", "tag");
    if (kind === "ready") {
      tag.appendChild(document.createTextNode(r.arrive ? th("tvbArrive") : th("tvbDoneAt")));
      tag.appendChild(el("b", "", r.arrive || r.done));
    } else if (kind === "milling") {
      tag.appendChild(document.createTextNode(th("tvbFinishAt")));
      tag.appendChild(el("b", "", r.finish));
    } else {
      tag.appendChild(document.createTextNode(r.receive.day === "today" ? th("tvbReceive") :
        (r.receive.day === "tomorrow" ? th("tvbTomorrow") : r.receive.day)));
      tag.appendChild(el("b", "", r.receive.time));
    }
    top.appendChild(tag);
    if (kind === "milling") {
      var bar = el("div", "tvb-bar"), fill = el("i");
      bar.appendChild(fill); box.appendChild(bar);
      fill.style.width = Math.max(1, Math.min(99, r.pct || 0)) + "%";
    }
    return box;
  }

  function column(kind) {
    var col = root.querySelector('[data-col="' + kind + '"]'), list = data[kind] || [], size = PER_PAGE[kind];
    var pages = Math.max(1, Math.ceil(list.length / size));
    if (page[kind] >= pages) page[kind] = 0;
    col.querySelector(".n").textContent = list.length;
    var rows = col.querySelector(".rows");
    rows.innerHTML = "";
    var shown = list.slice(page[kind] * size, page[kind] * size + size);
    if (!shown.length) rows.appendChild(el("div", "tvb-none", th("tvbNone")));
    for (var i = 0; i < shown.length; i++) rows.appendChild(row(kind, shown[i]));
    var more = col.querySelector(".more"), dots = col.querySelector(".dots");
    more.hidden = pages < 2;
    more.textContent = pages < 2 ? "" : th("tvbPage").replace("{page}", page[kind] + 1).replace("{pages}", pages);
    dots.innerHTML = "";
    if (pages > 1) for (var p = 0; p < pages; p++) dots.appendChild(el("i", p === page[kind] ? "on" : ""));
  }

  function prices() {
    var box = document.getElementById("tvb-prices"), list = data.prices || [];
    var pages = Math.max(1, Math.ceil(list.length / PRICE_PAGE));
    if (page.prices >= pages) page.prices = 0;
    box.innerHTML = "";
    var shown = list.slice(page.prices * PRICE_PAGE, page.prices * PRICE_PAGE + PRICE_PAGE);
    for (var i = 0; i < shown.length; i++) {
      var p = shown[i], card = el("div", "p"), v = el("div", "v");
      card.appendChild(el("div", "k", p.name));
      if (p.buy > 0) { v.appendChild(el("span", "", th("tvbBuy") + " ")); v.appendChild(el("b", "buy", money(p.buy))); v.appendChild(document.createTextNode("  ")); }
      if (p.sell > 0) { v.appendChild(el("span", "", " " + th("tvbSell") + " ")); v.appendChild(el("b", "sell", money(p.sell))); }
      card.appendChild(v);
      box.appendChild(card);
    }
    document.getElementById("tvb-updated").textContent = th("tvbUpdated").replace("{time}", data.updated);
  }

  function draw() { column("ready"); column("milling"); column("queue"); prices(); }

  function refresh() {
    var req = new XMLHttpRequest();
    req.open("GET", url, true);
    req.timeout = 10000;
    req.onload = function () {
      if (req.status === 200) {
        try { data = JSON.parse(req.responseText); syncClock(); draw(); document.getElementById("tvb-offline").hidden = true; return; } catch (e) { /* keep the last data */ }
      }
      document.getElementById("tvb-offline").hidden = false;
    };
    req.onerror = req.ontimeout = function () { document.getElementById("tvb-offline").hidden = false; };
    req.send();
  }

  function turn() {
    page.ready++; page.milling++; page.queue++; page.prices++;
    draw();
  }

  window.addEventListener("resize", fit);
  fit(); syncClock(); clock(); draw();
  setInterval(clock, 1000);
  setInterval(refresh, 15000);
  setInterval(turn, 10000);
  setTimeout(function () { window.location.reload(); }, 6 * 3600 * 1000);   // pick up a new version now and then
})();
