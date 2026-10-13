/* Shared behaviour for every page (VIEW layer). No inline scripts are used anywhere.
   - AOIModal: the pop-up from the design prototype (message, Yes/No question, text input, HTML content)
   - buttons and selects that ask before they send their form
   - notices from the server shown in the pop-up
   - selects whose option opens a form in the pop-up (the inventory inbound form)
   - the order details pop-up on the milling order list */
(function () {
  "use strict";
  var overlay = document.getElementById("app-modal-overlay");
  var messageEl = document.getElementById("app-modal-message"), inputEl = document.getElementById("app-modal-input");
  var yesBtn = document.getElementById("app-modal-yes"), noBtn = document.getElementById("app-modal-no");
  var actionsEl = document.getElementById("app-modal-actions");
  var pending = null;

  function close(result) {
    overlay.classList.remove("show");
    actionsEl.hidden = false;
    var done = pending; pending = null;
    if (done) done(result);
  }
  // options: {text | html | node, input: {value}, question: true}; a node brings its own buttons
  function open(options) {
    return new Promise(function (resolve) {
      if (pending) pending(null);
      pending = resolve;
      messageEl.classList.toggle("html", !!(options.html || options.node));
      if (options.node) { messageEl.innerHTML = ""; messageEl.appendChild(options.node); }
      else if (options.html) messageEl.innerHTML = options.html; else messageEl.textContent = options.text || "";
      actionsEl.hidden = !!options.node;
      inputEl.hidden = !options.input;
      if (options.input) inputEl.value = options.input.value || "";
      var ask = !!options.question || !!options.input;
      noBtn.hidden = !ask;
      yesBtn.textContent = ask && !options.input ? overlay.getAttribute("data-yes") : overlay.getAttribute("data-ok");
      noBtn.textContent = options.input ? overlay.getAttribute("data-cancel") : overlay.getAttribute("data-no");
      overlay.classList.add("show");
      var first = options.node && options.node.querySelector("input:not([type=hidden]), select");
      (first || (options.input ? inputEl : yesBtn)).focus();
    });
  }
  yesBtn.addEventListener("click", function () { close(inputEl.hidden ? true : inputEl.value); });
  noBtn.addEventListener("click", function () { close(inputEl.hidden ? false : null); });
  inputEl.addEventListener("keydown", function (e) { if (e.key === "Enter") { e.preventDefault(); close(inputEl.value); } });
  document.addEventListener("keydown", function (e) { if (e.key === "Escape" && pending) close(inputEl.hidden ? false : null); });

  window.AOIModal = {
    alert: function (text) { return open({ text: text }); },
    confirm: function (text) { return open({ text: text, question: true }); },
    prompt: function (text, value) { return open({ text: text, input: { value: value } }); },
    html: function (html) { return open({ html: html }); },
    confirmHtml: function (html) { return open({ html: html, question: true }); },
    form: function (node) { return open({ node: node }); },
    close: function () { close(null); },
    message: messageEl
  };

  // ----- ask first, then send -----
  function setHidden(form, name, value) {
    var field = form.querySelector('input[type="hidden"][name="' + name + '"]');
    if (!field) { field = document.createElement("input"); field.type = "hidden"; field.name = name; form.appendChild(field); }
    field.value = value;
  }
  // Runs data-confirm and data-prompts of `source`; resolves false when the user backs out.
  function askAll(source, form) {
    var chain = Promise.resolve(true), confirmText = source.getAttribute("data-confirm");
    if (confirmText) chain = chain.then(function () { return window.AOIModal.confirm(confirmText); });
    var prompts = source.getAttribute("data-prompts");
    (prompts ? JSON.parse(prompts) : []).forEach(function (p) {
      chain = chain.then(function (ok) {
        if (!ok) return false;
        return ask(p, p.label);
      });
    });
    return chain;

    // a prompt with "required" asks again until something is entered (or the user cancels)
    function ask(p, label) {
      return window.AOIModal.prompt(label, p.value || "").then(function (answer) {
        if (answer === null) return false;
        if (p.required && !answer.trim()) return ask(p, p.label + "\n\n" + (p.required_text || ""));
        setHidden(form, p.name, answer.trim());
        return true;
      });
    }
  }
  // <option data-form="template-id">: the option opens that form in the pop-up instead of sending at once.
  function openForm(templateId) {
    var tpl = document.getElementById(templateId);
    var node = tpl.content.firstElementChild.cloneNode(true);
    node.addEventListener("click", function (e) { if (e.target.closest("[data-modal-close]")) window.AOIModal.close(); });
    return window.AOIModal.form(node);
  }
  document.addEventListener("click", function (event) {
    var button = event.target.closest("button[data-confirm], button[data-prompts]");
    if (!button || !button.form) return;
    event.preventDefault();
    var form = button.form;
    if (overlay.contains(form)) {            // a button inside the pop-up: keep its form alive while the pop-up asks the question
      form.hidden = true;
      document.body.appendChild(form);
    }
    askAll(button, form).then(function (ok) {
      if (!ok) return;
      if (button.name) setHidden(form, button.name, button.value);
      form.submit();
    });
  });
  // <select data-autosubmit> sends its form when the value changes (after any question on the chosen option).
  document.querySelectorAll("select[data-autosubmit]").forEach(function (select) {
    var current = select.value;
    select.addEventListener("change", function () {
      var option = select.options[select.selectedIndex];
      if (option.hasAttribute("data-form")) {          // closed without saving: back to the current value
        openForm(option.getAttribute("data-form")).then(function () { select.value = current; });
        return;
      }
      askAll(option, select.form).then(function (ok) {
        if (ok) select.form.submit(); else select.value = current;
      });
    });
  });
  // Print Billing: only the receipt is printed (see @media print in app.css)
  document.addEventListener("click", function (event) {
    if (!event.target.closest("[data-print-receipt]")) return;
    document.body.classList.add("print-receipt");
    var done = function () { document.body.classList.remove("print-receipt"); };
    if ("onafterprint" in window) window.addEventListener("afterprint", done, { once: true });
    window.print();
    if (!("onafterprint" in window)) done();
  });
  document.querySelectorAll("[data-print]").forEach(function (button) {
    button.addEventListener("click", function () { window.print(); });
  });

  // ----- notices from the server: shown in the pop-up, like the prototype's alerts -----
  var notices = Array.prototype.slice.call(document.querySelectorAll(".notice[data-flash]"));
  var back = document.querySelector("[data-return-url]");      // saved from a screen opened by another screen
  var noticeText = notices.map(function (n) { return n.textContent.trim(); }).join("\n\n");
  notices.forEach(function (n) { n.hidden = true; });
  if (back) {
    var question = back.getAttribute("data-return-text");
    window.AOIModal.confirm(noticeText ? noticeText + "\n\n" + question : question).then(function (yes) {
      if (yes) window.location.href = back.getAttribute("href");
    });
  } else if (notices.length) {
    window.AOIModal.alert(noticeText);
  }

  // ----- changing the customer in the editable order details -----
  function custSearch(button) {
    var box = button.closest(".order-details"), out = box.querySelector("[data-cust-results]");
    var q = box.querySelector("[data-cust-query]").value.trim();
    fetch(button.getAttribute("data-cust-search") + "?q=" + encodeURIComponent(q), { credentials: "same-origin" })
      .then(function (r) { return r.ok ? r.json() : { customers: [] }; })
      .catch(function () { return { customers: [] }; })
      .then(function (data) {
        out.textContent = ""; out.hidden = false;
        var list = data.customers || [];
        if (!list.length) {
          var p = document.createElement("p"); p.className = "hint home-empty"; p.textContent = button.getAttribute("data-empty");
          out.appendChild(p); return;
        }
        list.forEach(function (c) {
          var row = document.createElement("button"); row.type = "button"; row.className = "demo-row cust-row";
          var name = document.createElement("span"); name.className = "demo-id"; name.textContent = c.name;
          var meta = document.createElement("span"); meta.className = "demo-meta"; meta.textContent = c.code + " · " + c.mobile;
          row.appendChild(name); row.appendChild(meta);
          row.addEventListener("click", function () {
            box.querySelector("[data-cust-code-field]").value = c.code;
            box.querySelector("[data-cust-name]").textContent = c.name;
            box.querySelector("[data-cust-mobile]").textContent = c.mobile || "-";
            var addr = box.querySelector("[data-cust-address]"), km = box.querySelector("[data-cust-distance]");
            if (addr && c.address) addr.value = c.address;
            if (km && c.distance !== null && c.distance !== undefined) km.value = c.distance;
            out.querySelectorAll(".cust-row").forEach(function (r) { r.classList.remove("selected"); });
            row.classList.add("selected");
          });
          out.appendChild(row);
        });
      });
  }
  document.addEventListener("click", function (event) {
    var button = event.target.closest("[data-cust-search]");
    if (button) { event.preventDefault(); custSearch(button); }
  });
  document.addEventListener("keydown", function (event) {
    if (event.key !== "Enter" || !event.target.matches("[data-cust-query]")) return;
    event.preventDefault();
    custSearch(event.target.closest(".order-details").querySelector("[data-cust-search]"));
  });

  // ----- screens that refresh themselves (Service Status on the TV) -----
  var auto = document.querySelector("[data-auto-refresh]");
  if (auto) setTimeout(function () { if (!overlay.classList.contains("show")) window.location.reload(); },
                       (Number(auto.getAttribute("data-auto-refresh")) || 30) * 1000);

  // ----- order details pop-up -----
  document.addEventListener("click", function (event) {
    var link = event.target.closest("a[data-popup]");
    if (!link) return;
    event.preventDefault();
    var href = link.getAttribute("href");
    fetch(href + (href.indexOf("?") < 0 ? "?" : "&") + "popup=1", { credentials: "same-origin" })
      .then(function (r) { if (!r.ok) throw new Error(r.status); return r.text(); })
      .then(function (html) {
        window.AOIModal.html(html);
        if (window.AOILabel) window.AOILabel.scan(window.AOIModal.message);
      })
      .catch(function () { window.location.href = link.getAttribute("href"); });
  });
})();

