#!/usr/bin/env python3
"""Green Story Engine - studio launcher.

A small Tkinter window that manages stories: Open (edit in the visual editor),
New, Play (serve + open a browser), Validate, and open the build folder.

Works next to a packaged dist/ (green-story-engine.pyz), next to the frozen
gse.exe built by tools/freeze.py, or from a source checkout (tools/gui_launcher.py,
where the CLI is cli/gse.py).

Run with pythonw so no console window appears:
  pythonw tools/gui_launcher.py
"""
import os
import subprocess
import sys
import threading
import time
import webbrowser
import tkinter as tk
from tkinter import messagebox, simpledialog, ttk

FROZEN = bool(getattr(sys, "frozen", False))
APP_DIR = os.path.dirname(os.path.abspath(sys.executable if FROZEN else __file__))
PYZ = os.path.join(APP_DIR, "green-story-engine.pyz")
STORY_DIR = os.path.join(APP_DIR, "stories")
SRC_CLI = os.path.join(os.path.dirname(APP_DIR), "cli", "gse.py")

EDITOR_PORT = 8911
SERVE_PORT = 8910
HIDE = 0x08000000  # CREATE_NO_WINDOW


def cli_command():
    """Return argv prefix for running the engine CLI from this app."""
    if FROZEN:
        return [sys.executable]
    if os.path.isfile(PYZ):
        return [sys.executable, PYZ]
    if os.path.isfile(SRC_CLI):
        return [sys.executable, SRC_CLI]
    raise RuntimeError("engine CLI not found (expected %s or %s)" % (PYZ, SRC_CLI))


def run_capture(argv):
    """Run the CLI and return (returncode, combined output)."""
    proc = subprocess.run(argv, capture_output=True, text=True,
                          creationflags=HIDE if os.name == "nt" else 0)
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


class Studio(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Green Story Engine - Studio")
        self.geometry("560x420")
        self.configure(bg="#eef1f5")
        self._busy = False
        self._build_panel()

    def _build_panel(self):
        head = ttk.Frame(self, padding=(14, 12))
        head.pack(fill="x")
        ttk.Label(head, text="Green Story Engine", font=("Segoe UI", 16, "bold")).pack(side="left")
        ttk.Label(head, text="story studio", foreground="#5a6472").pack(side="left", padx=8, pady=(6, 0))

        self.status = ttk.Label(self, text="", foreground="#2a7f3d", padding=(14, 2))
        self.status.pack(fill="x")

        body = ttk.Frame(self, padding=(14, 6))
        body.pack(fill="both", expand=True)
        self.list = tk.Listbox(body, font=("Segoe UI", 11), exportselection=False)
        self.list.pack(fill="both", expand=True)
        self.list.bind("<Double-Button-1>", lambda e: self.open_story())

        bar = ttk.Frame(self, padding=(14, 10))
        bar.pack(fill="x")
        for text, fn in (("Open", self.open_story), ("New", self.new_story),
                         ("Play", self.play_story), ("Validate", self.validate_story),
                         ("Refresh", self.refresh)):
            ttk.Button(bar, text=text, command=fn).pack(side="left", padx=(0, 6))
        ttk.Button(bar, text="Build Folder", command=self.open_build).pack(side="right")

        self.refresh()

    # -- helpers ----------------------------------------------------------
    def set_status(self, msg):
        self.status.config(text=msg)

    def selected(self):
        sel = self.list.curselection()
        if not sel:
            messagebox.showinfo("Green Story Engine", "Pick a story first.")
            return None
        name = self.list.get(sel[0])
        story = os.path.join(STORY_DIR, name, name + ".gse")
        if not os.path.isfile(story):
            messagebox.showerror("Green Story Engine", "Story file missing:\n%s" % story)
            return None
        return story

    def _background(self, fn):
        if self._busy:
            return
        self._busy = True
        self.set_status("working...")
        threading.Thread(target=self._done_wrap, args=(fn,), daemon=True).start()

    def _done_wrap(self, fn):
        try:
            fn()
        except Exception as e:  # pragma: no cover
            self.after(0, lambda: (self.set_status(""), messagebox.showerror("Green Story Engine", str(e))))
        finally:
            self._busy = False

    def _launch(self, argv):
        """Start the CLI without blocking this window."""
        subprocess.Popen(argv, creationflags=HIDE if os.name == "nt" else 0)

    # -- actions ----------------------------------------------------------
    def refresh(self):
        try:
            names = sorted(d for d in os.listdir(STORY_DIR)
                           if os.path.isdir(os.path.join(STORY_DIR, d))
                           and os.path.isfile(os.path.join(STORY_DIR, d, d + ".gse")))
        except OSError:
            names = []
        self.list.delete(0, "end")
        for n in names:
            self.list.insert("end", n)
        self.set_status("") if names else self.set_status("no stories yet - click New")

    def open_story(self):
        def work():
            story = self.selected()
            if not story:
                self.set_status("")
                return
            self._launch(cli_command() + ["edit", story])
            self.after(1200, lambda: webbrowser.open("http://127.0.0.1:%d" % EDITOR_PORT))
            self.set_status("editor starting at http://127.0.0.1:%d" % EDITOR_PORT)
        self._background(work)

    def new_story(self):
        name = simpledialog.askstring("New Story",
                                      "Story name (letters, numbers, - and _ only):",
                                      parent=self)
        if not name or not name.strip():
            return
        name = name.strip()
        def work():
            code, out = run_capture(cli_command() + ["new", name])
            if code != 0:
                self.after(0, lambda: messagebox.showerror("Green Story Engine", out or "create failed"))
                self.set_status("")
                return
            self.after(0, self.refresh)
            self.set_status("created '%s'" % name)
        self._background(work)

    def play_story(self):
        def work():
            story = self.selected()
            if not story:
                self.set_status("")
                return
            self._launch(cli_command() + ["serve", story, "--port", str(SERVE_PORT)])
            self.after(1200, lambda: webbrowser.open("http://127.0.0.1:%d" % SERVE_PORT))
            self.set_status("playing at http://127.0.0.1:%d" % SERVE_PORT)
        self._background(work)

    def validate_story(self):
        def work():
            story = self.selected()
            if not story:
                self.set_status("")
                return
            code, out = run_capture(cli_command() + ["validate", story])
            def show():
                self.set_status("validate: " + ("ok" if code == 0 else "FAILED"))
                messagebox.showinfo("Validate - %s" % os.path.basename(story), out)
            self.after(0, show)
        self._background(work)

    def open_build(self):
        build_dir = os.path.join(APP_DIR, "build")
        if not os.path.isdir(build_dir):
            os.makedirs(build_dir)
        os.startfile(build_dir) if os.name == "nt" else None


if __name__ == "__main__":
    Studio().mainloop()
