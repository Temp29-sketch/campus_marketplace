(() => {
  const toast = document.querySelector(".toast");
  if (!toast) return;
  let timer;
  window.showToast = (message) => {
    toast.textContent = message; toast.hidden = false;
    clearTimeout(timer); timer = setTimeout(() => { toast.hidden = true; }, 3000);
  };
  document.addEventListener("unihub:toast", (event) => window.showToast(event.detail || "Done"));
})();
