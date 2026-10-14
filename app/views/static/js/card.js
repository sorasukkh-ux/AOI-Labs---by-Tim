/* Customer QR card: the copies drop-down reloads the page (the page has no app.js). */
(function () {
  "use strict";
  var select = document.getElementById("cc-n");
  if (select) select.addEventListener("change", function () { select.form.submit(); });
})();
