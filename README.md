# Green Story Engine

A tiny engine for light story games (CYOA) with **character stats**, **global
stats**, **images**, a **sidebar UI**, and **saves**. A plain-text story file plus
an image folder compiles into a **single self-contained HTML game** that opens in
any browser and can be shared as one file.

Two front-ends for the same story format:
- **Browser UI** (sidebar, images, save/load) — the compiled game.
- **CLI** for AI agents: validate, play (interactive or piped), JSON step output.

No dependencies. Browser engine is plain JS; CLI + compiler are pure Python 3 stdlib.

## Install

**No Python required** — grab a build from the [Releases](../../releases) page:

| asset | use |
|---|---|
| `gse.exe` | Windows standalone executable (`gse.exe studio`, `gse.exe edit <story>`) |
| `gse` | Linux / macOS standalone executable |
| `green-story-engine-studio.zip` | portable studio folder (`Start Studio.cmd`, `gse.cmd`, engine + demo stories) |

Unzip or drop the executable in any folder you control. `engine/`, `editor/` and
`stories/` are unpacked next to it on first run, so `gse new` can write there.

**From source** — Python 3.8+ (with `tkinter` for the studio launcher), no packages:

```
git clone <this repo> && cd green-story-engine
python cli/gse.py selftest
```

## Quick start

```bash
python cli/gse.py validate stories/demo/demo.gse
python cli/gse.py run stories/demo/demo.gse            # play it
python cli/gse.py run stories/demo/demo.gse --json --script 1 1 1 1 1 1 1 1
python cli/gse.py build stories/demo/demo.gse          # -> build/demo/index.html
python cli/gse.py download stories/demo/demo.gse       # fetch remote image URLs into media/
python cli/gse.py serve stories/demo/demo.gse          # dev UI at :8910
python cli/gse.py edit stories/demo/demo.gse           # visual editor at :8911
python cli/gse.py studio                               # Tkinter launcher (Open/New/Play/Validate)
python cli/gse.py new my_story                          # scaffold a fresh story
python cli/gse.py selftest                              # offline self-tests
```

## CLI (cli/gse.py)

| command | description |
|---|---|
| `new <name>` | scaffold `stories/<name>/` with template story + sample headshots |
| `validate <f>` | parse + link/stat/icon/asset checks; exit code 1 on errors |
| `run <f>` | play. Interactive, piped answers, or `--script 1 2 1` |
| `run <f> --json` | one JSON object per step (the reasoner/agent mode) |
| `dump <f>` | parsed story as JSON (stdout) + checks (stderr) |
| `build <f>` | **compile a single-file game** to `build/<name>/index.html` (images base64-embedded) |
| `download <f>` | fetch every remote image URL in the story into `media/` and rewrite the `.gse` to use the local files |
| `image "<prompt>" --story <f> --out <name>` | generate one image from a prompt into the story's `media/` (see Images) |
| `generate <f>` | auto-generate a scene image for each `image::` line (from its caption) and rewrite the `.gse` |
| `serve <f>` | local dev server embedding the story in the engine UI |
| `edit <f>` | **visual story editor**: light/dark, form-based beats, per-line dialogue, variables, links — no markup |
| `studio` | Tkinter launcher: Open / New / Play / Validate |
| `selftest` | offline checks: demo parse/validate + editor REST round-trip |

`run` answers: a choice number or label, plus `back`/`b`, `state`, `save [file]`,
`load [file]`, `restart`/`r`, `quit`/`q`. Saves go to `<story>.save.json`.

`--json` emits `{event:"meta"}` then `{event:"passage"}` steps:
`passage, title, lines, characters (resolved icon per speaker), choices, effects,
charStats, globals, allStats, icon`.

## Story format (one `.gse` file)

```
:: config
{ "title": "...", "start": "start", "mediaDir": "media",
  "charStats": { "energy": {"label":"Energy","min":0,"max":100,"start":60} },
  "globals":   { "steps": {"label":"Steps taken","min":0,"max":9,"start":0} },
  "characters": { "player": { "name":"Hero", "icons":[ {"id":"plain","img":"hero_plain.svg","when":{...}} ] } } }

:: start
Narration lines.
image:: desk.svg Caption for a scene image
speaker:: player
Something they say.
[[Step closer|close]]
[[Call out|callout]] if:: energy >= 60     # guarded choice (or put guard on line above)

:: close
set:: steps + 1      # global stat (routes by name; '@steps' forces global too)
set:: energy -5      # character stat
[[Touch the glass|touch]]

:: bridge
No choices? `goto::` shows a Continue button.
goto:: next
```

