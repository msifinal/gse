/* Green Story Engine — core (pure logic, no DOM).
 * Attribution: story text can be any author's; this engine creates no work itself.
 * UMD: attaches window.StoryEngine in a browser; CommonJS exports in Node.
 * Python CLI (cli/gse.py) mirrors this logic for the same simple format.
 */
(function (root, factory) {
  if (typeof module === 'object' && module.exports) { module.exports = factory(); }
  else { root.StoryEngine = factory(); }
})(typeof self !== 'undefined' ? self : this, function () {
  'use strict';

  var HEADER_RE = /^::\s+(.+?)\s*$/;
  var SET_RE = /^set::\s+(.+?)\s*$/i;
  var ROLL_RE = /^roll::\s+(.+?)\s*$/i;
  var IF_RE = /^if::\s+(.+?)\s*$/i;
  var SPEAKER_RE = /^speaker::\s+(.+?)\s*$/i;
  var IMG_RE = /^(?:image|img|pic)::\s+(.+?)\s*$/i;
  var GOTO_RE = /^goto::\s+(.+?)\s*$/i;
  var CHOICE_RE = /^\[\[(.+?)\]\]/;
  var INLINE_IF = /^(?:if|if::)\s+(.+)$/i;

  function trim(s) { return String(s == null ? '' : s).replace(/^\s+|\s+$/g, ''); }
  function clone(o) { return o ? JSON.parse(JSON.stringify(o)) : o; }

  function splitChoice(body) {
    var i = body.indexOf('|');
    if (i > -1) return { label: trim(body.slice(0, i)), target: trim(body.slice(i + 1)) };
    return { label: trim(body), target: trim(body) };
  }

  function parseSet(expr) {
    var m = String(expr).match(/^(\S+)\s*([+\-=])\s*(.+)$/);
    if (!m) return { error: 'Bad effect "' + trim(expr) + '" (expected: set:: stat +10 | set:: stat = value)' };
    var num = Number(trim(m[3]));
    return { stat: m[1], op: m[2], value: isNaN(num) ? trim(m[3]) : num };
  }

  /* Parse a dice expression: NdS, dS, optionally +/-N modifiers,
   * optionally prefixed with "stat =". Result lands in 'lastroll' by default. */
  function parseRoll(expr) {
    var s = String(expr).trim();
    var stat = 'lastroll', rest = s;
    var eq = s.indexOf('=');
    if (eq > -1) {
      stat = trim(s.slice(0, eq));
      rest = trim(s.slice(eq + 1));
    }
    var m = rest.match(/^(\d*)d(\d+)((?:\s*[+-]\s*\d+)*)$/i);
    if (!m) return { error: 'Bad roll "' + trim(expr) + '" (expected: roll:: 2d6 | roll:: d20+1 | roll:: luck = d6)' };
    var count = m[1] ? parseInt(m[1], 10) : 1;
    var sides = parseInt(m[2], 10);
    if (!count || count < 1 || !sides || sides < 1) return { error: 'Bad roll "' + trim(expr) + '" (dice must be NdS, sides >= 1)' };
    var mods = [];
    var mm = m[3];
    if (mm) {
      mm = mm.trim();
      var signRe = /([+-])\s*(\d+)/g, signM;
      while ((signM = signRe.exec(mm))) mods.push({ sign: signM[1], value: parseInt(signM[2], 10) });
    }
    return { stat: stat, op: 'roll', dice: { count: count, sides: sides, mods: mods }, expr: rest };
  }

  function parseGuard(expr) {
    var m = String(expr).match(/^(\S+)\s*(==|!=|>=|<=|>|<)\s*(.+)$/);
    if (!m) return { error: 'Bad condition "' + trim(expr) + '" (expected: if:: stat >= 60)' };
    var num = Number(trim(m[3]));
    return { stat: m[1], op: m[2], value: isNaN(num) ? trim(m[3]) : num };
  }

  var ACTION_OPEN_RE = /\[open:([A-Za-z0-9_\-\.]+)\]/g;
  var ACTION_CLOSE_RE = /\[close\]/g;

  /* Resolve an action target token. 'modal.<name>' -> config modal,
   * otherwise a character id -> that character. Returns null if unknown. */
  function modalTargetInfo(story, token) {
    var s = String(token || '');
    if (s.indexOf('modal.') === 0) {
      var name = s.slice(6);
      return (story.config.modals || {})[name] ? { kind: 'modal', name: name } : null;
    }
    if (story.config.characters[s]) return { kind: 'char', name: s };
    return null;
  }

  /* Split a text line into segments, extracting inline action spans.
   * Syntax: [open:<charId|modal.name>]label[/open], [close]label[/close].
   * Returns { segs: [{text, act}], errors: [..] }. */
  function splitActions(text) {
    var segs = [], errors = [], i = 0;
    while (i < text.length) {
      ACTION_OPEN_RE.lastIndex = i; var om = ACTION_OPEN_RE.exec(text);
      ACTION_CLOSE_RE.lastIndex = i; var cm = ACTION_CLOSE_RE.exec(text);
      var om0 = om ? om.index : -1, cm0 = cm ? cm.index : -1;
      var isOpen = om0 > -1 && (cm0 === -1 || om0 <= cm0);
      var at = isOpen ? om0 : cm0;
      if (at === -1) {
        var tail = text.slice(i);
        if (tail) segs.push({ text: tail, act: null });
        break;
      }
      if (at > i) segs.push({ text: text.slice(i, at), act: null });
      if (isOpen) {
        var cid = om[1], j = om0 + om[0].length, close = text.indexOf('[/open]', j);
        if (close === -1) {
          errors.push('Unclosed action "[open:' + cid + ']"');
          segs.push({ text: text.slice(j), act: null });
          break;
        }
        var label = text.slice(j, close);
        if (!trim(label)) errors.push('Action "[open:' + cid + ']" has an empty label');
        segs.push({ text: label, act: { kind: 'open', target: cid } });
        i = close + 7;
      } else {
        var cj = cm0 + cm[0].length, cclose = text.indexOf('[/close]', cj);
        if (cclose === -1) {
          errors.push('Unclosed action "[close]"');
          segs.push({ text: text.slice(cj), act: null });
          break;
        }
        var clabel = text.slice(cj, cclose);
        if (!trim(clabel)) errors.push('Action "[close]" has an empty label');
        segs.push({ text: clabel, act: { kind: 'close' } });
        i = cclose + 8;
      }
    }
    return { segs: segs, errors: errors };
  }

  /* ---------------- raw parsing (twee text, or JSON object/string) ---------------- */
  function parse(textOrObject) {
    var errors = [], passages = {}, configRaw = null, storyObj = null;

    if (textOrObject && typeof textOrObject === 'object' && (textOrObject.passages || textOrObject.config)) {
      storyObj = textOrObject;
    } else if (typeof textOrObject === 'string' && /^\s*\{/.test(textOrObject)) {
      try {
        var maybe = JSON.parse(textOrObject);
        if (maybe && typeof maybe === 'object' && (maybe.passages || maybe.config)) storyObj = maybe;
      } catch (e) { /* not JSON -> treat as gse text */ }
    }

    if (storyObj) {
      configRaw = storyObj.config || null;
      var src = storyObj.passages || {};
      Object.keys(src).forEach(function (name) {
        var v = src[name];
        var lines = Array.isArray(v) ? v : (v.lines || []);
        passages[name] = { name: name, tags: (v && v.tags) || [], lines: lines };
      });
    } else {
      var text = String(textOrObject == null ? '' : textOrObject);
      if (text.charCodeAt(0) === 0xFEFF) text = text.slice(1);
      var cur = null;
      text.split(/\r?\n/).forEach(function (line) {
        var h;
        if ((h = line.match(HEADER_RE))) {
          var name = trim(h[1]), tags = [];
          var tm;
          if ((tm = name.match(/^(.*?)\s+\[(.*)\]$/))) { name = trim(tm[1]); tags = trim(tm[2]).split(/\s+/).filter(Boolean); }
          cur = { name: name, tags: tags, lines: [] };
          passages[name] = cur;
        } else if (cur) {
          cur.lines.push(line);
        }
      });
    }

    var cfgName = null;
    Object.keys(passages).forEach(function (name) {
      if (name.toLowerCase() === 'config') cfgName = name;
    });
    if (cfgName) {
      var cfgText = trim(passages[cfgName].lines.join('\n'));
      if (!cfgText) errors.push('Config passage is empty');
      else {
        try { configRaw = JSON.parse(cfgText); }
        catch (e) { errors.push('Config passage is not valid JSON: ' + e.message); }
      }
      delete passages[cfgName];
    }

    Object.keys(passages).forEach(function (name) {
      var p = passages[name];
      p.blocks = []; p.choices = []; p.effects = []; p.media = [];
      p.speaker = null; p.goto = null;
      var block = null, pendingGuard = null;

      function flush() { if (block) { p.blocks.push(block); block = null; } }

      p.lines.forEach(function (line) {
        var t = trim(line);
        if (!t || t.charAt(0) === '#' || /^\/\//.test(t) || t === '---') { pendingGuard = null; return; }
        var m;
        if ((m = line.match(SPEAKER_RE))) {
          flush();
          var who = trim(m[1]);
          p.speaker = (who.toLowerCase() === 'none' || who.toLowerCase() === 'narrator') ? null : who;
          pendingGuard = null; return;
        }
        if ((m = line.match(IMG_RE))) {
          flush();
          var rest = trim(m[1]), i = rest.indexOf(' ');
          p.media.push(i > -1 ? { path: rest.slice(0, i), caption: trim(rest.slice(i + 1)) } : { path: rest, caption: '' });
          pendingGuard = null; return;
        }
        if ((m = line.match(SET_RE))) {
          var e = parseSet(m[1]);
          if (e.error) errors.push(name + ': ' + e.error);
          else p.effects.push(e);
          pendingGuard = null; return;
        }
        if ((m = line.match(ROLL_RE))) {
          var e = parseRoll(m[1]);
          if (e.error) errors.push(name + ': ' + e.error);
          else p.effects.push(e);
          pendingGuard = null; return;
        }
        if ((m = line.match(IF_RE))) {
          var g = parseGuard(m[1]);
          if (g.error) errors.push(name + ': ' + g.error);
          else pendingGuard = g;
          return;
        }
        if ((m = line.match(GOTO_RE))) { flush(); p.goto = trim(m[1]); pendingGuard = null; return; }
        if ((m = line.match(CHOICE_RE))) {
          flush();
          var c = splitChoice(m[1]);
          var tail = trim(line.slice(line.indexOf(']]') + 2));
          var guard = pendingGuard || null;
          if (tail) {
            var tg = tail.match(INLINE_IF);
            if (tg) { var ig = parseGuard(tg[1]); if (ig.error) errors.push(name + ': ' + ig.error); else guard = ig; }
            else errors.push('Passage "' + name + '": choice has trailing text after ]] ("' + tail + '")');
          }
          p.choices.push({ label: c.label, target: c.target, guard: guard });
          pendingGuard = null; return;
        }
        if (!block) block = { type: 'text', speaker: p.speaker, text: [], segments: [] };
        block.text.push(t);
        var sa = splitActions(t);
        block.segments.push(sa.segs);
        sa.errors.forEach(function (e) { errors.push(name + ': ' + e); });
        pendingGuard = null;
      });
      flush();
      delete p.lines;
    });

    return { config: configRaw || {}, passages: passages, errors: errors };
  }

  /* ---------------- stat schema helpers ---------------- */
  function defaultStat(id, def) {
    def = def || {};
    if (def.values) {
      var vals = (def.values || []).map(String);
      var sval = def.start === undefined ? (vals[0] || '') : String(def.start);
      return { id: id, label: def.label || id, kind: 'enum', values: vals, start: sval, hidden: !!def.hidden,
               icon: def.icon || '', hint: def.hint || '' };
    }
    var min = def.min === undefined ? 0 : Number(def.min);
    var max = def.max === undefined ? 100 : Number(def.max);
    var start = def.start === undefined ? min + (max - min) / 2 : Number(def.start);
    if (start < min) start = min;
    if (start > max) start = max;
    return { id: id, label: def.label || id, kind: 'numeric', min: min, max: max, start: start, hidden: !!def.hidden,
             icon: def.icon || '', hint: def.hint || '' };
  }

  function statMap(raw, scope) {
    var map = {};
    Object.keys(raw || {}).forEach(function (id) { var d = defaultStat(id, raw[id]); d.scope = scope; map[id] = d; });
    return map;
  }

  /* find a stat by id. '@name' forces global scope. null if unknown. */
  function statInfo(story, id) {
    var scope = null, bare = id;
    if (typeof id === 'string' && id.charAt(0) === '@') { scope = 'global'; bare = id.slice(1); }
    var b = String(bare);
    if (scope !== 'global') {
      var dot = b.indexOf('.');
      if (dot > 0) {
        var cid = b.slice(0, dot), field = b.slice(dot + 1);
        if (field === 'status' && story.config.characters[cid]) {
          return { id: b, scope: 'status', char: cid,
                   def: { kind: 'status', label: (story.config.characters[cid].name || cid) + ' status' } };
        }
      }
    }
    var cg = scope === 'global' ? null : story.config.charStats[bare];
    if (cg) return { id: bare, scope: 'char', def: cg };
    var gg = scope === 'char' ? null : story.config.globals[bare];
    if (gg) return { id: bare, scope: 'global', def: gg };
    return null;
  }

  function buildStory(parsed) {
    var config = clone(parsed.config || {});
    var usesRoll = Object.keys(parsed.passages).some(function (n) {
      return (parsed.passages[n].effects || []).some(function (e) { return e.op === 'roll'; });
    });
    if (usesRoll && !(config.globals || {}).lastroll) {
      if (!config.globals) config.globals = {};
      config.globals.lastroll = { label: 'Last Roll', min: 0, max: 9999, start: 0, hidden: true };
    }
    config.charStats = statMap(config.charStats, 'char');
    config.globals = statMap(config.globals, 'global');
    if (!config.characters) config.characters = {};
    if (!config.characters.player) config.characters.player = { name: 'Player', icons: [] };
    Object.keys(config.characters).forEach(function (cid) {
      var c = config.characters[cid];
      if (c.icons === undefined) c.icons = [];
      c.icons.forEach(function (ic, i) { if (ic.id === undefined || ic.id === null) ic.id = 'icon' + i; });
    });
    var keys = Object.keys(parsed.passages);
    config.start = config.start || (keys.indexOf('start') > -1 ? 'start' : (keys[0] || ''));
    if (!config.title) config.title = 'Untitled Story';
    if (!config.mediaDir) config.mediaDir = 'media';
    if (!config.modals) config.modals = {};
    return { config: config, passages: parsed.passages, errors: parsed.errors || [] };
  }

  /* ---------------- merged stat access ---------------- */
  function merged(session) {
    var out = {};
    Object.keys(session.char).forEach(function (k) { out[k] = session.char[k]; });
    Object.keys(session.global).forEach(function (k) { out[k] = session.global[k]; });
    return out;
  }

  function applyEffects(story, session, pass) {
    pass.effects.forEach(function (e) {
      var info = statInfo(story, e.stat);
      if (!info) return;
      var d = info.def, bucket = session[info.scope];
      if (e.op === 'roll') {
        var total = 0;
        for (var i = 0; i < e.dice.count; i++) total += 1 + Math.floor(Math.random() * e.dice.sides);
        e.dice.mods.forEach(function (m) { total += m.sign === '-' ? -m.value : m.value; });
        if (info.scope === 'status') { session.status[info.char] = String(total); return; }
        if (d.kind === 'enum') { bucket[info.id] = String(total); return; }
        if (total < d.min) total = d.min;
        if (total > d.max) total = d.max;
        bucket[info.id] = total;
        return;
      }
      if (info.scope === 'status') {
        var cur = String(session.status[info.char] || '');
        if (e.op === '=') session.status[info.char] = String(e.value);
        else if (e.op === '+') session.status[info.char] = cur + String(e.value);
        else if (e.op === '-') session.status[info.char] = cur.replace(String(e.value), '');
        return;
      }
      if (d.kind === 'enum') {
        if (e.op === '=') bucket[info.id] = String(e.value);
        return;
      }
      var v = bucket[info.id] === undefined ? d.start : bucket[info.id];
      if (e.op === '+') v = v + Number(e.value);
      else if (e.op === '-') v = v - Number(e.value);
      else v = Number(e.value);
      if (v < d.min) v = d.min;
      if (v > d.max) v = d.max;
      bucket[info.id] = v;
    });
  }

  function evalGuard(story, guard, stats) {
    var info = statInfo(story, guard.stat);
    var v = stats[info ? info.id : guard.stat];
    if (!info || v === undefined) return false;
    if (info.def.kind === 'enum') {
      if (guard.op === '==') return String(v) === String(guard.value);
      if (guard.op === '!=') return String(v) !== String(guard.value);
      return false;
    }
    var n = Number(v), g = Number(guard.value);
    switch (guard.op) {
      case '==': return n === g;
      case '!=': return n !== g;
      case '>': return n > g;
      case '>=': return n >= g;
      case '<': return n < g;
      case '<=': return n <= g;
    }
    return false;
  }

  function matchOne(cond, v) {
    if (cond === undefined || cond === null) return true;
    if (Array.isArray(cond)) return cond.map(String).indexOf(String(v)) > -1;
    if (typeof cond === 'object') {
      var num = Number(v);
      if (isNaN(num)) return false;
      if (cond.min !== undefined && num < Number(cond.min)) return false;
      if (cond.max !== undefined && num > Number(cond.max)) return false;
      if (cond.gt !== undefined && num <= Number(cond.gt)) return false;
      if (cond.lt !== undefined && num >= Number(cond.lt)) return false;
      return true;
    }
    return String(cond) === String(v);
  }

  function evalWhen(when, stats) {
    if (!when) return true;
    return Object.keys(when).every(function (k) { return matchOne(when[k], stats[k]); });
  }

  function resolveIcon(story, charId, stats) {
    var c = story.config.characters[charId];
    if (!c) return null;
    var list = c.icons || [];
    for (var i = 0; i < list.length; i++) {
      if (evalWhen(list[i].when, stats)) return { id: list[i].id, img: list[i].img };
    }
    if (list.length) return { id: list[0].id, img: list[0].img };
    return null;
  }

  /* ---------------- view building ---------------- */
  function prettyName(name) {
    return name.replace(/[\-_]+/g, ' ').replace(/\b\w/g, function (c) { return c.toUpperCase(); });
  }

  function buildView(session) {
    var story = session.story;
    var pass = story.passages[session.current];
    var stats = merged(session);
    var lines = [], portraits = {}, seen = {};

    pass.blocks.forEach(function (b) {
      if (b.type === 'text') {
        var sp = b.speaker;
        if (sp && !seen[sp]) {
          portraits[sp] = { name: (story.config.characters[sp] || {}).name || sp, icon: resolveIcon(story, sp, stats) };
          seen[sp] = true;
        }
        var icon = sp ? portraits[sp].icon : null;
        if (b.segments) {
          var pieces = [], spans = [], pos = 0;
          b.segments.forEach(function (seglist, li) {
            if (li) { pieces.push(' '); pos += 1; }
            seglist.forEach(function (s) {
              pieces.push(s.text);
              if (s.act) spans.push({ kind: s.act.kind, target: s.act.target || null, from: pos, to: pos + s.text.length });
              pos += s.text.length;
            });
          });
          lines.push({ type: 'text', speaker: sp, icon: icon, text: pieces.join(''), spans: spans });
        } else {
          lines.push({ type: 'text', speaker: sp, icon: icon, text: b.text.join(' ') });
        }
      } else if (b.type === 'media') {
        lines.push({ type: 'media', path: b.path, caption: b.caption });
      }
    });

    var primaryId = story.config.characters.player ? 'player' : (Object.keys(story.config.characters)[0] || null);
    if (primaryId && !portraits[primaryId]) {
      portraits[primaryId] = { name: (story.config.characters[primaryId] || {}).name || primaryId, icon: resolveIcon(story, primaryId, stats) };
    }

    var choices = pass.choices
      .filter(function (c) { return !c.guard || evalGuard(story, c.guard, stats); })
      .map(function (c) { return { label: c.label, target: c.target }; });

    var continuation = null, ending = false;
    if (!choices.length) {
      if (pass.goto) continuation = { label: 'Continue', target: pass.goto };
      else ending = true;
    }

    var effectsLog = pass.effects.filter(function (e) {
      var info = statInfo(story, e.stat);
      return !(info && info.scope !== 'status' && info.def && info.def.hidden);
    }).map(function (e) {
      var info = statInfo(story, e.stat);
      var d = info ? info.def : null;
      var label = d ? d.label : e.stat, txt;
      if (e.op === 'roll') {
        var rs = stats[e.stat] === undefined ? '-' : stats[e.stat];
        txt = 'Roll ' + e.expr + ' = ' + rs;
      } else if (info && info.scope === 'status') txt = label + ' -> ' + String(e.value);
      else if (d && d.kind === 'enum') txt = label + ' -> ' + String(e.value);
      else if (e.op === '=') txt = label + ' = ' + String(e.value);
      else txt = label + ' ' + e.op + Math.abs(Number(e.value));
      return { stat: (info ? info.id : e.stat), scope: info ? info.scope : null, label: label, op: e.op, value: e.value, text: txt };
    });

    var visible = [];
    function pushStat(d, value) {
      if (d.hidden) return;
      var entry = { id: d.id, label: d.label, kind: d.kind, scope: d.scope, value: value, hidden: false,
                    icon: d.icon || '', hint: d.hint || '' };
      if (d.kind === 'enum') entry.values = d.values.slice();
      else {
        entry.min = d.min; entry.max = d.max;
        var span = d.max - d.min;
        entry.pct = span > 0 ? Math.round(((value - d.min) / span) * 100) : 100;
      }
      visible.push(entry);
    }
    Object.keys(story.config.charStats).forEach(function (id) { pushStat(story.config.charStats[id], session.char[id]); });
    Object.keys(story.config.globals).forEach(function (id) { pushStat(story.config.globals[id], session.global[id]); });

    return {
      passage: pass.name,
      title: prettyName(pass.name),
      tags: pass.tags,
      media: pass.media,
      lines: lines,
      portraits: portraits,
      status: clone(session.status),
      choices: choices,
      continuation: continuation,
      ending: ending,
      effects: effectsLog,
      stats: visible,
      allStats: { char: clone(session.char), global: clone(session.global), status: clone(session.status) },
      visit: session.visitCounts[pass.name] || 1
    };
  }

  /* ---------------- session ---------------- */
  function initStats(story) {
    var char = {}, g = {};
    Object.keys(story.config.charStats).forEach(function (id) { char[id] = clone(story.config.charStats[id].start); });
    Object.keys(story.config.globals).forEach(function (id) { g[id] = clone(story.config.globals[id].start); });
    return { char: char, global: g };
  }

  function initStatus(story) {
    var status = {};
    Object.keys(story.config.characters || {}).forEach(function (cid) {
      status[cid] = story.config.characters[cid].status || '';
    });
    return status;
  }

  function createSession(story) {
    var st = initStats(story);
    return { story: story, char: st.char, global: st.global, status: initStatus(story), history: [], current: null, visitCounts: {}, lastView: null };
  }

  function begin(session) {
    var st = initStats(session.story);
    session.char = st.char; session.global = st.global;
    session.status = initStatus(session.story);
    session.history = [];
    session.visitCounts = {};
    return enter(session, session.story.config.start);
  }

  function enter(session, id) {
    var story = session.story, pass = story.passages[id];
    if (!pass) throw new Error('Unknown passage: "' + id + '"');
    session.history.push({ id: id, snapshot: { char: clone(session.char), global: clone(session.global), status: clone(session.status) } });
    session.visitCounts[id] = (session.visitCounts[id] || 0) + 1;
    applyEffects(story, session, pass);
    session.current = id;
    session.lastView = buildView(session);
    return session.lastView;
  }

  function choose(session, key) {
    var view = session.lastView;
    if (!view) return { error: 'No active passage' };
    var idx = -1;
    if (typeof key === 'number') idx = key;
    else if (/^\d+$/.test(String(key))) idx = Number(key) - 1;
    else {
      var lb = String(key).toLowerCase();
      var i, j;
      for (i = 0; i < view.choices.length; i++) if (view.choices[i].label.toLowerCase() === lb) { idx = i; break; }
      if (idx === -1) for (j = 0; j < view.choices.length; j++) if (view.choices[j].label.toLowerCase().indexOf(lb) === 0) { idx = j; break; }
    }
    if (idx < 0 || idx >= view.choices.length) return { error: 'Invalid choice: ' + key };
    return { view: enter(session, view.choices[idx].target) };
  }

  function goOn(session) {
    var view = session.lastView;
    if (!view || view.ending) return { error: 'Story has ended' };
    if (view.continuation) return { view: enter(session, view.continuation.target) };
    if (view.choices.length) return { error: 'A choice is required' };
    return { error: 'Story has ended' };
  }

  function back(session) {
    if (!session.history.length) return { error: 'Nothing to go back to' };
    session.history.pop();
    var prev = session.history[session.history.length - 1];
    if (!prev) return { error: 'Nothing to go back to' };
    session.char = clone(prev.snapshot.char);
    session.global = clone(prev.snapshot.global);
    session.status = clone(prev.snapshot.status || {});
    session.current = prev.id;
    session.lastView = buildView(session);
    return { view: session.lastView };
  }

  /* ---------------- save / load ---------------- */
  function snapshot(session) {
    return {
      v: 1,
      passage: session.current,
      char: clone(session.char),
      global: clone(session.global),
      status: clone(session.status),
      visits: clone(session.visitCounts),
      history: session.history.map(function (h) {
        return { id: h.id, snapshot: { char: clone(h.snapshot.char), global: clone(h.snapshot.global), status: clone(h.snapshot.status || {}) } };
      })
    };
  }

  function restore(session, snap) {
    if (!snap || snap.v !== 1 || !session.story.passages[snap.passage]) return { error: 'Invalid save' };
    session.char = clone(snap.char || {});
    session.global = clone(snap.global || {});
    session.status = clone(snap.status || initStatus(session.story));
    session.visitCounts = clone(snap.visits || {});
    session.history = (snap.history || []).filter(function (h) {
      return session.story.passages[h.id];
    }).map(function (h) {
      return { id: h.id, snapshot: { char: clone(h.snapshot.char), global: clone(h.snapshot.global), status: clone(h.snapshot.status || {}) } };
    });
    if (!session.history.length) session.history.push({ id: snap.passage, snapshot: { char: clone(session.char), global: clone(session.global), status: clone(session.status) } });
    session.current = snap.passage;
    session.lastView = buildView(session);
    return { view: session.lastView };
  }

  /* ---------------- validation ---------------- */
  function validate(story) {
    var errors = [], warnings = [];
    story.errors.forEach(function (e) { errors.push(e); });
    var cfg = story.config, start = cfg.start;

    Object.keys(cfg.charStats).forEach(function (id) {
      if (cfg.globals[id]) warnings.push('Stat "' + id + '" is declared in both charStats and globals; it resolves to the character stat.');
    });

    if (!start || !story.passages[start]) errors.push('Start passage "' + (start || '(none)') + '" does not exist.');

    var reachable = {}, stack = [];
    if (start && story.passages[start]) { reachable[start] = true; stack.push(start); }
    while (stack.length) {
      var cur = stack.pop(), p = story.passages[cur];
      p.choices.forEach(function (c) {
        if (!story.passages[c.target]) errors.push('Passage "' + cur + '" links to missing passage "' + c.target + '".');
        else if (!reachable[c.target]) { reachable[c.target] = true; stack.push(c.target); }
      });
      if (p.goto) {
        if (!story.passages[p.goto]) errors.push('Passage "' + cur + '" jumps to missing passage "' + p.goto + '".');
        else if (!reachable[p.goto]) { reachable[p.goto] = true; stack.push(p.goto); }
      }
      p.effects.forEach(function (e) { if (!statInfo(story, e.stat)) warnings.push('Passage "' + cur + '" uses unknown stat "' + e.stat + '".'); });
      p.choices.forEach(function (c) { if (c.guard && !statInfo(story, c.guard.stat)) warnings.push('Passage "' + cur + '" guards on unknown stat "' + c.guard.stat + '".'); });
      p.blocks.forEach(function (b) {
        if (b.type === 'text' && b.speaker && !cfg.characters[b.speaker]) warnings.push('Passage "' + cur + '" has unknown speaker "' + b.speaker + '".');
        (b.segments || []).forEach(function (segl) {
          segl.forEach(function (s) {
            if (s.act && s.act.kind === 'open' && !modalTargetInfo(story, s.act.target)) {
              warnings.push('Passage "' + cur + '" action opens unknown target "' + s.act.target + '".');
            }
          });
        });
      });
    }
    Object.keys(story.passages).forEach(function (name) {
      if (!reachable[name]) warnings.push('Passage "' + name + '" is never reached from the start.');
    });
    Object.keys(cfg.characters).forEach(function (cid) {
      cfg.characters[cid].icons.forEach(function (ic) {
        if (!ic.img) errors.push('Character "' + cid + '" icon "' + ic.id + '" has no image.');
        if (ic.when) Object.keys(ic.when).forEach(function (k) {
          if (!cfg.charStats[k] && !cfg.globals[k]) warnings.push('Character "' + cid + '" icon "' + ic.id + '" checks unknown stat "' + k + '".');
        });
      });
    });
    return { errors: errors, warnings: warnings };
  }

  return {
    parse: parse, buildStory: buildStory, statInfo: statInfo,
    createSession: createSession, begin: begin, enter: enter, choose: choose,
    goOn: goOn, back: back, buildView: buildView, validate: validate,
    resolveIcon: resolveIcon, snapshot: snapshot, restore: restore,
    format: 'gse-v1'
  };
});