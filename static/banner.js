(() => {
  const root = document.querySelector('[data-banner-carousel]');
  if (!root) return;
  const slides = [...root.querySelectorAll('.banner-slide')];
  if (slides.length < 2) return;
  const dots = [...root.querySelectorAll('[data-banner-dot]')];
  let current = 0;
  let timer = null;
  const pauses = new Set();
  const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)');
  const wrap = window.bannerIndex.wrap;
  const stop = () => { window.clearInterval(timer); timer = null; };
  const start = () => {
    stop();
    if (!pauses.size && !document.hidden && !reducedMotion.matches) timer = window.setInterval(() => show(current + 1), 6000);
  };
  const pause = (reason) => { pauses.add(reason); stop(); };
  const resume = (reason) => { pauses.delete(reason); start(); };
  const show = (index) => {
    current = wrap(index, slides.length);
    slides.forEach((slide, i) => {
      const active = i === current;
      slide.classList.toggle('active', active);
      slide.setAttribute('aria-hidden', String(!active));
      slide.inert = !active;
    });
    dots.forEach((dot, i) => {
      if (i === current) dot.setAttribute('aria-current', 'true');
      else dot.removeAttribute('aria-current');
    });
  };
  const restart = () => { stop(); start(); };
  root.querySelector('[data-banner-prev]').addEventListener('click', () => { show(current - 1); restart(); });
  root.querySelector('[data-banner-next]').addEventListener('click', () => { show(current + 1); restart(); });
  dots.forEach((dot) => dot.addEventListener('click', () => { show(Number(dot.dataset.bannerDot)); restart(); }));
  root.addEventListener('keydown', (event) => {
    if (event.key === 'ArrowLeft') { event.preventDefault(); show(current - 1); restart(); }
    else if (event.key === 'ArrowRight') { event.preventDefault(); show(current + 1); restart(); }
  });
  root.addEventListener('mouseenter', () => pause('hover'));
  root.addEventListener('mouseleave', () => resume('hover'));
  root.addEventListener('focusin', () => pause('focus'));
  root.addEventListener('focusout', (event) => { if (!root.contains(event.relatedTarget)) resume('focus'); });
  let touchX = null;
  root.addEventListener('touchstart', (event) => { touchX = event.changedTouches[0].clientX; pause('touch'); }, { passive: true });
  root.addEventListener('touchend', (event) => {
    if (touchX !== null) {
      const delta = event.changedTouches[0].clientX - touchX;
      if (Math.abs(delta) > 40) show(current + (delta < 0 ? 1 : -1));
    }
    touchX = null;
    resume('touch');
  }, { passive: true });
  root.addEventListener('touchcancel', () => { touchX = null; resume('touch'); }, { passive: true });
  document.addEventListener('visibilitychange', () => document.hidden ? pause('hidden') : resume('hidden'));
  reducedMotion.addEventListener('change', restart);
  show(0);
  start();
})();
