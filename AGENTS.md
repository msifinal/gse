# AGENTS.md

## Unit tests

- Unit tests must not reference any specific story, franchise, or named character (real or fictional, e.g. comic/movie/TV characters).
- Use only generic, neutral placeholder characters (e.g. `ava`, `hero`) in test fixtures.
- Keep tests free of anything inappropriate; test engine behavior, not story content.

## Commands

```
python cli/gse.py validate stories/demo/demo.gse   # parse + link/stat/asset checks
python cli/gse.py selftest                        # demo + editor REST round-trip
python -m unittest discover -s tests -v           # full offline unit suite
python cli/gse.py build stories/demo/demo.gse      # single-file game -> build/demo/
python tools/package.py                           # portable zipapp studio -> dist/
python tools/freeze.py --clean                    # PyInstaller executable -> build/freeze/
```

## Engine parity

`engine/core.js` and `cli/gse.py` implement the same semantics. After touching
either, run `validate` + `selftest` + the unit suite. CI runs all three on
Linux and Windows.

## Code that is frozen-compatible

`cli/gse.py` must keep working when frozen into a PyInstaller executable:

- Asset paths go through `GSE_APP_ROOT` (`_app_root()`), never `__file__`.
- Sub-commands that re-invoke the CLI use `me = [] if getattr(sys, "frozen", False) else sys.argv[:1]`.
- New dynamic imports need a matching `--hidden-import` in `tools/freeze.py`.

## Releases

Push a `v*` tag. `.github/workflows/release.yml` builds the zipapp studio plus
standalone `gse` (Linux) / `gse.exe` (Windows) binaries and attaches them to a **draft**
GitHub Release — review and publish it manually. `build/`, `dist/`, `*.spec`,
`__pycache__/` and `*.save.json` are gitignored build output; never commit them.
Secrets live in `CF_API_TOKEN` / `CF_ACCOUNT_ID` env vars or
`.gse_credentials.json` one folder above the app root — never commit keys.

## Privacy

This project is published pseudonymously. Do not add real names, email addresses,
home directories, or other identifying details to source, docs, commit messages,
or issue templates.