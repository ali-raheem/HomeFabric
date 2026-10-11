document.addEventListener("DOMContentLoaded", function () {
  var btn = document.querySelector("[data-print]");
  if (btn) { btn.addEventListener("click", function () { window.print(); }); }
});
