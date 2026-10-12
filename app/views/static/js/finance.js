/* Margin Maintenance (VIEW layer): search the material cards, and "Apply to selected" copies the bulk
   targets and ranges into the ticked cards (saved with the Save button). */
(function () {
  "use strict";
  var search = document.querySelector("[data-card-search]");
  if (search) {
    var cards = document.querySelectorAll(search.getAttribute("data-card-search"));
    var none = document.getElementById("no-results");
    search.addEventListener("input", function () {
      var q = search.value.trim().toLowerCase(), shown = 0;
      cards.forEach(function (c) {
        var hit = !q || c.getAttribute("data-search").indexOf(q) >= 0 || c.textContent.toLowerCase().indexOf(q) >= 0;
        c.hidden = !hit; if (hit) shown++;
      });
      if (none) none.hidden = shown > 0;
    });
  }
  var apply = document.getElementById("margin-bulk-apply");
  if (apply) apply.addEventListener("click", function () {
    var bulk = {};
    document.querySelectorAll("[data-bulk]").forEach(function (i) { if (i.value.trim() !== "") bulk[i.getAttribute("data-bulk")] = i.value.trim(); });
    document.querySelectorAll(".margin-card").forEach(function (card) {
      var pick = card.querySelector(".margin-pick");
      if (!pick || !pick.checked) return;
      Object.keys(bulk).forEach(function (k) {
        var field = card.querySelector('[data-field="' + k + '"]');
        if (field) field.value = bulk[k];
      });
    });
  });
})();
