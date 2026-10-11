#!/usr/bin/env python3
"""Green Story Engine — CLI + single-file game compiler (agent-friendly).

Pure-stdlib Python mirror of engine/core.js. Same simple gse format.

Commands:
  new <name>            scaffold stories/<name>/ with template + sample headshots
  validate <story.gse> parse + link/stat/icon/asset checks (exit 1 on errors)
  run <story.gse>      play; answers piped, via --script, or interactive.
                        --json prints one JSON object per step for agents.
  dump <story.gse>     parsed story JSON (stdout) + checks (stderr)
  build <story.gse>    compile a single self-contained game -> build/<name>/index.html
  download <story.gse> fetch remote image URLs into media/ and rewrite the .gse to local files
  image <prompt>      generate one image (Pollinations.ai) into the story media folder
  generate <story.gse> generate scene images from their captions into media/ and rewrite the .gse
  serve <story.gse>    local dev server embedding the story in the engine UI
  edit <story.gse>     visual story editor (light/dark, no markup) at http://127.0.0.1:8911
  help                  this help
"""
import argparse
import base64
import json
import mimetypes
import os
import random
import re
import sys
import time
import urllib.request
from pathlib import Path
from urllib.parse import urlparse, parse_qs, quote, urlencode

ROOT = None  # set by _app_root() below
ENGINE_DIR = None
EDITOR_DIR = None
BUILD_DIR = None
STORY_EXT = ".gse"
DEMO = None


def _app_root():
    """Folder containing engine/, editor/, stories/.

    When packaged as a zipapp, GSE_APP_ROOT is set by the __main__ entry so
    ROOT points at the folder next to the archive (writeable for new stories).
    When run from source, derive it from this file's location.""" 
    env = os.environ.get("GSE_APP_ROOT")
    if env:
        return Path(env)
    return Path(__file__).resolve().parent.parent


ROOT = _app_root()
ENGINE_DIR = ROOT / "engine"
EDITOR_DIR = ROOT / "editor"
BUILD_DIR = ROOT / "build"
DEMO = ROOT / "stories" / "demo" / ("demo" + STORY_EXT)

HEADER_RE = re.compile(r"^::\s+(.+?)\s*$")
SET_RE = re.compile(r"^set::\s+(.+?)\s*$", re.I)
ROLL_RE = re.compile(r"^roll::\s+(.+?)\s*$", re.I)
IF_RE = re.compile(r"^if::\s+(.+?)\s*$", re.I)
SPEAKER_RE = re.compile(r"^speaker::\s+(.+?)\s*$", re.I)
IMG_RE = re.compile(r"^(?:image|img|pic)::\s+(.+?)\s*$", re.I)
GOTO_RE = re.compile(r"^goto::\s+(.+?)\s*$", re.I)
CHOICE_RE = re.compile(r"^\[\[(.+?)\]\]")
INLINE_IF = re.compile(r"^(?:if|if::)\s+(.+)$", re.I)
FX_RE = re.compile(r"^(\S+)\s*([+\-=])\s*(.+)$")
URL_RE = re.compile(r"^https?://", re.I)
GUARD_RE = re.compile(r"^(\S+)\s*(==|!=|>=|<=|>|<)\s*(.+)$")


def trim(s):
    return (" " + str(s)).strip()


def _numval(s):
    s = s.strip()
    if re.fullmatch(r"-?\d+", s):
        return int(s)
    try:
        return float(s)
    except (TypeError, ValueError):
        return s


def parse_set(expr):
    m = FX_RE.match(str(expr))
    if not m:
        return {"error": 'Bad effect "%s" (expected: set:: stat +10 | set:: stat = value)' % trim(expr)}
    return {"stat": m.group(1), "op": m.group(2), "value": _numval(m.group(3))}


DICE_RE = re.compile(r"^(\d*)d(\d+)((?:\s*[+-]\s*\d+)*)$", re.I)
DICE_MOD_RE = re.compile(r"([+-])\s*(\d+)", re.I)


def parse_roll(expr):
    """Parse a dice expression: NdS, dS, optionally +/-N modifiers, optionally
    prefixed with 'stat ='. Result lands in 'lastroll' by default."""
    s = str(expr).strip()
    stat, rest = "lastroll", s
    if "=" in s:
        left, right = s.split("=", 1)
        stat, rest = trim(left), trim(right)
    m = DICE_RE.match(rest)
    if not m:
        return {"error": 'Bad roll "%s" (expected: roll:: 2d6 | roll:: d20+1 | roll:: luck = d6)' % trim(expr)}
    count = int(m.group(1)) if m.group(1) else 1
    sides = int(m.group(2))
    if count < 1 or sides < 1:
        return {"error": 'Bad roll "%s" (dice must be NdS, sides >= 1)' % trim(expr)}
    mods = [{"sign": sm.group(1), "value": int(sm.group(2))}
            for sm in DICE_MOD_RE.finditer(m.group(3) or "")]
    return {"stat": stat, "op": "roll", "dice": {"count": count, "sides": sides, "mods": mods}, "expr": rest}


def parse_guard(expr):
    m = GUARD_RE.match(str(expr))
    if not m:
        return {"error": 'Bad condition "%s" (expected: if:: stat >= 60)' % trim(expr)}
    return {"stat": m.group(1), "op": m.group(2), "value": _numval(m.group(3))}


def split_choice(body):
    i = body.find("|")
    if i > -1:
        return {"label": trim(body[:i]), "target": trim(body[i + 1:])}
    return {"label": trim(body), "target": trim(body)}


ACTION_OPEN_RE = re.compile(r"\[open:([A-Za-z0-9_\-\.]+)\]")
ACTION_CLOSE_RE = re.compile(r"\[close\]")


def modal_target_info(story, token):
    """Resolve an action target token. Tokens are 'modal.<name>' for a config
    modal, or a character id for that character's profile modal. None if unknown."""
    s = str(token or "")
    if s.startswith("modal."):
        name = s[len("modal."):]
        d = story["config"].get("modals", {}).get(name)
        if d is None:
            return None
        return ("modal", name, d)
    if s in story["config"]["characters"]:
        return ("char", s, story["config"]["characters"][s])
    return None


def split_actions(text):
    """Split a text line into segments, extracting inline action spans.

    Syntax inside prose/dialogue lines:
      [open:<charId>]clickable label[/open]   opens that character's profile modal
      [close]clickable label[/close]          closes whatever modal is open

    Returns (segments, errs) where each segment is a dict with "text" and
    optionally "act" = {"kind": "open"|"close", "target": <token>|None}."""
    segs, errs, i = [], [], 0
    while i < len(text):
        om = ACTION_OPEN_RE.search(text, i)
        cm = ACTION_CLOSE_RE.search(text, i)
        if om and cm:
            nxt = om if om.start() <= cm.start() else cm
        else:
            nxt = om or cm
        if nxt is None:
            tail = text[i:]
            if tail:
                segs.append({"text": tail, "act": None})
            break
        if nxt.start() > i:
            segs.append({"text": text[i:nxt.start()], "act": None})
        if nxt is om:
            cid = om.group(1)
            j = om.end()
            close = text.find("[/open]", j)
            if close == -1:
                errs.append('Unclosed action "[open:%s]"' % cid)
                segs.append({"text": text[j:], "act": None})
                break
            label = text[j:close]
            if not label.strip():
                errs.append('Action "[open:%s]" has an empty label' % cid)
            segs.append({"text": label, "act": {"kind": "open", "target": cid}})
            i = close + len("[/open]")
        else:
            j = cm.end()
            close = text.find("[/close]", j)
            if close == -1:
                errs.append('Unclosed action "[close]"')
                segs.append({"text": text[j:], "act": None})
                break
            label = text[j:close]
            if not label.strip():
                errs.append('Action "[close]" has an empty label')
            segs.append({"text": label, "act": {"kind": "close"}})
            i = close + len("[/close]")
    return segs, errs


def parse_story(text_or_obj):
    """Parse gse text (or a {config,passages} object / JSON string)."""
    errors, passages, config_raw = [], {}, None
    story_obj = None

    if isinstance(text_or_obj, dict) and (text_or_obj.get("passages") or text_or_obj.get("config")):
        story_obj = text_or_obj
        config_raw = text_or_obj.get("config")
    elif isinstance(text_or_obj, str):
        if text_or_obj.startswith("\ufeff"):
            text_or_obj = text_or_obj[1:]
        if text_or_obj.lstrip().startswith("{"):
            try:
                maybe = json.loads(text_or_obj)
                if isinstance(maybe, dict) and (maybe.get("passages") or maybe.get("config")):
                    story_obj = maybe
                    config_raw = maybe.get("config")
            except ValueError:
                pass
        if story_obj is None:
            _parse_lines(text_or_obj, passages)
    else:
        return {"config": {}, "passages": {}, "errors": ["No story provided"]}

    if story_obj is not None:
        for name, v in story_obj.get("passages", {}).items():
            if isinstance(v, list):
                lines = v
            elif isinstance(v, dict):
                lines = v.get("lines", [])
            else:
                lines = []
            stories_tags = v.get("tags", []) if isinstance(v, dict) else []
            passages.setdefault(name, {"name": name, "tags": stories_tags, "lines": lines})

    if "config" in passages:
        cfg_text = trim("\n".join(passages["config"]["lines"]))
        if not cfg_text:
            errors.append("Config passage is empty")
        else:
            try:
                config_raw = json.loads(cfg_text)
            except ValueError as e:
                errors.append("Config passage is not valid JSON: %s" % e)
        del passages["config"]

    for name, p in passages.items():
        p["blocks"], p["choices"], p["effects"], p["media"] = [], [], [], []
        p["speaker"], p["goto"] = None, None
        block, pending_guard = None, None

        def flush():
            nonlocal block
            if block is not None:
                p["blocks"].append(block)
                block = None

        for line in p["lines"]:
            t = trim(line)
            if not t or t.startswith("#") or t.startswith("//") or t == "---":
                pending_guard = None
                continue
            m = SPEAKER_RE.match(line)
            if m:
                flush()
                who = trim(m.group(1)).lower()
                p["speaker"] = None if who in ("none", "narrator") else m.group(1).strip()
                pending_guard = None
                continue
            m = IMG_RE.match(line)
            if m:
                flush()
                rest = trim(m.group(1))
                if " " in rest:
                    i = rest.index(" ")
                    p["media"].append({"path": rest[:i].strip(), "caption": trim(rest[i + 1:])})
                else:
                    p["media"].append({"path": rest, "caption": ""})
                pending_guard = None
                continue
            m = SET_RE.match(line)
            if m:
                e = parse_set(m.group(1))
                if "error" in e:
                    errors.append(name + ": " + e["error"])
                else:
                    p["effects"].append(e)
                pending_guard = None
                continue
            m = ROLL_RE.match(line)
            if m:
                e = parse_roll(m.group(1))
                if "error" in e:
                    errors.append(name + ": " + e["error"])
                else:
                    p["effects"].append(e)
                pending_guard = None
                continue
            m = IF_RE.match(line)
            if m:
                g = parse_guard(m.group(1))
                if "error" in g:
                    errors.append(name + ": " + g["error"])
                else:
                    pending_guard = g
                continue
            m = GOTO_RE.match(line)
            if m:
                flush()
                p["goto"] = trim(m.group(1))
                pending_guard = None
                continue
            m = CHOICE_RE.match(line)
            if m:
                flush()
                c = split_choice(m.group(1))
                tail = trim(line[line.index("]]") + 2:])
                guard = pending_guard or None
                if tail:
                    tg = INLINE_IF.match(tail)
                    if tg:
                        ig = parse_guard(tg.group(1))
                        if "error" in ig:
                            errors.append(name + ": " + ig["error"])
                        else:
                            guard = ig
                    else:
                        errors.append('Passage "%s": choice has trailing text after ]] ("%s")' % (name, tail))
                p["choices"].append({"label": c["label"], "target": c["target"], "guard": guard})
                pending_guard = None
                continue
            if block is None:
                block = {"type": "text", "speaker": p["speaker"], "text": [], "segments": []}
            block["text"].append(t)
            segs, aerrs = split_actions(t)
            block["segments"].append(segs)
            for ae in aerrs:
                errors.append(name + ": " + ae)
            pending_guard = None
        flush()
        del p["lines"]

    return {"config": config_raw or {}, "passages": passages, "errors": errors}


