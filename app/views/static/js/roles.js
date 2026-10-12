/* Role Authorization: keeps the permission table consistent while it is edited.
   The same rule is applied again on the server (models/role.py normalise). */
(function () {
  "use strict";
  var boxes = Array.prototype.slice.call(document.querySelectorAll(".perm-check"));
  function row(menu) { return boxes.filter(function (b) { return b.getAttribute("data-menu") === menu; }); }
  boxes.forEach(function (box) {
    box.addEventListener("change", function () {
      var perm = box.getAttribute("data-perm"), same = row(box.getAttribute("data-menu"));
      // View off clears the other rights; any other right turns View on.
      if (perm === "view" && !box.checked) same.forEach(function (b) { b.checked = false; });
      if (perm !== "view" && box.checked) same.forEach(function (b) { if (b.getAttribute("data-perm") === "view") b.checked = true; });
    });
  });
  function setAll(value) { boxes.forEach(function (b) { if (!b.disabled) b.checked = value; }); }
  var allow = document.getElementById("role-allow-all-btn"), clear = document.getElementById("role-clear-all-btn");
  if (allow) allow.addEventListener("click", function () { setAll(true); });
  if (clear) clear.addEventListener("click", function () { setAll(false); });
})();
