// Small site-wide behaviours. Kept in a file (not inline attributes) so the
// Content-Security-Policy can forbid inline scripts.
// <form data-confirm="Are you sure?"> asks before submitting.
document.documentElement.classList.add("js-enabled");

function resetPasswordVisibility() {
  document.querySelectorAll(".password-field").forEach((field) => {
    const input = field.querySelector("input");
    const toggle = field.querySelector("[data-password-toggle]");
    if (!input || !toggle) return;
    input.type = "password";
    toggle.setAttribute("aria-label", "Show password");
    toggle.title = "Show password";
    toggle.setAttribute("aria-pressed", "false");
  });
}
resetPasswordVisibility();

document.addEventListener("click", (event) => {
  const button = event.target.closest("[data-back-button]");
  if (!button) return;
  let sameOriginReferrer = false;
  try { sameOriginReferrer = Boolean(document.referrer) && new URL(document.referrer).origin === location.origin; } catch (_) {}
  if (history.length > 1 && sameOriginReferrer) history.back();
  else location.assign(button.dataset.fallback || "/");
});

document.addEventListener("submit", (event) => {
  const form = event.target;
  const message = form.dataset && form.dataset.confirm;
  if (message && !window.confirm(message)) { event.preventDefault(); return; }
  if (form.matches("[data-wishlist-form]")) return;
  if (form.matches("[data-cart-form]")) return;
  if (event.defaultPrevented) return;
  resetPasswordVisibility();
  const button = form.querySelector("button[type=submit]:not(:disabled)");
  if (!button) return;
  button.dataset.originalText = button.textContent;
  button.textContent = "Please wait...";
  button.classList.add("is-loading");
  button.disabled = true;
  button.setAttribute("aria-busy", "true");
  document.documentElement.classList.add("navigation-started");
});

document.addEventListener("click", (event) => {
  document.querySelectorAll("details.user-menu[open]").forEach((menu) => {
    if (!menu.contains(event.target)) menu.open = false;
  });
});

document.addEventListener("click", (event) => {
  const toggle = event.target.closest("[data-password-toggle]");
  if (!toggle) return;
  const input = document.getElementById(toggle.getAttribute("aria-controls"));
  if (!input) return;
  const reveal = input.type === "password";
  input.type = reveal ? "text" : "password";
  toggle.setAttribute("aria-pressed", String(reveal));
  toggle.setAttribute("aria-label", reveal ? "Hide password" : "Show password");
  toggle.title = reveal ? "Hide password" : "Show password";
  const start = input.selectionStart;
  const end = input.selectionEnd;
  input.focus();
  if (start !== null && end !== null) input.setSelectionRange(start, end);
});
document.addEventListener("keydown", (event) => {
  if (event.key !== "Escape") return;
  document.querySelectorAll("details.user-menu[open]").forEach((menu) => {
    menu.open = false;
    menu.querySelector("summary").focus();
  });
});

document.addEventListener("click", (event) => {
  const link = event.target.closest("a[href]");
  if (!link || event.defaultPrevented || link.target || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
  const url = new URL(link.href, location.href);
  if (url.origin === location.origin && url.pathname !== location.pathname + url.hash) document.documentElement.classList.add("navigation-started");
});

window.addEventListener("pageshow", () => {
  resetPasswordVisibility();
  document.documentElement.classList.remove("navigation-started");
  document.querySelectorAll("button[aria-busy=true]").forEach((button) => {
    button.textContent = button.dataset.originalText || button.textContent;
    button.disabled = false;
    button.removeAttribute("aria-busy");
    button.classList.remove("is-loading");
  });
});

document.addEventListener("submit", async (event) => {
  const form = event.target.closest("form[data-cart-form]");
  if (!form) return;
  event.preventDefault();
  const button = form.querySelector("[data-add-to-cart]");
  button.disabled = true;
  try {
    const response = await fetch(form.action.split("#")[0], { method: "POST", body: new FormData(form), headers: { Accept: "application/json", "X-Requested-With": "fetch", "X-CSRFToken": form.querySelector('[name="csrf_token"]').value } });
    if (response.status === 401) {
      const target = new URL(location.href); target.hash = form.action.split("#")[1] || "";
      location.assign(`/login?next=${encodeURIComponent(target.pathname + target.search + target.hash)}`); return;
    }
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || "Could not add to cart.");
    document.querySelectorAll(".cart-count").forEach((badge) => { badge.textContent = result.cart_count; });
    document.querySelectorAll(".cart-link").forEach((link) => link.setAttribute("aria-label", `Cart, ${result.cart_count} items`));
    document.dispatchEvent(new CustomEvent("unihub:toast", { detail: "Added to your cart." }));
  } catch (error) {
    document.dispatchEvent(new CustomEvent("unihub:toast", { detail: error.message || "Could not add to cart. Please try again." }));
  } finally { button.disabled = false; }
});

function updateNetworkState() {
  const banner = document.querySelector(".offline-banner");
  const offline = !navigator.onLine;
  if (banner) banner.hidden = !offline;
  document.querySelectorAll("[data-add-to-cart], [data-checkout]").forEach((button) => {
    if (offline) { button.dataset.disabledOffline = button.disabled ? "kept" : ""; button.disabled = true; }
    else if (button.dataset.disabledOffline !== "kept") button.disabled = false;
    delete button.dataset.disabledOffline;
  });
}
window.addEventListener("online", updateNetworkState);
window.addEventListener("offline", updateNetworkState);
updateNetworkState();

function replaceFailedImage(image) {
  if (!(image instanceof HTMLImageElement) || !image.dataset.placeholder || image.dataset.fallbackDone) return;
  image.dataset.fallbackDone = "true";
  const placeholder = document.createElement("div");
  const framed = image.closest(".image-frame, .banner-image");
  placeholder.className = `${image.className} placeholder image-fallback${framed ? " image-frame-placeholder" : ""}`;
  placeholder.classList.remove("shimmer");
  placeholder.setAttribute("role", "img");
  placeholder.setAttribute("aria-label", image.alt || "Image unavailable");
  placeholder.innerHTML = '<svg viewBox="0 0 64 64" aria-hidden="true"><path d="M23 27V18a9 9 0 0 1 18 0v9" fill="none" stroke="currentColor" stroke-width="5" stroke-linecap="round"/><path d="M11 23h42l-3 31a4.5 4.5 0 0 1-4.5 4H18.5A4.5 4.5 0 0 1 14 54z" fill="currentColor"/></svg>';
  image.replaceWith(placeholder);
}
document.addEventListener("error", (event) => {
  replaceFailedImage(event.target);
}, true);
function scanFailedImages(root = document) {
  root.querySelectorAll('img[data-placeholder]').forEach((image) => {
    if (image.complete && image.naturalWidth === 0) replaceFailedImage(image);
  });
}
if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", () => scanFailedImages());
else scanFailedImages();
document.addEventListener("unihub:partial-render", () => scanFailedImages());

document.addEventListener("click", (event) => {
  const toggle = event.target.closest(".search-toggle");
  if (!toggle) return;
  const header = toggle.closest(".nav");
  const open = header.classList.toggle("search-open");
  toggle.setAttribute("aria-expanded", String(open));
  if (open) header.querySelector(".site-search input[type=search]").focus();
});
