(() => {
  const sidebar = document.getElementById("site-sidebar");
  const toggle = document.querySelector(".sidebar-toggle");
  const scrim = document.querySelector(".sidebar-scrim");
  if (!sidebar || !toggle || !scrim) return;
  function open() {
    sidebar.classList.add("open"); scrim.hidden = false; toggle.setAttribute("aria-expanded", "true");
    toggle.setAttribute("aria-label", "Close navigation");
    const first = sidebar.querySelector("a, button"); if (first) first.focus();
  }
  function close(returnFocus = true) {
    sidebar.classList.remove("open"); scrim.hidden = true; toggle.setAttribute("aria-expanded", "false");
    toggle.setAttribute("aria-label", "Open navigation"); if (returnFocus) toggle.focus();
  }
  toggle.addEventListener("click", () => sidebar.classList.contains("open") ? close() : open());
  scrim.addEventListener("click", () => close());
  sidebar.querySelectorAll("a").forEach((link) => link.addEventListener("click", () => close(false)));
  document.addEventListener("keydown", (event) => {
    if (!sidebar.classList.contains("open")) return;
    if (event.key === "Escape") { close(); return; }
    if (event.key !== "Tab") return;
    const focusable = [...sidebar.querySelectorAll("a[href], button:not(:disabled)")];
    if (!focusable.length) return;
    const first = focusable[0], last = focusable[focusable.length - 1];
    if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
    else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
  });
})();
