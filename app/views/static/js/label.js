/* The printed queue label and the small QR code of the order details, drawn from data attributes.
   Layout and wording follow the design prototype (the label text is fixed English, as printed). */
(function () {
  "use strict";
  function el(tag, cls, text) {
    var node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text !== undefined) node.textContent = text;
    return node;
  }
  function qrSvg(text) {
    return window.AOIQR.toSvg(window.AOIQR.encode(text), "QR " + text.replace(/[^A-Za-z0-9-]/g, ""));
  }
  // data: {no, customer, pay, receive, qr, date, time, addrEn, addrTh, tel, email, logo}
  function renderLabel(host, data) {
    host.textContent = "";
    var head = el("div", "ql-head");
    if (data.logo) { var img = el("img", "ql-logo"); img.src = data.logo; img.alt = "AOI"; head.appendChild(img); }
    var svc = el("div", "ql-svc"); svc.appendChild(el("span", "", "MILLING")); svc.appendChild(el("b", "", data.no)); head.appendChild(svc);
    host.appendChild(head);
    var addr = el("div", "ql-addr"); addr.appendChild(document.createTextNode(data.addrEn)); addr.appendChild(el("br")); addr.appendChild(document.createTextNode(data.addrTh));
    host.appendChild(addr);
    host.appendChild(el("div", "ql-rule"));
    host.appendChild(el("div", "ql-line", "Customer : " + data.customer));
    host.appendChild(el("div", "ql-line", "Payment: " + data.pay + " THB"));
    host.appendChild(el("div", "ql-line small", "Receive: " + data.receive));
    var qr = el("div", "ql-qr");
    try { qr.innerHTML = qrSvg(data.qr); } catch (error) { qr.textContent = data.qr; }
    host.appendChild(qr);
    host.appendChild(el("div", "ql-code", data.qr));
    var foot = el("div", "ql-foot"), contact = el("div");
    contact.appendChild(document.createTextNode("Tel: " + data.tel)); contact.appendChild(el("br")); contact.appendChild(document.createTextNode("Email: " + data.email));
    var date = el("div", "ql-date"); date.appendChild(document.createTextNode(data.date)); date.appendChild(el("br")); date.appendChild(document.createTextNode(data.time));
    foot.appendChild(contact); foot.appendChild(date); host.appendChild(foot);
  }
  // Draws every label ([data-label]) and details QR ([data-qr]) found inside `root`.
  function scan(root) {
    if (!window.AOIQR) return;
    root.querySelectorAll("[data-label]").forEach(function (host) { renderLabel(host, JSON.parse(host.getAttribute("data-label"))); });
    root.querySelectorAll(".od-qr[data-qr]").forEach(function (host) {
      var text = host.getAttribute("data-qr");
      host.textContent = "";
      var box = el("div"); try { box.innerHTML = qrSvg(text); } catch (error) { box.textContent = ""; }
      if (box.firstChild) host.appendChild(box.firstChild);
      host.appendChild(el("span", "", text));
    });
  }
  window.AOILabel = { render: renderLabel, scan: scan };
  scan(document);
})();
