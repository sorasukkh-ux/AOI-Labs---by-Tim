/* update 122: product transformation formulas - wait for the AI analysis, show the form again after an error */
(function () {
  "use strict";
  var box = document.getElementById("fm-job-run");
  if (box) {
    var url = box.getAttribute("data-poll"), back = box.getAttribute("data-back"), tries = 0;
    var poll = function () {
      tries++;
      fetch(url, { credentials: "same-origin", headers: { "Accept": "application/json" } })
        .then(function (r) { return r.json(); })
        .then(function (d) {
          if (d.state === "running" && tries < 220) { setTimeout(poll, 4000); return; }
          window.location.href = back;
        })
        .catch(function () { if (tries < 220) setTimeout(poll, 6000); });
    };
    setTimeout(poll, 4000);
  }
  var form = document.querySelector("#fm-edit[data-scroll]");
  if (form) form.scrollIntoView({ block: "start" });
})();
