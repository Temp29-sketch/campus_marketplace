(() => {
  const link = document.getElementById("link_url");
  const group = document.querySelector("[data-banner-button-field]");
  const buttonText = document.getElementById("button_text");
  if (!link || !group || !buttonText) return;
  const sync = () => {
    const hidden = !link.value.trim();
    group.hidden = hidden;
    buttonText.disabled = hidden;
  };
  link.addEventListener("input", sync);
  sync();
})();
