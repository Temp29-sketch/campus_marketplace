(() => {
  const hostel = document.querySelector('[data-hostel-select]');
  if (!hostel) return;
  const university = document.querySelector('#university_id');
  const blockWrap = document.querySelector('[data-block-fields]');
  const floorWrap = document.querySelector('[data-floor-fields]');
  const blockSelect = document.querySelector('[data-block-select]');
  const floorSelect = document.querySelector('[data-floor-select]');
  const option = () => hostel.selectedOptions[0];
  const rebuild = (rows, selectValue = '') => {
    const chosen = rows.find((row) => String(row.id) === String(selectValue));
    hostel.replaceChildren(new Option('Choose a hostel', ''));
    rows.forEach((h) => {
      const opt = new Option(h.name, h.id);
      opt.dataset.hasBlocks = h.has_blocks ? 'true' : 'false';
      opt.dataset.floorCount = h.floor_count || 0;
      opt.dataset.blocks = JSON.stringify(h.blocks || []);
      hostel.add(opt);
    });
    if (chosen) hostel.value = String(chosen.id);
  };
  const render = () => {
    const selected = option();
    blockSelect.replaceChildren(new Option('Choose a block', ''));
    floorSelect.replaceChildren(new Option('Choose a floor', ''));
    if (!selected || !selected.value) { blockWrap.hidden = true; floorWrap.hidden = true; return; }
    let blocks = [];
    try { blocks = JSON.parse(selected.dataset.blocks || '[]'); } catch (_) {}
    if (selected.dataset.hasBlocks === 'true') {
      blocks.forEach((b) => blockSelect.add(new Option(`${b.name} Block`, b.id)));
      if (blockSelect.dataset.selected) blockSelect.value = blockSelect.dataset.selected;
      blockWrap.hidden = false; floorWrap.hidden = true;
    } else {
      for (let n = 1; n <= Number(selected.dataset.floorCount || 0); n += 1) floorSelect.add(new Option(String(n), n));
      if (floorSelect.dataset.selected) floorSelect.value = floorSelect.dataset.selected;
      blockWrap.hidden = true; floorWrap.hidden = false;
    }
  };
  hostel.addEventListener('change', () => { blockSelect.dataset.selected = ''; floorSelect.dataset.selected = ''; render(); });
  if (university) university.addEventListener('change', async () => {
    try {
      const response = await fetch(`/api/hostels?university_id=${encodeURIComponent(university.value)}`, { headers: { Accept: 'application/json' } });
      if (!response.ok) throw new Error('Unable to load hostels');
      blockSelect.dataset.selected = ''; floorSelect.dataset.selected = '';
      const data = await response.json(); rebuild(data.hostels || []); render();
    } catch (_) { rebuild([]); render(); }
  });
  render();
})();
