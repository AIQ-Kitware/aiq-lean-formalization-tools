/* Injected into every viewer page the server renders.
 *
 * A viewer is a self-contained document that knows nothing about the shell
 * around it, and rewriting seven of them to add navigation would fork each from
 * its static twin. So the shell reaches in instead: declaration names become
 * clickable, every identifier in a Lean block answers for itself, and a click is
 * posted up to the parent frame, which owns routing.
 *
 * Everything here is inert when the page is opened directly, because then there
 * is no server behind it and no shell to route to.
 */
(function(){
  if (window.top === window.self) return;   // opened directly: stay static

  const NAME = /^[A-Za-z_][A-Za-z0-9_'₀-₉!?]*(\.[A-Za-z0-9_'₀-₉!?]+)+$/;
  const link = el => {
    const t = (el.textContent || '').trim();
    if (!NAME.test(t) || t.length > 200 || el.dataset.xr) return;
    el.dataset.xr = '1';
    el.style.cursor = 'pointer';
    el.style.textDecoration = 'underline dotted';
    el.style.textUnderlineOffset = '2px';
    el.title = 'Show every ledger that names ' + t;
    el.addEventListener('click', ev => {
      ev.preventDefault(); ev.stopPropagation();
      parent.postMessage({aiq: 'declaration', name: t}, '*');
    });
  };
  const scan = () => document.querySelectorAll('code, .decl h3 code, td code').forEach(link);
  scan();
  new MutationObserver(scan).observe(document.body, {childList: true, subtree: true});

  document.addEventListener('keydown', ev => {
    if (ev.key === '/' && !/input|textarea/i.test((ev.target.tagName || ''))) {
      ev.preventDefault(); parent.postMessage({aiq: 'focus-search'}, '*');
    }
  });

  /* -- what is this name? ------------------------------------------------
   * The interesting word in a rendered statement is usually a definition the
   * reviewer has not met, and leaving the page to look it up loses the
   * statement. Mark-up is deferred until the pointer first enters a block: an
   * alignment page carries six megabytes of them, and marking every one at load
   * would cost more than the page did.
   */
  const ID = /[\p{L}_][\p{L}\p{N}_'!?₀-₉]*(?:\.[\p{L}_][\p{L}\p{N}_'!?₀-₉]*)*/gu;
  const SKIP = new Set(['c','s','k','t']);   // comments, strings, keywords, sorts: already marked
  const cache = new Map();
  let card = null, hoverTimer = null, hoverFor = null;

  function markBlock(pre){
    if (pre.dataset.aiqIds) return;
    pre.dataset.aiqIds = '1';
    const walker = document.createTreeWalker(pre, NodeFilter.SHOW_TEXT);
    const texts = [];
    for (let n = walker.nextNode(); n; n = walker.nextNode()){
      const parent = n.parentElement;
      if (parent && parent !== pre && [...parent.classList].some(c => SKIP.has(c))) continue;
      if (/[\p{L}]/u.test(n.nodeValue || '')) texts.push(n);
    }
    for (const node of texts){
      const text = node.nodeValue;
      const out = document.createDocumentFragment();
      let last = 0;
      for (const m of text.matchAll(ID)){
        if (m[0].length < 2) continue;
        if (m.index > last) out.appendChild(document.createTextNode(text.slice(last, m.index)));
        const span = document.createElement('span');
        span.className = 'aiq-id';
        span.dataset.n = m[0];
        span.textContent = m[0];
        out.appendChild(span);
        last = m.index + m[0].length;
      }
      if (!last) continue;
      out.appendChild(document.createTextNode(text.slice(last)));
      node.parentNode.replaceChild(out, node);
    }
  }

  function ensureCard(){
    if (card) return card;
    const style = document.createElement('style');
    style.textContent = [
      '.aiq-id:hover{background:rgba(127,127,127,.18);border-radius:3px;cursor:help}',
      '#aiq-card{position:fixed;z-index:99999;max-width:min(560px,60vw);',
      '  font:12.5px/1.55 system-ui,sans-serif;background:var(--panel,#fff);',
      '  color:var(--fg,var(--ink,#1a1c1a));border:1px solid var(--line,#ddd);',
      '  border-radius:11px;box-shadow:0 6px 24px rgba(0,0,0,.18);padding:10px 12px;display:none}',
      '#aiq-card.on{display:block}',
      '#aiq-card h4{margin:0 0 3px;font:600 12.5px ui-monospace,monospace;word-break:break-all}',
      '#aiq-card .k{color:var(--muted,#777);font-size:11px;margin-bottom:7px}',
      '#aiq-card .d{margin:0 0 8px;max-height:9em;overflow:hidden}',
      '#aiq-card pre{background:rgba(127,127,127,.10);border-radius:7px;margin:0 0 8px;',
      '  padding:7px 9px;font:11.5px/1.6 ui-monospace,monospace;max-height:15em;',
      '  overflow:auto;white-space:pre-wrap}',
      '#aiq-card .f{color:var(--muted,#777);font-size:11px}',
    ].join('\n');
    document.head.appendChild(style);
    card = document.createElement('div');
    card.id = 'aiq-card';
    document.body.appendChild(card);
    return card;
  }

  const esc = s => String(s == null ? '' : s)
    .replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));

  function symbol(name){
    if (!cache.has(name))
      cache.set(name, fetch('/api/symbol?name=' + encodeURIComponent(name))
        .then(r => r.ok ? r.json() : null).catch(() => null));
    return cache.get(name);
  }

  function place(el){
    const r = el.getBoundingClientRect(), box = card.getBoundingClientRect();
    card.style.left = Math.max(8, Math.min(r.left, innerWidth - box.width - 14)) + 'px';
    const below = r.bottom + 8;
    card.style.top = (below + box.height > innerHeight - 8
      ? Math.max(8, r.top - box.height - 8) : below) + 'px';
  }

  function hide(){ clearTimeout(hoverTimer); hoverFor = null; if (card) card.classList.remove('on'); }

  document.addEventListener('mouseover', ev => {
    const pre = ev.target.closest && ev.target.closest('pre');
    if (pre) markBlock(pre);
    const el = ev.target.closest && ev.target.closest('.aiq-id');
    if (!el){ if (!card || !card.contains(ev.target)) hide(); return; }
    if (hoverFor === el) return;
    clearTimeout(hoverTimer);
    hoverFor = el;
    hoverTimer = setTimeout(async () => {
      const sym = await symbol(el.dataset.n);
      if (hoverFor !== el || !sym) return;
      el.dataset.q = sym.name;
      el.style.cursor = 'pointer';
      ensureCard().innerHTML =
        '<h4>' + esc(sym.name) + '</h4><div class="k">' + esc(sym.kind || 'declaration')
        + (sym.module ? ' · ' + esc(sym.module) : '') + '</div>'
        + (sym.docstring ? '<div class="d">' + esc(sym.docstring) + '</div>' : '')
        + (sym.signature ? '<pre>' + esc(sym.signature) + '</pre>' : '')
        + '<div class="f">click to audit'
        + (sym.candidates && sym.candidates.length
            ? ' · also matches ' + sym.candidates.map(esc).join(', ') : '')
        + '</div>';
      card.classList.add('on');
      place(el);
    }, 180);
  }, true);

  document.addEventListener('click', ev => {
    const el = ev.target.closest && ev.target.closest('.aiq-id[data-q]');
    if (!el) return;
    ev.preventDefault(); ev.stopPropagation();
    hide();
    parent.postMessage({aiq: 'audit', name: el.dataset.q}, '*');
  }, true);
  addEventListener('scroll', hide, true);

  /* The shell asks for a row by its census id. Viewers anchor rows differently
   * -- an exact id, a prefixed anchor, or nothing at all -- so try the cheap
   * selectors first and fall back to finding the text. */
  addEventListener('message', ev => {
    const m = ev.data || {};
    if (m.aiq === 'theme') {
      if (m.value === 'system') document.documentElement.removeAttribute('data-theme');
      else document.documentElement.setAttribute('data-theme', m.value);
      return;
    }
    if (m.aiq !== 'scroll-to' || !m.id) return;
    const key = (window.CSS && CSS.escape) ? CSS.escape(m.id) : m.id.replace(/[^\w-]/g, '\\$&');
    let el = document.getElementById(m.id)
          || document.querySelector('[id$="-' + key + '"]')
          || document.querySelector('[data-id="' + key + '"]');
    if (!el) {
      for (const c of document.querySelectorAll('code, td, h2, h3')) {
        if ((c.textContent || '').trim() === m.id) { el = c; break; }
      }
    }
    if (!el) return;
    const box = el.closest('section, tr, .row, .decl, article') || el;
    box.scrollIntoView({behavior: 'smooth', block: 'center'});
    const prev = box.style.outline;
    box.style.outline = '2px solid #2f6f4f';
    box.style.outlineOffset = '3px';
    setTimeout(() => { box.style.outline = prev; }, 2200);
  });

  parent.postMessage({aiq: 'ready', title: document.title}, '*');
})();
