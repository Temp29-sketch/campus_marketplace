(() => {
  const menu = document.querySelector('.notification-menu');
  if (!menu) return;
  const count = menu.querySelector('.notification-count');
  const items = menu.querySelector('.notification-items');
  const csrf = menu.dataset.csrfToken;
  const esc = (value) => { const el = document.createElement('span'); el.textContent = value || ''; return el.textContent; };
  const refresh = async () => {
    if (document.hidden) return;
    try {
      const response = await fetch(menu.dataset.endpoint, {headers: {'Accept': 'application/json'}});
      if (!response.ok) return;
      const data = await response.json();
      count.textContent = data.unread_count > 99 ? '99+' : String(data.unread_count);
      count.hidden = data.unread_count === 0;
      items.replaceChildren();
      if (!data.items.length) { const empty = document.createElement('p'); empty.className = 'muted'; empty.textContent = "You're all caught up"; items.append(empty); }
      data.items.forEach((item) => {
        const form = document.createElement('form'); form.method = 'post'; form.action = `/notifications/${item.id}/read`; form.className = `notification-row ${item.is_read ? '' : 'unread'}`;
        const token = document.createElement('input'); token.type = 'hidden'; token.name = 'csrf_token'; token.value = csrf;
        const next = document.createElement('input'); next.type = 'hidden'; next.name = 'next'; next.value = item.link_url;
        const button = document.createElement('button'); button.type = 'submit'; button.className = 'notification-item';
        const title = document.createElement('strong'); title.textContent = esc(item.title);
        const body = document.createElement('span'); body.textContent = esc(item.body);
        const time = document.createElement('small'); time.textContent = item.time_ago;
        button.append(title, body, time); if (!item.is_read) { const dot = document.createElement('i'); dot.className = 'unread-dot'; dot.setAttribute('aria-label', 'Unread'); button.append(dot); }
        form.append(token, next, button); items.append(form);
      });
      const all = document.createElement('form'); all.method = 'post'; all.action = '/notifications/read-all'; all.className = 'mark-all-form';
      const token = document.createElement('input'); token.type = 'hidden'; token.name = 'csrf_token'; token.value = csrf;
      const next = document.createElement('input'); next.type = 'hidden'; next.name = 'next'; next.value = window.location.pathname;
      const button = document.createElement('button'); button.type = 'submit'; button.className = 'link'; button.textContent = 'Mark all as read'; all.append(token, next, button); items.append(all);
    } catch (_) { /* Keep the shell usable if notifications are temporarily unavailable. */ }
  };
  refresh(); window.setInterval(refresh, 60000); document.addEventListener('visibilitychange', refresh);
  menu.addEventListener('toggle', () => { if (menu.open) refresh(); });
  document.addEventListener('click', (event) => { if (menu.open && !menu.contains(event.target)) menu.open = false; });
  document.addEventListener('keydown', (event) => { if (event.key === 'Escape' && menu.open) { menu.open = false; menu.querySelector('summary').focus(); } });
})();
