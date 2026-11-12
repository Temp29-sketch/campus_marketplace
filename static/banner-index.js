(() => {
  const wrap = (index, length) => length > 0 ? ((index % length) + length) % length : 0;
  if (typeof module !== 'undefined' && module.exports) module.exports = { wrap };
  else window.bannerIndex = { wrap };
})();
