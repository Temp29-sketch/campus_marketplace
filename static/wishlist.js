(() => {
  document.addEventListener("submit", async (event) => {
    const form = event.target.closest("form[data-wishlist-form]");
    if (!form) return;
    event.preventDefault();
    const button = form.querySelector("button");
    const wasSaved = button.getAttribute("aria-pressed") === "true";
    button.disabled = true;
    try {
      const response = await fetch(form.action.split("#")[0], {
        method: "POST", body: new FormData(form),
        headers: { Accept: "application/json", "X-Requested-With": "fetch", "X-CSRFToken": form.querySelector('[name="csrf_token"]').value },
      });
      if (response.status === 401) {
        const target = new URL(location.href);
        target.hash = `product-${form.action.match(/toggle\/(\d+)/)?.[1] || ""}`;
        location.assign(`/login?next=${encodeURIComponent(target.pathname + target.search + target.hash)}`);
        return;
      }
      if (!response.ok) throw new Error("Save failed");
      const result = await response.json();
      button.setAttribute("aria-pressed", String(result.saved));
      button.classList.toggle("saved", result.saved);
      button.setAttribute("aria-label", result.saved ? "Remove from wishlist" : "Save to wishlist");
      document.dispatchEvent(new CustomEvent("unihub:toast", { detail: result.saved ? "Saved to wishlist" : "Removed from wishlist" }));
      if (!result.saved && location.pathname === "/wishlist") {
        const card = form.closest(".wishlist-item");
        card.classList.add("removing");
        card.addEventListener("transitionend", () => card.remove(), { once: true });
        window.setTimeout(() => card.remove(), 250);
        if (document.querySelectorAll(".wishlist-item").length === 1) {
          window.setTimeout(() => {
            if (!document.querySelector(".wishlist-item")) document.querySelector(".wishlist-grid").outerHTML = '<div class="empty"><span class="empty-icon" aria-hidden="true">♡</span><h2>Nothing saved yet, tap the heart on any listing</h2><a class="btn" href="/">Browse listings</a></div>';
          }, 260);
        }
      }
    } catch (_) {
      button.setAttribute("aria-pressed", String(wasSaved));
      button.classList.toggle("saved", wasSaved);
      button.setAttribute("aria-label", wasSaved ? "Remove from wishlist" : "Save to wishlist");
      document.dispatchEvent(new CustomEvent("unihub:toast", { detail: "Could not update wishlist. Please try again." }));
    } finally { button.disabled = false; }
  });
})();