def _parse_lines(text, passages):
    cur = None
    for line in (text or "").splitlines():
        m = HEADER_RE.match(line)
        if m:
            name, tags = trim(m.group(1)), []
            tm = re.match(r"^(.*?)\s+\[(.*)\]$", name)
            if tm:
                name, tags = trim(tm.group(1)), trim(tm.group(2)).split()
            cur = {"name": name, "tags": tags, "lines": []}
            passages[name] = cur
        elif cur is not None:
            cur["lines"].append(line)


# ----------------------------------------------------------------- build story
def default_stat(sid, d, scope):
    d = d or {}
    if d.get("values"):
        vals = [str(v) for v in d["values"]]
        sval = str(d["start"]) if d.get("start") is not None else (vals[0] if vals else "")
        return {"id": sid, "label": d.get("label", sid), "kind": "enum", "values": vals, "start": sval,
                "hidden": bool(d.get("hidden")), "icon": d.get("icon", ""), "hint": d.get("hint", ""), "scope": scope}
    mn = float(d.get("min", 0))
    mx = float(d.get("max", 100))
    st = float(d.get("start", mn + (mx - mn) / 2))
    st = max(mn, min(mx, st))
    return {"id": sid, "label": d.get("label", sid), "kind": "numeric", "min": mn, "max": mx, "start": st,
            "hidden": bool(d.get("hidden")), "icon": d.get("icon", ""), "hint": d.get("hint", ""), "scope": scope}


def build_story(parsed):
    config = json.loads(json.dumps(parsed.get("config") or {}))
    uses_roll = any(e.get("op") == "roll" for p in parsed["passages"].values() for e in p.get("effects", []))
    if uses_roll and "lastroll" not in (config.get("globals") or {}):
        config.setdefault("globals", {})
        config["globals"]["lastroll"] = {"label": "Last Roll", "min": 0, "max": 9999, "start": 0, "hidden": True}
    config["charStats"] = {sid: default_stat(sid, d, "char") for sid, d in (config.get("charStats") or {}).items()}
    config["globals"] = {sid: default_stat(sid, d, "global") for sid, d in (config.get("globals") or {}).items()}
    chars = config.get("characters")
    if not isinstance(chars, dict) or not chars:
        chars = {}
        config["characters"] = chars
    chars.setdefault("player", {"name": "Player", "icons": []})
    for cid, c in chars.items():
        c.setdefault("icons", [])
        for i, ic in enumerate(c["icons"]):
            ic.setdefault("id", "icon%d" % i)
        c.setdefault("name", cid)
    keys = list(parsed["passages"].keys())
    config["start"] = config.get("start") or ("start" if "start" in keys else (keys[0] if keys else ""))
    config.setdefault("title", "Untitled Story")
    config.setdefault("mediaDir", "media")
    config.setdefault("modals", {})
    return {"config": config, "passages": parsed["passages"], "errors": list(parsed.get("errors", []))}


def stat_info(story, raw):
    """Resolve a stat reference to (scope, def) or None. '@name' forces global."""
    scope = None
    bare = raw
    s = str(raw)
    if s.startswith("@"):
        scope, bare = "global", s[1:]
    b = str(bare)
    if scope != "global" and "." in b:
        cid, field = b.split(".", 1)
        if field == "status" and cid in story["config"]["characters"]:
            return (b, "status", cid)
    if scope != "global" and bare in story["config"]["charStats"]:
        return (bare, "char", story["config"]["charStats"][bare])
    if scope != "char" and bare in story["config"]["globals"]:
        return (bare, "global", story["config"]["globals"][bare])
    return None


def merged_stats(session):
    out = dict(session["char"])
    out.update(session["global"])
    return out


def apply_effects(story, session, pass_):
    for e in pass_["effects"]:
        info = stat_info(story, e["stat"])
        if info is None:
            continue
        sid, scope, d = info
        bucket = session[scope]
        if e.get("op") == "roll":
            total = sum(random.randint(1, e["dice"]["sides"]) for _ in range(e["dice"]["count"]))
            for m in e["dice"]["mods"]:
                total += -m["value"] if m["sign"] == "-" else m["value"]
            if scope == "status":
                session["status"][d] = str(total)
                continue
            if d["kind"] == "enum":
                bucket[sid] = str(total)
                continue
            total = max(d["min"], min(d["max"], total))
            bucket[sid] = total
            continue
        if scope == "status":
            cur = str(session["status"].get(d, ""))
            if e["op"] == "=":
                session["status"][d] = str(e["value"])
            elif e["op"] == "+":
                session["status"][d] = cur + str(e["value"])
            elif e["op"] == "-":
                session["status"][d] = cur.replace(str(e["value"]), "")
            continue
        bucket = session[scope]
        if d["kind"] == "enum":
            if e["op"] == "=":
                bucket[sid] = str(e["value"])
            continue
        v = bucket.get(sid, d["start"])
        op, val = e["op"], e["value"]
        if op == "+":
            v = v + val
        elif op == "-":
            v = v - val
        else:
            v = val
        v = max(d["min"], min(d["max"], v))
        bucket[sid] = v


def eval_guard(story, guard, stats):
    info = stat_info(story, guard["stat"])
    v = stats.get(info[0]) if info else None
    if info is None or v is None:
        return False
    op, g = guard["op"], guard["value"]
    if info[2]["kind"] == "enum":
        if op == "==":
            return str(v) == str(g)
        if op == "!=":
            return str(v) != str(g)
        return False
    try:
        n = float(v)
    except (TypeError, ValueError):
        return False
    try:
        gg = float(g)
    except (TypeError, ValueError):
        return False
    return {"==": n == gg, "!=": n != gg, ">": n > gg, ">=": n >= gg, "<": n < gg, "<=": n <= gg}[op]


def match_one(cond, v):
    if cond is None:
        return True
    if isinstance(cond, list):
        return str(v) in [str(x) for x in cond]
    if isinstance(cond, dict):
        try:
            num = float(v)
        except (TypeError, ValueError):
            return False
        if cond.get("min") is not None and num < float(cond["min"]):
            return False
        if cond.get("max") is not None and num > float(cond["max"]):
            return False
        if cond.get("gt") is not None and num <= float(cond["gt"]):
            return False
        if cond.get("lt") is not None and num >= float(cond["lt"]):
            return False
        return True
    return str(cond) == str(v)


def eval_when(when, stats):
    if not when:
        return True
    for k, cond in when.items():
        if not match_one(cond, stats.get(k)):
            return False
    return True


def resolve_icon(story, cid, stats):
    c = story["config"]["characters"].get(cid)
    if not c:
        return None
    for ic in c["icons"]:
        if eval_when(ic.get("when"), stats):
            return {"id": ic["id"], "img": ic["img"]}
    if c["icons"]:
        return {"id": c["icons"][0]["id"], "img": c["icons"][0]["img"]}
    return None


def pretty_name(name):
    t = re.sub(r"[-_]+", " ", name)
    return re.sub(r"\b\w", lambda m: m.group(0).upper(), t)


