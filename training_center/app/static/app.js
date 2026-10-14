(function () {
  var body = document.body;
  var panes = [].slice.call(document.querySelectorAll('.tabpane'));
  var links = [].slice.call(document.querySelectorAll('nav a[data-t]'));
  function show(t) {
    if (!panes.some(function (p) { return p.id === t; })) t = body.getAttribute('data-tab') || 'today';
    panes.forEach(function (p) { p.classList.toggle('on', p.id === t); });
    links.forEach(function (a) { a.classList.toggle('on', a.getAttribute('data-t') === t); });
  }
  window.addEventListener('hashchange', function () { show((location.hash || '').slice(1)); window.scrollTo(0, 0); });
  show((location.hash || '').slice(1));

  var ls = null;
  try { ls = window.localStorage; } catch (e) { ls = null; }
  [].slice.call(document.querySelectorAll('details[data-k]')).forEach(function (d) {
    if (!d.hasAttribute('data-force') && ls) {
      try {
        var v = ls.getItem('tc.' + d.getAttribute('data-k'));
        if (v === '1') d.open = true; else if (v === '0') d.open = false;
      } catch (e) {}
    }
    d.addEventListener('toggle', function () {
      try { if (ls) ls.setItem('tc.' + d.getAttribute('data-k'), d.open ? '1' : '0'); } catch (e) {}
    });
  });

  var order = ['auto', 'light', 'dark'], names = { auto: 'Авто', light: 'Светлая', dark: 'Тёмная' };
  [].slice.call(document.querySelectorAll('.theme-form')).forEach(function (f) {
    f.addEventListener('submit', function (ev) {
      ev.preventDefault();
      var v = f.querySelector('input[name=v]').value;
      var next = order[(order.indexOf(v) + 1) % order.length];
      document.documentElement.setAttribute('data-theme', v);
      [].slice.call(document.querySelectorAll('.theme-form')).forEach(function (g) {
        g.querySelector('input[name=v]').value = next;
        g.querySelector('.lbl').textContent = names[v];
      });
      try {
        fetch(f.getAttribute('action'), { method: 'POST', headers: { 'Content-Type': 'application/x-www-form-urlencoded', 'X-Fetch': '1' }, body: 'v=' + encodeURIComponent(v) });
      } catch (e) {}
    });
  });
})();
