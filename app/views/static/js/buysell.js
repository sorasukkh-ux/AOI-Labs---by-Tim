/* Customer Service > Buy / Sell: the design's rate tables.
   Tabs switch between Milling, Buy and Sell; the customer chosen at the top is used by all three.
   Open creates Open orders; Close completes them at once (Buy: received into stock; Sell: released from stock),
   then shows the receipt (Print Billing). Totals here are a preview: the server prices and saves the orders. */
(function () {
  "use strict";
  var textHost = document.getElementById("bs-text");
  if (!textHost) return;
  var text = JSON.parse(textHost.getAttribute("data-text"));
  var custCode = document.getElementById("reg-cust-code");

  // ----- tabs -----
  var buttons = document.querySelectorAll("#svc-selector .svc-btn");
  buttons.forEach(function (btn) {
    btn.addEventListener("click", function () {
      buttons.forEach(function (b) { b.classList.toggle("active", b === btn); });
      document.querySelectorAll(".svc-panel").forEach(function (p) { p.classList.toggle("active", p.id === btn.getAttribute("data-svc")); });
    });
  });

  function fmt(n) { return n.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 }); }
  function el(tag, cls, content) { var n = document.createElement(tag); if (cls) n.className = cls; if (content !== undefined) n.textContent = content; return n; }

  document.querySelectorAll("form.bs-panel").forEach(function (form) {
    var errorEl = form.querySelector(".bs-error");
    function showError(message) { errorEl.textContent = message || ""; errorEl.classList.toggle("show", !!message); }
    // update 82: tapping a quantity of 0 selects it, so typing 30 gives 30 (not 030)
    form.querySelectorAll(".qty-cell input").forEach(function (i) {
      i.addEventListener("focus", function () { if (Number(i.value) === 0) i.select(); });
    });

    function rows() {
      return Array.prototype.map.call(form.querySelectorAll("tbody tr[data-mat]"), function (tr) {
        var input = tr.querySelector(".qty-cell input"), own = tr.querySelector(".rate-input");    // update 78: typed price
        var rate = own ? (Number(own.value) || 0) : (Number(tr.querySelector(".rate-cell").getAttribute("data-rate")) || 0);
        var changed = !!own && Math.abs(rate - Number(own.getAttribute("data-list"))) >= 0.005;
        tr.classList.toggle("price-changed", changed);
        return { tr: tr, mat: tr.getAttribute("data-mat"), name: tr.getAttribute("data-name"), changed: changed,
                 rate: rate, qty: Math.max(0, Number(input.value) || 0) };
      });
    }
    function render() {
      var amount = 0, qty = 0;
      rows().forEach(function (r) {
        var sum = r.rate * r.qty;
        r.tr.querySelector(".summary-cell").textContent = fmt(sum);
        amount += sum; qty += r.qty;
      });
      form.querySelector(".bs-total").textContent = fmt(amount) + " THB · " + fmt(qty) + " kg";
      var reason = form.querySelector(".bs-price-reason");
      if (reason) reason.hidden = !rows().some(function (r) { return r.changed && r.qty > 0; });
    }
    form.addEventListener("input", function () { showError(""); render(); });
    form.addEventListener("submit", function (e) { e.preventDefault(); });

    function ready() {
      if (!custCode || !custCode.value) { showError(text.alertCustInfoRequired); return false; }
      if (!rows().some(function (r) { return r.qty > 0; })) { showError(text.errNoItems); return false; }
      var why = form.querySelector('input[name="PRICE_REASON"]');
      if (why && rows().some(function (r) { return r.changed && r.qty > 0; }) && !why.value.trim()) {
        showError(text.errPriceReason); why.focus(); return false;
      }
      form.querySelector('input[name="CUST_CODE"]').value = custCode.value;
      return true;
    }
    function post(url, extra) {
      var data = new FormData(form);
      Object.keys(extra || {}).forEach(function (k) { data.set(k, extra[k]); });
      return fetch(url, { method: "POST", body: data, credentials: "same-origin",
                          headers: { "Accept": "application/json", "X-CSRF-Token": data.get("csrf_token") } })
        .then(function (r) { return r.json().then(function (b) { return b; }, function () { return { ok: false, error: text.errServer }; }); })
        .catch(function () { return { ok: false, error: text.errServer }; });
    }
    function save(mode, extra) {
      var data = { mode: mode };
      Object.keys(extra || {}).forEach(function (k) { data[k] = extra[k]; });
      return post(form.getAttribute("data-new-url"), data).then(function (result) {
        if (!result.ok) { showError(result.error); return; }
        if (mode === "open") {
          var ask = result.list_url ? window.AOIModal.confirm(result.message) : window.AOIModal.alert(result.message);
          ask.then(function (yes) { window.location.href = yes && result.list_url ? result.list_url : result.new_url + "&svc=" + panelName(); });
          return;
        }
        // closed: show the receipt with Print Billing, then start again with the same customer
        fetch(result.billing_url, { credentials: "same-origin" }).then(function (r) { return r.text(); }).then(function (html) {
          var box = el("div");
          box.appendChild(el("p", "od-title", result.message));
          var holder = el("div"); holder.innerHTML = html; box.appendChild(holder);
          return window.AOIModal.html(box.innerHTML);
        }).then(function () { window.location.href = result.new_url + "&svc=" + panelName(); });
      });
    }
    function panelName() { return form.getAttribute("data-type") === "BY" ? "buy" : "sell"; }

    var openBtn = form.querySelector(".bs-open-btn"), closeBtn = form.querySelector(".bs-close-btn");
    if (openBtn) openBtn.addEventListener("click", function () { if (ready()) save("open"); });
    if (closeBtn) closeBtn.addEventListener("click", function () {
      if (!ready()) return;
      post(form.getAttribute("data-check-url")).then(function (check) {
        if (!check.ok) { showError(check.error); return; }
        if (form.getAttribute("data-type") === "BY") return closeBuy(check);
        closeSell(check);
      });
    });

    // Buy > Close: where the goods are received (one location for this purchase)
    function closeBuy(check) {
      var node = document.getElementById("buy-close-tpl").content.firstElementChild.cloneNode(true);
      var lines = node.querySelector(".bs-lines");
      check.lines.forEach(function (l) {
        var row = el("div", "od-row");
        row.appendChild(el("span", "od-label", l.name)); row.appendChild(el("span", "od-value", fmt(l.qty) + " kg · " + fmt(l.amount) + " THB"));
        lines.appendChild(row);
      });
      node.querySelector('input[name="LOC_ID"]').value = check.suggest_loc || "";
      node.addEventListener("click", function (e) { if (e.target.closest("[data-modal-close]")) window.AOIModal.close(); });
      node.addEventListener("submit", function (e) {
        e.preventDefault();
        var locInput = node.querySelector('input[name="LOC_ID"]');
        if (window.AOIFormatLoc) locInput.value = window.AOIFormatLoc(locInput.value);
        var loc = locInput.value.trim();
        if (!/^\d{2}-\d{3}-\d{3}$/.test(loc)) { node.querySelector('input[name="LOC_ID"]').focus(); return; }
        var status = node.querySelector('select[name="INV_STATUS"]').value;
        window.AOIModal.close();
        save("close", { LOC_ID: loc, INV_STATUS: status });
      });
      window.AOIModal.form(node);
    }

    // Sell > Close: the stock that will be released, with a warning when it is short (as in the design)
    function closeSell(check) {
      var box = el("div");
      box.appendChild(el("p", "od-title", text.sellReleaseDetailsTitle));
      var table = el("table", "bs-check-table"), head = el("tr");
      [text.colSku, text.colLocation, text.colOnHand].forEach(function (h, i) { head.appendChild(el("th", i === 2 ? "num" : "", h)); });
      var thead = el("thead"); thead.appendChild(head); table.appendChild(thead);
      var body = el("tbody");
      check.lines.forEach(function (l) {
        var tr = el("tr");
        tr.appendChild(el("td", "", l.name));
        tr.appendChild(el("td", "", l.locations.length ? l.locations.map(function (x) { return x[0] + " (" + fmt(x[1]) + ")"; }).join(", ") : text.noStockAvailable));
        tr.appendChild(el("td", "num" + (l.short ? " short" : ""), "-" + fmt(l.qty) + " kg"));
        body.appendChild(tr);
        if (l.short) {
          var warn = el("tr"), td = el("td", "short", text.insufficientStockWarning.replace("{available}", fmt(l.available)).replace("{needed}", fmt(l.qty)));
          td.colSpan = 3; warn.appendChild(td); body.appendChild(warn);
        }
      });
      table.appendChild(body); box.appendChild(table);
      box.appendChild(el("p", "", text.confirmCloseQuestion));
      var confirmBox = el("div"); confirmBox.appendChild(box);
      window.AOIModal.confirmHtml(confirmBox.innerHTML).then(function (yes) { if (yes) save("close"); });
    }
    render();
  });
})();
