// Small site-wide behaviours. Kept in a file (not inline attributes) so the
// Content-Security-Policy can forbid inline scripts.
// <form data-confirm="Are you sure?"> asks before submitting.
document.addEventListener("submit", (event) => {
  const message = event.target.dataset && event.target.dataset.confirm;
  if (message && !window.confirm(message)) event.preventDefault();
});

document.addEventListener("click", (event) => {
  document.querySelectorAll("details.user-menu[open]").forEach((menu) => {
    if (!menu.contains(event.target)) menu.open = false;
  });
});
document.addEventListener("keydown", (event) => {
  if (event.key !== "Escape") return;
  document.querySelectorAll("details.user-menu[open]").forEach((menu) => {
    menu.open = false;
    menu.querySelector("summary").focus();
  });
});
