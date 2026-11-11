(() => {
  const radios = document.querySelectorAll('input[name="theme"]');
  if (!radios.length) return;
  let current = document.documentElement.dataset.theme || 'light';
  radios.forEach((radio) => {
    radio.checked = radio.value === current;
    radio.addEventListener('change', () => {
      if (!radio.checked) return;
      document.documentElement.dataset.theme = radio.value;
      try { localStorage.setItem('theme', radio.value); } catch (_) {}
    });
  });
})();