def build_view(session):
    story, pass_ = session["story"], session["story"]["passages"][session["current"]]
    stats = merged_stats(session)
    lines, portraits, seen = [], {}, set()

    for b in pass_["blocks"]:
        if b["type"] == "text":
            sp = b["speaker"]
            if sp and sp not in seen:
                portraits[sp] = {"name": story["config"]["characters"].get(sp, {}).get("name", sp),
                                 "icon": resolve_icon(story, sp, stats)}
                seen.add(sp)
            icon = portraits[sp]["icon"] if sp in portraits else None
            if b.get("segments"):
                pieces, spans, pos = [], [], 0
                for li, seglist in enumerate(b["segments"]):
                    if li:
                        pieces.append(" ")
                        pos += 1
                    for s in seglist:
                        pieces.append(s["text"])
                        if s.get("act"):
                            act = s["act"]
                            spans.append({"kind": act["kind"], "target": act.get("target") or None,
                                          "from": pos, "to": pos + len(s["text"])})
                        pos += len(s["text"])
                lines.append({"type": "text", "speaker": sp, "icon": icon,
                              "text": "".join(pieces), "spans": spans})
            else:
                lines.append({"type": "text", "speaker": sp, "icon": icon, "text": " ".join(b["text"])})
        elif b["type"] == "media":
            lines.append({"type": "media", "path": b["path"], "caption": b["caption"]})

    primary_id = "player" if "player" in story["config"]["characters"] else next(iter(story["config"]["characters"]), None)
    if primary_id and primary_id not in portraits:
        portraits[primary_id] = {"name": story["config"]["characters"].get(primary_id, {}).get("name", primary_id),
                                 "icon": resolve_icon(story, primary_id, stats)}

    choices = [{"label": c["label"], "target": c["target"]}
               for c in pass_["choices"] if not c["guard"] or eval_guard(story, c["guard"], stats)]
    continuation, ending = None, False
    if not choices:
        if pass_["goto"]:
            continuation = {"label": "Continue", "target": pass_["goto"]}
        else:
            ending = True

    effects_log = []
    for e in pass_["effects"]:
        info = stat_info(story, e["stat"])
        if info and info[1] != "status" and info[2].get("hidden"):
            continue
        if e.get("op") == "roll":
            rs = stats.get(e["stat"], "-")
            effects_log.append({"stat": info[0] if info else e["stat"],
                                "scope": info[1] if info else None, "label": e["stat"],
                                "op": "roll", "value": rs, "text": "Roll %s = %s" % (e["expr"], rs)})
            continue
        if info and info[1] == "status":
            cid = info[2]
            cname = story["config"]["characters"].get(cid, {}).get("name", cid)
            label = "%s status" % cname
            txt = "%s -> %s" % (label, e["value"])
        else:
            d = info[2] if info else None
            label = d["label"] if d else e["stat"]
            if d and d["kind"] == "enum":
                txt = "%s -> %s" % (label, e["value"])
            elif e["op"] == "=":
                txt = "%s = %s" % (label, e["value"])
            else:
                txt = "%s %s%s" % (label, e["op"], abs(e["value"]))
        effects_log.append({"stat": info[0] if info else e["stat"],
                            "scope": info[1] if info else None, "label": label,
                            "op": e["op"], "value": e["value"], "text": txt})

    visible = []
    scope_key = {"char": "charStats", "global": "globals"}
    for scope in ("char", "global"):
        for sid, d in story["config"][scope_key[scope]].items():
            if d["hidden"]:
                continue
            entry = {"id": sid, "label": d["label"], "kind": d["kind"], "scope": d["scope"],
                     "value": session[scope][sid], "hidden": False,
                     "icon": d.get("icon", ""), "hint": d.get("hint", "")}
            if d["kind"] == "enum":
                entry["values"] = list(d["values"])
            else:
                span = d["max"] - d["min"]
                entry["min"], entry["max"] = d["min"], d["max"]
                entry["pct"] = round((session[scope][sid] - d["min"]) / span * 100) if span > 0 else 100
            visible.append(entry)

    return {"passage": pass_["name"], "title": pretty_name(pass_["name"]), "tags": pass_["tags"],
            "media": pass_["media"], "lines": lines, "portraits": portraits, "status": json.loads(json.dumps(session["status"])),
            "choices": choices,
            "continuation": continuation, "ending": ending, "effects": effects_log,
            "stats": visible,
            "allStats": {"char": json.loads(json.dumps(session["char"])),
                         "global": json.loads(json.dumps(session["global"])),
                         "status": json.loads(json.dumps(session["status"]))},
            "visit": session["visitCounts"].get(pass_["name"], 1)}


def create_session(story):
    return {"story": story, "char": {}, "global": {}, "status": {}, "history": [], "current": None,
            "visitCounts": {}, "lastView": None}


def init_stats(story):
    ch = {sid: json.loads(json.dumps(d["start"])) for sid, d in story["config"]["charStats"].items()}
    gl = {sid: json.loads(json.dumps(d["start"])) for sid, d in story["config"]["globals"].items()}
    return ch, gl


def init_status(story):
    return {cid: (c.get("status") or "") for cid, c in story["config"]["characters"].items()}


def begin(session):
    session["char"], session["global"] = init_stats(session["story"])
    session["status"] = init_status(session["story"])
    session["history"], session["visitCounts"] = [], {}
    return enter(session, session["story"]["config"]["start"])


def enter(session, pid):
    story, pass_ = session["story"], session["story"]["passages"].get(pid)
    if pass_ is None:
        raise RuntimeError('Unknown passage: "%s"' % pid)
    session["history"].append({"id": pid, "snapshot": {"char": json.loads(json.dumps(session["char"])),
                                                       "global": json.loads(json.dumps(session["global"])),
                                                       "status": json.loads(json.dumps(session["status"]))}})
    session["visitCounts"][pid] = session["visitCounts"].get(pid, 0) + 1
    apply_effects(story, session, pass_)
    session["current"] = pid
    session["lastView"] = build_view(session)
    return session["lastView"]


def choose(session, key):
    view = session["lastView"]
    if view is None:
        return {"error": "No active passage"}
    idx = -1
    if isinstance(key, int):
        idx = key
    elif isinstance(key, str) and key.strip().isdigit():
        idx = int(key.strip()) - 1
    else:
        lb = str(key).lower()
        for i, c in enumerate(view["choices"]):
            if c["label"].lower() == lb:
                idx = i
                break
        if idx == -1:
            for i, c in enumerate(view["choices"]):
                if c["label"].lower().startswith(lb):
                    idx = i
                    break
    if idx < 0 or idx >= len(view["choices"]):
        return {"error": "Invalid choice: %s" % key}
    return {"view": enter(session, view["choices"][idx]["target"])}


def go_on(session):
    view = session["lastView"]
    if view is None or view["ending"]:
        return {"error": "Story has ended"}
    if view["continuation"]:
        return {"view": enter(session, view["continuation"]["target"])}
    if view["choices"]:
        return {"error": "A choice is required"}
    return {"error": "Story has ended"}


def back(session):
    if len(session["history"]) <= 1:
        return {"error": "Nothing to go back to"}
    session["history"].pop()
    prev = session["history"][-1]
    session["char"] = json.loads(json.dumps(prev["snapshot"]["char"]))
    session["global"] = json.loads(json.dumps(prev["snapshot"]["global"]))
    session["status"] = json.loads(json.dumps(prev["snapshot"].get("status") or {}))
    session["current"] = prev["id"]
    session["lastView"] = build_view(session)
    return {"view": session["lastView"]}


def snapshot(session):
    return {"v": 1, "passage": session["current"],
            "char": json.loads(json.dumps(session["char"])),
            "global": json.loads(json.dumps(session["global"])),
            "status": json.loads(json.dumps(session["status"])),
            "visits": json.loads(json.dumps(session["visitCounts"])),
            "history": [{"id": h["id"],
                         "snapshot": {"char": json.loads(json.dumps(h["snapshot"]["char"])),
                                      "global": json.loads(json.dumps(h["snapshot"]["global"])),
                                      "status": json.loads(json.dumps(h["snapshot"].get("status") or {}))}}
                        for h in session["history"]]}


def restore(session, snap):
    if not isinstance(snap, dict) or snap.get("v") != 1 or snap.get("passage") not in session["story"]["passages"]:
        return {"error": "Invalid save"}
    session["char"] = json.loads(json.dumps(snap.get("char") or {}))
    session["global"] = json.loads(json.dumps(snap.get("global") or {}))
    session["status"] = json.loads(json.dumps(snap.get("status") or init_status(session["story"])))
    session["visitCounts"] = json.loads(json.dumps(snap.get("visits") or {}))
    hist = []
    for h in snap.get("history") or []:
        if h.get("id") in session["story"]["passages"]:
            hist.append({"id": h["id"], "snapshot": {"char": json.loads(json.dumps(h["snapshot"]["char"])),
                                                     "global": json.loads(json.dumps(h["snapshot"]["global"])),
                                                     "status": json.loads(json.dumps(h["snapshot"].get("status") or {}))}})
    if not hist:
        hist.append({"id": snap["passage"],
                     "snapshot": {"char": json.loads(json.dumps(session["char"])),
                                  "global": json.loads(json.dumps(session["global"])),
                                  "status": json.loads(json.dumps(session["status"]))}})
    session["history"] = hist
    session["current"] = snap["passage"]
    session["lastView"] = build_view(session)
    return {"view": session["lastView"]}


# ----------------------------------------------------------------- validate
def validate(story, media_dir=None):
    errors, warnings = list(story["errors"]), []
    cfg, start = story["config"], story["config"]["start"]
    for sid in cfg["charStats"]:
        if sid in cfg["globals"]:
            warnings.append('Stat "%s" is declared in both charStats and globals.' % sid)
    if not start or start not in story["passages"]:
        errors.append('Start passage "%s" does not exist.' % (start or "(none)"))

    reachable, stack = {}, []
    if start and start in story["passages"]:
        reachable[start] = True
        stack.append(start)
    media_base = str(media_dir) if media_dir else None
    while stack:
        cur = stack.pop()
        p = story["passages"][cur]
        for c in p["choices"]:
            if c["target"] not in story["passages"]:
                errors.append('Passage "%s" links to missing passage "%s".' % (cur, c["target"]))
            elif c["target"] not in reachable:
                reachable[c["target"]] = True
                stack.append(c["target"])
        if p["goto"]:
            if p["goto"] not in story["passages"]:
                errors.append('Passage "%s" jumps to missing passage "%s".' % (cur, p["goto"]))
            elif p["goto"] not in reachable:
                reachable[p["goto"]] = True
                stack.append(p["goto"])
        for e in p["effects"]:
            if stat_info(story, e["stat"]) is None:
                warnings.append('Passage "%s" uses unknown stat "%s".' % (cur, e["stat"]))
        for c in p["choices"]:
            if c["guard"] and stat_info(story, c["guard"]["stat"]) is None:
                warnings.append('Passage "%s" guards on unknown stat "%s".' % (cur, c["guard"]["stat"]))
        for b in p["blocks"]:
            if b["type"] == "text" and b["speaker"] and b["speaker"] not in cfg["characters"]:
                warnings.append('Passage "%s" has unknown speaker "%s".' % (cur, b["speaker"]))
            for segs in (b.get("segments") or []):
                for s in segs:
                    act = s.get("act")
                    if act and act["kind"] == "open" and modal_target_info(story, act.get("target")) is None:
                        warnings.append('Passage "%s" action opens unknown target "%s".' % (cur, act.get("target")))
        if media_base:
            for md in p["media"]:
                if is_url(md["path"]):
                    continue
                fp = os.path.join(media_base, md["path"].replace("/", os.sep))
                if not os.path.isfile(fp):
                    errors.append('Passage "%s" references missing media file "%s".' % (cur, md["path"]))

    for name in story["passages"]:
        if name not in reachable:
            warnings.append('Passage "%s" is never reached from the start.' % name)

    for cid, c in cfg["characters"].items():
        for ic in c["icons"]:
            if not ic.get("img"):
                errors.append('Character "%s" icon "%s" has no image.' % (cid, ic["id"]))
            elif media_base and not is_url(ic["img"]):
                fp = os.path.join(media_base, ic["img"].replace("/", os.sep))
                if not os.path.isfile(fp):
                    errors.append('Character "%s" icon "%s" references missing image "%s".' % (cid, ic["id"], ic["img"]))
            for k in (ic.get("when") or {}):
                if k not in cfg["charStats"] and k not in cfg["globals"]:
                    warnings.append('Character "%s" icon "%s" checks unknown stat "%s".' % (cid, ic["id"], k))
    for mname, m in (cfg.get("modals") or {}).items():
        img = m.get("image")
        if img and media_base and not is_url(img):
            fp = os.path.join(media_base, img.replace("/", os.sep))
            if not os.path.isfile(fp):
                errors.append('Modal "%s" references missing image "%s".' % (mname, img))
    return {"errors": errors, "warnings": warnings}


