# Green Story Engine — Build Plan

A tiny engine for light story games (CYOA). One plain-text story file + images in a
folder compiles into a **single, self-contained HTML game** (browser UI with a
sidebar) and can also be played/piped through a **CLI for AI agents**.

## Goal (per user requirements)
- Simple CYOA: `[[Choice|target]]`, guarded choices, linear `goto`.
- **Character stats** (the player hero: energy, courage, ...) — sidebar bars/pills.
- **Global stats** (story state: steps, keys, reputation, ...) — sidebar section.
- **Images**: scene images inside passages + **stat-driven headshots** per
  character (icons picked from live stats).
- **Saves**: browser (localStorage) + CLI save/load file.
- **Compile to HTML**: `python cli/gse.py build <story>` emits one game.html with
  the engine + story + images (base64) all inlined. Open it anywhere.

## Runtime decision
- `node` is NOT installed; `python 3.10` IS.
- Browser engine: `engine/core.js` + `engine/ui.js` (no deps).
- CLI + compiler: `cli/gse.py` — Python stdlib only.

## File layout
```
green_story_engine/
  PLAN.md  README.md  package.json (docs; python scripts)
  engine/
    index.html    shell w/ embed markers (story + media map injected by build/serve)
    core.js       parse + state + views (UMD)
    ui.js         sidebar UI, rendering, input, save/load
    style.css     dark theme w/ sidebar layout
  cli/gse.py      new | validate | run | dump | build | serve
  stories/<name>/
    <name>.gse   the story (gse format)
    media/*       images (icons + scenes)
  build/<name>/index.html    <- compiled single-file game
```

## gse story format (one .gse file)
- Passages: `:: name [tags]` then content lines. First `:: config` = JSON metadata.
- Narration: plain lines. `---` = scene break. `#` / `//` = comments.
- Choices: `[[Label|target]]` (+ optional inline `if:: stat >= 60`, or the guard on
  a line just above). No-pipe form `[[target]]`.
- Effects: `set:: stat +10` / `-5` / `= value` — run on passage entry.
  Character stats by name; global stats by name too (routes by declaration) —
  force with `@name` (e.g. `set:: @keys +1`).
- Speakers: `speaker:: player` (or `speaker:: none` = narrator). Speakers resolve
  an icon per character from live stats. `image:: file.svg caption` inline scene image.
- Linear: `goto:: target` shows a Continue button when a passage has no choices.
- Config schema:
```json
{ "title": "...", "start": "start", "mediaDir": "media",
  "charStats": { "energy": {"label":"Energy","min":0,"max":100,"start":60} },
  "globals":   { "steps": {"label":"Steps taken","min":0,"max":9,"start":0},
                 "keys":   {"label":"Keys","min":0,"max":9,"start":0} },
  "characters": { "player": { "name":"Hero", "icons":[ {"id":"plain","img":"hero_plain.svg","when":{"energy":{"max":74}}}, ... ] } } }
```
- Stats: numeric (`min/max/start`) or enum (`values/start`); `"hidden": true` hides.
- Icon `when` matchers: string (eq) | `["a","b"]` (any-of) | number | `{min,max,gt,lt}`.
  First match wins; icon with no `when` = fallback. Conditions read merged
  (charStats + globals), so headshots can react to global stats too.

## Engine state (mirrored in JS + Python)
- Session: `char` (char stats), `global` (globals), history entries
  `{id, snapshot:{char,global}}`, visitCounts, current passage, lastView.
- View: passage, title, lines (text/media + resolved icon per speaker), choices,
  continuation, ending, effects log, visible stats (char then global), allStats.
- Choose by 1-based number or label (case-insensitive, prefix match).
- Save/load: `snapshot(session)` -> `{passage, char, global, visits, history}`
  and `restore(session, snap)`. Browser uses localStorage; CLI uses a JSON file.

