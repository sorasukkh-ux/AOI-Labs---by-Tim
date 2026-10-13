/* Inventory forms (VIEW layer): the design's operation type -> reason -> remark behaviour, and the
   transfer form's "available at this location" note. Hidden parts are disabled so they are not sent. */
(function () {
  "use strict";
  var INCREASE = ["I", "AI"], DECREASE = ["O", "AD"];

  document.querySelectorAll("form.inv-form").forEach(function (form) {
    var optype = form.querySelector('[name="OPE_TYPE"]');
    var remarkRow = form.querySelector(".inv-remark-row");
    function group() {
      var v = optype ? optype.value : "I";
      return INCREASE.indexOf(v) >= 0 ? "increase" : (DECREASE.indexOf(v) >= 0 ? "decrease" : "status");
    }
    function setShown(el, shown) {
      el.hidden = !shown;
      el.querySelectorAll("input, select").forEach(function (f) { f.disabled = !shown; });
    }
    function refresh() {
      var g = group();
      form.querySelectorAll("[data-op-show]").forEach(function (el) { setShown(el, el.getAttribute("data-op-show") === g); });
      var reason = null;
      form.querySelectorAll("[data-reason]").forEach(function (sel) { if (!sel.disabled && !sel.closest("[hidden]")) reason = sel; });
      if (remarkRow) setShown(remarkRow, !!reason && reason.value === "other");
      var qty = form.querySelector('[name="INV_QTY"]');
      if (qty && form.id === "inv-op-form") qty.required = g !== "status";      // a status change can take the whole location
    }
    form.addEventListener("change", refresh);
    refresh();
  });

  var from = document.querySelector("[data-transfer-from]"), note = document.getElementById("transfer-available-note");
  if (from && note) {
    var show = function () {
      var opt = from.options[from.selectedIndex];
      note.textContent = note.getAttribute("data-text") + ": " + Number(opt.getAttribute("data-qty")).toLocaleString(undefined, { minimumFractionDigits: 2 }) + " kg";
    };
    from.addEventListener("change", show); show();
  }
})();
