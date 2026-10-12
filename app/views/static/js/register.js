/* Customer Service > Milling: choose the customer, live output weights, and the Review step.
   Review asks the server for the payment, the receive time and the label, so the numbers come from the MODEL. */
(function () {
  "use strict";
  var form = document.getElementById("reg-form");
  if (!form) return;
  var ratios = JSON.parse(form.getAttribute("data-ratios")), text = JSON.parse(form.getAttribute("data-text"));
  var byId = function (id) { return document.getElementById(id); };
  var paddy = byId("paddy-in"), custCode = byId("reg-cust-code");
  var nameInput = byId("top-cust-name"), scanInput = byId("reg-scan"), results = byId("reg-cust-results");
  var mobile = byId("top-cust-mobile"), lineId = byId("top-cust-lineid"), newRow = byId("reg-new-customer-row");
  var typeSelect = byId("reg-delivery-type"), address = byId("reg-cust-address"), distance = byId("reg-distance-km");
  var panel = byId("reg-result-panel"), errorEl = byId("reg-review-error"), staleNote = byId("reg-stale-note");
  var confirmBtn = byId("reg-confirm-btn"), reviewBtn = byId("review-btn");
  var reviewed = false, chosenName = nameInput.value;

  function el(tag, cls, content) { var n = document.createElement(tag); if (cls) n.className = cls; if (content !== undefined) n.textContent = content; return n; }
  function showError(message) { errorEl.textContent = message || ""; errorEl.classList.toggle("show", !!message); }
  function markStale() {
    if (!reviewed) return;
    panel.classList.add("stale"); staleNote.hidden = false; confirmBtn.disabled = true;
  }

  // ----- live outputs -----
  function updateOutputs() {
    var kg = Number(paddy.value) || 0, v = byId("reg-variety"), r = ratios[v ? v.value : "WH"] || {};
    ["rice", "bran", "broken", "husk"].forEach(function (k) {
      byId("reg-" + k + "-out").value = (Math.round(kg * (r[k] || 0) * 100) / 100).toFixed(2);
    });
  }
  function toggleDelivery() {
    var isDelivery = typeSelect.value === "D";
    byId("reg-address-row").hidden = !isDelivery;
    byId("reg-distance-row").hidden = !isDelivery;
    if (isDelivery) {                       // start from the customer's own address and distance
      if (!address.value.trim()) address.value = form.getAttribute("data-address") || "";
      if (distance.value === "") distance.value = form.getAttribute("data-distance") || "";
    }
  }
  form.addEventListener("input", function () { showError(""); updateOutputs(); markStale(); });
  typeSelect.addEventListener("change", toggleDelivery);

  // ----- customer -----
  function clearCustomer() {
    custCode.value = ""; mobile.value = ""; lineId.value = ""; chosenName = "";
    form.setAttribute("data-address", ""); form.setAttribute("data-distance", "");
  }
  function choose(c) {
    custCode.value = c.code; nameInput.value = c.name; chosenName = c.name;
    mobile.value = c.mobile || ""; lineId.value = c.line || ""; scanInput.value = c.code;
    form.setAttribute("data-address", c.address || "");
    form.setAttribute("data-distance", c.distance === null || c.distance === undefined ? "" : c.distance);
    results.hidden = true; if (newRow) newRow.hidden = true;
    address.value = ""; distance.value = "";
    toggleDelivery(); showError(""); markStale();
  }
  function renderResults(list) {
    results.textContent = ""; results.hidden = false;
    if (newRow) newRow.hidden = false;
    if (!list.length) { results.appendChild(el("p", "hint home-empty", text.custListEmpty)); return; }
    list.forEach(function (c) {
      var row = el("button", "demo-row cust-row"); row.type = "button"; row.setAttribute("data-cust-code", c.code);
      row.appendChild(el("span", "demo-id", c.name)); row.appendChild(el("span", "demo-meta", c.code + " · " + c.mobile));
      if (c.distance !== null && c.distance !== undefined) row.appendChild(el("span", "badge manual", Number(c.distance).toFixed(2) + " km"));
      row.addEventListener("click", function () { choose(c); });
      results.appendChild(row);
    });
  }
  function lookup(params) {
    return fetch(form.getAttribute("data-search-url") + "?" + params, { credentials: "same-origin" })
      .then(function (r) { return r.ok ? r.json() : { customers: [] }; }).catch(function () { return { customers: [] }; });
  }
  function search() { lookup("q=" + encodeURIComponent(nameInput.value.trim())).then(function (r) { renderResults(r.customers || []); }); }
  byId("cust-search-btn").addEventListener("click", search);
  nameInput.addEventListener("keydown", function (e) { if (e.key === "Enter") { e.preventDefault(); search(); } });
  nameInput.addEventListener("input", function () { if (nameInput.value !== chosenName) { clearCustomer(); markStale(); } });
  function scan() {
    var code = scanInput.value.trim();
    if (!code) return;
    // the exact customer ID (a scanned QR) is chosen at once; a partly typed ID lists the matching customers
    lookup("code=" + encodeURIComponent(code)).then(function (r) {
      var list = r.customers || [];
      if (r.exact && list.length) choose(list[0]); else renderResults(list);
    });
  }
  scanInput.addEventListener("change", scan);
  scanInput.addEventListener("keydown", function (e) { if (e.key === "Enter") { e.preventDefault(); scan(); } });

  // ----- review -----
  function payLine(host, line) {
    var row = el("div", "result-row pay-part"), label = el("span");
    label.appendChild(el("span", "", line.label));
    if (line.detail) label.appendChild(el("small", "", line.detail));
    row.appendChild(label); row.appendChild(el("b", "", line.amount.toFixed(2) + " THB"));
    host.appendChild(row);
  }
  function review() {
    if (!custCode.value) { panel.hidden = true; return showError(text.errCustomerRequired); }
    if (!(Number(paddy.value) > 0)) { panel.hidden = true; return showError(text.alertEnterPaddy); }
    reviewBtn.disabled = true;
    var data = new FormData(form);
    fetch(form.getAttribute("data-quote-url"), { method: "POST", body: data, credentials: "same-origin",
                                                 headers: { "X-CSRF-Token": data.get("csrf_token") } })
      .then(function (r) { return r.json().then(function (body) { return body; }, function () { return { ok: false, error: text.errServer }; }); })
      .then(function (q) {
        reviewBtn.disabled = false;
        if (!q.ok) { panel.hidden = true; reviewed = false; return showError(q.error || text.errServer); }
        showError("");
        byId("reg-pay-amount").textContent = q.total.toFixed(2) + " THB";
        var lines = byId("reg-pay-lines"); lines.textContent = "";
        q.lines.forEach(function (l) { payLine(lines, l); });
        byId("reg-receive-time").textContent = q.receive;
        byId("reg-sku-rice").textContent = q.outputs.rice.toFixed(2) + " kg";
        byId("reg-sku-bran").textContent = q.take.bran.toFixed(2) + " kg";       // what the customer takes home
        byId("reg-sku-broken").textContent = q.take.broken.toFixed(2) + " kg";
        byId("reg-sku-husk").textContent = q.outputs.husk.toFixed(2) + " kg";
        byId("reg-qr-preview-wrap").hidden = q.is_delivery;                       // the label is for take-away orders
        confirmBtn.textContent = q.is_delivery ? text.btnConfirmOrder : text.btnPrintQR;
        if (!q.is_delivery && window.AOILabel) window.AOILabel.render(byId("reg-qr-label"), q.label);
        panel.hidden = false; panel.classList.remove("stale"); staleNote.hidden = true; confirmBtn.disabled = false; reviewed = true;
        panel.scrollIntoView({ block: "nearest" });
      })
      .catch(function () { reviewBtn.disabled = false; showError(text.errServer); });
  }
  if (reviewBtn) reviewBtn.addEventListener("click", review);
  byId("reg-cancel-result-btn").addEventListener("click", function () { panel.hidden = true; reviewed = false; });
  // ----- Print QR: save the order, print its label, then ask where to go -----
  function askNext(result) {
    window.AOIModal.confirm(result.message + (result.list_url ? "\n\n" + result.question : "")).then(function (yes) {
      window.location.href = yes && result.list_url ? result.list_url : result.new_url;
    });
  }
  function printThenAsk(result) {
    if (result.is_delivery || !window.AOILabel) return askNext(result);       // delivery orders have no label
    window.AOILabel.render(byId("reg-qr-label"), result.label);              // the real queue number, not the preview
    var asked = false;
    var after = function () { if (!asked) { asked = true; askNext(result); } };
    if ("onafterprint" in window) window.addEventListener("afterprint", after, { once: true });
    window.print();
    if (!("onafterprint" in window)) after();
  }
  function save() {
    confirmBtn.disabled = true;
    var data = new FormData(form);
    fetch(form.getAttribute("action") || window.location.pathname, {
      method: "POST", body: data, credentials: "same-origin",
      headers: { "Accept": "application/json", "X-CSRF-Token": data.get("csrf_token") } })
      .then(function (r) { return r.json().then(function (body) { return body; }, function () { return { ok: false, error: text.errServer }; }); })
      .then(function (result) {
        if (!result.ok) { confirmBtn.disabled = false; return showError(result.error || text.errServer); }
        printThenAsk(result);
      })
      .catch(function () { confirmBtn.disabled = false; showError(text.errServer); });
  }
  form.addEventListener("submit", function (event) {
    event.preventDefault();
    // Enter in a field, or Print QR after a change: review first.
    if (!reviewed || confirmBtn.disabled) return review();
    save();
  });

  updateOutputs(); toggleDelivery();
})();
