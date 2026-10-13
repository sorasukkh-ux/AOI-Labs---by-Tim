/* Milling Production: live total of the four output ratios and the sample calculation.
   The server checks the same rule again before saving (models/rules/yield_rule.py). */
(function () {
  "use strict";
  var form = document.getElementById("mp-form");
  if (!form) return;
  var text = JSON.parse(form.getAttribute("data-text"));
  var keys = ["rice", "bran", "broken", "husk"];
  var totalRow = document.getElementById("mp-yield-total-row"), totalEl = document.getElementById("mp-yield-total");
  var errorEl = document.getElementById("mp-yield-error"), saveBtn = document.getElementById("mp-save-btn");
  var sample = document.getElementById("mp-sample-paddy");

  function read() {
    var out = {};
    keys.forEach(function (k) { var v = document.getElementById("mp-yield-" + k).value.trim(); out[k] = v === "" ? NaN : Number(v); });
    return out;
  }
  function check(y) {
    var inRange = true, sum = 0;
    keys.forEach(function (k) { var v = y[k]; if (isNaN(v) || v < 0 || v > 1) inRange = false; else sum += v; });
    var total = Math.round(sum * 10000) / 10000;
    return { inRange: inRange, total: total, ok: inRange && total <= 1 };
  }
  function render() {
    var y = read(), c = check(y), pct = (c.total * 100).toFixed(2);
    totalRow.classList.toggle("over", !c.ok);
    if (!c.inRange) {
      totalEl.textContent = "-";
      errorEl.textContent = text.mpErrRange;
    } else if (!c.ok) {
      totalEl.textContent = text.mpTotalOver.replace("{pct}", pct).replace("{over}", ((c.total - 1) * 100).toFixed(2));
      errorEl.textContent = text.mpErrTotal.replace("{pct}", pct);
    } else {
      totalEl.textContent = text.mpTotalOk.replace("{pct}", pct).replace("{loss}", ((1 - c.total) * 100).toFixed(2));
      errorEl.textContent = "";
    }
    errorEl.classList.toggle("show", !c.ok);
    if (saveBtn) saveBtn.disabled = !c.ok;
    var paddy = Number(sample.value) || 0;
    keys.forEach(function (k) {
      document.getElementById("mp-prev-" + k + "-kg").textContent = c.inRange ? (paddy * y[k]).toFixed(2) + " kg" : "-";
    });
  }
  form.addEventListener("input", render);
  sample.addEventListener("input", render);
  form.addEventListener("submit", function (event) { if (!check(read()).ok) event.preventDefault(); });
  render();
})();
