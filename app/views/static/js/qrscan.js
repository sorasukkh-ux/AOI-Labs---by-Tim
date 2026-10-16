/* ===== Scan a QR with the camera of the iPad / phone (update 37) =====
   Every <input data-scan> gets a camera button. The camera picture is read in the page (qrdecode.js, nothing is
   sent anywhere); the same text read twice in a row fills the field, then:
     data-scan=""        the field's own input / change handlers run (e.g. the customer look-up on New Order)
     data-scan="submit"  the field's form is sent (list searches)
     data-scan="enter"   as if Enter was pressed (the customer search in the order details)
   The browser allows the camera only on an https:// link (or localhost); otherwise the button explains it. */
(function () {
  "use strict";
  var box = document.getElementById("qr-scan-overlay");
  if (!box || !window.AOIQRDecode) return;
  var text = function (k) { return box.getAttribute("data-" + k) || ""; };
  var video = box.querySelector("video"), status = box.querySelector("[data-scan-status]");
  var canvas = document.createElement("canvas"), ctx = canvas.getContext("2d", { willReadFrequently: true });
  var stream = null, target = null, timer = null, last = null, busy = false;
  var canCamera = !!(navigator.mediaDevices && navigator.mediaDevices.getUserMedia);

  var ICON = '<svg aria-hidden="true" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">' +
    '<path d="M4 8V5a1 1 0 0 1 1-1h3M16 4h3a1 1 0 0 1 1 1v3M20 16v3a1 1 0 0 1-1 1h-3M8 20H5a1 1 0 0 1-1-1v-3"/>' +
    '<rect x="8" y="8" width="3" height="3"/><rect x="13" y="8" width="3" height="3"/><rect x="8" y="13" width="3" height="3"/><path d="M13 13h3v3"/></svg>';

  function addButton(input) {
    if (input.getAttribute("data-scan-ready")) return;
    input.setAttribute("data-scan-ready", "1");
    var b = document.createElement("button");
    b.type = "button"; b.className = "scan-btn"; b.innerHTML = ICON;
    b.title = text("title"); b.setAttribute("aria-label", text("title"));
    b.addEventListener("click", function () { open(input); });
    input.insertAdjacentElement("afterend", b);
    var wrap = input.parentElement;
    if (wrap) wrap.classList.add("has-scan");
  }
  function scanAll(rootNode) {
    if (!rootNode.querySelectorAll) return;
    if (rootNode.matches && rootNode.matches("input[data-scan]")) addButton(rootNode);
    rootNode.querySelectorAll("input[data-scan]").forEach(addButton);
  }
  scanAll(document);
  new MutationObserver(function (list) {                 // pop-ups loaded later (order details)
    list.forEach(function (m) { m.addedNodes.forEach(function (n) { if (n.nodeType === 1) scanAll(n); }); });
  }).observe(document.body, { childList: true, subtree: true });

  function say(msg, cls) { status.textContent = msg; status.className = "qs-status" + (cls ? " " + cls : ""); }

  var frozen = [];
  function freeze(on) {
    if (on) {
      Array.prototype.forEach.call(document.body.children, function (node) {
        if (node === box || node.tagName === "SCRIPT" || node.inert) return;
        node.inert = true; frozen.push(node);
      });
    } else { frozen.forEach(function (node) { node.inert = false; }); frozen = []; }
  }
  function open(input) {
    target = input; last = null;
    if (document.activeElement && document.activeElement.blur) document.activeElement.blur();
    box.classList.add("show"); box.classList.remove("done"); freeze(true);
    document.documentElement.classList.add("qs-open");
    if (!window.isSecureContext) { say(text("https"), "err"); return; }
    if (!canCamera) { say(text("nocam"), "err"); return; }
    say(text("starting"));
    navigator.mediaDevices.getUserMedia({ audio: false, video: { facingMode: { ideal: "environment" }, width: { ideal: 1280 }, height: { ideal: 720 } } })
      .then(function (s) {
        if (!box.classList.contains("show")) { s.getTracks().forEach(function (t) { t.stop(); }); return; }
        stream = s; video.srcObject = s;
        video.setAttribute("playsinline", ""); video.muted = true;
        var p = video.play(); if (p && p.catch) p.catch(function () {});
        say(text("aim"));
        timer = setInterval(tick, 180);
      })
      .catch(function (err) {
        say(err && (err.name === "NotAllowedError" || err.name === "SecurityError") ? text("denied") : text("nocam"), "err");
      });
  }
  function close() {
    clearInterval(timer); timer = null;
    if (stream) stream.getTracks().forEach(function (t) { t.stop(); });
    stream = null; video.srcObject = null;
    box.classList.remove("show"); freeze(false);
    document.documentElement.classList.remove("qs-open");
  }
  function tick() {
    if (busy || !video.videoWidth) return;
    busy = true;
    try {
      var vw = video.videoWidth, vh = video.videoHeight, scale = Math.min(1, 720 / Math.max(vw, vh));
      canvas.width = Math.round(vw * scale); canvas.height = Math.round(vh * scale);
      ctx.drawImage(video, 0, 0, canvas.width, canvas.height);
      var found = window.AOIQRDecode.decode(ctx.getImageData(0, 0, canvas.width, canvas.height));
      if (found) {
        if (found === last) done(found);
        last = found;
      }
    } catch (e) { /* the next frame */ }
    busy = false;
  }
  function done(value) {
    clearInterval(timer); timer = null;
    box.classList.add("done"); say(value, "ok");
    if (navigator.vibrate) navigator.vibrate(60);
    var input = target;
    setTimeout(function () {
      close();
      fill(input, value.trim());
    }, 450);
  }
  function fill(input, value) {
    if (!input) return;
    input.value = value;
    input.dispatchEvent(new Event("input", { bubbles: true }));
    var mode = input.getAttribute("data-scan");
    if (mode === "submit" && input.form) {
      if (input.form.requestSubmit) input.form.requestSubmit(); else input.form.submit();
    } else if (mode === "enter") {
      input.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true, cancelable: true }));
    } else {
      input.dispatchEvent(new Event("change", { bubbles: true }));
    }
    // no focus() here: on the iPad it opens the keyboard and its next / previous field arrows
  }
  box.addEventListener("click", function (e) { if (e.target.closest("[data-scan-close]")) close(); });
  document.addEventListener("keydown", function (e) { if (e.key === "Escape" && box.classList.contains("show")) close(); });
  document.addEventListener("visibilitychange", function () { if (document.hidden && box.classList.contains("show")) close(); });
  window.AOIScan = { open: open, close: close, fill: fill };
})();
