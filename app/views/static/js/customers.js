/* Customer Registration: before saving, the name is checked (it must not be registered yet) and a pop-up shows
   the details to confirm. The server checks again when the form arrives. */
(function () {
  "use strict";
  var form = document.getElementById("cust-form");
  if (!form || form.getAttribute("data-mode") === "view" || !window.AOIModal) return;
  var text = JSON.parse(form.getAttribute("data-text"));
  var confirmed = false;
  function value(name) { var f = form.querySelector('[name="' + name + '"]'); return f ? f.value.trim() : ""; }
  function row(label, val) {
    var r = document.createElement("div"); r.className = "od-row";
    var a = document.createElement("span"); a.className = "od-label"; a.textContent = label;
    var b = document.createElement("span"); b.className = "od-value"; b.textContent = val || "-";
    r.appendChild(a); r.appendChild(b); return r;
  }
  form.addEventListener("submit", function (event) {
    if (confirmed) return;
    event.preventDefault();
    var name = value("CUST_NAME");
    if (!name || !value("MOBILE_NO") || !value("ADDRESS")) { window.AOIModal.alert(text.required); return; }
    var url = form.getAttribute("data-check-url") + "?name=" + encodeURIComponent(name) + "&code=" + encodeURIComponent(form.getAttribute("data-code") || "");
    fetch(url, { credentials: "same-origin" })
      .then(function (r) { return r.json(); })
      .then(function (res) {
        if (!res.ok) {
          window.AOIModal.alert(text.dup.replace("{name}", res.dup.name).replace("{code}", res.dup.code));
          return;
        }
        var box = document.createElement("div"); box.className = "order-details cust-confirm";
        var title = document.createElement("p"); title.className = "od-title"; title.textContent = text.title; box.appendChild(title);
        var note = document.createElement("p"); note.className = "cap-note"; note.textContent = text.note; box.appendChild(note);
        var cc = form.querySelector('[name="MOBILE_CC"]');
        box.appendChild(row(text.name, name));
        box.appendChild(row(text.phone, (cc ? cc.value + " " : "") + value("MOBILE_NO")));
        box.appendChild(row(text.address, value("ADDRESS")));
        box.appendChild(row(text.distance, value("DISTANCE_KM")));
        box.appendChild(row(text.line, value("LINE_ID")));
        var holder = document.createElement("div"); holder.appendChild(box);
        window.AOIModal.confirmHtml(holder.innerHTML).then(function (yes) {
          if (!yes) return;
          confirmed = true;
          form.submit();
        });
      })
      .catch(function () { window.AOIModal.alert(text.server); });
  });
})();
