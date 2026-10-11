#!/usr/bin/env python3
"""Package the engine as a portable, stdlib-only studio app.

Creates dist/ containing:
  green-story-engine.pyz   zipapp of the CLI (engine code)
  gui_launcher.py          Tkinter launcher (Open / New / Play / Validate)
  Start Studio.cmd         double-click launcher (uses pythonw, no console)
  gse.cmd                  terminal CLI wrapper:  gse <command> [...]
  engine/ editor/ stories/ live folders the zipapp needs next to itself
  README.txt               quick-start notes

Usage:
  python tools/package.py              build into dist/
  python tools/package.py --install X  build, then copy dist/* into folder X

For a dependency-free executable that needs no Python, use tools/freeze.py
(requires PyInstaller).
"""
import argparse
import os
import shutil
import sys
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
DIST = ROOT / "dist"
PYZ = DIST / "green-story-engine.pyz"

FOLDERS = ["engine", "editor", "stories"]
FILES = ["README.md", "LICENSE"]


def _main_py():
    return (
        "import os, sys\n"
        "_app_dir = os.path.dirname(os.path.abspath(sys.argv[0]))\n"
        "os.environ.setdefault('GSE_APP_ROOT', _app_dir)\n"
        "from cli.gse import main\n"
        "main()\n"
    )


def build():
    if DIST.exists():
        shutil.rmtree(str(DIST))
    DIST.mkdir(parents=True)

    with zipfile.ZipFile(str(PYZ), "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("__main__.py", _main_py())
        z.write(str(ROOT / "cli" / "__init__.py"), "cli/__init__.py")
        z.write(str(ROOT / "cli" / "gse.py"), "cli/gse.py")
        for f in FILES:
            p = ROOT / f
            if p.is_file():
                z.write(str(p), f)
        z.write(str(HERE / "gui_launcher.py"), "tools/gui_launcher.py")

    for f in FOLDERS:
        shutil.copytree(str(ROOT / f), str(DIST / f))
    for f in FILES:
        p = ROOT / f
        if p.is_file():
            shutil.copy2(str(p), str(DIST / f))
    shutil.copy2(str(HERE / "gui_launcher.py"), str(DIST / "gui_launcher.py"))

    (DIST / "Start Studio.cmd").write_text(
        '@echo off\r\n'
        'rem Green Story Engine - studio launcher (no console window)\r\n'
        'start "" pythonw "%~dp0gui_launcher.py"\r\n',
        encoding="utf-8")

    (DIST / "gse.cmd").write_text(
        '@echo off\r\n'
        'python "%~dp0green-story-engine.pyz" %*\r\n',
        encoding="utf-8")

    (DIST / "README.txt").write_text(
        "Green Story Engine - portable studio\n"
        "------------------------------------\n"
        "Run 'Start Studio.cmd' to open the launcher (Open / New / Play / Validate),\n"
        "or launch it from the terminal with:  gse studio\n"
        "Or use the command line from this folder:\n"
        "  gse edit <story>        visual story editor\n"
        "  gse new <name>          scaffold a new story\n"
        "  gse validate <story>    check a story\n"
        "  gse run <story>         play in the terminal\n"
        "  gse build <story>       compile a single-file HTML game into build/\n"
        "  gse download <story>    fetch remote image URLs into the story's media/\n"
        "  gse selftest            run the offline self-tests\n"
        "Stories live in stories/; games build to build/.\n"
        "Requires: a local Python 3.8+ with tkinter for the launcher.\n"
        "No Python? Download the standalone build from the project's Releases page.\n",
        encoding="utf-8")

    size = PYZ.stat().st_size
    print("built %s" % PYZ)
    print("  bytes  : %d" % size)
    print("  dist   : %s" % DIST)
    print("  try    : python %s selftest" % PYZ)
    return DIST


def main():
    ap = argparse.ArgumentParser(description="package the engine as a portable studio app")
    ap.add_argument("--install", default=None, help="copy dist/ into this folder (e.g. Desktop/GreenStoryStudio)")
    args = ap.parse_args()
    build()
    if args.install:
        target = Path(args.install).expanduser().resolve()
        if target.exists():
            shutil.rmtree(str(target))
        shutil.copytree(str(DIST), str(target))
        print("installed to %s" % target)


if __name__ == "__main__":
    sys.exit(main())
