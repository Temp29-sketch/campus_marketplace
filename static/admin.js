document.addEventListener('click', async (event) => {
  const button = event.target.closest('[data-copy-account]');
  if (!button) return;
  const value = document.querySelector('[data-account-number]')?.textContent?.trim();
  if (!value) return;
  try { await navigator.clipboard.writeText(value); button.textContent = 'Copied'; }
  catch (_) { button.textContent = 'Copy unavailable'; }
});
