/* The two interactions the console cannot render on the server: picking a block
   range in the reading view, and the keyboard of the review queue. Everything
   else is a form post or an HTMX swap. No framework, no build step. */
(function () {
  "use strict";

  function selectionForm() {
    return document.getElementById("selection");
  }

  function setRange(first, last) {
    var form = selectionForm();
    if (!form) { return; }
    form.querySelector("[name=first]").value = first;
    form.querySelector("[name=last]").value = last;
    document.querySelectorAll(".block").forEach(function (block) {
      var number = Number(block.dataset.number);
      block.classList.toggle("selected", number >= first && number <= last);
    });
    if (window.htmx) { window.htmx.trigger(form, "rangechanged"); }
  }

  function readRange() {
    var form = selectionForm();
    if (!form) { return [0, 0]; }
    return [Number(form.querySelector("[name=first]").value || 0),
            Number(form.querySelector("[name=last]").value || 0)];
  }

  /* Click the first block, then the last: the expert's workflow is block numbers. */
  document.addEventListener("click", function (event) {
    var block = event.target.closest(".block");
    if (!block || !selectionForm() || event.target.closest("a")) { return; }
    var number = Number(block.dataset.number);
    var range = readRange();
    if (!range[0] || number < range[0] || range[1]) {
      setRange(number, number);
    } else {
      setRange(range[0], number);
    }
  });

  /* Typing the numbers must work as well as clicking. */
  document.addEventListener("input", function (event) {
    if (!event.target.closest("#selection")) { return; }
    var range = readRange();
    if (range[0]) { setRange(range[0], Math.max(range[0], range[1] || range[0])); }
  });

  /* Review queue: y confirm, e edit, n reject, f flag, j/k next and previous, x select for a bulk action. */
  var SHORTCUTS = {y: "confirm", e: "edit", n: "reject", f: "flag", j: "next", k: "previous", x: "select"};
  document.addEventListener("keydown", function (event) {
    var tag = (event.target.tagName || "").toLowerCase();
    var typing = tag === "textarea" || tag === "select" || (tag === "input" && event.target.type !== "checkbox");
    if (event.metaKey || event.ctrlKey || event.altKey || typing) {
      return;
    }
    var action = SHORTCUTS[event.key];
    var target = action && document.querySelector("[data-key=" + action + "]");
    if (!target) { return; }
    event.preventDefault();
    if (target.tagName.toLowerCase() === "a") { target.click(); } else { target.click(); }
  });

  /* Review queue bulk action. Every card is a page load, so the ticked facts are kept in sessionStorage per pack
     and filter and restored on the next card; without JavaScript the ticks and "all matching" still work. */
  function bulkForm() {
    return document.getElementById("bulk");
  }

  function factBoxes() {
    return Array.prototype.slice.call(document.querySelectorAll("input[name=fact_ids]"));
  }

  /* The same filter must give the same key whichever link reached it: the next-card link adds mode=queue and the
     filter form sends empty fields, so both are dropped and the rest sorted. */
  function selectionKey() {
    var kept = [];
    new URLSearchParams(window.location.search).forEach(function (value, name) {
      var navigation = ["fact_id", "notice", "error", "page"].indexOf(name) !== -1;
      if (!navigation && value && !(name === "mode" && value === "queue")) { kept.push(name + "=" + value); }
    });
    return "review-selection:" + window.location.pathname + "?" + kept.sort().join("&");
  }

  function storedSelection() {
    try { return JSON.parse(window.sessionStorage.getItem(selectionKey()) || "[]"); } catch (error) { return []; }
  }

  function storeSelection() {
    var chosen = storedSelection().filter(function (id) {
      return !factBoxes().some(function (box) { return box.value === id; });
    });
    factBoxes().forEach(function (box) { if (box.checked) { chosen.push(box.value); } });
    try { window.sessionStorage.setItem(selectionKey(), JSON.stringify(chosen)); } catch (error) { /* private window */ }
  }

  function refreshBulk() {
    var form = bulkForm();
    if (!form) { return; }
    var boxes = factBoxes();
    var ticked = boxes.filter(function (box) { return box.checked; }).length;
    var matching = form.querySelector("[name=scope]").checked;
    var all = form.querySelector("[data-select-all]");
    all.checked = boxes.length > 0 && ticked === boxes.length;
    all.indeterminate = ticked > 0 && ticked < boxes.length;
    var button = form.querySelector("[data-bulk-submit]");
    var count = matching ? Number(form.dataset.total) : ticked;
    button.disabled = count === 0;
    button.textContent = count === 0 ? "Select facts to apply"
      : "Apply to " + (matching ? "all " : "") + count + " fact" + (count === 1 ? "" : "s");
    var action = form.querySelector("[name=action]").value;
    button.classList.toggle("danger", action === "reject");
    form.querySelector("[name=note]").required = action !== "confirm";
  }

  if (bulkForm()) {
    var remembered = storedSelection();
    factBoxes().forEach(function (box) { box.checked = remembered.indexOf(box.value) !== -1; });
    refreshBulk();
  }

  document.addEventListener("change", function (event) {
    if (!bulkForm()) { return; }
    if (event.target.matches("[data-select-all]")) {
      factBoxes().forEach(function (box) { box.checked = event.target.checked; });
    }
    if (event.target.matches("[data-select-all], input[name=fact_ids]")) { storeSelection(); }
    refreshBulk();
  });

  document.addEventListener("submit", function (event) {
    var form = event.target;
    if (form.id !== "bulk") { return; }
    var action = form.querySelector("[name=action]").value;
    var label = form.querySelector("[data-bulk-submit]").textContent.replace("Apply to ", "");
    if (action === "reject" && !window.confirm("Remove " + label + " from the curation file?")) {
      event.preventDefault();
      return;
    }
    try { window.sessionStorage.removeItem(selectionKey()); } catch (error) { /* private window */ }
  });

  /* The log of a running job scrolls with its own output. */
  document.addEventListener("htmx:afterSwap", function (event) {
    var log = event.target.querySelector ? event.target.querySelector(".job-log") : null;
    if (log) { log.scrollTop = log.scrollHeight; }
  });
}());
