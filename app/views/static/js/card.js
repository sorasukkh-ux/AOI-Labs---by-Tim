/* Customer QR card: the copies drop-down reloads the page (the page has no app.js).
   Update 48: Print also sends the card (text + QR picture) to the customer's LINE when "also send" is ticked;
   "Send to LINE only" sends without printing. It is sent once per page view. */
(function () {
  "use strict";
  var select = document.getElementById("cc-n");
  if (select) select.addEventListener("change", function () { select.form.submit(); });
  var form = document.getElementById("cc-line-form"), status = document.getElementById("cc-line-status");
  var printBtn = document.getElementById("cc-print"), sent = false;
  function say(text, bad) { if (!status) return; status.hidden = false; status.textContent = text; status.className = "cc-line-status" + (bad ? " bad" : " ok"); }
  function sendLine() {
    if (!form || sent) return Promise.resolve();
    sent = true;
    form.querySelector('input[name="PUBLIC_BASE"]').value = window.location.origin;
    return fetch(form.getAttribute("action"), { method: "POST", body: new FormData(form), credentials: "same-origin",
                                                headers: { "Accept": "application/json" } })
      .then(function (r) { return r.json().catch(function () { return { ok: false, message: "LINE: error " + r.status }; }); })
      .then(function (res) { say(res.message || "", !res.ok); if (!res.ok) sent = false; })
      .catch(function () { sent = false; say("LINE: no connection", true); });
  }
  if (printBtn) printBtn.addEventListener("click", function () {
    var tick = document.getElementById("cc-line-on");
    if (tick && tick.checked) sendLine();
    window.print();
  });
  var only = document.getElementById("cc-line-send");
  if (only) only.addEventListener("click", function () { sent = false; sendLine(); });
})();
