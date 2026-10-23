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
  var pending = null, inerted = [];

  // update 37: while the pop-up is open the page behind it is "inert" - the iPad keyboard arrows (next / previous
  // field) and Tab cannot jump to a drop-down behind the pop-up (it opened the priority list over Start milling)
  // update 40: everything except the path from <body> to the pop-up, so it also works when the pop-up has been
  // moved into a full-screen element (SCADA full screen)
  function freeze(on) {
    if (on) {
      if (inerted.length) return;
      for (var keep = overlay; keep && keep !== document.body && keep.parentElement; keep = keep.parentElement) {
        Array.prototype.forEach.call(keep.parentElement.children, function (node) {
          if (node === keep || node.id === "qr-scan-overlay" || node.id === "app-modal-overlay" || node.tagName === "SCRIPT" || node.inert) return;
          node.inert = true; node.setAttribute("aria-hidden", "true"); inerted.push(node);
        });
      }
    } else {
      inerted.forEach(function (node) { node.inert = false; node.removeAttribute("aria-hidden"); });
      inerted = [];
    }
  }

  // update 40: in full screen only the full-screen element is drawn - the pop-ups (this one and the camera scanner)
  // move into it, and back to <body> when full screen ends; otherwise the ON / OFF confirmation was invisible
  function hostOverlays() {
    var host = document.fullscreenElement || document.webkitFullscreenElement || document.body;
    [overlay, document.getElementById("qr-scan-overlay")].forEach(function (node) {
      if (node && node.parentElement !== host) host.appendChild(node);
    });
    if (overlay.classList.contains("show")) { freeze(false); freeze(true); }
  }
  document.addEventListener("fullscreenchange", hostOverlays);
  document.addEventListener("webkitfullscreenchange", hostOverlays);

  function close(result) {
    freeze(false);
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
      freeze(true);
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
  // update 47: forms that send a LINE picture tell the server the address the staff use (https, reachable by LINE)
  document.addEventListener("submit", function (event) {
    var f = event.target, field = f.querySelector && f.querySelector("input[data-public-base]");
    if (field) field.value = window.location.origin;
  }, true);
  // update 39: <button data-open-form="template-id"> opens that form in the pop-up (e.g. Edit items of a take-home order)
  document.addEventListener("click", function (event) {
    var opener = event.target.closest("button[data-open-form]");
    if (opener) { event.preventDefault(); openForm(opener.getAttribute("data-open-form")); }
  });
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
      if (!form.isConnected) { form.hidden = true; document.body.appendChild(form); }   // redrawn meanwhile: still sent
      // update 40: a form marked data-ajax is sent by its page's script (submit event), e.g. the SCADA in full screen
      if (form.hasAttribute("data-ajax") && form.requestSubmit) form.requestSubmit(); else form.submit();
    });
  });
  // <select data-autosubmit> sends its form when the value changes (after any question on the chosen option).
  document.querySelectorAll("select[data-autosubmit]").forEach(function (select) {
    var current = select.value;
    select.addEventListener("change", function () {
      var option = select.options[select.selectedIndex];
      // update 54: on the iPad the drop-down is still closing when "change" fires - a pop-up opened at that moment
      // could be lost (the choice stayed on the screen and nothing happened). Let the drop-down go first.
      select.blur();
      setTimeout(function () {
        if (option.hasAttribute("data-form")) {          // closed without saving: back to the current value
          openForm(option.getAttribute("data-form")).then(function () { select.value = current; });
          return;
        }
        askAll(option, select.form).then(function (ok) {
          if (ok) select.form.submit(); else select.value = current;
        });
      }, 60);
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
  // update 62: Send LINE in the receipt pop-up (the pop-up is filled later, so the click is caught on the page)
  document.addEventListener("click", function (event) {
    var button = event.target.closest("[data-receipt-line]");
    if (!button || button.disabled) return;
    var wrap = button.closest(".receipt-wrap") || document;
    var status = wrap.querySelector("[data-receipt-line-status]");
    var say = function (text, bad) {
      if (!status) return;
      status.hidden = false; status.textContent = text; status.className = "rc-line-status " + (bad ? "bad" : "ok");
    };
    var data = new FormData();
    data.set("ids", button.getAttribute("data-ids") || "");
    data.set("PUBLIC_BASE", window.location.origin);
    data.set("csrf_token", button.getAttribute("data-csrf") || "");
    button.disabled = true;
    fetch(button.getAttribute("data-receipt-line"), { method: "POST", body: data, credentials: "same-origin",
          headers: { "Accept": "application/json", "X-CSRF-Token": button.getAttribute("data-csrf") || "" } })
      .then(function (r) { return r.json().catch(function () { return { ok: false, message: "LINE: error " + r.status }; }); })
      .then(function (res) { say(res.message || "", !res.ok); button.disabled = false; })
      .catch(function () { say("LINE: no connection", true); button.disabled = false; });
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

  // ----- update 65: notices for the staff - a customer linked their LINE account. Asked every 20 s; the last time
  // asked is kept in this browser, so a notice is not lost while the operator moves to another page. -----
  var noticeBox = document.getElementById("staff-notices");
  if (noticeBox && window.fetch) {
    var KEY = "aoi-notice-since", since = noticeBox.getAttribute("data-since");
    try {
      var kept = window.localStorage.getItem(KEY);
      if (kept && kept < since && Date.parse(since) - Date.parse(kept) < 30 * 60000) since = kept;
    } catch (e) { /* private mode: from now on */ }
    var fill = function (text, item) {
      return text.replace("{name}", item.name).replace("{code}", item.code).replace("{when}", item.when).replace("{via}", item.via)
                 .replace("{line}", item.line_name ? " (" + item.line_name + ")" : "");
    };
    var show = function (item) {
      var card = document.createElement("div");
      card.className = "staff-notice";
      card.setAttribute("data-notice-cust", item.code);
      var title = document.createElement("b"); title.textContent = "\uD83D\uDD17 " + noticeBox.getAttribute("data-title");
      var body = document.createElement("p"); body.textContent = fill(noticeBox.getAttribute("data-text"), item);
      var row = document.createElement("div"); row.className = "sn-actions";
      if (item.url) {
        var open = document.createElement("a"); open.className = "action small primary"; open.href = item.url;
        open.textContent = noticeBox.getAttribute("data-open"); row.appendChild(open);
      }
      var close = document.createElement("button"); close.type = "button"; close.className = "action small";
      close.textContent = noticeBox.getAttribute("data-close");
      close.addEventListener("click", function () { card.remove(); });
      row.appendChild(close);
      card.appendChild(title); card.appendChild(body); card.appendChild(row);
      noticeBox.appendChild(card);
    };
    var askNotices = function () {
      if (document.hidden) return;
      fetch(noticeBox.getAttribute("data-url") + "?since=" + encodeURIComponent(since), { credentials: "same-origin", cache: "no-store",
            headers: { "Accept": "application/json" } })
        .then(function (r) { return r.ok ? r.json() : null; })
        .then(function (res) {
          if (!res) return;
          res.items.forEach(function (item) {
            if (!noticeBox.querySelector('[data-notice-cust="' + item.code + '"]')) show(item);
          });
          since = res.now;
          try { window.localStorage.setItem(KEY, since); } catch (e) { /* ignore */ }
        })
        .catch(function () { /* server away: asked again next round */ });
    };
    setTimeout(askNotices, 1500);
    setInterval(askNotices, 20000);
  }

  // ----- update 63: live dashboards (AI). Every N seconds the same page is fetched again and each [data-live] part
  // whose content changed is swapped in place (no reload, the scroll position stays). Paused while the tab is hidden,
  // a pop-up is open, the user types in a field or has changed a field that is not sent yet. The bar shows the time of
  // the last update, or that the server cannot be reached (a stopped server shows here within one round). -----
  var liveRoot = document.querySelector("[data-live-refresh]");
  if (liveRoot && window.fetch && window.DOMParser) {
    var liveEvery = Math.max(5, Number(liveRoot.getAttribute("data-live-refresh")) || 15) * 1000;
    var liveBar = document.querySelector("[data-live-clock]");
    var liveBusy = false, lostSince = null;
    var hhmmss = function (d) { return [d.getHours(), d.getMinutes(), d.getSeconds()].map(function (n) { return ("0" + n).slice(-2); }).join(":"); };
    var setBar = function (state, text) {
      if (!liveBar) return;
      liveBar.className = "live-bar live-" + state;
      liveBar.querySelector("span").textContent = text;
    };
    var dirty = function (part) {
      return Array.prototype.some.call(part.querySelectorAll("input, select, textarea"), function (f) {
        if (f.type === "hidden" || f.type === "submit" || f.type === "button") return false;
        if (f.type === "checkbox" || f.type === "radio") return f.checked !== f.defaultChecked;
        if (f.tagName === "SELECT") {                     // no option marked selected: the first one is the default
          var opts = Array.prototype.slice.call(f.options), marked = opts.some(function (o) { return o.defaultSelected; });
          return opts.some(function (o, i) { return o.selected !== (marked ? o.defaultSelected : i === 0 && !f.multiple); });
        }
        return f.value !== f.defaultValue;
      });
    };
    var paused = function () {
      var a = document.activeElement;
      return document.hidden || overlay.classList.contains("show") || !!(a && a.matches && a.matches("input, select, textarea"));
    };
    var liveTick = function () {
      if (liveBusy) return;
      if (paused()) { if (!lostSince && liveBar) setBar("paused", liveBar.getAttribute("data-paused")); return; }
      liveBusy = true;
      fetch(window.location.href, { credentials: "same-origin", headers: { "X-AOI-Live": "1" }, cache: "no-store" })
        .then(function (r) {
          if (r.redirected && /\/login/.test(r.url)) { window.location.reload(); throw null; }   // signed out
          if (!r.ok) throw new Error("HTTP " + r.status);
          return r.text();
        })
        .then(function (html) {
          var doc = new DOMParser().parseFromString(html, "text/html"), changed = [];
          document.querySelectorAll("[data-live][id]").forEach(function (part) {
            var fresh = doc.getElementById(part.id);
            if (!fresh || fresh.outerHTML === part.outerHTML || dirty(part)) return;
            part.replaceWith(document.importNode(fresh, true));
            changed.push(part.id);
          });
          if (changed.length) document.dispatchEvent(new CustomEvent("aoi:live", { detail: changed }));
          lostSince = null;
          var served = doc.querySelector("[data-live-clock] span");               // the server's time (Thai time)
          setBar("ok", served ? served.textContent : (liveBar ? liveBar.getAttribute("data-ok") : "") + " " + hhmmss(new Date()));
        })
        .catch(function (err) {
          if (err === null) return;
          if (!lostSince) lostSince = new Date();
          var pill = document.querySelector(".dash-banner .dash-live[class*=\"lvl-\"]");          // the banner's level is not true any more
          if (pill && liveBar) { pill.className = "dash-live lvl-crit"; pill.lastChild.textContent = liveBar.getAttribute("data-down"); }
          setBar("lost", (liveBar ? liveBar.getAttribute("data-lost") : "") + " " + hhmmss(lostSince));
        })
        .then(function () { liveBusy = false; });
    };
    setInterval(liveTick, liveEvery);
    document.addEventListener("visibilitychange", function () { if (!document.hidden) liveTick(); });
  }

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
  // update 37: a scanned milling label (one exact order) opens its details at once - not over a message
  var autoPopup = document.querySelector("a[data-auto-popup]");
  if (autoPopup && !overlay.classList.contains("show")) autoPopup.click();
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

/* Administration: a bar of quick jumps to each section of the page (cards with a title, home sub-groups).
   It stays at the top while scrolling; the section on screen is highlighted. */
(function () {
  var label = document.body.getAttribute("data-quick-jump");
  if (!label) return;
  var targets = [];
  document.querySelectorAll(".home-subgroup, .card").forEach(function (node) {
    if (node.closest("#app-modal-overlay, template") || (node.parentElement && node.parentElement.closest(".card"))) return;
    // update 57: a card that is one tile of a grid or row (the KPI tiles of Milling Analysis) is not a section -
    // the bar was put inside the grid and squeezed into one tile
    var shown = node.parentElement && window.getComputedStyle(node.parentElement).display;
    if (shown && /grid|flex/.test(shown)) return;
    var head = node.querySelector(".home-sub-title, .card-title");
    if (!head || !head.textContent.trim()) return;
    if (!node.id) node.id = "qj-" + targets.length;
    targets.push({ node: node, text: head.textContent.replace(/\s+/g, " ").trim() });
  });
  if (targets.length < 3) return;
  var nav = document.createElement("nav");
  nav.className = "quick-jump"; nav.setAttribute("aria-label", label);
  // update 59: the chips are in a track that scrolls sideways; when they do not all fit, the ‹ › buttons, the mouse
  // wheel and the ≡ button (shows every chip on several rows) reach the hidden ones - before, a mouse could not
  var bar = document.createElement("div");
  bar.className = "qj-track";
  var links = targets.map(function (tg) {
    var a = document.createElement("a");
    a.href = "#" + tg.node.id; a.className = "qj-chip";
    a.textContent = tg.text.length > 28 ? tg.text.slice(0, 27) + "\u2026" : tg.text;
    a.title = tg.text;
    a.addEventListener("click", function (e) {
      e.preventDefault();
      if (tg.node.tagName === "DETAILS") tg.node.open = true;          // update 43: e.g. Machine setup opens when jumped to
      tg.node.scrollIntoView({ behavior: "smooth", block: "start" }); history.replaceState(null, "", "#" + tg.node.id);
    });
    bar.appendChild(a);
    return a;
  });
  var top = document.createElement("a");
  top.href = "#"; top.className = "qj-chip qj-top"; top.textContent = "\u2191 " + (document.body.getAttribute("data-quick-top") || "Top");
  top.addEventListener("click", function (e) { e.preventDefault(); window.scrollTo({ top: 0, behavior: "smooth" }); });
  bar.appendChild(top);
  function arrow(cls, text, dir) {
    var b = document.createElement("button");
    b.type = "button"; b.className = "qj-arrow " + cls; b.textContent = text; b.setAttribute("aria-label", text);
    b.addEventListener("click", function () { bar.scrollBy({ left: dir * Math.max(160, bar.clientWidth * 0.7), behavior: "smooth" }); });
    return b;
  }
  var prev = arrow("qj-prev", "\u2039", -1), next = arrow("qj-next", "\u203A", 1);
  var all = document.createElement("button");
  all.type = "button"; all.className = "qj-arrow qj-all"; all.textContent = "\u2261"; all.setAttribute("aria-expanded", "false");
  all.setAttribute("aria-label", label);
  all.addEventListener("click", function () {
    var open = nav.classList.toggle("open"); all.setAttribute("aria-expanded", open ? "true" : "false"); update();
  });
  nav.appendChild(prev); nav.appendChild(bar); nav.appendChild(next); nav.appendChild(all);
  function update() {
    var over = bar.scrollWidth > bar.clientWidth + 2 && !nav.classList.contains("open");
    nav.classList.toggle("overflow", over || nav.classList.contains("open"));
    prev.disabled = !over || bar.scrollLeft <= 2;
    next.disabled = !over || bar.scrollLeft + bar.clientWidth >= bar.scrollWidth - 2;
  }
  bar.addEventListener("scroll", update, { passive: true });
  window.addEventListener("resize", update);
  bar.addEventListener("wheel", function (e) {                 // a mouse wheel moves the chips sideways
    if (nav.classList.contains("open") || Math.abs(e.deltaY) <= Math.abs(e.deltaX) || bar.scrollWidth <= bar.clientWidth) return;
    e.preventDefault(); bar.scrollLeft += e.deltaY;
  }, { passive: false });
  links.forEach(function (l) { l.addEventListener("click", function () { if (nav.classList.contains("open")) all.click(); }); });
  targets[0].node.parentNode.insertBefore(nav, targets[0].node);
  setTimeout(update, 0);
  if ("IntersectionObserver" in window) {
    var observer = new IntersectionObserver(function (entries) {
      entries.forEach(function (en) {
        if (!en.isIntersecting) return;
        var i = targets.findIndex(function (tg) { return tg.node === en.target; });
        links.forEach(function (l, j) { l.classList.toggle("active", j === i); });
        if (links[i] && links[i].scrollIntoView && bar.scrollWidth > bar.clientWidth) {
          bar.scrollLeft = links[i].offsetLeft - 16;
        }
      });
    }, { rootMargin: "-80px 0px -60% 0px" });
    targets.forEach(function (tg) { observer.observe(tg.node); });
  }
})();