# ----------------------------------------------------------------- render
def bar(pct, width=12):
    pct = max(0, min(100, int(pct)))
    filled = round(pct / 100 * width)
    return "#" * filled + "-" * (width - filled)


_ASCII_MAP = {ord("—"): "-", ord("–"): "-", ord("‘"): "'", ord("’"): "'",
              ord("“"): '"', ord("”"): '"', ord("…"): "...", ord("•"): "*",
              ord("→"): "->"}


def _clean(s):
    return str(s).translate(_ASCII_MAP)


def print_view(view):
    print("\n" + "-" * 6 + " " + view["title"] + " " + "-" * 6)
    for line in view["lines"]:
        if line["type"] == "media":
            caption = (" - " + _clean(line["caption"])) if line["caption"] else ""
            print("  [image: %s%s]" % (line["path"], caption))
        else:
            prefix = ""
            if line["speaker"]:
                name = view["portraits"][line["speaker"]]["name"]
                icon = (line["icon"]["img"] if line["icon"] else "") or "(no icon)"
                prefix = "  %s [%s]: " % (name, icon)
            else:
                prefix = "  "
            print(prefix + _clean(line["text"]))
    for e in view["effects"]:
        print("  > %s" % e["text"])
    if view["stats"]:
        last = None
        for s in view["stats"]:
            if s["scope"] != last:
                print("\n  [" + ("Character" if s["scope"] == "char" else "Story") + "]")
                last = s["scope"]
            if s["kind"] == "enum":
                print("    * %s: %s" % (s["label"], s["value"]))
            else:
                print("    * %s %s %s/%s" % (s["label"], bar(s["pct"]), int(s["value"]), int(s["max"])))
    if view["choices"]:
        print("\n  Choose:")
        for i, c in enumerate(view["choices"], 1):
            print("    %d) %s" % (i, c["label"]))


def view_json(view):
    lines = []
    for line in view["lines"]:
        if line["type"] == "media":
            lines.append({"type": "media", "path": line["path"], "caption": line["caption"]})
        else:
            lines.append({"type": "text", "speaker": line["speaker"], "text": line["text"],
                          "spans": line.get("spans") or [],
                          "icon": (line["icon"]["img"] if line["icon"] else None)})
    icons = {}
    for cid, info in view["portraits"].items():
        icons[cid] = {"name": info["name"],
                      "icon": (info["icon"]["img"] if info["icon"] else None),
                      "iconId": (info["icon"]["id"] if info["icon"] else None)}
    return {"event": "passage", "passage": view["passage"], "title": view["title"],
            "tags": view["tags"], "lines": lines, "characters": icons,
            "choices": view["choices"], "continuation": view["continuation"],
            "ending": view["ending"], "effects": view["effects"],
            "charStats": {s["id"]: s["value"] for s in view["stats"] if s["scope"] == "char"},
            "globals": {s["id"]: s["value"] for s in view["stats"] if s["scope"] == "global"},
            "allStats": view["allStats"],
            "icon": icons.get("player", {}).get("icon") if "player" in icons else None}


# ----------------------------------------------------------------- commands
def load_story(path):
    p = Path(path)
    if not p.is_file():
        sys.exit("error: story file not found: %s" % path)
    parsed = parse_story(p.read_text(encoding="utf-8"))
    story = build_story(parsed)
    return story, p


def media_dir_of(p, story):
    md = p.parent / story["config"]["mediaDir"]
    return md if md.is_dir() else None


def collect_refs(story):
    """Return sorted set of image names referenced (icons + inline media).

    Local names are normalized to the platform separator; remote URLs are kept
    exactly as written so they can be detected and downloaded."""
    def norm(ref):
        return ref if is_url(ref) else ref.replace("/", os.sep)

    refs = set()
    for cid, c in story["config"]["characters"].items():
        for ic in c["icons"]:
            if ic.get("img"):
                refs.add(norm(ic["img"]))
    for m in (story["config"].get("modals") or {}).values():
        if m.get("image"):
            refs.add(norm(m["image"]))
    for p in story["passages"].values():
        for md in p["media"]:
            refs.add(norm(md["path"]))
    return sorted(refs)


def is_url(ref):
    """True if an image reference is a remote http(s) URL."""
    return bool(URL_RE.match(ref or ""))


