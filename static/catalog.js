// Live filtering for the catalog. Without JavaScript the form and chips still work
// as normal page loads; this just swaps the results in place as you type or click.
(() => {
  const root = document.getElementById("catalog");
  if (!root || !window.fetch || !window.URLSearchParams) return;

  const form = root.querySelector("form.search");
  const results = document.getElementById("results");
  const chips = root.querySelectorAll("a.chip[data-filter]");
  let timer = null;
  let controller = null;

  function paramsFromForm() {
    const params = new URLSearchParams();
    for (const [key, value] of new FormData(form)) {
      if (value && !(key === "sort" && value === "new")) params.set(key, value);
    }
    return params;
  }

  function syncChips() {
    chips.forEach((chip) => {
      chip.classList.toggle("active", form.elements[chip.dataset.filter].value === chip.dataset.value);
    });
  }

  async function load(params) {
    clearTimeout(timer);
    if (controller) controller.abort(); // a newer keystroke wins
    controller = new AbortController();
    const query = params.toString();
    const url = location.pathname + (query ? "?" + query : "");
    results.setAttribute("aria-busy", "true");
    results.classList.add("loading");
    try {
      const response = await fetch(url + (query ? "&" : "?") + "partial=1", {
        headers: { "X-Requested-With": "fetch" },
        signal: controller.signal,
      });
      if (!response.ok) throw new Error("bad response");
      results.innerHTML = await response.text();
      history.replaceState(null, "", url);
    } catch (error) {
      if (error.name !== "AbortError") window.location.href = url; // fall back to a normal page load
      return;
    } finally {
      results.removeAttribute("aria-busy");
      results.classList.remove("loading");
    }
  }

  form.addEventListener("submit", (event) => {
    event.preventDefault();
    load(paramsFromForm());
  });
  form.elements["q"].addEventListener("input", () => {
    clearTimeout(timer);
    timer = setTimeout(() => load(paramsFromForm()), 250);
  });
  form.elements["sort"].addEventListener("change", () => load(paramsFromForm()));

  chips.forEach((chip) => {
    chip.addEventListener("click", (event) => {
      if (event.metaKey || event.ctrlKey || event.shiftKey) return; // let "open in new tab" work
      event.preventDefault();
      form.elements[chip.dataset.filter].value = chip.dataset.value;
      syncChips();
      load(paramsFromForm());
    });
  });

  results.addEventListener("click", (event) => {
    const link = event.target.closest(".pager a");
    if (!link) return;
    event.preventDefault();
    load(new URL(link.href).searchParams).then(() => root.scrollIntoView({ behavior: "smooth" }));
  });
})();
