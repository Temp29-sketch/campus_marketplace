// Live feed updates keep normal links/forms as the no-JS fallback.
(() => {
  const root = document.getElementById("catalog");
  const form = document.querySelector(".site-search");
  const results = document.getElementById("results");
  if (!root || !form || !results || !window.fetch || !window.URLSearchParams) return;
  const sort = document.getElementById("catalog-sort");
  const chips = root.querySelectorAll("a.chip[data-filter]");
  let timer = null;
  let slowTimer = null;
  let controller = null;

  function paramsFromPage() {
    const params = new URLSearchParams(new FormData(form));
    if (sort && sort.value !== "new") params.set("sort", sort.value);
    return params;
  }
  function syncChips(params) {
    const active = params.get("category") || "";
    chips.forEach((chip) => chip.classList.toggle("active", chip.dataset.value === active));
  }
  function showSkeletons() {
    results.innerHTML = `<p class="muted result-count">Loading listings…</p><div class="grid skeleton-grid" aria-hidden="true">${Array.from({length: 6}, () => '<div class="skeleton-card"><div class="skeleton-image shimmer"></div><div class="skeleton-line shimmer"></div><div class="skeleton-line short shimmer"></div><div class="skeleton-button shimmer"></div></div>').join("")}</div><p class="slow-message" hidden>Taking longer than usual...</p>`;
  }
  async function load(params, options = {}) {
    clearTimeout(timer);
    clearTimeout(slowTimer);
    if (controller) controller.abort();
    controller = new AbortController();
    const requestController = controller;
    const query = params.toString();
    const url = location.pathname + (query ? `?${query}` : "");
    const loadingStarted = Date.now();
    showSkeletons();
    results.setAttribute("aria-busy", "true");
    const slow = results.querySelector(".slow-message");
    const requestSlowTimer = setTimeout(() => { if (slow) slow.hidden = false; }, 8000);
    slowTimer = requestSlowTimer;
    try {
      const response = await fetch(url + (query ? "&" : "?") + "partial=1", {
        headers: { "X-Requested-With": "fetch", "Accept": "text/html" }, signal: requestController.signal,
      });
      if (!response.ok) throw new Error("Could not load listings");
      const markup = await response.text();
      await new Promise((resolve) => setTimeout(resolve, Math.max(0, 350 - (Date.now() - loadingStarted))));
      if (requestController.signal.aborted) return;
      results.innerHTML = markup;
      document.dispatchEvent(new Event("unihub:partial-render"));
      if (!options.popstate) history.pushState({ feed: true }, "", url);
      syncChips(params);
    } catch (error) {
      if (error.name !== "AbortError") {
        await new Promise((resolve) => setTimeout(resolve, Math.max(0, 350 - (Date.now() - loadingStarted))));
        if (requestController.signal.aborted) return;
        results.innerHTML = '<div class="fetch-error"><p>Could not load listings. Check your connection.</p><button class="btn retry-feed" type="button">Retry</button></div>';
        results.querySelector(".retry-feed").addEventListener("click", () => load(params));
      }
    } finally {
      clearTimeout(requestSlowTimer);
      if (controller === requestController) results.removeAttribute("aria-busy");
    }
  }
  form.addEventListener("submit", (event) => { event.preventDefault(); load(paramsFromPage()); });
  form.elements.q.addEventListener("input", () => { clearTimeout(timer); timer = setTimeout(() => load(paramsFromPage()), 250); });
  if (sort) sort.addEventListener("change", () => load(paramsFromPage()));
  chips.forEach((chip) => chip.addEventListener("click", (event) => {
    if (event.metaKey || event.ctrlKey || event.shiftKey) return;
    event.preventDefault(); form.elements.category.value = chip.dataset.value;
    const stickyTop = root.querySelector(".sticky-chips").getBoundingClientRect().top + window.scrollY - parseFloat(getComputedStyle(root.querySelector(".sticky-chips")).top || "0");
    load(paramsFromPage()).then(() => window.scrollTo({ top: Math.max(0, stickyTop), behavior: "smooth" }));
  }));
  results.addEventListener("click", (event) => {
    const link = event.target.closest(".pager a");
    if (!link) return;
    event.preventDefault(); load(new URL(link.href).searchParams).then(() => root.scrollIntoView({ behavior: "smooth" }));
  });
  window.addEventListener("popstate", () => load(new URLSearchParams(location.search), { popstate: true }));
})();