def _fetch_image(url):
    """Return (bytes, mime) for a remote image, or None on any failure."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "gse/1.0"})
        with urllib.request.urlopen(req, timeout=20) as r:
            data = r.read()
            ctype = (r.headers.get("Content-Type") or "").split(";")[0].strip().lower() or "image/png"
        if not data or len(data) > 12_000_000:
            return None
        return data, ctype
    except Exception:
        return None


def _download_image(url, media_dir):
    """Download a URL into media_dir with a collision-free name; return the filename or None."""
    got = _fetch_image(url)
    if not got:
        return None
    data, ctype = got
    ext = mimetypes.guess_extension(ctype) or "png"
    if not ext.startswith("."):
        ext = "." + ext
    if ext in (".jpe", ".jfif"):
        ext = ".jpeg"
    base = re.sub(r"[^A-Za-z0-9_-]+", "-", Path(urlparse(url).path).name).strip("-")
    base = (base.rsplit(".", 1)[0] if "." in base else base) or "image"
    n, candidate = 2, base + ext
    while (media_dir / candidate).exists():
        candidate = "%s-%d%s" % (base, n, ext)
        n += 1
    (media_dir / candidate).write_bytes(data)
    return candidate


# ----------------------------------------------------------------- image generation
# Providers:
#   cf            Cloudflare Workers AI - free 10k neurons/day, no queue,
#                 flux-1-schnell. Needs CF_API_TOKEN + CF_ACCOUNT_ID (env or
#                 the private .gse_credentials.json next to the repo).
#   horde         Stable Horde crowdsourced SDXL cluster - free, no token needed
#                 (set HORDE_API_KEY to raise priority), but queues are long.
#   pollinations  Pollinations.ai - free, instant, no token, lower quality.
POLLINATION_URL = "https://image.pollinations.ai/prompt/"
HORDE_URL = "https://stablehorde.net/api/v2/"
DEFAULT_IMAGE_MODEL = "flux"
ANON_HORDE_KEY = "0000000000"
DEFAULT_HORDE_MODELS = ["Juggernaut XL", "Deliberate", "DreamShaper XL"]
CF_IMAGE_MODEL = "@cf/black-forest-labs/flux-1-schnell"
CF_IMAGE_URL = "https://api.cloudflare.com/client/v4/accounts/{account}/ai/run/{model}"


def _credentials_path():
    """Credentials live OUTSIDE the repo: the parent of the app root (the dev
    folder), so git never sees them. Override with GSE_CREDENTIALS."""
    env = os.environ.get("GSE_CREDENTIALS")
    if env:
        return Path(env)
    return ROOT.parent / ".gse_credentials.json"


def _load_cred(name):
    """API secret from env var (preferred) or the private credentials file."""
    val = os.environ.get(name)
    if val:
        return val
    try:
        d = json.loads(_credentials_path().read_text(encoding="utf-8"))
        return d.get(name) or ""
    except Exception:
        return ""


def _slug(text):
    """Filesystem-safe lowercase slug from free text (used for generated filenames)."""
    s = re.sub(r"[^A-Za-z0-9]+", "-", str(text).lower()).strip("-")
    return (s[:60] or "image").strip("-")


def _pollination_url(prompt, width=1024, height=1024, seed=None, model=None):
    """Build the Pollinations.ai image URL for a prompt (GET, no API key)."""
    q = [("width", int(width)), ("height", int(height)),
         ("nologo", "true"), ("seed", int(seed))] if seed is not None else \
        [("width", int(width)), ("height", int(height)), ("nologo", "true")]
    if model:
        q.append(("model", model))
    return POLLINATION_URL + quote(prompt) + "?" + urlencode(q)


def _pollinations_image(prompt, width=1024, height=1024, seed=None, model=None, timeout=120):
    """Fetch generated image bytes from Pollinations.ai, or None on any failure."""
    url = _pollination_url(prompt, width, height, seed, model or DEFAULT_IMAGE_MODEL)
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "gse/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = r.read()
        if not data or len(data) > 12_000_000:
            return None
        return data
    except Exception:
        return None


def _horde_body(prompt, width=1024, height=1024, seed=None, model=None):
    """Request body for the Stable Horde /generate/async endpoint (pure, testable)."""
    models = [model] if model else list(DEFAULT_HORDE_MODELS)
    params = {"width": int(width), "height": int(height),
              "steps": 25, "cfg_scale": 7, "sampler_name": "k_euler_a"}
    if seed is not None:
        params["seed"] = int(seed)
    return {"prompt": prompt, "models": models, "params": params}


def _horde_image(prompt, width=1024, height=1024, seed=None, model=None, timeout=300):
    """Render a prompt on the Stable Horde cluster; bytes or None.

    Uses the anonymous key by default (no signup, lowest queue priority); set
    HORDE_API_KEY to a registered key for faster priority. Keys are static and
    never expire, so there is no refresh loop: if a request comes back 401/403
    or the key is rejected, we just fall back to the anonymous key."""
    for key in (_env_horde_key(), ANON_HORDE_KEY):
        if not key:
            continue
        got = _horde_image_with_key(prompt, width, height, seed, model, timeout, key)
        if got is not None:
            return got
    return None


def _env_horde_key():
    return os.environ.get("HORDE_API_KEY", "")


def _horde_image_with_key(prompt, width, height, seed, model, timeout, key):
    headers = {"User-Agent": "gse/1.0 (horde imagegen)",
               "Content-Type": "application/json",
               "apikey": key}
    try:
        body = json.dumps(_horde_body(prompt, width, height, seed, model)).encode()
        req = urllib.request.Request(HORDE_URL + "generate/async", data=body,
                                     headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=30) as r:
            resp = json.loads(r.read().decode())
        jid = resp.get("id")
        if not jid:
            return None
        deadline = time.time() + timeout
        while time.time() < deadline:
            time.sleep(4)
            req2 = urllib.request.Request(HORDE_URL + "generate/status/" + jid,
                                          headers=headers)
            with urllib.request.urlopen(req2, timeout=30) as r:
                st = json.loads(r.read().decode())
            if st.get("done"):
                for g in st.get("generations") or []:
                    if g.get("img"):
                        return base64.b64decode(g["img"])
                return None
            wait = st.get("wait_time") or 0
            if wait > 300:
                print("  ! horde queue wait ~%ds (too long, give up; set HORDE_API_KEY for priority)" % wait,
                      file=sys.stderr)
                return None
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            return None
    except Exception:
        return None
    return None


def _sniff_ext(data):
    """Correct file extension for raw image bytes ("" if unknown)."""
    if data[:3] == b"\xff\xd8\xff":
        return ".jpg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return ".png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"
    if data[:4] in (b"GIF8",):
        return ".gif"
    return ""


def _cf_body(prompt, width=1024, height=1024, seed=None, model=None):
    """Request body for Cloudflare Workers AI text-to-image (pure, testable).

    flux-1-schnell (the default) rejects extra properties like width/height/
    steps with AiError 5006, so those are only sent when a width or height was
    explicitly requested. The model derives its own output size otherwise."""
    body = {"prompt": prompt}
    if width is not None or height is not None:
        body["width"] = int(width if width is not None else 1024)
        body["height"] = int(height if height is not None else 1024)
    if seed is not None:
        body["seed"] = int(seed)
    return body


def _cf_mp(params):
    """Build a multipart/form-data body for CF image models that require it
    (flux-2-dev family). The params ride in a part named 'json'."""
    b = "----gse" + ("%032x" % random.getrandbits(128))
    part = '--%s\r\nContent-Disposition: form-data; name="json"\r\n\r\n%s\r\n' % (b, json.dumps(params))
    body = part.encode() + ("--%s--\r\n" % b).encode()
    return body, b


def _cf_image(prompt, width=1024, height=1024, seed=None, model=CF_IMAGE_MODEL, timeout=300):
    """Render a prompt on Cloudflare Workers AI free tier; bytes or None.

    Free allowance is 10,000 neurons/day. Reads CF_API_TOKEN / CF_ACCOUNT_ID
    from env or the private credentials file. Some models (flux-2-dev) require
    multipart/form-data; we fall back to that automatically when the JSON
    request is rejected for it."""
    token = _load_cred("CF_API_TOKEN")
    account = _load_cred("CF_ACCOUNT_ID")
    if not token or not account:
        print("  ! set CF_API_TOKEN + CF_ACCOUNT_ID (env or .gse_credentials.json)", file=sys.stderr)
        return None
    model = model or CF_IMAGE_MODEL
    params = _cf_body(prompt, width, height, seed, model)
    url = CF_IMAGE_URL.format(account=account, model=model)
    try:
        raw = _cf_request(url, token, json.dumps(params).encode(), "application/json", timeout)
        return _cf_extract(raw)
    except urllib.error.HTTPError as e:
        errbody = e.read().decode("utf-8", "replace")
        if "multipart" in errbody:
            try:
                body, boundary = _cf_mp(params)
                raw = _cf_request(url, token, body, "multipart/form-data; boundary=" + boundary, timeout)
                return _cf_extract(raw)
            except Exception:
                return None
        return None
    except Exception:
        return None


def _cf_request(url, token, data, ctype, timeout):
    req = urllib.request.Request(url, data=data, method="POST", headers={
        "Authorization": "Bearer " + token,
        "Content-Type": ctype,
        "User-Agent": "gse/1.0 (cf imagegen)"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def _cf_extract(raw):
    if raw.lstrip().startswith(b"{"):
        try:
            d = json.loads(raw.decode())
        except ValueError:
            return None
        img = (d.get("result") or {}).get("image") if isinstance(d, dict) else None
        if not img:
            return None
        data = base64.b64decode(img)
    else:
        data = raw
    if not data or len(data) > 12_000_000:
        return None
    return data


def _generate_image(prompt, width=1024, height=1024, seed=None, model=None,
                    provider="cf", timeout=300):
    """Render a prompt with the chosen free provider; returns bytes or None.

    The Cloudflare flux-1-schnell model derives its own output size and rejects
    explicit width/height, so those are dropped for the cf provider. Other
    providers use the requested dimensions."""
    if provider == "cf":
        return _cf_image(prompt, None, None, seed, model, timeout)
    if provider == "pollinations":
        return _pollinations_image(prompt, width, height, seed, model, timeout)
    return _horde_image(prompt, width, height, seed, model, timeout)


def _next_name(media_dir, stem, ext=".png"):
    """Collision-free filename in media_dir for a new generated image."""
    candidate, n = stem + ext, 2
    while (media_dir / candidate).exists():
        candidate = "%s-%d%s" % (stem, n, ext)
        n += 1
    return candidate


def _plan_generations(story, media_dir, force=False):
    """Scene images (local, non-URL) whose file is missing. Each plan has the
    original path string (for rewriting the .gse), a prompt and a target stem."""
    plans = []
    for pname, passage in story["passages"].items():
        for md in passage["media"]:
            orig, caption = md["path"], md["caption"]
            if is_url(orig) or "/" in orig or "\\" in orig:
                continue
            target = media_dir / orig
            if target.exists() and not force:
                continue
            prompt = caption or pname
            plans.append({"passage": pname, "orig": orig,
                          "stem": _slug(prompt), "prompt": prompt})
    return plans


def cmd_image(args):
    """Generate a single image from a prompt into the story's media folder."""
    story_path = args.story or str(DEMO)
    story, p = load_story(story_path)
    md = p.parent / story["config"]["mediaDir"]
    md.mkdir(parents=True, exist_ok=True)
    if args.out:
        out = Path(args.out)
        if not out.is_absolute():
            out = md / out
    else:
        out = md / ("gen_" + _slug(args.prompt) + ".png")
    data = _generate_image(args.prompt, args.width, args.height, args.seed, args.model, args.provider)
    if not data:
        print("x generation failed (network or service error): %s" % out)
        sys.exit(1)
    out.parent.mkdir(parents=True, exist_ok=True)
    ext = _sniff_ext(data)
    if ext and out.suffix.lower() != ext:
        out = out.with_suffix(ext)
    out.write_bytes(data)
    print("v %s" % out)
    print("  add to the story with: image:: %s %s" % (out.name, args.prompt))


def cmd_generate(args):
    """Generate scene images for story image:: lines (from their captions)."""
    story_path = args.story or str(DEMO)
    story, p = load_story(story_path)
    md = p.parent / story["config"]["mediaDir"]
    md.mkdir(parents=True, exist_ok=True)
    text = p.read_text(encoding="utf-8")

    plans = _plan_generations(story, md, force=args.force)
    if not plans:
        print("no scene images need generating (all referenced files exist? use --force)")
        return
    generated, failed = [], []
    for plan in plans:
        data = _generate_image(plan["prompt"], args.width, args.height, args.seed, args.model, args.provider)
        if not data:
            failed.append((plan, "generation failed"))
            continue
        ext = _sniff_ext(data) or ".png"
        name = _next_name(md, plan["stem"], ext)
        (md / name).write_bytes(data)
        text = text.replace(plan["orig"], name)
        generated.append((plan, name))
    p.write_text(text, encoding="utf-8")
    for plan, name in generated:
        print("  v %s -> %s   (%s)" % (plan["orig"], name, plan["prompt"]))
    for plan, err in failed:
        print("  x %s: %s" % (plan["orig"], err))
    res = validate(build_story(parse_story(text)), media_dir=md)
    print("media : %d generated, %d failed" % (len(generated), len(failed)))
    print("story : %s" % p)
    if res["errors"]:
        for e in res["errors"]:
            print("  x " + e)
        sys.exit(1)


def cmd_validate(args):
    path = args.story or str(DEMO)
    story, p = load_story(path)
    res = validate(story, media_dir=media_dir_of(p, story))
    print("story: %s" % p)
    print("title: %s   passages: %d   char stats: %d   globals: %d   characters: %d" % (
        story["config"]["title"], len(story["passages"]), len(story["config"]["charStats"]),
        len(story["config"]["globals"]), len(story["config"]["characters"])))
    if res["warnings"]:
        print("\n%d warning(s):" % len(res["warnings"]))
        for w in res["warnings"]:
            print("  ! " + w)
    if res["errors"]:
        print("\n%d error(s):" % len(res["errors"]))
        for e in res["errors"]:
            print("  x " + e)
        sys.exit(1)
    print("\nOK - story is valid.")


def cmd_dump(args):
    path = args.story or str(DEMO)
    story, p = load_story(path)
    print(json.dumps({"config": story["config"], "passages": story["passages"]}, indent=2))
    res = validate(story, media_dir=media_dir_of(p, story))
    for w in res["warnings"]:
        print("WARN: " + w, file=sys.stderr)
    for e in res["errors"]:
        print("ERROR: " + e, file=sys.stderr)


