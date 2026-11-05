/* update 118 (Tim): price billboard settings save themselves - no Save button. A change waits 0.7 s (typing waits until
   the pause), then the form is sent by fetch (X-AOI-Fetch: 1, JSON answer); "Open price billboard" saves first. */
(function () {
  "use strict";
  var form = document.getElementById("bb-form"), pill = document.getElementById("bb-autosave");
  if (!form || !pill) return;
  var open = document.getElementById("bb-open-save"), timer = null, busy = null, dirty = false;
  function show(state, text) { pill.className = "bb-autosave " + state; pill.textContent = text; }
  function save() {
    clearTimeout(timer); timer = null;
    if (busy) { dirty = true; return busy; }
    dirty = false;
    show("saving", pill.getAttribute("data-saving"));
    busy = fetch(window.location.pathname + window.location.search, { method: "POST", body: new FormData(form), credentials: "same-origin",
                                                                       headers: { "X-AOI-Fetch": "1", "Accept": "application/json" } })
      .then(function (r) { return r.json(); })
      .then(function (d) {
        var now = new Date(), hm = ("0" + now.getHours()).slice(-2) + ":" + ("0" + now.getMinutes()).slice(-2) + ":" + ("0" + now.getSeconds()).slice(-2);
        if (d.ok) show("ok", pill.getAttribute("data-saved").replace("{time}", hm));
        else show("err", d.message || pill.getAttribute("data-failed"));
        return d.ok;
      })
      .catch(function () { show("err", pill.getAttribute("data-failed")); return false; })
      .then(function (ok) { busy = null; if (dirty) return save(); return ok; });
    return busy;
  }
  function later(ms) { clearTimeout(timer); timer = setTimeout(save, ms); show("wait", pill.getAttribute("data-saving")); }
  form.addEventListener("change", function () { later(700); });
  form.addEventListener("input", function (e) { if (e.target.matches("input[type=text], input[type=number], textarea")) later(1200); });
  form.addEventListener("submit", function (e) { e.preventDefault(); save(); });
  if (open) open.addEventListener("click", function (e) {
    if (!timer && !busy) return;                           // nothing waiting: open at once
    e.preventDefault();
    var w = window.open("about:blank", "_blank"), href = open.getAttribute("href");
    save().then(function () { if (w) w.location.href = href; else window.location.href = href; });
  });
})();
