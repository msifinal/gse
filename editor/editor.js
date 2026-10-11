/* Green Story Engine — visual story editor.
 * Loads a gse .gse story through engine/core.js, edits it as beats/variables/
 * characters/links (no markup needed), and serializes back to gse text.
 * Light/dark theme, saves via /api/story (served by `gse.py edit`).
 */
(function () {
  'use strict';

  var GLOB = typeof window !== 'undefined' ? window
          : (typeof self !== 'undefined' ? self : this);
  var E = GLOB.StoryEngine;
  var mediaBase = '/media/';
  var model = null;          // editable story model
  var dirty = false;
  var view = 'beats';        // 'beats' | 'settings'
  var selBeat = null;        // selected beat name (or null)
  var settingsTab = 'general';
  var lastResult = null;     // {ok, errors, warnings} from last save

  /* ---------------------------------------------------------------- helpers */
  function el(tag, cls, text) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text !== undefined && text !== null) n.textContent = text;
    return n;
  }
  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }
  function slug(s) {
    return String(s || '').toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '') || 'beat';
  }
  function uniqueName(base, names) {
    var s = slug(base), out = s, k = 2;
    while (names.indexOf(out) > -1) { out = s + '_' + k; k++; }
    return out;
  }
  function showMsg(text, kind, list) {
    var m = document.getElementById('msg');
    m.hidden = false;
    m.className = 'msg ' + (kind || 'ok');
    m.innerHTML = '';
    m.appendChild(el('span', null, text));
    if (list && list.length) {
      var ul = el('ul');
      list.forEach(function (x) { ul.appendChild(el('li', null, x)); });
      m.appendChild(ul);
    }
    clearTimeout(showMsg._t);
    showMsg._t = setTimeout(function () { m.hidden = true; }, list && list.length ? 7000 : 2600);
  }

  /* ---------------------------------------------------------------- data model */
  function defToVar(id, def, scope) {
    def = def || {};
    if (def.values && def.values.length) {
      var vals = def.values.map(String);
      return { id: id, scope: scope, kind: 'list', label: def.label || id,
               values: vals, start: def.start === undefined ? (vals[0] || '') : String(def.start),
               hidden: !!def.hidden };
    }
    return { id: id, scope: scope, kind: 'range', label: def.label || id,
             min: Number(def.min === undefined ? 0 : def.min),
             max: Number(def.max === undefined ? 100 : def.max),
             start: Number(def.start === undefined ? 50 : def.start),
             hidden: !!def.hidden };
  }

  function rulesFromWhen(when) {
    var rules = [];
    Object.keys(when || {}).forEach(function (k) {
      var c = when[k];
      if (typeof c === 'string') { rules.push({ stat: k, op: '==', value: c }); return; }
      if (Array.isArray(c)) throw new Error('array matcher');
      if (c && typeof c === 'object') {
        var allowed = ['min', 'max', 'gt', 'lt'];
        Object.keys(c).forEach(function (kk) { if (allowed.indexOf(kk) === -1) throw new Error('matcher key ' + kk); });
        if (c.min !== undefined) rules.push({ stat: k, op: '>=', value: String(c.min) });
        if (c.max !== undefined) rules.push({ stat: k, op: '<=', value: String(c.max) });
        if (c.gt !== undefined) rules.push({ stat: k, op: '>', value: String(c.gt) });
        if (c.lt !== undefined) rules.push({ stat: k, op: '<', value: String(c.lt) });
        return;
      }
      throw new Error('complex matcher');
    });
    return rules;
  }

  function iconFrom(ic) {
    var base = { id: ic.id || 'icon', img: ic.img || '' };
    if (ic.when === undefined || ic.when === null) { base.rules = []; return base; }
    try {
      base.rules = rulesFromWhen(ic.when);
    } catch (e) {
      base.rawWhen = JSON.stringify(ic.when);
    }
    return base;
  }

  function beatFrom(p) {
    var lines = [];
    p.blocks.forEach(function (b) {
      if (b.type !== 'text') return;
      b.text.forEach(function (ln) { lines.push({ speaker: b.speaker || null, text: ln }); });
    });
    var effects = p.effects.map(function (e) {
      if (e.op === 'roll') return { stat: e.stat, scope: scopeOfStat(e.stat), op: 'roll', value: e.expr };
      return { stat: e.stat, scope: scopeOfStat(e.stat), op: e.op, value: e.value };
    });
    var choices = p.choices.map(function (c) {
      return { label: c.label, target: c.target,
               guard: c.guard ? { stat: c.guard.stat, scope: scopeOfStat(c.guard.stat), op: c.guard.op, value: c.guard.value } : null };
    });
    return { name: p.name, tags: (p.tags || []).slice(), lines: lines,
             media: (p.media || []).map(function (m) { return { path: m.path, caption: m.caption }; }),
             effects: effects, choices: choices, goto: p.goto || null };
  }

  function scopeOfStat(id) {
    if (!model) return null;
    var bare = String(id).charAt(0) === '@' ? String(id).slice(1) : String(id);
    for (var i = 0; i < model.variables.length; i++) {
      if (model.variables[i].id === bare) return model.variables[i].scope;
    }
    return null;
  }

  function modelFromStory(story, meta) {
    var variables = [];
    Object.keys(story.config.charStats).forEach(function (id) {
      variables.push(defToVar(id, story.config.charStats[id], 'char'));
    });
    Object.keys(story.config.globals).forEach(function (id) {
      variables.push(defToVar(id, story.config.globals[id], 'global'));
    });
    var characters = Object.keys(story.config.characters).map(function (cid) {
      return { id: cid, name: story.config.characters[cid].name || cid,
               icons: story.config.characters[cid].icons.map(iconFrom) };
    });
    var modals = Object.keys(story.config.modals || {}).map(function (mid) {
      var m = story.config.modals[mid];
      return { id: mid, title: m.title || '', image: m.image || '', text: m.text || '' };
    });
    var beats = Object.keys(story.passages).map(function (name) {
      return beatFrom(story.passages[name]);
    });
    var m = { meta: meta || {}, title: story.config.title || 'Untitled Story',
              credit: story.config.credit || '', start: story.config.start || '',
              mediaDir: story.config.mediaDir || 'media',
              variables: variables, characters: characters, modals: modals, beats: beats };
    model = m;
    return m;
  }

  /* ---------------------------------------------------------------- serialize */
  function varById(id) {
    for (var i = 0; i < model.variables.length; i++) {
      if (model.variables[i].id === id) return model.variables[i];
    }
    return null;
  }
  function refText(v) {
    if (!v) return '';
    var bare = String(v.stat).replace(/^@/, '');
    var def = varById(bare);
    var pref = '';
    if (def && def.scope === 'global') {
      for (var i = 0; i < model.variables.length; i++) {
        if (model.variables[i].scope === 'char' && model.variables[i].id === bare) { pref = '@'; break; }
      }
    }
    if (v.op === 'roll') {
      return bare === 'lastroll' ? String(v.value) : pref + bare + ' = ' + String(v.value);
    }
    return pref + bare + ' ' + v.op + ' ' + String(v.value);
  }

  function mergeBound(cur, add) {
    var base = (cur && typeof cur === 'object' && !Array.isArray(cur)) ? cur : {};
    var out = {};
    if (base.min !== undefined) out.min = base.min;
    if (base.max !== undefined) out.max = base.max;
    if (base.gt !== undefined) out.gt = base.gt;
    if (base.lt !== undefined) out.lt = base.lt;
    if (add.min !== undefined) out.min = out.min === undefined ? add.min : Math.max(out.min, add.min);
    if (add.max !== undefined) out.max = out.max === undefined ? add.max : Math.min(out.max, add.max);
    if (add.gt !== undefined) out.gt = out.gt === undefined ? add.gt : Math.max(out.gt, add.gt);
    if (add.lt !== undefined) out.lt = out.lt === undefined ? add.lt : Math.min(out.lt, add.lt);
    return out;
  }

  function whenFromIcon(ic) {
    if (ic.rawWhen) {
      try { return JSON.parse(ic.rawWhen); } catch (e) { return undefined; }
    }
    if (!ic.rules || !ic.rules.length) return undefined;
    var when = {};
    ic.rules.forEach(function (r) {
      var tr = String(r.value).trim();
      var isNum = /^-?\d+(\.\d+)?$/.test(tr);
      var k = r.stat;
      if (r.op === '==') {
        if (isNum) { var n = Number(tr); when[k] = mergeBound(when[k], { min: n, max: n }); }
        else when[k] = tr;
      } else if (r.op === '>=') when[k] = mergeBound(when[k], { min: Number(tr) });
      else if (r.op === '>') when[k] = mergeBound(when[k], { gt: Number(tr) });
      else if (r.op === '<=') when[k] = mergeBound(when[k], { max: Number(tr) });
      else if (r.op === '<') when[k] = mergeBound(when[k], { lt: Number(tr) });
    });
    return when;
  }

  function configFromModel() {
    var charStats = {}, globals = {};
    model.variables.forEach(function (v) {
      var d = { label: v.label || v.id };
      if (v.hidden) d.hidden = true;
      if (v.kind === 'list') {
        var vals = (v.values || []).filter(function (x) { return String(x).trim(); }).map(String);
        if (!vals.length) return;
        d.values = vals;
        if (vals.indexOf(String(v.start)) === -1) d.start = vals[0];
        else d.start = String(v.start);
      } else {
        d.min = Number(v.min); d.max = Number(v.max); d.start = Number(v.start);
      }
      (v.scope === 'global' ? globals : charStats)[v.id] = d;
    });
    var characters = {};
    model.characters.forEach(function (c) {
      var icons = [];
      c.icons.forEach(function (ic) {
        var icon = { id: ic.id || 'icon' };
        if (ic.img) icon.img = ic.img;
        var w = whenFromIcon(ic);
        if (w) icon.when = w;
        icons.push(icon);
      });
      characters[c.id] = { name: c.name || c.id, icons: icons };
    });
    var modals = {};
    (model.modals || []).forEach(function (m) {
      var d = {};
      if (m.title) d.title = m.title;
      if (m.image) d.image = m.image;
      if (m.text) d.text = m.text;
      modals[m.id] = d;
    });
    var cfg = { title: model.title || 'Untitled Story' };
    if (model.credit) cfg.credit = model.credit;
    cfg.start = model.start || (model.beats.length ? model.beats[0].name : 'start');
    cfg.mediaDir = model.mediaDir || 'media';
    cfg.charStats = charStats;
    cfg.globals = globals;
    cfg.characters = characters;
    if (Object.keys(modals).length) cfg.modals = modals;
    return cfg;
  }

  function beatText(b) {
    var lines = [':: ' + b.name + (b.tags.length ? ' [' + b.tags.join(' ') + ']' : '')];
    b.effects.forEach(function (e) {
      lines.push((e.op === 'roll' ? 'roll:: ' : 'set:: ') + refText(e));
    });
    b.media.forEach(function (m) {
      lines.push('image:: ' + m.path + (m.caption ? ' ' + m.caption : ''));
    });
    var curSp = null, emitted = false;
    (b.lines || []).forEach(function (ln) {
      var t = String(ln.text || '').trim();
      if (!t) return;
      var sp = ln.speaker || null;
      if (sp !== curSp) {
        curSp = sp;
        if (sp) lines.push('speaker:: ' + sp);
        else if (emitted) lines.push('speaker:: none');
        emitted = true;
      }
      lines.push(ln.text);
    });
    b.choices.forEach(function (c) {
      var line = '[[' + c.label + '|' + c.target + ']]';
      if (c.guard) line += ' if:: ' + refText(c.guard);
      lines.push(line);
    });
    if (b.goto) lines.push('goto:: ' + b.goto);
    return lines.join('\n');
  }

  function textFromModel() {
    var out = [':: config', JSON.stringify(configFromModel(), null, 2), ''];
    model.beats.forEach(function (b) {
      out.push(beatText(b));
      out.push('');
    });
    return out.join('\n');
  }

  /* ---------------------------------------------------------------- actions */
  function mark() {
    dirty = true;
    document.getElementById('dirty').hidden = false;
  }
  function currentBeat() {
    if (!selBeat) return null;
    for (var i = 0; i < model.beats.length; i++) {
      if (model.beats[i].name === selBeat) return model.beats[i];
    }
    return null;
  }
  function beatIndex(name) {
    for (var i = 0; i < model.beats.length; i++) if (model.beats[i].name === name) return i;
    return -1;
  }
  function linkCountTo(name) {
    var n = 0;
    model.beats.forEach(function (b) {
      b.choices.forEach(function (c) { if (c.target === name) n++; });
      if (b.goto === name) n++;
    });
    if (model.start === name) n++;
    return n;
  }
  function renameBeat(oldName, newName) {
    if (oldName === newName) return;
    model.beats.forEach(function (b) {
      b.choices.forEach(function (c) { if (c.target === oldName) c.target = newName; });
      if (b.goto === oldName) b.goto = newName;
    });
    if (model.start === oldName) model.start = newName;
    if (selBeat === oldName) selBeat = newName;
  }
  function renameVar(oldId, newId) {
    if (oldId === newId) return;
    model.variables.forEach(function (v) { if (v.id === oldId) v.id = newId; });
    model.beats.forEach(function (b) {
      b.effects.forEach(function (e) { if (e.stat === oldId) e.stat = newId; });
      b.choices.forEach(function (c) { if (c.guard && c.guard.stat === oldId) c.guard.stat = newId; });
    });
    model.characters.forEach(function (c) {
      c.icons.forEach(function (ic) {
        if (!ic.rules) return;
        ic.rules.forEach(function (r) { if (r.stat === oldId) r.stat = newId; });
      });
    });
  }

  function applyDownloaded(map) {
    model.meta.mediaFiles = model.meta.mediaFiles || [];
    Object.keys(map).forEach(function (url) {
      var f = map[url];
      model.beats.forEach(function (b) {
        b.media.forEach(function (m) { if (m.path === url) m.path = f; });
      });
      model.characters.forEach(function (c) {
        c.icons.forEach(function (ic) { if (ic.img === url) ic.img = f; });
      });
      if (model.meta.mediaFiles.indexOf(f) === -1) model.meta.mediaFiles.push(f);
    });
    model.meta.mediaFiles.sort();
    renderMain();
  }

  function saveStory() {
    var text = textFromModel();
    fetch('/api/story', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text: text })
    }).then(function (r) { return r.json(); }).then(function (res) {
      if (res.ok) {
        dirty = false;
        document.getElementById('dirty').hidden = true;
        if (res.downloaded) {
          var dls = Object.keys(res.downloaded);
          applyDownloaded(res.downloaded);
          showMsg('Saved. Downloaded image' + (dls.length > 1 ? 's' : '') + ' into ' + (model.mediaDir || 'media') + ':', 'ok', dls.map(function (u) { return res.downloaded[u]; }));
        } else if (res.warnings && res.warnings.length) {
          showMsg('Saved. ' + res.errors.length + ' error(s), ' + res.warnings.length + ' warning(s) to review', 'warn', res.errors.concat(res.warnings).slice(0, 8));
        } else {
          showMsg('Saved. Story is valid.', 'ok');
        }
      } else {
        showMsg('Not saved — the story text has structural errors', 'err', (res.errors || []).slice(0, 10));
      }
    }, function () {
      showMsg('Save failed', 'err');
    });
  }

  /* ---------------------------------------------------------------- rendering */
  function renderTop() {
    var d = document.getElementById('dirty');
    d.hidden = !dirty;
  }

  function renderBeatList() {
    var list = document.getElementById('beat-list');
    list.innerHTML = '';
    model.beats.forEach(function (b) {
      var item = el('button', 'beat-item' + (b.name === selBeat ? ' sel' : ''));
      var nm = el('span', 'beat-name', b.name);
      item.appendChild(nm);
      if (b.name === model.start) item.appendChild(el('span', 'badge start', 'START'));
      var isEnd = !b.choices.length && !b.goto;
      if (isEnd) item.appendChild(el('span', 'badge end', 'END'));
      item.onclick = function () { selectBeat(b.name); };
      list.appendChild(item);
    });
  }

  function selectBeat(name) {
    selBeat = name;
    view = 'beats';
    document.getElementById('tab-beats').classList.add('active');
    document.getElementById('tab-settings').classList.remove('active');
    renderBeatList();
    renderMain();
  }

  function optionList(sel, values, selected) {
    sel.innerHTML = '';
    values.forEach(function (v) {
      var o = document.createElement('option');
      o.value = v;
      o.textContent = v;
      if (String(v) === String(selected)) o.selected = true;
      sel.appendChild(o);
    });
  }

  function isUrlImg(s) {
    return /^https?:\/\//i.test(String(s || ''));
  }
  function previewUrl(name) {
    return isUrlImg(name) ? name : mediaBase + name;
  }
  function mediaInput(value, onInput, placeholder) {
    var inp = document.createElement('input');
    inp.className = 'input';
    inp.value = value || '';
    inp.placeholder = placeholder || 'pick a file or paste an image URL';
    inp.setAttribute('list', 'gse-media-list');
    inp.oninput = function () { onInput(inp.value); };
    var dl = document.getElementById('gse-media-list');
    if (!dl) {
      dl = document.createElement('datalist');
      dl.id = 'gse-media-list';
      document.body.appendChild(dl);
    }
    dl.innerHTML = '';
    availableMedia().forEach(function (f) {
      var o = document.createElement('option');
      o.value = f;
      dl.appendChild(o);
    });
    return inp;
  }

  /* ---- beat editor */
  function effectRow(beat, e) {
    var row = el('div', 'row effect');
    var selVar = document.createElement('select');
    selVar.className = 'input';
    var opts = model.variables.map(function (v) {
      return { id: v.id, label: v.label + ' (' + (v.scope === 'global' ? 'story' : 'character') + ')' };
    });
    opts.forEach(function (o) {
      var op = document.createElement('option');
      op.value = o.id;
      op.textContent = o.label;
      if (o.id === e.stat) op.selected = true;
      selVar.appendChild(op);
    });
    selVar.onchange = function () { e.stat = selVar.value; e.scope = scopeOfStat(e.stat); refreshValue(); mark(); };
    selVar.title = 'Variable to change';
    row.appendChild(selVar);

    var selOp = document.createElement('select');
    selOp.className = 'input';
    ['+', '-', '=', 'roll'].forEach(function (o) {
      var op = document.createElement('option');
      op.value = o; op.textContent = o === '=' ? 'set to' : (o === 'roll' ? 'roll' : o);
      if (o === e.op) op.selected = true;
      selOp.appendChild(op);
    });
    selOp.onchange = function () { e.op = selOp.value; e.scope = scopeOfStat(e.stat); refreshValue(); mark(); };
    row.appendChild(selOp);

    var vInput = valueInput(e);
    vInput.className = 'input';
    vInput.oninput = function () { e.value = vInput.value; mark(); };
    row.appendChild(vInput);

    var rm = el('button', 'btn icon danger', '\u00d7');
    rm.title = 'Remove effect';
    rm.onclick = function () { removeFrom(beat.effects, e); renderBeatPanel(); mark(); };
    row.appendChild(rm);
    function refreshValue() {
      var nv = valueInput(e);
      nv.className = 'input';
      nv.oninput = function () { e.value = nv.value; mark(); };
      row.replaceChild(nv, vInput);
      vInput = nv;
    }
    return row;
  }

  function valueInput(e) {
    var def = varById(e.stat);
    if (def && def.kind === 'list' && e.op === '=') {
      var s = document.createElement('select');
      s.className = 'input';
      optionList(s, def.values, e.value);
      s.onchange = function () { e.value = s.value; mark(); };
      return s;
    }
    var i = document.createElement('input');
    i.type = 'text';
    i.value = e.value === undefined ? '' : String(e.value);
    i.placeholder = 'value';
    return i;
  }

  function removeFrom(arr, item) {
    var i = arr.indexOf(item);
    if (i > -1) arr.splice(i, 1);
  }

  function guardEditor(beat, guard, host) {
    var label = el('div', null);
    label.appendChild(el('span', 'choice-label', 'Only if'));
    host.appendChild(label);
    var g = el('div', 'row guard-row');
    var selVar = document.createElement('select');
    selVar.className = 'input';
    model.variables.forEach(function (v) {
      var op2 = document.createElement('option');
      op2.value = v.id; op2.textContent = v.label;
      if (guard.stat === v.id) op2.selected = true;
      selVar.appendChild(op2);
    });
    selVar.onchange = function () { guard.stat = selVar.value; guard.scope = scopeOfStat(guard.stat); refresh(); mark(); };
    g.appendChild(selVar);
    var selOp = document.createElement('select');
    selOp.className = 'input';
    var ops = ['>=', '>', '<=', '<', '==', '!='];
    ops.forEach(function (o) {
      var op2 = document.createElement('option');
      op2.value = o; op2.textContent = o;
      if (o === guard.op) op2.selected = true;
      selOp.appendChild(op2);
    });
    selOp.onchange = function () { guard.op = selOp.value; mark(); };
    g.appendChild(selOp);
    var vin = document.createElement('input');
    vin.type = 'text';
    vin.className = 'input';
    vin.value = String(guard.value === undefined ? '' : guard.value);
    vin.oninput = function () { guard.value = vin.value; mark(); };
    g.appendChild(vin);
    host.appendChild(g);
    function refresh() {
      var def = varById(guard.stat);
      if (def && def.kind === 'list') {
        var s = document.createElement('select');
        s.className = 'input';
        optionList(s, def.values, guard.value);
        s.onchange = function () { guard.value = s.value; mark(); };
        g.replaceChild(s, vin);
        vin = s;
      } else if (vin.tagName === 'SELECT') {
        var i = document.createElement('input');
        i.type = 'text';
        i.className = 'input';
        i.value = String(guard.value === undefined ? '' : guard.value);
        i.oninput = function () { guard.value = i.value; mark(); };
        g.replaceChild(i, vin);
        vin = i;
      }
    }
  }

  function choiceRow(beat, c) {
    var box = el('div', 'panel row-choice');
    var row = el('div', 'row choice');
    var lab = document.createElement('input');
    lab.className = 'input';
    lab.value = c.label;
    lab.placeholder = 'Button text';
    lab.oninput = function () { c.label = lab.value; mark(); };
    row.appendChild(lab);

    var selT = document.createElement('select');
    selT.className = 'input';
    optionList(selT, beatNames().concat(['+ New beat\u2026']), c.target);
    selT.onchange = function () {
      if (selT.value === '+ New beat\u2026') {
        var nb = addBeat();
        if (nb) { c.target = nb; renderBeatPanel(); return; }
        selT.value = c.target;
        return;
      }
      c.target = selT.value;
      mark();
    };
    selT.title = 'Link to beat';
    row.appendChild(selT);

    var rm = el('button', 'btn icon danger', '\u00d7');
    rm.title = 'Remove link';
    rm.onclick = function () { removeFrom(beat.choices, c); renderBeatPanel(); mark(); };
    row.appendChild(rm);
    box.appendChild(row);

    var guardbox = el('div');
    if (c.guard) {
      guardEditor(beat, c.guard, guardbox);
    }
    var addG = el('button', 'muted-btn small', c.guard ? 'Remove condition' : '+ Condition');
    addG.onclick = function () {
      if (c.guard) { c.guard = null; renderBeatPanel(); }
      else {
        c.guard = { stat: model.variables.length ? model.variables[0].id : '', scope: model.variables.length ? model.variables[0].scope : null, op: '>=', value: '' };
        renderBeatPanel();
      }
      mark();
    };
    box.appendChild(addG);
    return box;
  }

  function beatNames() {
    return model.beats.map(function (b) { return b.name; });
  }

  function primaryCharId() {
    for (var i = 0; i < model.characters.length; i++) {
      if (model.characters[i].id === 'player') return 'player';
    }
    return model.characters.length ? model.characters[0].id : null;
  }
  function alignOf(sp) {
    if (!sp) return 'center';
    return sp === primaryCharId() ? 'right' : 'left';
  }
  function dialogueRow(beat, ln) {
    var row = el('div', 'row dlg');
    var sel = document.createElement('select');
    sel.className = 'input dlg-sel';
    var noneOpt = document.createElement('option');
    noneOpt.value = '';
    noneOpt.textContent = 'Narrator (center)';
    if (!ln.speaker) noneOpt.selected = true;
    sel.appendChild(noneOpt);
    model.characters.forEach(function (c) {
      var o = document.createElement('option');
      o.value = c.id;
      o.textContent = c.name || c.id;
      if (c.id === ln.speaker) o.selected = true;
      sel.appendChild(o);
    });
    var side = el('span', 'dlg-side', alignOf(ln.speaker));
    sel.onchange = function () {
      ln.speaker = sel.value || null;
      side.textContent = alignOf(ln.speaker);
      mark();
    };
    var inp = document.createElement('input');
    inp.className = 'input dlg-text';
    inp.placeholder = 'line text';
    inp.value = ln.text;
    inp.oninput = function () { ln.text = inp.value; mark(); };
    var rm = el('button', 'btn icon danger', '\u00d7');
    rm.onclick = function () { removeFrom(beat.lines, ln); renderBeatPanel(); mark(); };
    row.appendChild(sel);
    row.appendChild(side);
    row.appendChild(inp);
    row.appendChild(rm);
    return row;
  }

  function renderBeatPanel() {
    var main = document.getElementById('main');
    main.innerHTML = '';
    var b = currentBeat();
    if (!b) {
      var empty = el('div', 'panel');
      empty.appendChild(el('p', null, 'Select a beat from the list, or add a new one.'));
      empty.appendChild(el('p', 'hint', 'Beats are the story beats: each one is a moment of the story. Add text, change variables, and link it to other beats with choices.'));
      main.appendChild(empty);
      return;
    }
    var last = model.beats[model.beats.length - 1] === b;

    var head = el('div', 'panel');
    var hrow = el('div', 'beat-head');
    var nameIn = document.createElement('input');
    nameIn.className = 'beat-title';
    nameIn.value = b.name;
    nameIn.onchange = function () {
      var nn = uniqueName(nameIn.value, model.beats.map(function (x) { return x.name; }).filter(function (n) { return n !== b.name; }));
      renameBeat(b.name, nn);
      b.name = nn;
      nameIn.value = nn;
      renderBeatList();
      mark();
    };
    hrow.appendChild(nameIn);
    var tagIn = document.createElement('input');
    tagIn.className = 'input beat-tags';
    tagIn.value = b.tags.join(' ');
    tagIn.placeholder = 'tags (optional)';
    tagIn.oninput = function () { b.tags = tagIn.value.split(/[\s,]+/).filter(Boolean); mark(); };
    hrow.appendChild(tagIn);
    if (b.name === model.start) hrow.appendChild(el('span', 'badge start', 'START BEAT'));
    var isEnd = !b.choices.length && !b.goto;
    if (isEnd) hrow.appendChild(el('span', 'ending-note', 'Ending \u2014 no links here means the story can end'));
    head.appendChild(hrow);
    var ctrl = el('div', 'btn-row');
    ctrl.appendChild(el('button', 'btn small', 'Set as start beat'));
    ctrl.lastChild.onclick = function () { model.start = b.name; renderBeatList(); mark(); };
    ctrl.appendChild(el('button', 'btn small danger', 'Delete beat'));
    ctrl.lastChild.onclick = function () { deleteBeat(b.name); };
    head.appendChild(ctrl);
    main.appendChild(head);

    /* dialogue */
    var tp = el('div', 'panel');
    var tt = el('div', 'panel-title', 'Dialogue');
    var addLn = el('button', 'muted-btn small', '+ Add line');
    addLn.onclick = function () {
      b.lines.push({ speaker: primaryCharId() || null, text: '' });
      renderBeatPanel();
      mark();
    };
    tt.appendChild(addLn);
    tp.appendChild(tt);
    tp.appendChild(el('p', 'hint', 'Right = the player, left = other characters, center = the narrator.'));
    if (!b.lines.length) tp.appendChild(el('p', 'hint', 'No dialogue yet. Add lines for a character or the narrator.'));
    b.lines.forEach(function (ln) { tp.appendChild(dialogueRow(b, ln)); });
    main.appendChild(tp);

    /* image */
    var mp = el('div', 'panel');
    mp.appendChild(el('div', 'panel-title', 'Scene image (optional)'));
    var imgRow = el('div', 'field');
    imgRow.appendChild(el('label', 'flabel', 'Image'));
    var currentImg = b.media.length ? b.media[0].path : '';
    var pvEl = null, pimg = null;
    var selImg = mediaInput(currentImg, function (v) {
      if (!v) b.media = [];
      else if (b.media.length) b.media[0].path = v;
      else b.media = [{ path: v, caption: '' }];
      if (pvEl) {
        if (v) { pvEl.hidden = false; pimg.src = previewUrl(v); }
        else pvEl.hidden = true;
      }
      mark();
    }, 'file name, or paste an image URL to download it');
    imgRow.appendChild(selImg);
    var capRow = el('div', 'field', null);
    capRow.appendChild(el('label', 'flabel', 'Caption'));
    var capIn = document.createElement('input');
    capIn.className = 'input';
    capIn.value = b.media.length ? b.media[0].caption : '';
    capIn.oninput = function () {
      if (b.media.length) b.media[0].caption = capIn.value;
      mark();
    };
    capRow.appendChild(capIn);
    mp.appendChild(imgRow);
    mp.appendChild(capRow);
    if (currentImg) {
      pvEl = el('div', 'media-preview');
      pimg = document.createElement('img');
      pimg.src = previewUrl(currentImg);
      pimg.alt = '';
      pvEl.appendChild(pimg);
      mp.appendChild(pvEl);
    }
    main.appendChild(mp);

    /* effects */
    var fp = el('div', 'panel');
    var ft = el('div', 'panel-title', 'On entering this beat, change');
    var addFx = el('button', 'muted-btn small', '+ Change a variable');
    addFx.onclick = function () {
      b.effects.push({ stat: model.variables.length ? model.variables[0].id : '', scope: null, op: '+', value: '1' });
      renderBeatPanel();
      mark();
    };
    ft.appendChild(addFx);
    fp.appendChild(ft);
    if (!b.effects.length) fp.appendChild(el('p', 'hint', 'No variables change on entering this beat.'));
    b.effects.forEach(function (e) { fp.appendChild(effectRow(b, e)); });
    main.appendChild(fp);

    /* links */
    var lp = el('div', 'panel');
    var lt = el('div', 'panel-title', 'Choices (how the player moves on)');
    var addL = el('button', 'muted-btn small', '+ Add choice');
    addL.onclick = function () {
      b.choices.push({ label: 'Choice ' + (b.choices.length + 1), target: b.name, guard: null });
      renderBeatPanel();
      mark();
    };
    lt.appendChild(addL);
    lp.appendChild(lt);
    if (!b.choices.length) lp.appendChild(el('p', 'hint', 'No choices. The player either continues via the "next" setting below, or the story ends here.'));
    b.choices.forEach(function (c) { lp.appendChild(choiceRow(b, c)); });
    main.appendChild(lp);

    /* goto / ending */
    var gp = el('div', 'panel');
    gp.appendChild(el('div', 'panel-title', 'If the player has no choice'));
    var gRow = el('div', 'field');
    gRow.appendChild(el('label', 'flabel', 'Continue to'));
    var selG = document.createElement('select');
    selG.className = 'input';
    optionList(selG, ['\u2014 ends here'].concat(beatNames()), b.goto || '\u2014 ends here');
    selG.onchange = function () {
      b.goto = selG.value === '\u2014 ends here' ? null : selG.value;
      renderBeatPanel();
      mark();
    };
    gRow.appendChild(selG);
    gp.appendChild(gRow);
    main.appendChild(gp);
  }

  function availableMedia() {
    var list = (model.meta.mediaFiles || []).slice();
    model.beats.forEach(function (b) {
      b.media.forEach(function (m) { if (list.indexOf(m.path) === -1) list.push(m.path); });
    });
    model.characters.forEach(function (c) {
      c.icons.forEach(function (ic) { if (ic.img && list.indexOf(ic.img) === -1) list.push(ic.img); });
    });
    return list.sort();
  }

  function addBeat() {
    var base = prompt('Name for the new beat (or leave blank for a suggested one)', '');
    if (base === null) return null;
    var nn = uniqueName(base || 'beat', beatNames());
    model.beats.push({ name: nn, tags: [], lines: [],
                       media: [], effects: [], choices: [], goto: null });
    mark();
    return nn;
  }

  function deleteBeat(name) {
    var n = linkCountTo(name);
    if (!confirm('Delete beat "' + name + '"' + (n ? ' (also removes ' + n + ' link(s) pointing here)' : '') + '?')) return;
    removeFrom(model.beats, model.beats[beatIndex(name)]);
    if (selBeat === name) selBeat = model.beats.length ? model.beats[Math.min(beatIndex(name), model.beats.length - 1)].name : null;
    renderBeatList();
    renderMain();
    mark();
  }

  /* ---- settings */
  function renderSettings() {
    var main = document.getElementById('main');
    main.innerHTML = '';
    var tabs = el('div', 'var-tabs');
    ['general', 'variables', 'characters', 'modals'].forEach(function (t) {
      var b = el('button', 'nav-btn' + (settingsTab === t ? ' active' : ''),
                 t === 'general' ? 'General' : t === 'variables' ? 'Variables' : t === 'characters' ? 'Characters' : 'Modals');
      b.onclick = function () { settingsTab = t; renderSettings(); };
      tabs.appendChild(b);
    });
    main.appendChild(tabs);
    if (settingsTab === 'general') renderSettingsGeneral(main);
    else if (settingsTab === 'variables') renderSettingsVariables(main);
    else if (settingsTab === 'characters') renderSettingsCharacters(main);
    else renderSettingsModals(main);
  }

  function renderSettingsGeneral(main) {
    var p = el('div', 'panel');
    p.appendChild(el('div', 'panel-title', 'Story settings'));

    var f1 = el('div', 'field');
    f1.appendChild(el('label', 'flabel', 'Title'));
    var t1 = document.createElement('input');
    t1.className = 'input'; t1.value = model.title;
    t1.oninput = function () { model.title = t1.value; mark(); };
    f1.appendChild(t1); p.appendChild(f1);

    var f2 = el('div', 'field');
    f2.appendChild(el('label', 'flabel', 'Credit / author line (optional)'));
    var t2 = document.createElement('input');
    t2.className = 'input'; t2.value = model.credit;
    t2.oninput = function () { model.credit = t2.value; mark(); };
    f2.appendChild(t2); p.appendChild(f2);

    var f3 = el('div', 'field');
    f3.appendChild(el('label', 'flabel', 'First beat (where the story starts)'));
    var s3 = document.createElement('select');
    s3.className = 'input';
    optionList(s3, beatNames(), model.start);
    s3.onchange = function () { model.start = s3.value; renderBeatList(); mark(); };
    f3.appendChild(s3); p.appendChild(f3);

    var f4 = el('div', 'field');
    f4.appendChild(el('label', 'flabel', 'Image folder name'));
    var t4 = document.createElement('input');
    t4.className = 'input'; t4.value = model.mediaDir;
    t4.oninput = function () { model.mediaDir = t4.value; mark(); };
    f4.appendChild(t4); p.appendChild(f4);

    p.appendChild(el('p', 'hint', 'Saved to ' + model.meta.pathLabel + ' \u2014 drop image files into the media folder and Save; images appear in the pickers.'));
    main.appendChild(p);
  }

  function variableRows(scope, host) {
    var vars = model.variables.filter(function (v) { return v.scope === scope; });
    if (!vars.length) host.appendChild(el('p', 'hint', 'No ' + scope + ' variables yet. Add one to track something.'));
    vars.forEach(function (v) {
      var row = el('div', 'row var');
      var idIn = document.createElement('input');
      idIn.className = 'input'; idIn.value = v.id; idIn.placeholder = 'id';
      idIn.onchange = function () {
        if (idIn.value && idIn.value !== v.id) {
          var clash = model.variables.some(function (x) { return x.id === idIn.value; });
          if (clash) { showMsg('A variable named "' + idIn.value + '" already exists', 'err'); idIn.value = v.id; return; }
          renameVar(v.id, idIn.value);
          v.id = idIn.value;
          renderSettings();
        }
      };
      row.appendChild(idIn);

      var labIn = document.createElement('input');
      labIn.className = 'input'; labIn.value = v.label; labIn.placeholder = 'label';
      labIn.oninput = function () { v.label = labIn.value; mark(); };
      row.appendChild(labIn);

      var selK = document.createElement('select');
      selK.className = 'input';
      optionList(selK, ['range', 'list'], v.kind);
      selK.onchange = function () {
        v.kind = selK.value === 'range' ? 'range' : 'list';
        renderSettings();
        mark();
      };
      row.appendChild(selK);

      var rm = el('button', 'btn icon danger', '\u00d7');
      rm.onclick = function () { removeFrom(model.variables, v); renderSettings(); mark(); };
      row.appendChild(rm);
      host.appendChild(row);

      if (v.kind === 'range') {
        var r2 = el('div', 'row var');
        ['min', 'max', 'start'].forEach(function (fld) {
          var f = el('div', 'field');
          f.appendChild(el('label', 'flabel', fld));
          var i = document.createElement('input');
          i.type = 'number'; i.className = 'input'; i.value = v[fld];
          i.oninput = function () { v[fld] = Number(i.value); mark(); };
          f.appendChild(i); r2.appendChild(f);
        });
        host.appendChild(r2);
      } else {
        var r3 = el('div', 'row var');
        var fv = el('div', 'field');
        fv.appendChild(el('label', 'flabel', 'Options (comma-separated)'));
        var iv = document.createElement('input');
        iv.className = 'input'; iv.value = v.values.join(', ');
        iv.oninput = function () {
          v.values = iv.value.split(',').map(function (s) { return s.trim(); }).filter(Boolean);
          mark();
        };
        fv.appendChild(iv); r3.appendChild(fv);
        var fs = el('div', 'field');
        fs.appendChild(el('label', 'flabel', 'Starts at'));
        var selV = document.createElement('select');
        selV.className = 'input';
        optionList(selV, v.values.length ? v.values : [' '], v.values.indexOf(v.start) > -1 ? v.start : (v.values[0] || ''));
        selV.onchange = function () { v.start = selV.value; mark(); };
        fs.appendChild(selV); r3.appendChild(fs);
        host.appendChild(r3);
      }
    });
  }

  function renderSettingsVariables(main) {
    var p = el('div', 'panel');
    var titleRow = el('div', 'panel-title', 'Variables');
    p.appendChild(titleRow);
    p.appendChild(el('p', 'hint', 'Variables track numbers (e.g. Energy) or choices (e.g. Weather). Character variables belong to the player; Story variables are global state.'));
    ['char', 'global'].forEach(function (scope) {
      var sec = el('div', 'section');
      var st = el('div', 'section-title', scope === 'char' ? 'Character variables' : 'Story / global variables');
      var add = el('button', 'muted-btn small', '+ Add');
      add.onclick = function () {
        var id = uniqueName(scope === 'global' ? 'flag' : 'trait', model.variables.map(function (v) { return v.id; }));
        model.variables.push({ id: id, scope: scope, kind: 'range', label: id,
                               min: 0, max: 100, start: 0, hidden: false });
        renderSettings();
        mark();
      };
      st.appendChild(add);
      sec.appendChild(st);
      variableRows(scope, sec);
      p.appendChild(sec);
    });
    main.appendChild(p);
  }

  function renderSettingsCharacters(main) {
    var p = el('div', 'panel');
    var titleRow = el('div', 'panel-title', 'Characters');
    var addC = el('button', 'muted-btn small', '+ Add character');
    addC.onclick = function () {
      var cid = uniqueName('hero', model.characters.map(function (c) { return c.id; }));
      model.characters.push({ id: cid, name: cid, icons: [] });
      renderSettings();
      mark();
    };
    titleRow.appendChild(addC);
    p.appendChild(titleRow);
    p.appendChild(el('p', 'hint', 'Characters appear in the sidebar and speak dialogue. Icons are the headshots shown while a stat (or combination) is active \u2014 first match wins.'));

    model.characters.forEach(function (c) {
      var sec = el('div', 'section');
      var row = el('div', 'row char');
      var idIn = document.createElement('input');
      idIn.className = 'input'; idIn.value = c.id; idIn.placeholder = 'id';
      idIn.onchange = function () {
        if (idIn.value && idIn.value !== c.id) {
          var clash = model.characters.some(function (x) { return x.id === idIn.value; });
          if (clash) { showMsg('A character named "' + idIn.value + '" already exists', 'err'); idIn.value = c.id; return; }
          var old = c.id;
          c.id = idIn.value;
          model.beats.forEach(function (b) {
            b.lines.forEach(function (ln) { if (ln.speaker === old) ln.speaker = c.id; });
          });
          renderSettings();
          mark();
        }
      };
      row.appendChild(idIn);
      var nameIn = document.createElement('input');
      nameIn.className = 'input'; nameIn.value = c.name; nameIn.placeholder = 'display name';
      nameIn.oninput = function () { c.name = nameIn.value; mark(); };
      row.appendChild(nameIn);
      var rm = el('button', 'btn icon danger', '\u00d7');
      rm.onclick = function () { if (confirm('Remove character "' + c.id + '"?')) { removeFrom(model.characters, c); renderSettings(); mark(); } };
      row.appendChild(rm);
      sec.appendChild(row);

      if (!c.icons.length) sec.appendChild(el('p', 'hint', 'No icons yet. Add at least one to show this character.'));
      c.icons.forEach(function (ic) {
        var irow = el('div', 'row icon');
        var idI = document.createElement('input');
        idI.className = 'input'; idI.value = ic.id; idI.placeholder = 'icon id';
        idI.oninput = function () { ic.id = idI.value; mark(); };
        irow.appendChild(idI);
        var imgS = mediaInput(ic.img, function (v) { ic.img = v; mark(); }, 'file name, or paste an image URL to download it');
        imgS.className = 'input';
        irow.appendChild(imgS);
        var rm2 = el('button', 'btn icon danger', '\u00d7');
        rm2.onclick = function () { removeFrom(c.icons, ic); renderSettings(); mark(); };
        irow.appendChild(rm2);
        sec.appendChild(irow);
        var rules = el('div');
        sec.appendChild(rules);
        if (ic.rawWhen) {
          var wr = el('div', 'field');
          wr.appendChild(el('label', 'flabel', 'Advanced conditions (JSON, keep as-is if unsure)'));
          var ta = document.createElement('textarea');
          ta.className = 'input'; ta.rows = 2;
          ta.value = ic.rawWhen;
          ta.oninput = function () { ic.rawWhen = ta.value; mark(); };
          wr.appendChild(ta);
          rules.appendChild(wr);
        } else if (ic.rules && ic.rules.length) {
          var anyRules = false;
          ic.rules.forEach(function (r) {
            if (!anyRules) {
              rules.appendChild(el('div', 'subtle', 'Only when \u2014 conditions for this headshot:'));
              anyRules = true;
            }
            var rr = el('div', 'row rule');
            var sv = document.createElement('select');
            sv.className = 'input';
            optionList(sv, model.variables.map(function (v) { return v.id; }), r.stat);
            sv.onchange = function () { r.stat = sv.value; mark(); };
            rr.appendChild(sv);
            var so = document.createElement('select');
            so.className = 'input';
            optionList(so, ['>=', '>', '<=', '<', '=='], r.op);
            so.onchange = function () { r.op = so.value; mark(); };
            rr.appendChild(so);
            var vi = document.createElement('input');
            vi.className = 'input'; vi.value = r.value;
            vi.oninput = function () { r.value = vi.value; mark(); };
            rr.appendChild(vi);
            var x = el('button', 'btn icon danger', '\u00d7');
            x.onclick = function () { removeFrom(ic.rules, r); renderSettings(); mark(); };
            rr.appendChild(x);
            rules.appendChild(rr);
          });
        } else {
          rules.appendChild(el('div', 'subtle', 'No conditions \u2014 shown as the default headshot for this character.'));
        }
      });
      var addIcon = el('button', 'muted-btn small', '+ Add icon');
      addIcon.onclick = function () {
        var ni = { id: uniqueName('icon', c.icons.map(function (x) { return x.id; })),
                   img: availableMedia()[0] || '', rules: [] };
        c.icons.push(ni);
        renderSettings();
        mark();
      };
      sec.appendChild(addIcon);
      p.appendChild(sec);
    });
    main.appendChild(p);
  }

  function renderSettingsModals(main) {
    var p = el('div', 'panel');
    var titleRow = el('div', 'panel-title', 'Modals');
    var addM = el('button', 'muted-btn small', '+ Add modal');
    addM.onclick = function () {
      var mid = uniqueName('info', model.modals.map(function (m) { return m.id; }));
      model.modals.push({ id: mid, title: '', image: '', text: '' });
      renderSettings();
      mark();
    };
    titleRow.appendChild(addM);
    p.appendChild(titleRow);
    p.appendChild(el('p', 'hint', 'Re-usable info windows. Open one from dialogue with [open:modal.<id>]text[/open]; close any modal with [close]text[/close].'));

    if (!model.modals.length) p.appendChild(el('p', 'hint', 'No modals yet. Add one to show an item, map, clue, or note.'));

    model.modals.forEach(function (m) {
      var sec = el('div', 'section');
      var row = el('div', 'row char');
      var idIn = document.createElement('input');
      idIn.className = 'input'; idIn.value = m.id; idIn.placeholder = 'id';
      idIn.onchange = function () {
        if (idIn.value && idIn.value !== m.id) {
          var clash = model.modals.some(function (x) { return x.id === idIn.value; });
          if (clash) { showMsg('A modal named "' + idIn.value + '" already exists', 'err'); idIn.value = m.id; return; }
          var old = m.id;
          m.id = idIn.value;
          model.beats.forEach(function (b) {
            b.lines.forEach(function (ln) {
              if (ln.text) ln.text = ln.text.split('[open:modal.' + old + ']').join('[open:modal.' + m.id + ']');
            });
          });
          renderSettings();
          mark();
        }
      };
      row.appendChild(idIn);
      var rm = el('button', 'btn icon danger', '\u00d7');
      rm.onclick = function () { if (confirm('Remove modal "' + m.id + '"?')) { removeFrom(model.modals, m); renderSettings(); mark(); } };
      row.appendChild(rm);
      sec.appendChild(row);

      var f1 = el('div', 'field');
      f1.appendChild(el('label', 'flabel', 'Title'));
      var t1 = document.createElement('input');
      t1.className = 'input'; t1.value = m.title;
      t1.oninput = function () { m.title = t1.value; mark(); };
      f1.appendChild(t1); sec.appendChild(f1);

      var f2 = el('div', 'field');
      f2.appendChild(el('label', 'flabel', 'Image'));
      var imgS = mediaInput(m.image, function (v) { m.image = v; mark(); }, 'file name, or paste an image URL to download it');
      imgS.className = 'input';
      f2.appendChild(imgS); sec.appendChild(f2);

      var f3 = el('div', 'field');
      f3.appendChild(el('label', 'flabel', 'Text'));
      var ta = document.createElement('textarea');
      ta.className = 'input'; ta.rows = 4;
      ta.value = m.text;
      ta.oninput = function () { m.text = ta.value; mark(); };
      f3.appendChild(ta); sec.appendChild(f3);

      p.appendChild(sec);
    });
    main.appendChild(p);
  }

  /* ---------------------------------------------------------------- main render */
  function renderMain() {
    if (view === 'beats') {
      document.getElementById('tab-beats').classList.add('active');
      document.getElementById('tab-settings').classList.remove('active');
      renderBeatPanel();
    } else {
      document.getElementById('tab-beats').classList.remove('active');
      document.getElementById('tab-settings').classList.add('active');
      renderSettings();
    }
  }

  /* ---------------------------------------------------------------- init */
  function initUI() {
    var documentEl = {
      tabBeats: document.getElementById('tab-beats'),
      tabSettings: document.getElementById('tab-settings'),
      btnTheme: document.getElementById('btn-theme'),
      btnSave: document.getElementById('btn-save'),
      btnOpen: document.getElementById('btn-open'),
      addBeat: document.getElementById('add-beat'),
      inpTitle: document.getElementById('inp-title'),
      fileChip: document.getElementById('story-file')
    };
    documentEl.tabBeats.onclick = function () { view = 'beats'; renderMain(); };
    documentEl.tabSettings.onclick = function () { view = 'settings'; renderMain(); };
    documentEl.addBeat.onclick = function () {
      var nn = addBeat();
      if (!nn) return;
      selBeat = nn;
      renderBeatList();
      renderMain();
    };
    documentEl.btnOpen.onclick = openPicker;
    documentEl.btnSave.onclick = saveStory;
    documentEl.inpTitle.oninput = function () { model.title = documentEl.inpTitle.value; mark(); };
    documentEl.fileChip.textContent = model.meta.pathLabel || '';

    var theme = localStorage.getItem('gse-editor-theme') || 'light';
    setTheme(theme);
    documentEl.btnTheme.onclick = function () {
      setTheme(document.documentElement.getAttribute('data-theme') === 'dark' ? 'light' : 'dark');
    };
    function setTheme(t) {
      document.documentElement.setAttribute('data-theme', t);
      documentEl.btnTheme.textContent = t === 'dark' ? '\u2600 Light mode' : '\u263e Dark mode';
      localStorage.setItem('gse-editor-theme', t);
    }
    window.setThemeEditor = setTheme;

    /* live check while typing: show errors/warnings as you type (debounced) */
    var checkT = null;
    document.addEventListener('input', function () {
      clearTimeout(checkT);
      checkT = setTimeout(checkNow, 500);
    });
    function checkNow() {
      try {
        var text = textFromModel();
        var parsed = E.parse(text);
        if (parsed.errors.length) { liveNote(parsed.errors.slice(0, 3), true); return; }
        var story = E.buildStory(parsed);
        var res = E.validate(story);
        if (res.errors.length) liveNote(res.errors.slice(0, 3), true);
        else if (res.warnings.length) liveNote(res.warnings.slice(0, 3), false);
        else liveNote([]);
      } catch (e) { liveNote([String(e)], true); }
    }
    function liveNote(errs, isErr) {
      var m = document.getElementById('msg');
      if (!errs.length) { m.hidden = true; return; }
      m.hidden = false;
      m.className = 'msg ' + (isErr ? 'err' : 'warn');
      m.innerHTML = '';
      m.appendChild(el('span', null, isErr ? 'A problem before you save:' : 'Heads-up:'));
      var ul = el('ul');
      errs.forEach(function (x) { ul.appendChild(el('li', null, x)); });
      m.appendChild(ul);
      clearTimeout(liveNote._t);
      liveNote._t = setTimeout(function () { m.hidden = true; }, 5000);
    }

    renderBeatList();
    if (model.start && beatIndex(model.start) > -1) selBeat = model.start;
    else selBeat = model.beats.length ? model.beats[0].name : null;
    renderMain();
  }

  /* ---------------------------------------------------------------- open / new */
  function bootStory(data) {
    mediaBase = data.mediaBase || '/media/';
    var parsed = E.parse(data.text);
    if (parsed.errors.length) { boot('Could not parse the story: ' + parsed.errors.join(' | ')); return; }
    var story = E.buildStory(parsed);
    modelFromStory(story, { mediaFiles: data.mediaFiles || [], mediaDir: data.mediaDir || 'media', pathLabel: data.pathLabel || (data.name || '') + '.gse' });
    model.mediaDir = data.mediaDir || 'media';
    document.title = 'Editing \u2014 ' + model.title;
    document.getElementById('inp-title').value = model.title;
    initUI();
  }

  function openPicker() {
    var modal = document.getElementById('modal-root');
    modal.innerHTML = '';
    var box = el('div', 'modal panel');
    box.appendChild(el('h2', null, 'Story Library'));
    var hint = el('p', 'hint', 'Pick a story to edit, or create a new one below.');
    box.appendChild(hint);
    var list = el('div', 'open-list');
    var ul = document.createElement('ul');
    list.appendChild(ul);
    box.appendChild(list);
    var newRow = el('div', 'field');
    newRow.appendChild(el('label', 'flabel', 'New story name'));
    var nameIn = document.createElement('input');
    nameIn.className = 'input';
    nameIn.placeholder = 'e.g. my_first_story';
    newRow.appendChild(nameIn);
    var create = el('button', 'btn primary small', 'Create');
    create.onclick = function () {
      if (!String(nameIn.value || '').trim()) { showMsg('Enter a name for the new story', 'err'); return; }
      fetch('/api/new', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name: nameIn.value })
      }).then(function (r) { return r.json(); }).then(function (res) {
        if (res.ok) { modal.hidden = true; switchStory(res.name); }
        else showMsg(res.errors[0] || 'Could not create story', 'err');
      }, function () { showMsg('Create failed', 'err'); });
    };
    newRow.appendChild(create);
    box.appendChild(newRow);
    var acts = el('div', 'modal-actions');
    var canc = el('button', 'btn', 'Close');
    canc.onclick = function () { modal.hidden = true; };
    acts.appendChild(canc);
    box.appendChild(acts);

    fetch('/api/stories').then(function (r) { return r.json(); }).then(function (data) {
      var names = data.stories || [];
      if (!names.length) {
        var nonl = el('p', 'hint', 'No stories yet\u2014create one above.');
        list.insertBefore(nonl, ul);
      }
      names.forEach(function (nm) {
        var li = document.createElement('li');
        var b = el('button', 'btn small', nm);
        if (nm === data.current) {
          b.className += ' on';
          b.textContent += '   (open)';
        }
        b.onclick = function () {
          modal.hidden = true;
          switchStory(nm);
        };
        li.appendChild(b);
        ul.appendChild(li);
      });
    }, function () {
      list.insertBefore(el('p', 'hint', 'Could not list stories.'), ul);
    });
    modal.appendChild(box);
    modal.hidden = false;
  }

  function switchStory(name) {
    fetch('/api/open?name=' + encodeURIComponent(name)).then(function (r) { return r.json(); }).then(function (res) {
      if (res.ok) { reload(); return; }
      showMsg('Could not open "' + name + '"', 'err');
      openPicker();
    }, function () { showMsg('Open failed', 'err'); });
  }

  function reload() { load(); }

  /* ---------------------------------------------------------------- boot */
  function boot(msg) {
    var main = document.getElementById('main');
    main.innerHTML = '';
    var p = el('div', 'panel');
    p.appendChild(el('h2', null, 'Story editor'));
    p.appendChild(el('p', 'hint', msg));
    main.appendChild(p);
  }

  function load() {
    if (typeof window === 'undefined' || !window.document) return; // headless: used by tests
    function onFail() {
      boot('Could not load the story. Start the editor with  python cli/gse.py edit <story>');
    }
    fetch('/api/story').then(function (r) { return r.json(); }).then(bootStory, onFail);
  }

  /* export pure helpers for headless round-trip tests */
  GLOB.GSEditor = { modelFromStory: modelFromStory, textFromModel: textFromModel, slug: slug };
  if (typeof document !== 'undefined') {
    document.readyState === 'loading'
      ? document.addEventListener('DOMContentLoaded', load)
      : load();
  }
})();