def cmd_run(args):
    path = args.story or str(DEMO)
    story, p = load_story(path)
    res = validate(story, media_dir=media_dir_of(p, story))
    if not args.json:
        for e in res["errors"]:
            print("  x " + e)
    default_save = p.parent / (p.stem + ".save.json")
    session = create_session(story)
    queued = list(args.script or [])
    if not sys.stdin.isatty():
        queued.extend(ln.strip() for ln in sys.stdin.read().splitlines() if ln.strip())

    if args.json:
        meta = {"event": "meta", "title": story["config"]["title"], "start": story["config"]["start"],
                "chars": [{"id": cid, "name": c["name"], "icons": c["icons"]}
                          for cid, c in story["config"]["characters"].items()],
                "charStats": {sid: {"label": d["label"], "kind": d["kind"], "hidden": d["hidden"]}
                              for sid, d in story["config"]["charStats"].items()},
                "globals": {sid: {"label": d["label"], "kind": d["kind"], "hidden": d["hidden"]}
                            for sid, d in story["config"]["globals"].items()}}
        print(json.dumps(meta, ensure_ascii=True))

    begin(session)
    while True:
        view = session["lastView"]
        if args.json:
            print(json.dumps(view_json(view), ensure_ascii=True))
            sys.stdout.flush()
        else:
            print_view(view)
        if view["ending"]:
            if args.json:
                print(json.dumps({"event": "end", "passage": view["passage"]}))
            else:
                print("\n  The End")
            break
        if view["continuation"]:
            if not args.json and sys.stdin.isatty() and not queued:
                input("  [enter to continue] ")
            go_on(session)
            continue

        ans = None
        if queued:
            ans = queued.pop(0)
        elif sys.stdin.isatty():
            ans = input("\n  > ").strip()
        else:
            if args.json:
                print(json.dumps({"event": "no-more-answers"}))
            else:
                print("\n  [no more piped answers - story unfinished]")
            break

        low = ans.lower()
        if low in ("q", "quit"):
            break
        if low in ("b", "back"):
            r = back(session)
            if r.get("view"):
                continue
        if low in ("r", "restart"):
            begin(session)
            continue
        if low == "state":
            if args.json:
                print(json.dumps({"event": "state", "passage": view["passage"], "state": view["allStats"]}))
            else:
                print(json.dumps(view["allStats"], indent=2))
            continue
        if low == "save" or low.startswith("save "):
            fp = default_save if " " not in low or not low[5:].strip() else Path(low[5:].strip())
            try:
                fp.write_text(json.dumps(snapshot(session), indent=2), encoding="utf-8")
                print("  saved -> %s" % fp)
            except OSError as e:
                print("  ! save failed: %s" % e)
            continue
        if low == "load" or low.startswith("load "):
            fp = default_save if " " not in low or not low[5:].strip() else Path(low[5:].strip())
            if not fp.is_file():
                print("  ! no save at %s" % fp)
                continue
            try:
                r = restore(session, json.loads(fp.read_text(encoding="utf-8")))
                if r.get("error"):
                    print("  ! " + r["error"])
                else:
                    print("  loaded <- %s" % fp)
                    continue  # re-render newly restored pass
            except Exception as e:
                print("  ! load failed: %s" % e)
            continue
        if ans == "":
            continue

        r = choose(session, ans)
        if r.get("error"):
            print("  ! " + r["error"])
            if not sys.stdin.isatty():
                if args.json:
                    print(json.dumps({"event": "invalid-choice", "error": r["error"]}))
                else:
                    print("  valid: %s" % ", ".join("%d=%s" % (i + 1, c["label"]) for i, c in enumerate(view["choices"])))
                break


def template_twee(safe, title):
    return ''':: config
{
  "title": "%(title)s",
  "credit": "auto-generated by gse new %(name)s",
  "start": "start",
  "mediaDir": "media",
  "charStats": {
    "energy": {"label": "Energy", "min": 0, "max": 100, "start": 60}
  },
  "globals": {
    "steps": {"label": "Steps taken", "min": 0, "max": 9, "start": 0}
  },
  "characters": {
    "player": {
      "name": "Hero",
      "icons": [
        {"id": "plain", "img": "hero_plain.svg"},
        {"id": "bright", "img": "hero_happy.svg", "when": {"energy": {"min": 75}}}
      ]
    },
    "friend": {
      "name": "Sam",
      "icons": [
        {"id": "plain", "img": "friend_plain.svg"}
      ]
    }
  }
}

:: start
image:: desk.svg A plain desk, ready for a short conversation.
A quiet street on a fine morning. Sam is already at the corner.
speaker:: friend
There you are! Quick question before we head off.
[[Ask what it is|ask]]

:: ask
speaker:: player
What is on your mind?
speaker:: friend
Walk to the library or take the bus? I need a tiebreaker.
set:: steps + 1
[[Walk|walk]]
[[Take the bus|ride]]

:: walk
set:: energy + 15
speaker:: player
Walking sounds right for the morning.
speaker:: friend
Great - we can stop past the flower stalls too.
[[Keep going|end]]

:: ride
speaker:: player
The bus it is. Faster, and the seats are fine.
speaker:: friend
Works for me.
[[Get off at the square|end]]

:: end
speaker:: player
However we got here, it was a nice way to start the day.
-- The end --
''' % {"title": title, "name": safe}


def safe_name(name):
    return re.sub(r"[^A-Za-z0-9_-]", "-", name).strip("-")


def scaffold_story(safe, title):
    base = ROOT / "stories" / safe
    media = base / "media"
    media.mkdir(parents=True)
    (base / (safe + STORY_EXT)).write_text(template_twee(safe, title), encoding="utf-8")
    for fname, content in TEMPLATE_SVGS.items():
        (media / fname).write_text(content, encoding="utf-8")
    return base


def cmd_new(args):
    safe = safe_name(args.name)
    if not safe:
        sys.exit("error: invalid story name")
    base = ROOT / "stories" / safe
    if base.exists():
        sys.exit("error: %s already exists" % base)
    scaffold_story(safe, safe.replace("-", " ").title())
    print("created %s" % base)
    print("  story:  %s   (edit this file)" % (base / (safe + STORY_EXT)))
    print("  images: %s   (drop your headshots here, then pick them in the editor)" % (base / "media"))
    story_file = base / (safe + STORY_EXT)
    cmd = "gse" if getattr(sys, "frozen", False) else "python cli/gse.py"
    print("\nnext steps:")
    print("  %s edit %s       # visual story editor" % (cmd, story_file))
    print("  %s validate %s" % (cmd, story_file))
    print("  %s build %s      # compile single-file game" % (cmd, story_file))


# ----------------------------------------------------------------- build
def cmd_build(args):
    path = args.story or str(DEMO)
    story, p = load_story(path)
    res = validate(story, media_dir=media_dir_of(p, story))
    if res["errors"]:
        print("%d error(s):" % len(res["errors"]))
        for e in res["errors"]:
            print("  x " + e)
        sys.exit(1)

    media_path = p.parent / story["config"]["mediaDir"]
    if not media_path.is_dir():
        sys.exit("error: media dir not found: %s" % media_path)

    name = p.parent.name if p.parent.parent == ROOT / "stories" else p.stem
    out_dir = BUILD_DIR / name
    out_dir.mkdir(parents=True, exist_ok=True)

    media_map = {}
    remote_ok = 0
    for ref in collect_refs(story):
        if is_url(ref):
            got = _fetch_image(ref)
            if not got:
                print("  ! remote image not embedded (it will load over the network): %s" % ref)
                continue
            data, mime = got
            media_map[ref] = "data:%s;base64,%s" % (mime, base64.b64encode(data).decode("ascii"))
            remote_ok += 1
            continue
        fp = media_path / ref
        if not fp.is_file():
            print("  x missing media: %s" % ref)
            sys.exit(1)
        mime = mimetypes.guess_type(fp.name)[0] or "image/png"
        data = base64.b64encode(fp.read_bytes()).decode("ascii")
        media_map[ref.replace(os.sep, "/")] = "data:%s;base64,%s" % (mime, data)

    index = (ENGINE_DIR / "index.html").read_text(encoding="utf-8")
    core = (ENGINE_DIR / "core.js").read_text(encoding="utf-8").replace("</script", "<\\/script")
    ui = (ENGINE_DIR / "ui.js").read_text(encoding="utf-8").replace("</script", "<\\/script")
    css = (ENGINE_DIR / "style.css").read_text(encoding="utf-8")

    story_text = p.read_text(encoding="utf-8")
    embedded_story = json.dumps(story_text).replace("</", "<\\/")
    index = index.replace('null; /* __STORY_TEXT__ */', embedded_story)
    index = index.replace('"media/"; /* __STORY_MEDIA__ */', json.dumps(""))
    index = index.replace('null; /* __STORY_MEDIA_MAP__ */', json.dumps(media_map))
    index = index.replace('<link rel="stylesheet" href="style.css"/>',
                          "<style>\n" + css.replace("</style", "<\\/style") + "\n</style>")
    index = index.replace('<script src="core.js"></script>', "<script>\n" + core + "\n</script>")
    index = index.replace('<script src="ui.js"></script>', "<script>\n" + ui + "\n</script>")

    out = out_dir / "index.html"
    out.write_text(index, encoding="utf-8")
    print("built %s" % out)
    print("  title : %s" % story["config"]["title"])
    print("  media : %d image(s) embedded (base64)%s" % (
        len(media_map), " (%d from URLs)" % remote_ok if remote_ok else ""))
    print("  size  : %d bytes" % out.stat().st_size)
    print("\nopen %s in any browser, or share the single file." % out)


# ----------------------------------------------------------------- download
def cmd_download(args):
    """Fetch remote image URLs referenced by a story into its media folder,
    then rewrite the .gse so those references point at the local files."""
    path = args.story or str(DEMO)
    story, p = load_story(path)
    md = p.parent / story["config"]["mediaDir"]
    md.mkdir(parents=True, exist_ok=True)
    text = p.read_text(encoding="utf-8")

    downloaded = {}
    errors = []
    refs = collect_refs(story)
    urls = [r for r in refs if is_url(r)]
    if not urls:
        print("no remote image URLs in %s (nothing to download)" % p)
        return
    for url in urls:
        local = _download_image(url, md)
        if local:
            downloaded[url] = local
        else:
            errors.append(url)
    if downloaded:
        for url, local in downloaded.items():
            text = text.replace(url, local)
        p.write_text(text, encoding="utf-8")
    for url, local in sorted(downloaded.items()):
        print("  v %s -> %s" % (url, local))
    for url in errors:
        print("  x failed to download: %s" % url)
    res = validate(build_story(parse_story(text)), media_dir=md)
    print("media : %d downloaded, %d failed, %d total referenced" % (
        len(downloaded), len(errors), len(refs)))
    print("story : %s" % p)
    if res["errors"]:
        print("  x %d error(s):" % len(res["errors"]))
        for e in res["errors"]:
            print("    " + e)
        sys.exit(1)


# ----------------------------------------------------------------- serve
def cmd_serve(args):
    path = args.story or str(DEMO)
    import http.server

    story, p = load_story(path)
    story_text = p.read_text(encoding="utf-8")
    media_dir = p.parent / story["config"]["mediaDir"]
    port = args.port or 8910

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, code, body, ctype):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            path_ = self.path.split("?")[0]
            if path_ == "/":
                html = (ENGINE_DIR / "index.html").read_text(encoding="utf-8")
                html = html.replace('null; /* __STORY_TEXT__ */',
                                     json.dumps(story_text).replace("</", "<\\/"))
                html = html.replace('"media/"; /* __STORY_MEDIA__ */', json.dumps("/media/"))
                self._send(200, html.encode("utf-8"), "text/html; charset=utf-8")
                return
            if path_ in ("/core.js", "/ui.js", "/style.css"):
                fp = ENGINE_DIR / path_[1:]
                ctype = "text/javascript" if path_.endswith(".js") else "text/css"
                self._send(200, fp.read_bytes(), ctype)
                return
            if path_.startswith("/media/"):
                rel = path_[len("/media/"):].replace("/", os.sep)
                fp = media_dir / rel
                if fp.is_file():
                    mime = mimetypes.guess_type(fp.name)[0] or "application/octet-stream"
                    self._send(200, fp.read_bytes(), mime)
                    return
                self._send(404, b"not found", "text/plain")
                return
            self._send(404, b"not found", "text/plain")

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print("")
    print("  Green Story Engine - \"%s\"" % story["config"]["title"])
    print("  URL: http://127.0.0.1:%d" % port)
    print("  stop: Ctrl+C")
    print("")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        srv.server_close()


