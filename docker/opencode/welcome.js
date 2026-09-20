// Swiss TIP welcome panel for the OpenCode web interface of the demo image.
//
// The shim loads this file before the interface and serves it preceded by a
// line that sets window.__SWISSTIP_WELCOME__ to the content of welcome.json
// and the agent's worktree; the interface's content security policy rules
// out an inline script for that. It does three things:
//
//   1. In a browser that has no project yet, it registers the workspace as
//      the one project, so that the home screen offers "New session" at once
//      instead of an empty project list.
//   2. On the home screen while the server has no sessions, and on every
//      new-session screen, it shows what the knowledge base covers and a few
//      sample questions; on the new-session screen the panel takes the place
//      of the OpenCode logo. The start script adds the server's release,
//      shown in small print under the note.
//   3. A sample question clicked there is typed into the prompt and sent. On
//      the home screen it opens a new session first.
//
// The interface is OpenCode's, pinned to one version in the Dockerfile. The
// script finds its elements by their data-component and data-action
// attributes; if a later version renames them, the panel does not appear and
// the interface works as before.
(function () {
  "use strict";
  var config = window.__SWISSTIP_WELCOME__;
  if (!config) return;

  var SERVER_KEY = "opencode.global.dat:server";
  var MARK = "data-swisstip-welcome";
  var pending = null;

  function registerWorkspace() {
    if (!config.worktree) return;
    try {
      var state = JSON.parse(localStorage.getItem(SERVER_KEY) || "null") || {};
      state.projects = state.projects || {};
      if ((state.projects.local || []).length) return;
      state.list = state.list || [];
      state.projects.local = [{ worktree: config.worktree, expanded: true }];
      state.lastProject = state.lastProject || {};
      state.lastProject.local = config.worktree;
      state.recentlyClosed = state.recentlyClosed || {};
      localStorage.setItem(SERVER_KEY, JSON.stringify(state));
    } catch (error) {
      console.warn("swiss-tip welcome: the workspace could not be registered", error);
    }
  }

  var STYLE = [
    "[data-swisstip-welcome]{font-family:inherit;color:var(--v2-text-text-base,#171717);text-align:left}",
    "[data-swisstip-welcome=header]{display:flex;flex-direction:column;gap:8px;padding:0 4px}",
    "[data-swisstip-welcome] h1{margin:0;font-size:20px;line-height:28px;font-weight:600;letter-spacing:-0.2px}",
    "[data-swisstip-welcome] p{margin:0;font-size:13px;line-height:20px;color:var(--v2-text-text-muted,#6b6b6b)}",
    "[data-swisstip-welcome] p[data-swisstip-welcome=release]{font-size:11px;line-height:16px}",
    "[data-swisstip-welcome=samples]{display:flex;flex-direction:column;gap:8px;margin-top:4px}",
    "[data-swisstip-welcome] h2{margin:0 4px;font-size:11px;line-height:16px;font-weight:530;text-transform:uppercase;letter-spacing:0.4px;color:var(--v2-text-text-muted,#6b6b6b)}",
    "[data-swisstip-welcome] ul{list-style:none;margin:0;padding:0;display:grid;grid-template-columns:repeat(auto-fill,minmax(260px,1fr));gap:8px}",
    "[data-swisstip-welcome] button{all:unset;box-sizing:border-box;display:block;width:100%;height:100%;padding:10px 12px;border-radius:10px;cursor:pointer;",
    "font-size:13px;line-height:18px;color:var(--v2-text-text-base,#171717);background:var(--v2-background-bg-base,#fff);",
    "border:1px solid var(--v2-border-border-muted,#e5e5e5);transition:border-color .15s,background-color .15s}",
    "[data-swisstip-welcome] button:hover{border-color:var(--v2-border-border-base,#bdbdbd);background:var(--v2-background-bg-layer,#f5f5f5)}",
    "[data-swisstip-welcome] button:focus-visible{outline:2px solid var(--v2-text-text-accent,#2563eb);outline-offset:1px}",
    "[data-swisstip-welcome=home]{display:flex;flex-direction:column;gap:20px;margin:0 auto 8px;max-width:720px;width:100%}"
  ].join("");

  function element(tag, attributes, text) {
    var node = document.createElement(tag);
    for (var name in attributes) node.setAttribute(name, attributes[name]);
    if (text) node.textContent = text;
    return node;
  }

  function header() {
    var node = element("div", { "data-swisstip-welcome": "header" });
    node.appendChild(element("h1", {}, config.title));
    if (config.intro) node.appendChild(element("p", {}, config.intro));
    if (config.note) node.appendChild(element("p", {}, config.note));
    if (config.release) node.appendChild(element("p", { "data-swisstip-welcome": "release" }, "Knowledge release " + config.release));
    return node;
  }

  function samples(onPick) {
    var node = element("div", { "data-swisstip-welcome": "samples" });
    var questions = config.questions || [];
    if (!questions.length) return node;
    node.appendChild(element("h2", {}, config.samples_heading || "Try a question"));
    var list = element("ul", {});
    questions.forEach(function (question) {
      var item = element("li", {});
      var button = element("button", { type: "button" }, question);
      button.addEventListener("click", function () { onPick(question); });
      item.appendChild(button);
      list.appendChild(item);
    });
    node.appendChild(list);
    return node;
  }

  // Call found() once the selector matches, polling for up to `limit` ms.
  function when(selector, limit, found, missing) {
    var deadline = Date.now() + limit;
    (function poll() {
      var node = document.querySelector(selector);
      if (node && !node.disabled) found(node);
      else if (Date.now() < deadline) setTimeout(poll, 50);
      else if (missing) missing();
    })();
  }

  // The prompt is a contenteditable element read by the interface on input;
  // insertText goes through the browser's editing path, so the interface sees
  // an ordinary typed text and enables its send button. A prompt sent before
  // the model selector is shown goes to another model than the configured
  // one, so the question waits for it.
  function ask(question) {
    when("[data-action=prompt-model]", 10000, type, function () {
      console.warn("swiss-tip welcome: no model selector; the question is typed but not sent");
      type(null);
    });
    function type(model) {
      var input = document.querySelector("[data-component=prompt-input]");
      if (!input) return;
      input.focus();
      document.execCommand("selectAll", false);
      document.execCommand("insertText", false, question);
      if (!model) return;
      when("[data-action=prompt-submit]", 1000, function (button) { button.click(); }, function () {
        console.warn("swiss-tip welcome: the send button stayed disabled; the question is in the prompt");
      });
    }
  }

  function decorateNewSession(view) {
    if (view.querySelector("[" + MARK + "]")) return;
    var form = view.querySelector("form[data-component=prompt-input-v2]");
    if (!form) return;
    var logo = view.querySelector('svg[viewBox="0 0 720 129"]');
    var column = logo ? logo.parentElement : null;
    if (!column) return;
    logo.style.display = "none";
    // The block starts a quarter down the view, which leaves too little room
    // below the prompt for the samples.
    if (column.parentElement) column.parentElement.style.top = "min(10%, 80px)";
    column.insertBefore(header(), logo);
    var below = element("div", { "data-swisstip-welcome": "below", style: "margin-top:24px" });
    below.appendChild(samples(ask));
    column.appendChild(below);
    if (pending) {
      var question = pending;
      pending = null;
      ask(question);
    }
  }

  // The home screen without sessions says "Nothing here yet" above its
  // "New session" button; the panel replaces that text and keeps the button.
  // With sessions, the only such button is the one floating in the header,
  // in a container that passes clicks through, and the panel stays out.
  function decorateHome(button) {
    var column = button.parentElement;
    if (!column || column.querySelector("[" + MARK + "=home]")) return;
    if (getComputedStyle(column).pointerEvents === "none") return;
    for (var node = button.previousElementSibling; node; node = node.previousElementSibling) {
      node.style.display = "none";
    }
    var panel = element("div", { "data-swisstip-welcome": "home" });
    panel.appendChild(header());
    panel.appendChild(samples(function (question) {
      pending = question;
      button.click();
    }));
    column.insertBefore(panel, button);
  }

  function update() {
    scheduled = false;
    var view = document.querySelector("[data-component=session-new-design]");
    if (view) decorateNewSession(view);
    document.querySelectorAll("[data-action=home-new-session]").forEach(decorateHome);
  }

  var scheduled = false;
  function schedule() {
    if (scheduled) return;
    scheduled = true;
    requestAnimationFrame(update);
  }

  registerWorkspace();
  document.addEventListener("DOMContentLoaded", function () {
    document.head.appendChild(element("style", { "data-swisstip-welcome": "style" }, STYLE));
    new MutationObserver(schedule).observe(document.body, { childList: true, subtree: true });
    schedule();
  });
})();