Directives: `:: name [tags]`, `speaker:: id|none`, `image:: file.svg caption`,
`set:: stat +value|-value|= value` (runs on entry), `if:: stat >= 60` (guards the
next/parent decision), `goto:: target`, `---` scene break, comments `#` / `//`.
A passage with no choices and no `goto` is an ending.

`roll:: NdS` rolls dice on entry (`roll:: 2d6`, `roll:: d20`, modifiers like
`2d6+1`) and stores the result in the hidden `lastroll` global so guards can
branch on it (`if:: lastroll >= 15`). `roll:: stat = NdS` writes a named stat
instead. A hidden `lastroll` stat is auto-registered when a story rolls.

## Stats

- **charStats** = the hero's stats (sidebar "Character"); **globals** = story
  state (sidebar "Story").
- Numeric stats: `{min,max,start}`. Enum stats: `{values:[...], start}`.
  `"hidden":true` tracks without showing: the stat is omitted from the sidebar,
  and its changes (`set::` / `roll::`) are omitted from the change log while
  still applying to state.
- Optional `"icon"` (short emoji/text) renders at the start of the sidebar chip,
  with the stat's `"label"` shown right beside it. Optional `"hint"` is shown as
  a tooltip when hovering the chip — use it to explain what the stat means or
  what condition it tracks.
- Effects route by declaration: a name in `charStats` edits the character, a name
  in `globals` edits the story; `@<name>` forces globe. Guards: `== != > >= < <=`
  (numeric), `== !=` (enum).

## Images

- **Scene images**: `image:: file.svg caption` inside a passage.
- **Headshots**: each `characters.<id>.icons[]` entry has `{id, img, when}`;
  `when` conditions read live stats (charStats + globals merged), first match
  wins — so the portrait reacts to the hero's stats as they change. Matchers: string
  (equals) / `["a","b"]` (any-of) / number / `{min,max,gt,lt}`. No `when` = fallback.
- Drop your own images (png/jpg/webp/gif/svg) into `stories/<name>/media/` and
  point icons at them.
- **Remote images**: an `image::` path or an icon `img` can also be a full
  `https://...` URL. The game loads those directly; `build` downloads and
  embeds them so the compiled file still works offline (falls back to loading
  over the network if the download fails).
- **Generated images**: 
  ```
  python cli/gse.py image "<prompt>" --story stories/r/r.gse --out apple.png
  ```
  renders one image from the prompt into the story's `media/` (correct extension
  auto-detected). `--story <f>` picks which project's `media/` to write into;
  `--out <name>` names the output file (relative paths land in `media/`). Without
  `--out` it defaults to `media/gen_<slug>.png`. Always pass the full command
  above so the image lands somewhere you can find.
  `gse generate stories/r/r.gse` turns every `image:: line caption` into a
  generated file and rewrites the story to point at it. Flags: `--width`,
  `--height`, `--seed`, `--model`, `--force`.
  The generator has **no memory** — each prompt is rendered independently, so
  write every caption as a complete, self-contained description, and repeat a
  short, consistent character/setting descriptor across all prompts for that
  story to keep continuity between scenes and portraits.
  - `--provider` selects the free backend (default `cf`):
    - `cf` — **Cloudflare Workers AI** (flux-1-schnell): free 10k neurons/day,
      no queue. Needs `CF_API_TOKEN` + `CF_ACCOUNT_ID`.
    - `horde` — Stable Horde crowdsourced SDXL (no token, but queues can be long).
    - `pollinations` — Pollinations.ai: instant, no account, lower resolution.
  - Secrets are read from env vars, or from `..\.gse_credentials.json` (one
    folder above this repo, deliberately outside git). Never commit keys.
- **Auto-download**: `gse download <f>` fetches every remote URL into `media/`
  (collision-safe filenames) and rewrites the `.gse` to point at the local
  files, so the story stays self-contained. The visual editor does the same
  automatically on Save — paste a URL into any image field and it is downloaded
  into `media/` and the reference is rewritten for you.

## Saves & the compiled game

- Browser: Save / Load / Clear buttons in the sidebar use localStorage
  (keyed per story title). Keyboard: `S` save, `L` load, `B` back, `R` restart, `1-9` choose.
- `build` inlines engine + story + all images into one `build/<name>/index.html`
  (uncompressed, works offline, shareable). `serve` is for editing.

## Structure