# ----------------------------------------------------------------- edit (visual story editor)
def _list_media(media_dir):
    if not media_dir or not media_dir.is_dir():
        return []
    out = []
    for p in media_dir.rglob("*"):
        if p.is_file():
            out.append(p.relative_to(media_dir).as_posix())
    return sorted(out)


def _edit_resolve(path):
    """Turn a user argument into a .gse path, auto-scaffolding a new story if needed."""
    p = Path(path)
    if p.is_dir():
        p = p / (p.name + STORY_EXT)
    if p.suffix not in (STORY_EXT, ".twee"):
        p = p.with_suffix(STORY_EXT)
    if p.is_file():
        return p.resolve()
    safe = safe_name(p.stem) or "story"
    if not (ROOT / "stories" / safe).exists():
        print("no story found at %s - creating a new one (gse new %s)" % (p, safe))
        scaffold_story(safe, safe.replace("-", " ").title())
    p = ROOT / "stories" / safe / (safe + STORY_EXT)
    return p.resolve()


def _stories_index():
    """Name -> story file path for existing stories under ROOT/stories/<name>/."""
    out = {}
    base = ROOT / "stories"
    if base.is_dir():
        for d in sorted(base.iterdir()):
            if d.is_dir():
                f = d / (d.name + STORY_EXT)
                if f.is_file():
                    out[d.name] = f
    return out


def cmd_edit(args):
    cur = {"path": _edit_resolve(args.story or str(DEMO))}
    import http.server

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, code, body, ctype):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _media_dir(self):
            try:
                parsed = parse_story(cur["path"].read_text(encoding="utf-8"))
                md = cur["path"].parent / (parsed["config"].get("mediaDir") or "media")
            except Exception:
                md = cur["path"].parent / "media"
            return md

        def _story_json(self):
            text = cur["path"].read_text(encoding="utf-8")
            md = self._media_dir()
            return json.dumps({
                "name": cur["path"].parent.name,
                "pathLabel": str(cur["path"]),
                "mediaDir": md.name,
                "mediaBase": "/media/",
                "mediaFiles": _list_media(md),
                "text": text,
            })

        def do_GET(self):
            p = self.path.split("?")[0]
            if p == "/":
                self._send(200, (EDITOR_DIR / "editor.html").read_bytes(),
                           "text/html; charset=utf-8")
                return
            if p in ("/editor.js", "/editor.css", "/core.js", "/ui.js", "/style.css"):
                base = EDITOR_DIR if p in ("/editor.js", "/editor.css") else ENGINE_DIR
                fp = base / p[1:]
                ctype = "text/javascript" if p.endswith(".js") else "text/css"
                self._send(200, fp.read_bytes(), ctype)
                return
            if p == "/api/story":
                self._send(200, self._story_json().encode("utf-8"),
                           "application/json; charset=utf-8")
                return
            if p == "/api/stories":
                idx = _stories_index()
                current = cur["path"].parent.name if cur["path"].parent.parent == ROOT / "stories" else None
                self._send(200, json.dumps({"stories": sorted(idx.keys()),
                                            "current": current}).encode("utf-8"),
                           "application/json; charset=utf-8")
                return
            if p == "/api/open":
                q = parse_qs(urlparse(self.path).query)
                name = (q.get("name") or [None])[0]
                idx = _stories_index()
                if not name or name not in idx:
                    self._send(400, json.dumps({"ok": False,
                                                "errors": ["unknown story '%s'" % name]}).encode("utf-8"),
                               "application/json; charset=utf-8")
                    return
                cur["path"] = idx[name]
                self._send(200, json.dumps({"ok": True,
                                            "pathLabel": str(cur["path"])}).encode("utf-8"),
                           "application/json; charset=utf-8")
                return
            if p == "/play":
                html = (ENGINE_DIR / "index.html").read_text(encoding="utf-8")
                text = cur["path"].read_text(encoding="utf-8")
                html = html.replace('null; /* __STORY_TEXT__ */',
                                     json.dumps(text).replace("</", "<\\/"))
                html = html.replace('"media/"; /* __STORY_MEDIA__ */', json.dumps("/media/"))
                self._send(200, html.encode("utf-8"), "text/html; charset=utf-8")
                return
            if p.startswith("/media/"):
                rel = p[len("/media/"):].replace("/", os.sep)
                fp = self._media_dir() / rel
                if fp.is_file():
                    mime = mimetypes.guess_type(fp.name)[0] or "application/octet-stream"
                    self._send(200, fp.read_bytes(), mime)
                    return
                self._send(404, b"not found", "text/plain")
                return
            self._send(404, b"not found", "text/plain")

        def do_POST(self):
            p = self.path.split("?")[0]
            if p == "/api/new":
                try:
                    length = int(self.headers.get("Content-Length", 0))
                    data = json.loads(self.rfile.read(length).decode("utf-8"))
                except Exception as e:
                    self._send(400, json.dumps({"ok": False,
                                                "errors": ["bad request: %s" % e]}).encode("utf-8"),
                               "application/json; charset=utf-8")
                    return
                safe = safe_name(str(data.get("name", "")))
                if not safe:
                    self._send(400, json.dumps({"ok": False,
                                                "errors": ["invalid story name"]}).encode("utf-8"),
                               "application/json; charset=utf-8")
                    return
                base = ROOT / "stories" / safe
                if base.exists():
                    self._send(400, json.dumps({"ok": False,
                                                "errors": ["story '%s' already exists" % safe]}).encode("utf-8"),
                               "application/json; charset=utf-8")
                    return
                scaffold_story(safe, safe.replace("-", " ").title())
                cur["path"] = base / (safe + STORY_EXT)
                self._send(200, json.dumps({"ok": True, "name": safe,
                                            "pathLabel": str(cur["path"])}).encode("utf-8"),
                           "application/json; charset=utf-8")
                return
            if p != "/api/story":
                self._send(404, b"not found", "text/plain")
                return
            try:
                length = int(self.headers.get("Content-Length", 0))
                if length > 2_000_000:
                    raise ValueError("story too large")
                data = json.loads(self.rfile.read(length).decode("utf-8"))
                text = str(data.get("text", ""))
            except Exception as e:
                self._send(400, json.dumps({"ok": False, "errors": ["bad request: %s" % e]}).encode("utf-8"),
                           "application/json; charset=utf-8")
                return
            parsed = parse_story(text)
            if parsed["errors"]:
                self._send(400, json.dumps({"ok": False, "errors": parsed["errors"]}).encode("utf-8"),
                           "application/json; charset=utf-8")
                return
            story = build_story(parsed)
            md = self._media_dir()
            try:
                md.mkdir(parents=True, exist_ok=True)
            except Exception:
                pass
            downloaded = {}
            for ref in collect_refs(story):
                if is_url(ref):
                    local = _download_image(ref, md)
                    if local:
                        downloaded[ref] = local
            if downloaded:
                for url, local in downloaded.items():
                    text = text.replace(url, local)
            cur["path"].write_bytes(text.encode("utf-8"))
            res = validate(story, media_dir=md)
            self._send(200, json.dumps({"ok": True, "errors": res["errors"],
                                        "warnings": res["warnings"],
                                        "downloaded": downloaded,
                                        "bytes": len(text.encode("utf-8"))}).encode("utf-8"),
                       "application/json; charset=utf-8")

    port = args.port or 8911
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print("")
    print("  Green Story Engine - Story Editor")
    print("  story: %s" % cur["path"])
    print("  URL:   http://127.0.0.1:%d   (light/dark toggle, Save writes the .gse)" % port)
    print("  play:  http://127.0.0.1:%d/play" % port)
    print("  stop:  Ctrl+C")
    print("")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        srv.server_close()