/* TV display link (Milling Maintenance): built from the address in the browser, so it is right behind any
   forwarding (GitHub Codespaces, a proxy) where the server only sees its own local address. */
(function () {
  var inputs = document.querySelectorAll("[data-tv-path]");
  for (var i = 0; i < inputs.length; i++) inputs[i].value = window.location.origin + inputs[i].getAttribute("data-tv-path");
})();

/* Location ID (BAY-BANK-LEVEL): type the digits only, the dashes are added as you type. 00001001 -> 00-001-001. */
(function () {
  function formatLoc(value) {
    var d = String(value || "").replace(/\D/g, "").slice(0, 8);
    if (d.length > 5) return d.slice(0, 2) + "-" + d.slice(2, 5) + "-" + d.slice(5);
    if (d.length > 2) return d.slice(0, 2) + "-" + d.slice(2);
    return d;
  }
  window.AOIFormatLoc = formatLoc;
  document.addEventListener("input", function (event) {
    var input = event.target;
    if (!input || !input.hasAttribute || !input.hasAttribute("data-loc")) return;
    var formatted = formatLoc(input.value);
    if (formatted !== input.value) input.value = formatted;
  });
})();

/* Start milling (In process): the scanned QR must be this order's label. A handheld scanner types the code and
   presses Enter, which would send the form; the check here stops a wrong bag before it reaches the server. */
(function () {
  document.addEventListener("submit", function (event) {
    var form = event.target;
    if (!form.classList || !form.classList.contains("scan-start-form")) return;
    var input = form.querySelector('input[name="SCAN_QR"]');
    var code = (input.value || "").replace(/\s+/g, "").toUpperCase();
    var expect = input.getAttribute("data-expect").toUpperCase();
    if (code !== expect && code !== expect.replace(/^AOI-/, "")) {
      event.preventDefault();
      input.setCustomValidity(input.getAttribute("data-wrong"));
      input.reportValidity();
      input.select();
    }
  }, true);
  document.addEventListener("input", function (event) {
    if (event.target.name === "SCAN_QR" && event.target.setCustomValidity) event.target.setCustomValidity("");
  });
  // the scanner's Enter goes on to the weight instead of sending the form
  document.addEventListener("keydown", function (event) {
    if (event.key !== "Enter" || event.target.name !== "SCAN_QR") return;
    var kg = event.target.form && event.target.form.querySelector('input[name="ACT_PADDY"]');
    if (!kg) return;
    event.preventDefault();
    kg.focus(); kg.select();
  });
})();
