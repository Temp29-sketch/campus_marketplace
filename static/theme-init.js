(() => {
  let theme = 'light';
  try { const saved = localStorage.getItem('theme'); if (['light', 'dark', 'device'].includes(saved)) theme = saved; } catch (_) {}
  document.documentElement.dataset.theme = theme;
})();