# ----------------------------------------------------------------- selftest
def cmd_selftest(args):
    import subprocess
    import tempfile
    import socket
    import urllib.error

    # argv prefix for re-invoking this CLI. A frozen executable already points
    # sys.executable at itself, so argv[0] must not be repeated.
    me = [] if getattr(sys, "frozen", False) else sys.argv[:1]  # script path or zipapp
    state = {"ok": True}

    def check(label, cond, extra=""):
        print("  %s %s%s" % ("ok" if cond else "xx", label,
                             ("  -- " + extra) if extra else ""))
        if not cond:
            state["ok"] = False

    def http(path, body=None, port=None):
        url = "http://127.0.0.1:%d%s" % (port, path)
        data = body.encode("utf-8") if body is not None else None
        req = urllib.request.Request(url, data=data,
                                     method="POST" if data is not None else "GET")
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status, r.read().decode("utf-8")
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode("utf-8")

    print("one story engine checks")
    story, p = load_story(str(DEMO))
    res = validate(story, media_dir=media_dir_of(p, story))
    check("demo parses + validates", not res["errors"], "; ".join(res["errors"]))
    check("demo has characters + passages",
          len(story["config"]["characters"]) >= 1 and len(story["passages"]) >= 2)

    print("\neditor REST API (isolated app root)")
    with tempfile.TemporaryDirectory() as td:
        env = dict(os.environ)
        env["GSE_APP_ROOT"] = td
        import shutil
        shutil.copytree(ENGINE_DIR, Path(td) / "engine")
        shutil.copytree(EDITOR_DIR, Path(td) / "editor")
        base = Path(td) / "stories" / "cfg_demo"
        (base / "media").mkdir(parents=True)
        story_file = base / ("cfg_demo" + STORY_EXT)
        story_file.write_text(template_twee("cfg_demo", "Config Demo"), encoding="utf-8")
        for fname, content in TEMPLATE_SVGS.items():
            (base / "media" / fname).write_text(content, encoding="utf-8")

        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
        proc = subprocess.Popen(
            [sys.executable] + me + ["edit", str(story_file), "--port", str(port)],
            env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            code, text = http("/api/story", port=port)
            check("GET /api/story works", code == 200 and "cfg_demo" in text)

            code, text = http("/api/stories", port=port)
            check("GET /api/stories lists cfg_demo", code == 200 and "cfg_demo" in text)

            code, text = http("/api/new", body=json.dumps({"name": "Magic Town"}), port=port)
            check("POST /api/new creates story", code == 200 and "Magic-Town" in text)

            code, text = http("/api/open?name=Magic-Town", port=port)
            check("GET /api/open switches story", code == 200)

            code, text = http("/api/story", port=port)
            check("current story is the new one", code == 200 and 'Magic-Town' in text)

            code, text = http("/api/story", port=port)
            import re as _re
            new_text = _re.sub(r'"title": "Magic Town"', '"title": "Rebound"', text)
            code, text = http("/api/story", body=new_text, port=port)
            check("POST /api/story saves story", code == 200 and '"ok": true' in text)

            code, text = http("/play", port=port)
            check("GET /play renders the game", code == 200 and "magic-town" in text.lower())

            code, text = http("/api/open?name=nope_zzz", port=port)
            check("GET /api/open rejects unknown story", code == 400)
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()

    print("\nCLI download (remote URL -> local media)")
    import base64 as _b64
    import http.server as _hs
    import threading as _th
    tiny_png = _b64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk"
        "+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==")

    class _Srv(_hs.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "image/png")
            self.send_header("Content-Length", str(len(tiny_png)))
            self.end_headers()
            self.wfile.write(tiny_png)

        def log_message(self, *a):
            pass

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port_now = s.getsockname()[1]
    srv = _hs.ThreadingHTTPServer(("127.0.0.1", port_now), _Srv)
    srv_t = _th.Thread(target=srv.serve_forever, daemon=True)
    srv_t.start()
    try:
        with tempfile.TemporaryDirectory() as td:
            dl = Path(td) / "dl_story" / "dl_story.gse"
            dl.parent.mkdir(parents=True)
            dl.write_text(''':: config
{
  "title": "Download Selftest",
  "mediaDir": "media",
  "characters": {
    "hero": {
      "name": "Hero",
      "status": "A hero.",
      "icons": [ { "id": "plain", "img": "http://127.0.0.1:%d/pixel.png" } ]
    }
  }
}

:: start
Hello. [open:hero]Meet hero[/open]
''' % port_now, encoding="utf-8")
            story2, _ = load_story(str(dl))
            refs = [r for r in collect_refs(story2) if is_url(r)]
            check("download sees a remote ref", len(refs) == 1 and refs[0].endswith("pixel.png"))
            cmd_download(type("A", (), {"story": str(dl)})())
            final = dl.read_text(encoding="utf-8")
            md = dl.parent / "media"
            media_files = list(md.iterdir()) if md.exists() else []
            check("download rewrites story to a local name",
                  "http://" not in final and len(media_files) == 1)
            check("downloaded bytes match source",
                  len(media_files) == 1 and media_files[0].read_bytes() == tiny_png)
    finally:
        srv.shutdown()
        srv_t.join(timeout=5)

    print("\nselftest %s" % ("PASS" if state["ok"] else "FAIL"))
    sys.exit(0 if state["ok"] else 1)


# ----------------------------------------------------------------- sample headshots (placeholder art)
_S = '<svg xmlns="http://www.w3.org/2000/svg" width="200" height="200" viewBox="0 0 200 200">'
_E = "</svg>"


def _svg(body):
    return _S + body + _E


TEMPLATE_SVGS = {
    "hero_plain.svg": _svg(
        '<rect width="200" height="200" rx="22" fill="#d9ecf2"/>'
        '<circle cx="100" cy="88" r="40" fill="#6b4a2f"/>'
        '<circle cx="100" cy="116" r="56" fill="#e8b88a"/>'
        '<circle cx="77" cy="112" r="5.5" fill="#2d1a0e"/>'
        '<circle cx="123" cy="112" r="5.5" fill="#2d1a0e"/>'
        '<path d="M80 140 q20 10 40 0" stroke="#a4633a" stroke-width="3.5" fill="none" stroke-linecap="round"/>'),
    "hero_happy.svg": _svg(
        '<rect width="200" height="200" rx="22" fill="#ffe7c2"/>'
        '<circle cx="100" cy="88" r="40" fill="#5b3f26"/>'
        '<circle cx="100" cy="116" r="56" fill="#f2c39b"/>'
        '<path d="M71 112 q6 -10 12 0" stroke="#2d1a0e" stroke-width="4" fill="none" stroke-linecap="round"/>'
        '<path d="M117 112 q6 -10 12 0" stroke="#2d1a0e" stroke-width="4" fill="none" stroke-linecap="round"/>'
        '<path d="M76 138 q24 22 48 0 Z" fill="#c26b4a"/>'
        '<circle cx="63" cy="128" r="7" fill="#f27f7f" opacity="0.45"/>'
        '<circle cx="137" cy="128" r="7" fill="#f27f7f" opacity="0.45"/>'),
    "friend_plain.svg": _svg(
        '<rect width="200" height="200" rx="22" fill="#d6dfe8"/>'
        '<circle cx="100" cy="88" r="40" fill="#3c332b"/>'
        '<circle cx="100" cy="116" r="56" fill="#cbd5cf"/>'
        '<path d="M70 112 L90 112 M110 112 L130 112" stroke="#26333b" stroke-width="3.5" stroke-linecap="round"/>'
        '<path d="M84 142 q8 -5 16 -4 q8 -1 16 4" stroke="#5b4a3a" stroke-width="3.5" fill="none" stroke-linecap="round"/>'),
    "desk.svg": (
        '<svg xmlns="http://www.w3.org/2000/svg" width="640" height="320" viewBox="0 0 640 320">'
        '<rect width="640" height="320" fill="#e7ecf2"/>'
        '<rect x="0" y="212" width="640" height="108" fill="#a97d4f"/>'
        '<rect x="30" y="150" width="300" height="70" rx="6" fill="#6b5a45"/>'
        '<rect x="150" y="96" width="120" height="60" rx="4" fill="#8a6f52"/>'
        '<rect x="410" y="120" width="150" height="26" rx="8" fill="#3d434b"/>'
        '<rect x="250" y="196" width="70" height="16" fill="#555a63"/>'
        '<circle cx="180" cy="140" r="14" fill="#26201a"/>'
        '<rect x="166" y="152" width="28" height="30" fill="#4a4f5a"/>'
        '<rect x="176" y="118" width="8" height="22" fill="#26201a"/>'
        '</svg>'),
}


def main():
    ap = argparse.ArgumentParser(prog="gse", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="command")
    p_new = sub.add_parser("new", help="scaffold a new story")
    p_new.add_argument("name")
    p_val = sub.add_parser("validate", help="check a story")
    p_val.add_argument("story", nargs="?")
    p_run = sub.add_parser("run", help="play a story")
    p_run.add_argument("story", nargs="?")
    p_run.add_argument("--json", action="store_true", help="emit JSON objects per step")
    p_run.add_argument("--script", nargs="*", default=None, help="answers to auto-play, e.g. --script 1 2 1")
    p_dump = sub.add_parser("dump", help="print parsed story")
    p_dump.add_argument("story", nargs="?")
    p_srv = sub.add_parser("serve", help="launch dev web UI")
    p_srv.add_argument("story", nargs="?")
    p_srv.add_argument("--port", type=int, default=8910)
    p_st = sub.add_parser("selftest", help="run offline checks + editor REST round-trip")
    p_ed = sub.add_parser("edit", help="launch the visual story editor (light/dark)")
    p_ed.add_argument("story", nargs="?")
    p_ed.add_argument("--port", type=int, default=8911)
    sub.add_parser("studio", help="launch the Tkinter studio launcher (Open / New / Play / Validate)")
    p_bld = sub.add_parser("build", help="compile single-file game")
    p_bld.add_argument("story", nargs="?")
    p_bld.add_argument("--out", default=None, help="output directory (default build/<name>)")
    p_dl = sub.add_parser("download", help="fetch remote image URLs into media/ and rewrite the story")
    p_dl.add_argument("story", nargs="?")
    p_img = sub.add_parser("image", help="generate one image from a prompt (Pollinations.ai) into the story media folder")
    p_img.add_argument("prompt")
    p_img.add_argument("--story", default=None, help="story whose mediaDir to use (default demo)")
    p_img.add_argument("--out", default=None, help="output filename (default media/gen_<slug>.png)")
    p_img.add_argument("--width", type=int, default=1024)
    p_img.add_argument("--height", type=int, default=1024)
    p_img.add_argument("--seed", type=int, default=None, help="fixed random seed for reproducibility")
    p_img.add_argument("--model", default=None, help="cf: model id e.g. @cf/black-forest-labs/flux-1-schnell; horde: SDXL checkpoint; pollinations: flux/turbo")
    p_img.add_argument("--provider", choices=["cf", "horde", "pollinations"], default="cf",
                       help="image backend (default cf: Cloudflare Workers AI, free no queue)")
    p_gen = sub.add_parser("generate", help="generate scene images for a story from their image:: captions")
    p_gen.add_argument("story", nargs="?")
    p_gen.add_argument("--force", action="store_true", help="regenerate even when the file exists")
    p_gen.add_argument("--width", type=int, default=1024)
    p_gen.add_argument("--height", type=int, default=1024)
    p_gen.add_argument("--seed", type=int, default=None, help="fixed random seed for reproducibility")
    p_gen.add_argument("--model", default=None, help="cf: model id e.g. @cf/black-forest-labs/flux-1-schnell; horde: SDXL checkpoint; pollinations: flux/turbo")
    p_gen.add_argument("--provider", choices=["cf", "horde", "pollinations"], default="cf",
                       help="image backend (default cf: Cloudflare Workers AI, free no queue)")

    args = ap.parse_args()
    if not args.command:
        ap.print_help()
        sys.exit(0)
    if args.command == "new":
        cmd_new(args)
    elif args.command == "validate":
        cmd_validate(args)
    elif args.command == "run":
        cmd_run(args)
    elif args.command == "dump":
        cmd_dump(args)
    elif args.command == "build":
        cmd_build(args)
    elif args.command == "download":
        cmd_download(args)
    elif args.command == "image":
        cmd_image(args)
    elif args.command == "generate":
        cmd_generate(args)
    elif args.command == "serve":
        cmd_serve(args)
    elif args.command == "edit":
        cmd_edit(args)
    elif args.command == "selftest":
        cmd_selftest(args)
    elif args.command == "studio":
        cmd_studio(args)


def cmd_studio(args):
    """Open the Tkinter studio launcher (used by the frozen executable)."""
    launcher = ROOT / "gui_launcher.py"
    if not launcher.is_file():
        print("studio launcher not found: %s" % launcher, file=sys.stderr)
        sys.exit(1)
    sys.path.insert(0, str(launcher.parent))
    os.environ.setdefault("GSE_APP_ROOT", str(ROOT))
    import gui_launcher
    gui_launcher.Studio().mainloop()


if __name__ == "__main__":
    main()