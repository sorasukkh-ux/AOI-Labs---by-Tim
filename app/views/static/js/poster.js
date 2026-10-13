/* LINE counter poster: the Print button (the QR is drawn by label.js). */
(function () {
  "use strict";
  var button = document.querySelector("[data-print]");
  if (button) button.addEventListener("click", function () { window.print(); });
})();