```
engine/core.js     logic: parse, state, saves (browser + shared semantics)
engine/ui.js       sidebar UI, rendering, save/load
engine/style.css
editor/            visual story editor (light/dark, no-markup authoring)
cli/gse.py         Python mirror: new/validate/run/dump/build/serve/edit/studio
stories/demo/      sample story + sample headshots (SVG placeholders)
tools/             packaging: package.py (zipapp) + freeze.py (PyInstaller)
tests/             offline stdlib unit suite
build/<name>/      compiled single-file games
```

To edit, just change the `.gse` and re-run `build` (or `serve`).
`core.js` and `gse.py` implement the same semantics — after touching either, run
`python cli/gse.py validate stories/demo/demo.gse` + a scripted run.

## Visual editor (`python cli/gse.py edit <f>`)

```
python cli/gse.py edit stories/demo/demo.gse        # http://127.0.0.1:8911
python cli/gse.py edit my_new_story                  # auto-scaffolds if missing
```

No markup needed — the editor is forms:

- **Beats** (left list): each beat is a moment of the story. Edit its name, tags,
  **plain-text story text** with a **voice** (speaker) picker, an optional **scene
  image**, variable changes ("on entering this beat"), and **choices** that link to
  other beats (pick the target from a dropdown, add a condition, or create a new
  beat inline).
- **Settings → General**: title, credit, first beat, image folder.
- **Settings → Variables**: track numbers (range with min/max/start) or lists
  (e.g. `weather: sunny/rainy`); character stats vs story/global state.
- **Settings → Characters**: display names + headshot **icons** with optional
  "only when" conditions (e.g. `energy >= 75`).
- **Light/Dark toggle** in the top bar (persisted per browser).
- **Open / New**: "Open / New" in the top bar lists every story in
  `stories/` and opens it in place, or scaffolds a brand-new one — no need to
  restart the editor to switch stories.
- **Images by URL**: the scene-image and icon fields accept either a file from
  your media folder or a pasted `https://...` URL; Save downloads it into
  `media/` and rewrites the story to the local file.
- **Save** writes the `.gse` back to disk (server refuses only structurally broken
  text; problems show as a warning toast). **Play** opens `/play` — the real game
  UI — so you can test as you go.
- Live validation: as you type, the editor re-parses and previews errors/warnings
  using the same `core.js` the game uses.

Dialogue is per **line** — each beat has `+ Add line` rows with a speaker picker
(empty = narrator) and a side badge. In the game, the main character (the beat's
`player` or first character) speaks right-aligned, other characters left-aligned,
and the narrator centered. Picking a speaker on one line keeps it for the next
line; selecting "(narrator)" returns to the narrator voice. The editor writes
`speaker:: <id>` markers and re-syncs them on load, so multi-voice scenes round-trip
cleanly between the editor and hand-written `.gse` files.

## Portable studio app

`python tools/package.py` builds a dependency-free app into `dist/`:

```
dist/green-story-engine.pyz   the whole CLI as one zipapp
dist/gui_launcher.py          Tkinter launcher: Open / New / Play / Validate
dist/Start Studio.cmd         double-click launcher (pythonw, no console)
dist/gse.cmd                  terminal wrapper:  gse <command> [...]
dist/engine/ editor/ stories/ live folders the zipapp needs next to itself
dist/README.md LICENSE        docs and licence
```

Run `Start Studio.cmd` for a windowed studio, or from the terminal:

```
dist\gse.cmd selftest
dist\gse.cmd studio
dist\gse.cmd edit stories\demo\demo.gse
dist\gse.cmd new my_story
```

`--install <folder>` copies the whole `dist/` somewhere else (e.g. a USB stick or
Desktop): `python tools/package.py --install <folder>`.

### Standalone executable (no Python needed)

`python tools/freeze.py` needs PyInstaller (`pip install pyinstaller`) and produces a
single `gse` / `gse.exe` with the engine, editor and demo stories embedded. The assets
are unpacked next to the executable on first run so new stories can be written beside
it. `--clean` wipes the caches first.

Both builds run on every push to `main` (CI) and are attached to a draft GitHub Release
when a `v*` tag is pushed — see `.github/workflows/`.

## Tests

```
python cli/gse.py selftest               # demo + editor REST round-trip
python -m unittest discover -s tests -v  # full offline unit suite
```

`tests/test_core.py` covers parse/build, validation, dialogue speaker blocks,
remote-image refs, scaffolding, scripted play to an ending, a `build` smoke test,
and the editor API round-trip. All stdlib, no network required.

## Contributing

`python cli/gse.py validate`, `selftest` and the unit suite run on every push
across Linux, Windows and macOS. Keep `engine/core.js` and `cli/gse.py` in sync —
they implement the same semantics — and re-run the suite after changing either.

## License

MIT — see [LICENSE](LICENSE).