## CLI (cli/gse.py)
- `new <name>`       scaffold stories/<name>/ + sample headshots
- `validate <f>`     checks (exit 1 on errors; CI-friendly)
- `run <f>`          interactive / piped / `--script "1 2"` / `--json` (agent mode)
- `dump <f>`         parsed story JSON (stdout) + checks (stderr)
- `build <f>`        compile single-file game to build/<name>/index.html
- `serve <f>`        local dev server embedding the story in the engine UI
- run answers: number/label, `back`/`b`, `state`, `save [file]`, `load [file]`,
  `restart`/`r`, `quit`/`q`

## Browser UI
- Sidebar: character portrait (live icon), **Character** stat bars, **Story**
  global stat bars, Save / Load / Reset controls.
- Main: breadcrumbs, scene image, narration with speaker chips, effects notes,
  choice buttons (number keys 1-9, B back, R restart, S save, L load).
- Keys work unless typing in a text field.

## Build order (check off as done)
- [x] Env check: python available, node absent -> gse.py in Python
- [x] PLAN.md (this file)
- [x] engine/core.js  (stats model + saves)
- [x] engine/ui.js + index.html + style.css (sidebar + saves + media map)
- [x] cli/gse.py (mirror + build + save/load)
- [x] stories/demo update (charStats/globals) + SVG assets
- [x] README.md + package.json
- [x] Verify: validate -> run (script/json/piped) -> save/load -> build -> serve
- [x] Final status below

## Visual editor (light/dark, no-markup)
- [x] editor/editor.html + editor.css (light + dark themes, toggle persisted)
- [x] editor/editor.js: model from parse, serialize back to gse text
- [x] Beat editor: name/tags, plain text, voice picker, scene image, effects
      ("on entering this beat, change"), choices + target dropdown + condition
- [x] Settings: general (title/start/mediaDir), variables (range/list,
      char vs global), characters + icons with "only when" rules
- Images: scene image (file or pasted URL, downloaded on Save) + icon pickers
      accept both media files and https URLs
- [x] cli/gse.py edit: serves editor + /api/story (GET/POST) + /media + /play,
      auto-scaffolds missing stories
- [x] cli/gse.py edit: Open / New storage (GET /api/stories, POST /api/new,
      GET /api/open) + editor "Open / New" modal
- [x] Per-line dialogue: speaker picker per line (+ narrator), serialized as
      `speaker:: <id>` / `speaker:: none`; engine renders aligned chat bubbles
      (main char right, others left, narrator centered)
- [x] Demo: "The Staple Pitch" (safe staple-pitch theme), verified + built
- [x] Template + media scrubbed of animal/transformation references
- [x] Verified: JScript round-trip (parse->model->serialize->parse == identical
      semantics; icons/vars/chars preserved) + HTTP smoke + save-mutate-restore
      + auto-scaffold cleanup; editor.js kept ES3-clean (runs in cscript)
- [x] Packaging: tools/package.py -> dist/ zipapp + Tkinter launcher
      (Start Studio.cmd / gse.cmd), selftest command, offline unit tests
      (tests/test_core.py, `python -m unittest discover -s tests`)

## Resume point (if credits run out)
- Engine parity kept by same spec; after editing either core.js or gse.py, run:
  `python cli/gse.py validate stories/demo/demo.gse` then the run/build checks above,
  and `python cli/gse.py selftest`.

## Final status
- Build: DONE (build/demo/index.html is a single self-contained game, 48.3 KB, 5 images
  embedded as base64, no leftover markers, `</` sequences escaped via `<\\/`).
- Verify: DONE via `python cli/gse.py validate/run/build/serve` + `selftest`, a JScript
  harness that runs engine/core.js headlessly (parse, build story, validate, full
  playthrough, snapshot/restore round-trip), and `python -m unittest discover -s tests`
  (11 tests, all pass).
- Editor: DONE (`gse.py edit` — light/dark visual authoring, per-line dialogue,
  Open / New storage; round-trip proven identical to the source story).