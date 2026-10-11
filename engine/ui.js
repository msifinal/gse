(function () {
  'use strict';
  var E = window.StoryEngine;
  var app = document.getElementById('app');
  var story = null, session = null;
  var mediaBase = window.STORY_MEDIA || 'media/';
  var mediaMap = window.STORY_MEDIA_MAP || null;
  var rootEl = null;
  var saveKey = null;
  var modalOpen = null;

  function el(tag, cls, text) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text !== undefined && text !== null) n.textContent = text;
    return n;
  }

  function primaryCharId() {
    var c = story ? story.config.characters : null;
    if (c && c.player) return 'player';
    for (var k in c) { return k; }
    return null;
  }

  function lineSide(line) {
    if (!line.speaker) return 'center';
    return line.speaker === primaryCharId() ? 'right' : 'left';
  }

  function imgUrl(name) {
    if (!name) return '';
    if (/^https?:\/\//i.test(name)) return name;
    if (mediaMap && mediaMap[name]) return mediaMap[name];
    return mediaBase + name;
  }

  function primaryChar() {
    var c = story.config.characters;
    if (c.player) return 'player';
    for (var k in c) return k;
    return null;
  }

  function loadStory(text) {
    var parsed = E.parse(text);
    if (parsed.errors.length) { boot(parsed.errors); return; }
    story = E.buildStory(parsed);
    var res = E.validate(story);
    if (res.errors.length) boot(res.errors.slice(0, 6), true);
    document.title = story.config.title;
    saveKey = 'gse-save-' + story.config.title;
    renderShell();
    startGame();
  }

  function startGame() {
    session = E.createSession(story);
    E.begin(session);
    render(session.lastView);
    note('New game');
  }

  function boot(errors, allowAnyway) {
    app.innerHTML = '';
    var card = el('div', 'editor');
    card.appendChild(el('h2', null, 'Green Story Engine'));
    if (errors && errors.length) {
      var ul = el('ul', 'err');
      errors.forEach(function (e) { ul.appendChild(el('li', null, e)); });
      card.appendChild(ul);
      if (!allowAnyway) { app.appendChild(card); return; }
    }
    var ta = el('textarea', 'editor-ta');
    ta.rows = 16;
    ta.placeholder = 'Paste a story here, then Start.\n\nBetter: open this page via  python cli/gse.py serve <story>   or use   gse.py build.';
    card.appendChild(ta);
    var btn = el('button', 'btn', 'Start');
    btn.onclick = function () {
      app.innerHTML = '';
      try { loadStory(ta.value); }
      catch (e) { boot([String(e)]); }
    };
    card.appendChild(btn);
    card.appendChild(el('p', 'hint', 'gse format: :: name  /  text  /  [[Label|target]]  /  set:: stat +10  /  image:: file.svg  /  speaker:: player  /  :: config {JSON}'));
    app.appendChild(card);
  }

  function renderShell() {
    app.innerHTML = '';
    rootEl = el('div', 'wrap');

    var top = el('div', 'topbar');
    var tl = el('div', 'titlebox');
    tl.appendChild(el('div', 'title', story.config.title));
    if (story.config.credit) tl.appendChild(el('div', 'credit', story.config.credit));
    top.appendChild(tl);
    var nav = el('div', 'nav');
    var bBack = el('button', 'btn small', '? Back');
    var bRestart = el('button', 'btn small', 'Restart');
    bRestart.onclick = function () { startGame(); };
    nav.appendChild(bBack); nav.appendChild(bRestart);
    top.appendChild(nav);
    rootEl.appendChild(top);

    var layout = el('div', 'layout');
    rootEl.appendChild(layout);
    app.appendChild(rootEl);

    rootEl._bBack = bBack;
    rootEl._layout = layout;
    bBack.onclick = function () { var r = E.back(session); if (!r.error) render(r.view); };
  }

  function note(msg) {
    var n = rootEl._note;
    if (!n) return;
    n.textContent = msg;
    n.classList.add('show');
    clearTimeout(n._t);
    n._t = setTimeout(function () { n.classList.remove('show'); }, 1400);
  }

  function saveGame() {
    if (!session || !saveKey) return;
    try {
      localStorage.setItem(saveKey, JSON.stringify(E.snapshot(session)));
      note('Saved');
    } catch (e) { note('Save failed'); }
  }

  function loadGame() {
    if (!session || !saveKey) return;
    var raw = localStorage.getItem(saveKey);
    if (!raw) { note('No save found'); return; }
    try {
      var r = E.restore(session, JSON.parse(raw));
      if (r.error) { note(r.error); return; }
      render(r.view);
      note('Loaded');
    } catch (e) { note('Load failed'); }
    return;
  }

  function resetSave() {
    if (saveKey) localStorage.removeItem(saveKey);
    note('Save cleared');
  }

  function renderSidebar(host, view) {
    var sidebar = el('div', 'sidebar');

    var pid = primaryChar();
    var info = pid && view.portraits[pid];
    if (info) {
      var card = el('div', 'portrait clickable');
      card.title = 'Click for profile';
      card.onclick = function () { showProfile(pid); };
      var img = el('img', 'portrait-img');
      img.alt = info.name;
      if (info.icon && info.icon.img) img.src = imgUrl(info.icon.img);
      else img.hidden = true;
      card.appendChild(img);
      var cap = el('div', 'portrait-cap');
      cap.appendChild(el('div', 'pname', info.name));
      if (info.icon) cap.appendChild(el('div', 'pid', 'icon: ' + info.icon.id));
      card.appendChild(cap);
      sidebar.appendChild(card);
    }

    var charStats = view.stats.filter(function (s) { return s.scope === 'char'; });
    if (charStats.length) {
      var cs = el('div', 'panel');
      cs.appendChild(el('div', 'panel-title', 'Character'));
      charStats.forEach(function (s) { cs.appendChild(statRow(s)); });
      sidebar.appendChild(cs);
    }

    var globals = view.stats.filter(function (s) { return s.scope === 'global'; });
    if (globals.length) {
      var gs = el('div', 'panel');
      gs.appendChild(el('div', 'panel-title', 'Story'));
      globals.forEach(function (s) { gs.appendChild(statRow(s)); });
      sidebar.appendChild(gs);
    }

    var saves = el('div', 'panel');
    saves.appendChild(el('div', 'panel-title', 'Game'));
    var row1 = el('div', 'savebar');
    var bSave = el('button', 'btn small', 'Save');
    var bLoad = el('button', 'btn small', 'Load');
    var bReset = el('button', 'btn small', 'Clear');
    bSave.onclick = saveGame; bLoad.onclick = loadGame; bReset.onclick = resetSave;
    row1.appendChild(bSave); row1.appendChild(bLoad); row1.appendChild(bReset);
    saves.appendChild(row1);
    var noteEl = el('div', 'note');
    rootEl._note = noteEl;
    saves.appendChild(noteEl);
    sidebar.appendChild(saves);

    host.appendChild(sidebar);
  }

  function statRow(s) {
    var row = el('div', 'stat');
    if (s.hint) row.title = s.hint;
    if (s.icon) row.appendChild(el('span', 'sicon', s.icon));
    var lab = el('span', 'slabel', s.label);
    if (s.kind === 'enum') {
      row.appendChild(lab);
      row.appendChild(el('span', 'pill', String(s.value)));
    } else {
      row.appendChild(lab);
      var bar = el('div', 'bar');
      var fill = el('div', 'barfill');
      fill.style.width = Math.max(0, Math.min(100, s.pct)) + '%';
      bar.appendChild(fill);
      row.appendChild(bar);
      row.appendChild(el('span', 'sval', String(s.value)));
    }
    return row;
  }

  function renderBreadcrumbs(host) {
    var crumbs = el('div', 'crumbs');
    session.history.forEach(function (h) {
      crumbs.appendChild(el('span', 'crumb', h.id.replace(/[\-_]+/g, ' ')));
    });
    host.appendChild(crumbs);
  }

  function mergedStats() {
    var out = {};
    if (!session) return out;
    Object.keys(session.char || {}).forEach(function (k) { out[k] = session.char[k]; });
    Object.keys(session.global || {}).forEach(function (k) { out[k] = session.global[k]; });
    return out;
  }

  function showModal(o) {
    var overlay = el('div', 'modal-overlay');
    var box = el('div', 'modal');
    if (o && o.image) {
      var big = el('img', 'modal-img');
      big.src = imgUrl(o.image);
      big.alt = (o && o.title) || '';
      box.appendChild(big);
    }
    if (o && o.title) box.appendChild(el('div', 'modal-name', o.title));
    if (o && o.text) {
      var body = el('div', 'modal-body');
      body.textContent = o.text;
      box.appendChild(body);
    }
    var close = el('button', 'btn small', 'Close');
    close.onclick = function () { closeModal(overlay); };
    box.appendChild(close);
    overlay.appendChild(box);
    overlay.onclick = function (e) { if (e.target === overlay) closeModal(overlay); };
    document.body.appendChild(overlay);
    modalOpen = overlay;
  }

  function showProfile(charId) {
    var c = story.config.characters[charId];
    if (!c) return;
    var icon = E.resolveIcon(story, charId, mergedStats());
    showModal({
      title: c.name || charId,
      text: (session.status && session.status[charId]) || '',
      image: (icon && icon.img) || null
    });
  }

  function openActionModal(target) {
    if (typeof target === 'string' && target.indexOf('modal.') === 0) {
      var m = (story.config.modals || {})[target.slice(6)];
      if (m) { showModal({ title: m.title, text: m.text, image: m.image || null }); return; }
    }
    showProfile(target);
  }

  function closeModal(overlay) {
    if (overlay && overlay.parentNode) overlay.parentNode.removeChild(overlay);
    if (modalOpen === overlay) modalOpen = null;
  }

  function renderChoices(host, view) {
    if (!view.choices.length) return;
    var box = el('div', 'choices');
    view.choices.forEach(function (c, i) {
      var btn = el('button', 'choice', (i + 1) + '. ' + c.label);
      btn.onclick = function () { chooseIdx(i); };
      box.appendChild(btn);
    });
    host.appendChild(box);
  }

  function handleAction(act) {
    if (!act) return;
    if (act.kind === 'open' && act.target) openActionModal(act.target);
    else if (act.kind === 'close') closeModal(modalOpen);
  }

  function renderText(host, line) {
    if (!line.spans || !line.spans.length) {
      host.appendChild(document.createTextNode(line.text));
      return;
    }
    var pos = 0;
    line.spans.forEach(function (s) {
      if (s.from > pos) host.appendChild(document.createTextNode(line.text.slice(pos, s.from)));
      var btn = el('button', 'act', line.text.slice(s.from, s.to));
      btn.type = 'button';
      btn.onclick = (function (a) { return function () { handleAction(a); }; })({ kind: s.kind, target: s.target });
      host.appendChild(btn);
      pos = s.to;
    });
    if (pos < line.text.length) host.appendChild(document.createTextNode(line.text.slice(pos)));
  }

  function chooseIdx(i) {
    var r = E.choose(session, i);
    if (!r.error) render(r.view);
  }

  function render(view) {
    var layout = rootEl._layout;
    if (!layout) return;
    layout.innerHTML = '';
    window.scrollTo(0, 0);

    var side = el('div', 'side');
    renderSidebar(side, view);
    layout.appendChild(side);

    var main = el('div', 'main');
    renderBreadcrumbs(main);

    var heading = el('div', 'stage-head');
    heading.appendChild(el('div', 'ptitle', view.title));
    if (view.tags && view.tags.length) heading.appendChild(el('div', 'ptags', '#' + view.tags.join(' #')));
    main.appendChild(heading);

    var sc = el('div', 'scene');
    (view.media || []).forEach(function (m) {
      if (!m.path) return;
      var img = el('img', 'sceneimg');
      img.src = imgUrl(m.path);
      img.alt = m.caption || m.path;
      sc.appendChild(img);
      if (m.caption) sc.appendChild(el('div', 'scenecap', m.caption));
    });
    if (sc.childNodes.length) main.appendChild(sc);

    var prose = el('div', 'prose');
    view.lines.forEach(function (line) {
      if (line.type === 'media') {
        var fig = el('figure', 'mediafig');
        if (line.path) {
          var mimg = el('img', 'media');
          mimg.src = imgUrl(line.path);
          mimg.alt = line.caption || line.path;
          fig.appendChild(mimg);
        }
        if (line.caption) fig.appendChild(el('figcaption', null, line.caption));
        prose.appendChild(fig);
        return;
      }
      var p = el('p', 'para ' + lineSide(line));
      if (line.speaker) {
        var chip = el('span', 'speaker spclick');
        var sp = story.config.characters[line.speaker];
        chip.title = 'icon: ' + (line.icon ? line.icon.id : '?') + ' — click for profile';
        chip.appendChild(el('span', 'spname', (sp && sp.name) || line.speaker));
        if (line.icon && line.icon.img) {
          var simg = el('img', 'spimg');
          simg.src = imgUrl(line.icon.img);
          simg.alt = '';
          chip.appendChild(simg);
        }
        chip.appendChild(el('span', 'spcolon', ':'));
        chip.onclick = function () { showProfile(line.speaker); };
        p.appendChild(chip);
      }
      renderText(p, line);
      prose.appendChild(p);
    });
    main.appendChild(prose);

    if (view.effects.length) {
      var fx = el('div', 'fx');
      view.effects.forEach(function (e) { fx.appendChild(el('span', 'fxchip', e.text)); });
      main.appendChild(fx);
    }

    renderChoices(main, view);

    if (view.ending) {
      var end = el('div', 'end');
      end.appendChild(el('div', null, 'The End'));
      var again = el('button', 'btn', 'Play again');
      again.onclick = function () { startGame(); };
      end.appendChild(again);
      main.appendChild(end);
    } else if (view.continuation) {
      var cont = el('button', 'choice', 'Continue');
      cont.onclick = function () { var r = E.goOn(session); if (!r.error) render(r.view); };
      main.appendChild(cont);
    }

    var hist = el('div', 'hist');
    hist.appendChild(el('p', 'hint', '1-9 choose  B back  R restart  S save  L load'));
    main.appendChild(hist);
    layout.appendChild(main);
  }

  function key(e) {
    if (e.ctrlKey || e.metaKey || e.altKey) return;
    var k = e.key.toLowerCase(), tag = (e.target || {}).tagName;
    if (k === 'escape' && modalOpen) { closeModal(modalOpen); e.preventDefault(); return; }
    if (tag === 'TEXTAREA' || tag === 'INPUT') return;
    if (!session || !story || !rootEl) return;
    var view = session.lastView;
    if (/^[1-9]$/.test(k)) {
      var i = Number(k) - 1;
      if (view.choices[i]) { chooseIdx(i); e.preventDefault(); }
      return;
    }
    if (k === 'b' && session.history.length > 1) {
      var r = E.back(session);
      if (!r.error) render(r.view);
      e.preventDefault();
    } else if (k === 'r') { startGame(); e.preventDefault(); }
    else if (k === 's') { saveGame(); e.preventDefault(); }
    else if (k === 'l') { loadGame(); e.preventDefault(); }
  }

  document.addEventListener('keydown', key);
  if (window.STORY_TEXT) { loadStory(window.STORY_TEXT); }
  else { boot([]); }
})();