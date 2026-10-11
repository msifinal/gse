#!/usr/bin/env python3
"""Build a standalone gse executable with PyInstaller.

Produces a single `gse.exe` (or `gse` on macOS/Linux) that needs no Python
installed. The engine assets (engine/, editor/, stories/) are embedded and
copied next to the executable on first run, so `gse new` can still write into a
writeable folder beside the binary.

Usage:
  pip install pyinstaller
  python tools/freeze.py            # build + lay out a release folder
  python tools/freeze.py --clean    # also remove PyInstaller caches
"""
import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
OUT = ROOT / "build" / "freeze"
APP_NAME = "gse"

ENTRY = '''\
"""Entry point for the frozen gse executable."""
import os
import shutil
import sys
from pathlib import Path

ASSETS = ("engine", "editor", "stories")


def bundle_dir():
    """Read-only folder PyInstaller unpacked this app into."""
    return Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))


def app_dir():
    """Writeable folder next to the executable (or the repo, from source)."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def install_assets(app, src):
    for name in ASSETS:
        dst = app / name
        if dst.exists():
            continue
        s = src / name
        if s.is_dir():
            shutil.copytree(s, dst)


def main():
    app = app_dir()
    src = bundle_dir()
    if src != app:
        try:
            install_assets(app, src)
        except OSError:
            pass
    os.environ.setdefault("GSE_APP_ROOT", str(app))
    from cli.gse import main as cli_main
    cli_main()


if __name__ == "__main__":
    main()
'''


def _run(argv, cwd=None):
    print("$ " + " ".join(str(a) for a in argv))
    subprocess.run([str(a) for a in argv], cwd=str(cwd or ROOT), check=True)


def build(out_dir=OUT, clean=False):
    if clean:
        for d in (ROOT / "build", HERE / "__pycache__"):
            if d.exists():
                shutil.rmtree(str(d))

    entry = OUT / "_frozen_entry.py"
    entry.parent.mkdir(parents=True, exist_ok=True)
    entry.write_text(ENTRY, encoding="utf-8")

    sep = ";" if os.name == "nt" else ":"
    args = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm", "--clean", "--onefile", "--console",
        "--name", APP_NAME,
        "--distpath", str(out_dir / "bin"),
        "--workpath", str(out_dir / "work"),
        "--specpath", str(out_dir),
        "--paths", str(ROOT),
        "--hidden-import", "cli.gse",
        "--hidden-import", "tkinter",
        "--hidden-import", "tkinter.ttk",
        "--hidden-import", "tkinter.messagebox",
        "--hidden-import", "tkinter.simpledialog",
        "--hidden-import", "webbrowser",
        # imported dynamically by `gse studio`
        "--hidden-import", "gui_launcher",
    ]
    for asset in ("engine", "editor", "stories"):
        args += ["--add-data", "%s%s%s" % (ROOT / asset, sep, asset)]
    args += ["--add-data", "%s%sgui_launcher.py" % (HERE / "gui_launcher.py", sep)]
    args.append(str(entry))
    _run(args)

    exe = out_dir / "bin" / (APP_NAME + (".exe" if os.name == "nt" else ""))
    print("built %s" % exe)
    return exe


def layout(exe, out_dir=OUT):
    """Assemble the shippable folder next to the built executable."""
    if out_dir.exists():
        shutil.rmtree(str(out_dir / "release"), ignore_errors=True)
    rel = out_dir / "release"
    rel.mkdir(parents=True)

    shutil.copy2(str(exe), str(rel / exe.name))
    for asset in ("engine", "editor", "stories"):
        shutil.copytree(str(ROOT / asset), str(rel / asset))
    shutil.copy2(str(ROOT / "README.md"), str(rel / "README.md"))
    shutil.copy2(str(ROOT / "LICENSE"), str(rel / "LICENSE"))
    shutil.copy2(str(HERE / "gui_launcher.py"), str(rel / "gui_launcher.py"))

    if os.name == "nt":
        (rel / "Start Studio.cmd").write_text(
            '@echo off\r\n'
            'rem Green Story Engine - studio launcher (no console window)\r\n'
            'start "" "%~dp0gse.exe" studio\r\n',
            encoding="utf-8")
        (rel / "gse.cmd").write_text(
            '@echo off\r\n"%~dp0gse.exe" %*\r\n', encoding="utf-8")
    else:
        (rel / "Start Studio.sh").write_text(
            '#!/bin/sh\nexec "$(dirname "$0")/gse" studio\n', encoding="utf-8")
        (rel / "Start Studio.sh").chmod(0o755)

    print("release folder: %s" % rel)
    return rel


def main():
    ap = argparse.ArgumentParser(description="build a standalone gse executable")
    ap.add_argument("--clean", action="store_true", help="remove build caches first")
    ap.add_argument("--out", default=str(OUT), help="output folder")
    args = ap.parse_args()

    out = Path(args.out).resolve()
    exe = build(out, clean=args.clean)
    layout(exe, out)
    print("try: %s selftest" % (out / "release" / exe.name))
    return 0


if __name__ == "__main__":
    sys.exit(main())