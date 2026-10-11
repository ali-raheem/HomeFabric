/* Progressive enhancement only: every feature works without JavaScript. */
(function () {
  "use strict";
  document.addEventListener("DOMContentLoaded", function () {
    var toggle = document.querySelector(".nav-toggle");
    var nav = document.getElementById("site-nav");
    if (toggle && nav) {
      toggle.addEventListener("click", function () {
        var open = toggle.getAttribute("aria-expanded") === "true";
        toggle.setAttribute("aria-expanded", String(!open));
        nav.classList.toggle("open", !open);
      });
    }
    document.querySelectorAll("select[data-autosubmit]").forEach(function (el) {
      el.addEventListener("change", function () { el.form.submit(); });
    });
    document.querySelectorAll("form[data-confirm]").forEach(function (form) {
      form.addEventListener("submit", function (ev) {
        if (!window.confirm(form.getAttribute("data-confirm"))) { ev.preventDefault(); }
      });
    });
    document.querySelectorAll("[data-copy]").forEach(function (btn) {
      btn.addEventListener("click", function () {
        var target = document.getElementById(btn.getAttribute("data-copy"));
        if (!target || !navigator.clipboard) { return; }
        navigator.clipboard.writeText(target.value || target.textContent).then(function () {
          btn.textContent = "Copied";
        });
      });
    });
  });
  document.addEventListener("htmx:afterSwap", function (ev) {
    var toggle = ev.detail.requestConfig && ev.detail.requestConfig.elt;
    if (toggle && toggle.classList.contains("expand")) {
      toggle.setAttribute("aria-expanded", "true");
      toggle.setAttribute("data-loaded", "1");
      toggle.textContent = toggle.textContent.replace("+", "−");
    }
  });
  /* After the first load, the tree toggle shows and hides children locally. */
  document.addEventListener("click", function (ev) {
    var btn = ev.target.closest && ev.target.closest(".expand[data-loaded]");
    if (!btn) { return; }
    var list = document.getElementById(btn.getAttribute("aria-controls"));
    var open = btn.getAttribute("aria-expanded") === "true";
    btn.setAttribute("aria-expanded", String(!open));
    btn.textContent = btn.textContent.replace(open ? "−" : "+", open ? "+" : "−");
    if (list) { list.hidden = open; }
  });
})();
