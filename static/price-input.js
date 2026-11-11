// Pure normalization/formatting helper, also usable from a small Node test.
function formatPriceInput(value) {
  const source = String(value ?? "");
  let cleaned = "";
  let hasDecimal = false;
  let decimals = 0;
  for (const char of source) {
    if (char >= "0" && char <= "9") {
      if (hasDecimal) {
        if (decimals < 2) { cleaned += char; decimals += 1; }
      } else cleaned += char;
    } else if (char === "." && !hasDecimal) {
      hasDecimal = true;
      cleaned += char;
    }
  }
  const parts = cleaned.split(".");
  parts[0] = parts[0].replace(/\B(?=(\d{3})+(?!\d))/g, ",");
  return parts.join(".");
}
if (typeof module !== "undefined" && module.exports) module.exports = { formatPriceInput };
if (typeof document !== "undefined") {
  document.querySelectorAll("[data-price-input]").forEach((input) => {
    input.value = formatPriceInput(input.value).replace(/\.00$/, "");
    input.addEventListener("input", () => {
      const start = input.selectionStart;
      const before = input.value.slice(0, start ?? input.value.length);
      const digitCount = (before.match(/[0-9]/g) || []).length;
      const decimalBefore = before.includes(".");
      const formatted = formatPriceInput(input.value);
      input.value = formatted;
      let position = 0, seenDigits = 0, seenDecimal = false;
      if (digitCount > 0 || decimalBefore) {
        while (position < formatted.length) {
          const ch = formatted[position];
          if (ch >= "0" && ch <= "9") seenDigits += 1;
          if (ch === ".") seenDecimal = true;
          position += 1;
          if (seenDigits >= digitCount && (!decimalBefore || seenDecimal)) break;
        }
      }
      input.setSelectionRange(position, position);
    });
  });
}
