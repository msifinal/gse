#!/usr/bin/env python3
"""Unit tests for the Green Story Engine (stdlib unittest, offline).

Run:  python -m unittest discover -s tests -v
  or:  python tests/test_core.py
"""
import os
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "cli"))
import gse  # noqa: E402

DEMO = gse.ROOT / "stories" / "demo" / "demo.gse"


class ParseBuildTests(unittest.TestCase):
    def test_demo_parses(self):
        parsed = gse.parse_story(DEMO.read_text(encoding="utf-8"))
        self.assertEqual(parsed["errors"], [])
        story = gse.build_story(parsed)
        self.assertIn("config", story)
        self.assertGreaterEqual(len(story["passages"]), 2)
        self.assertEqual(story["config"]["title"], "The Staple Pitch")

    def test_demo_validates(self):
        story, p = gse.load_story(str(DEMO))
        res = gse.validate(story, media_dir=gse.media_dir_of(p, story))
        self.assertEqual(res["errors"], [])

    def test_run_to_ending(self):
        """Play the demo scripted; it must reach an ending."""
        from io import StringIO
        import contextlib
        story, _ = gse.load_story(str(DEMO))
        session = gse.create_session(story)
        answers = iter("1")
        gse.begin(session)
        steps = 0
        buf = StringIO()
        with contextlib.redirect_stdout(buf):
            while not session["lastView"]["ending"] and steps < 60:
                steps += 1
                gse.go_on(session) if session["lastView"]["continuation"] else gse.choose(session, next(answers, "1"))
        self.assertTrue(session["lastView"]["ending"], "story should end")

    def test_safe_name(self):
        self.assertEqual(gse.safe_name("My First Story!"), "My-First-Story")
        self.assertEqual(gse.safe_name("  ...  "), "")
        self.assertEqual(gse.safe_name("a_b-1"), "a_b-1")

    def test_utf8_bom_tolerated(self):
        """A UTF-8 BOM in front of the story must not break header detection."""
        text = "\ufeff:: config\n{\"title\": \"BOM\"}\n\n:: start\nHi.\n"
        parsed = gse.parse_story(text)
        self.assertEqual(parsed["errors"], [])
        story = gse.build_story(parsed)
        self.assertEqual(story["config"]["title"], "BOM")
        self.assertIn("start", story["passages"])

    def test_scaffold_roundtrip(self):
        """scaffold_story + parse/validate in an isolated app root."""
        old_root = gse.ROOT
        with tempfile.TemporaryDirectory() as td:
            try:
                gse.ROOT = Path(td)
                base = gse.scaffold_story("unit_demo", "Unit Demo")
                story_file = base / ("unit_demo" + gse.STORY_EXT)
                parsed = gse.parse_story(story_file.read_text(encoding="utf-8"))
                self.assertEqual(parsed["errors"], [])
                story = gse.build_story(parsed)
                res = gse.validate(story, media_dir=base / "media")
                self.assertEqual(res["errors"], [])
            finally:
                gse.ROOT = old_root


class DialogueTests(unittest.TestCase):
    def test_per_line_speakers(self):
        """Editor-style {lines: [...]} with speaker:: markers -> blocks."""
        obj = {
            "config": {"title": "T", "start": "a",
                       "charStats": {}, "globals": {}, "characters": {}},
            "passages": {
                "a": {"lines": [
                    "Narrator intro.",
                    "speaker:: player",
                    "Hello!",
                    "speaker:: sales",
                    "Howdy.",
                    "speaker:: none",
                    "The room goes quiet.",
                ]}
            }
        }
        parsed = gse.parse_story(obj)
        self.assertEqual(parsed["errors"], [])
        p = parsed["passages"]["a"]
        speakers = [b.get("speaker") for b in p["blocks"]]
        self.assertEqual(speakers, [None, "player", "sales", None])

    def test_url_images_do_not_need_local_media(self):
        text = """:: config
{"title":"Web","start":"a","charStats":{},"globals":{},"characters":{}}

:: a
image:: https://example.com/pic.png A caption
Text.
"""
        parsed = gse.parse_story(text)
        story = gse.build_story(parsed)
        refs = gse.collect_refs(story)
        self.assertIn("https://example.com/pic.png", refs)
        res = gse.validate(story, media_dir=None)
        self.assertEqual(res["errors"], [])


class StatusTests(unittest.TestCase):
    """Character profile status text, settable via set:: <char>.status = ..."""

    STATUS_STORY = """:: config
{"title":"Status","start":"start","charStats":{},"globals":{},
 "characters":{"ava":{"name":"Ava","status":"calm","icons":[]}}}
:: start
set:: ava.status = furious
goto:: a

:: a
speaker:: ava
Hey.
"""

    def _run(self, text):
        parsed = gse.parse_story(text)
        self.assertEqual(parsed["errors"], [])
        story = gse.build_story(parsed)
        session = gse.create_session(story)
        gse.begin(session)
        return story, session

    def test_status_initialized_from_config(self):
        _, session = self._run(StatusTests.STATUS_STORY)
        self.assertEqual(session["status"]["ava"], "furious")

    def test_status_in_view_and_effects(self):
        story, session = self._run(StatusTests.STATUS_STORY)
        self.assertEqual(story["passages"]["start"]["effects"][0]["stat"], "ava.status")
        self.assertEqual(session["lastView"]["status"]["ava"], "furious")

    def test_status_append_and_remove(self):
        text = """:: config
{"title":"Status","start":"start","charStats":{},"globals":{},
 "characters":{"ava":{"name":"Ava","status":"","icons":[]}}}
:: start
set:: ava.status = calm
set:: ava.status + , now furious
goto:: a

:: a
speaker:: ava
Hey.
"""
        _, session = self._run(text)
        self.assertEqual(session["status"]["ava"], "calm, now furious")
        gse.enter(session, "a")
        self.assertEqual(session["status"]["ava"], "calm, now furious")

    def test_status_snapshot_restore_roundtrip(self):
        _, session = self._run(StatusTests.STATUS_STORY)
        snap = gse.snapshot(session)
        self.assertEqual(snap["status"]["ava"], "furious")
        restored = gse.restore(session, snap)
        self.assertFalse(restored.get("error"))
        self.assertEqual(session["status"]["ava"], "furious")

    def test_status_back_restores_previous(self):
        text = """:: config
{"title":"Status","start":"start","charStats":{},"globals":{},
 "characters":{"ava":{"name":"Ava","status":"calm","icons":[]}}}
:: start
set:: ava.status = furious
goto:: a

:: a
set:: ava.status = calm
"""
        _, session = self._run(text)
        self.assertEqual(session["status"]["ava"], "furious")
        gse.enter(session, "a")
        self.assertEqual(session["status"]["ava"], "calm")
        gse.back(session)
        self.assertEqual(session["status"]["ava"], "calm")

    def test_status_no_warning_for_unknown_char(self):
        text = """:: config
{"title":"Status","start":"start","charStats":{},"globals":{},
 "characters":{"ava":{"name":"Ava","status":"","icons":[]}}}
:: start
set:: mark.status = gone
goto:: a

:: a
speaker:: ava
Hey.
"""
        parsed = gse.parse_story(text)
        story = gse.build_story(parsed)
        res = gse.validate(story, media_dir=None)
        warnings = [w for w in res["warnings"] if "status" in w]
        self.assertTrue(any("unknown stat" in w and "mark.status" in w for w in warnings))


class RollTests(unittest.TestCase):
    """roll:: NdS performs an RNG roll into lastroll (or a named stat)."""

    ROLL_STORY = """:: config
{"title":"Roll","start":"start","charStats":{},"globals":{},
 "characters":{"ava":{"name":"Ava","icons":[]}}}
:: start
roll:: 2d6
goto:: a

:: a
speaker:: ava
Hey.
"""

    def _parse(self, line):
        text = """:: config
{"title":"R","start":"start","charStats":{},"globals":{},
 "characters":{"ava":{"name":"Ava","icons":[]}}}
:: start
%s
goto:: a

:: a
speaker:: ava
Hey.
""" % line
        parsed = gse.parse_story(text)
        self.assertEqual(parsed["errors"], [], parsed["errors"])
        return gse.build_story(parsed)

    def test_roll_parses_into_lastroll(self):
        story = self._parse("roll:: 2d6")
        e = story["passages"]["start"]["effects"][0]
        self.assertEqual(e["op"], "roll")
        self.assertEqual(e["stat"], "lastroll")
        self.assertEqual(e["dice"]["count"], 2)
        self.assertEqual(e["dice"]["sides"], 6)

    def test_roll_d20_single_die(self):
        story = self._parse("roll:: d20")
        e = story["passages"]["start"]["effects"][0]
        self.assertEqual(e["dice"]["count"], 1)
        self.assertEqual(e["dice"]["sides"], 20)

    def test_roll_into_named_stat(self):
        story = self._parse("roll:: luck = d6")
        e = story["passages"]["start"]["effects"][0]
        self.assertEqual(e["stat"], "luck")
        self.assertEqual(e["dice"]["sides"], 6)

    def test_roll_with_modifier(self):
        story = self._parse("roll:: 2d6+1")
        e = story["passages"]["start"]["effects"][0]
        self.assertEqual(e["dice"]["mods"], [{"sign": "+", "value": 1}])

    def test_roll_bad_syntax_is_error(self):
        parsed = gse.parse_story(""":: config
{"title":"R","start":"start","charStats":{},"globals":{},
 "characters":{"ava":{"name":"Ava","icons":[]}}}
:: start
roll:: cheese
goto:: a

:: a
speaker:: ava
Hey.
""")
        self.assertTrue(any("Bad roll" in e for e in parsed["errors"]))

    def test_lastroll_auto_registered_hidden(self):
        story = self._parse("roll:: d20")
        self.assertIn("lastroll", story["config"]["globals"])
        self.assertTrue(story["config"]["globals"]["lastroll"]["hidden"])

    def test_no_lastroll_without_rolls(self):
        story = self._parse("set:: ava.mood = fine")
        self.assertNotIn("lastroll", story["config"]["globals"])

    def test_roll_executes_into_stats(self):
        import unittest.mock as mock
        with mock.patch("random.randint", return_value=3):
            story = self._parse("roll:: 2d6")
            session = gse.create_session(story)
            gse.begin(session)
            self.assertEqual(session["lastView"]["allStats"]["global"]["lastroll"], 6)

    def test_roll_clamped_to_stat_range(self):
        import unittest.mock as mock
        story = self._parse("roll:: luck = d100")
        story["config"]["charStats"]["luck"] = gse.default_stat("luck", {"min": 0, "max": 10, "start": 5}, "char")
        with mock.patch("random.randint", return_value=50):
            session = gse.create_session(story)
            gse.begin(session)
            self.assertEqual(session["lastView"]["allStats"]["char"]["luck"], 10)

    def test_roll_modifier_applied(self):
        import unittest.mock as mock
        with mock.patch("random.randint", return_value=3):
            story = self._parse("roll:: 2d6+2")
            session = gse.create_session(story)
            gse.begin(session)
            self.assertEqual(session["lastView"]["allStats"]["global"]["lastroll"], 8)

    def test_roll_guard_branches_on_lastroll(self):
        import unittest.mock as mock
        text = """:: config
{"title":"R","start":"start","charStats":{},"globals":{},
 "characters":{"ava":{"name":"Ava","icons":[]}}}
:: start
roll:: d20

[[High|high]] if:: lastroll >= 15
[[Low|low]] if:: lastroll < 15

:: high
speaker:: ava
Great.

:: low
speaker:: ava
Meh.
"""
        parsed = gse.parse_story(text)
        self.assertEqual(parsed["errors"], [], parsed["errors"])
        story = gse.build_story(parsed)
        with mock.patch("random.randint", return_value=18):
            session = gse.create_session(story)
            gse.begin(session)
            self.assertEqual([c["label"] for c in session["lastView"]["choices"]], ["High"])
        with mock.patch("random.randint", return_value=4):
            session = gse.create_session(story)
            gse.begin(session)
            self.assertEqual([c["label"] for c in session["lastView"]["choices"]], ["Low"])


class HiddenStatTests(unittest.TestCase):
    """hidden:true stats stay out of the sidebar and out of the change log."""

    HIDDEN_STORY = """:: config
{"title":"H","start":"start",
 "charStats":{"energy":{"label":"Energy","min":0,"max":100,"start":50}},
 "globals":{"aura":{"label":"Aura","min":0,"max":1,"start":0,"hidden":true}},
 "characters":{"ava":{"name":"Ava","icons":[]}}}
:: start
set:: energy +10
set:: aura =1
goto:: a

:: a
speaker:: ava
Hey.
"""

    def _run(self, text):
        parsed = gse.parse_story(text)
        self.assertEqual(parsed["errors"], [], parsed["errors"])
        story = gse.build_story(parsed)
        session = gse.create_session(story)
        gse.begin(session)
        return story, session

    def test_hidden_change_applies_but_is_not_logged(self):
        _, session = self._run(HiddenStatTests.HIDDEN_STORY)
        self.assertEqual(session["lastView"]["allStats"]["global"]["aura"], 1)
        self.assertNotIn("aura", [e["id"] for e in session["lastView"]["stats"]])
        self.assertTrue(all(e["stat"] != "aura" for e in session["lastView"]["effects"]))

    def test_visible_change_still_logged(self):
        _, session = self._run(HiddenStatTests.HIDDEN_STORY)
        self.assertEqual(session["lastView"]["allStats"]["char"]["energy"], 60)
        self.assertTrue(any(e["stat"] == "energy" for e in session["lastView"]["effects"]))

    def test_roll_into_hidden_stat_not_logged(self):
        import unittest.mock as mock
        with mock.patch("random.randint", return_value=5):
            _, session = self._run(""":: config
{"title":"H","start":"start","charStats":{},"globals":{},
 "characters":{"ava":{"name":"Ava","icons":[]}}}
:: start
roll:: d20
goto:: a

:: a
speaker:: ava
Hey.
""")
            self.assertEqual(session["lastView"]["allStats"]["global"]["lastroll"], 5)
            self.assertTrue(all(e["op"] != "roll" for e in session["lastView"]["effects"]))


class StatChipTests(unittest.TestCase):
    """icon renders in the chip and hint reaches the view for the tooltip."""

    def _view(self, text):
        parsed = gse.parse_story(text)
        self.assertEqual(parsed["errors"], [], parsed["errors"])
        story = gse.build_story(parsed)
        session = gse.create_session(story)
        gse.begin(session)
        return session["lastView"]

    def test_icon_and_hint_reach_the_view(self):
        view = self._view(""":: config
{"title":"H","start":"start",
 "charStats":{"hero":{"label":"Hero Level","icon":"LV","hint":"How much of the hero is still them.","min":0,"max":10,"start":0}},
 "globals":{},
 "characters":{"ava":{"name":"Ava","icons":[]}}}
:: start
speaker:: ava
Hey.
""")
        entry = next(s for s in view["stats"] if s["id"] == "hero")
        self.assertEqual(entry["icon"], "LV")
        self.assertEqual(entry["hint"], "How much of the hero is still them.")
        self.assertEqual(entry["label"], "Hero Level")

    def test_missing_icon_hint_default_to_empty(self):
        view = self._view(""":: config
{"title":"H","start":"start",
 "charStats":{"level":{"label":"Level","min":0,"max":10,"start":0}},
 "globals":{},
 "characters":{"ava":{"name":"Ava","icons":[]}}}
:: start
speaker:: ava
Hi.
""")
        entry = next(s for s in view["stats"] if s["id"] == "level")
        self.assertEqual(entry["icon"], "")
        self.assertEqual(entry["hint"], "")

    def test_enum_stat_carries_icon_and_hint(self):
        view = self._view(""":: config
{"title":"H","start":"start",
 "charStats":{"mood":{"label":"Mood","values":["low","high"],"start":"low","icon":"MD","hint":"Current mood of the hero."}},
 "globals":{},
 "characters":{"ava":{"name":"Ava","icons":[]}}}
:: start
speaker:: ava
Hi.
""")
        entry = next(s for s in view["stats"] if s["id"] == "mood")
        self.assertEqual(entry["kind"], "enum")
        self.assertEqual(entry["icon"], "MD")
        self.assertEqual(entry["hint"], "Current mood of the hero.")


class ActionTests(unittest.TestCase):
    """Inline action spans: [open:<charId|modal.name>]...[/open], [close]...[/close]."""

    STORY = """:: config
{"title":"Stars","start":"a","charStats":{},"globals":{},
 "characters":{"ava":{"name":"Ava","icons":[]}}}
:: a
speaker:: ava
[open:ava]Inspect Ava[/open] and then [close]stop looking[/close]. Done.
"""
    STORY_EDITOR = {
        "config": {"title": "E", "start": "a", "charStats": {}, "globals": {},
                   "characters": {"ava": {"name": "Ava"}}},
        "passages": {"a": {"lines": [
            "speaker:: ava",
            "[open:ava]Inspect Ava[/open]. Placeholder.",
        ]}}
    }

    def _view(self):
        parsed = gse.parse_story(ActionTests.STORY)
        self.assertEqual(parsed["errors"], [])
        story = gse.build_story(parsed)
        session = gse.create_session(story)
        return gse.begin(session)

    def test_spans_and_clean_text(self):
        v = self._view()
        line = v["lines"][0]
        self.assertEqual(line["text"], "Inspect Ava and then stop looking. Done.")
        self.assertEqual(line["spans"], [
            {"kind": "open", "target": "ava", "from": 0, "to": 11},
            {"kind": "close", "target": None, "from": 21, "to": 33},
        ])

    def test_no_markup_left_in_text(self):
        v = self._view()
        self.assertNotIn("[open", v["lines"][0]["text"])
        self.assertNotIn("[/open]", v["lines"][0]["text"])

    def test_editor_format_also_parses_actions(self):
        parsed = gse.parse_story(ActionTests.STORY_EDITOR)
        self.assertEqual(parsed["errors"], [])
        story = gse.build_story(parsed)
        session = gse.create_session(story)
        v = gse.begin(session)
        self.assertEqual(v["lines"][0]["spans"], [{"kind": "open", "target": "ava", "from": 0, "to": 11}])

    def test_unclosed_action_is_an_error(self):
        parsed = gse.parse_story(""":: config
{"title":"S","start":"a","charStats":{},"globals":{}, "characters":{}}
:: a
[open:ava]never closed
""")
        self.assertTrue(any("Unclosed action" in e for e in parsed["errors"]))

    def test_empty_label_is_an_error(self):
        parsed = gse.parse_story(""":: config
{"title":"S","start":"a","charStats":{},"globals":{}, "characters":{}}
:: a
[close][/close]
""")
        self.assertTrue(any("empty label" in e for e in parsed["errors"]))

    def test_unknown_action_char_warns(self):
        parsed = gse.parse_story(""":: config
{"title":"S","start":"a","charStats":{},"globals":{}, "characters":{}}
:: a
[open:ghost]who?[/open]
""")
        story = gse.build_story(parsed)
        res = gse.validate(story, media_dir=None)
        self.assertTrue(any("unknown target \"ghost\"" in w for w in res["warnings"]))

    def test_action_spans_in_json_view(self):
        parsed = gse.parse_story(ActionTests.STORY)
        story = gse.build_story(parsed)
        session = gse.create_session(story)
        v = gse.begin(session)
        j = gse.view_json(v)
        self.assertTrue(all("spans" in l for l in j["lines"] if l["type"] == "text"))
        self.assertEqual(j["lines"][0]["spans"][0]["target"], "ava")


class ModalTests(unittest.TestCase):
    """Named modals in config, opened via [open:modal.<name>]."""

    STORY = """:: config
{"title":"M","start":"a","charStats":{},"globals":{},
 "characters":{"ava":{"name":"Ava","icons":[]}},
 "modals":{"map":{"title":"The City","image":"city_map.png",
                 "text":"District by district, the fires spread."}}}
:: a
speaker:: ava
[open:modal.map]Look at the map[/open]. Oh and [open:ava]Ava[/open].
"""

    def _story(self):
        parsed = gse.parse_story(ModalTests.STORY)
        self.assertEqual(parsed["errors"], [])
        return gse.build_story(parsed)

    def test_config_modals_default_and_parsed(self):
        story = self._story()
        self.assertIn("modals", story["config"])
        self.assertEqual(story["config"]["modals"]["map"]["title"], "The City")

    def test_modal_action_span_and_validate(self):
        story = self._story()
        session = gse.create_session(story)
        v = gse.begin(session)
        self.assertEqual(v["lines"][0]["spans"], [
            {"kind": "open", "target": "modal.map", "from": 0, "to": 15},
            {"kind": "open", "target": "ava", "from": 24, "to": 27},
        ])
        res = gse.validate(story, media_dir=None)
        self.assertEqual([e for e in res["errors"] if "modal" in e], [])

    def test_unknown_modal_warns(self):
        parsed = gse.parse_story(""":: config
{"title":"M","start":"a","charStats":{},"globals":{}, "characters":{},
 "modals":{"map":{"title":"The City","text":"..."}}}
:: a
[open:modal.nope]what[/open]
""")
        story = gse.build_story(parsed)
        res = gse.validate(story, media_dir=None)
        self.assertTrue(any("unknown target \"modal.nope\"" in w for w in res["warnings"]))

    def test_modal_image_collected_as_ref(self):
        story = self._story()
        refs = gse.collect_refs(story)
        self.assertIn("city_map.png", refs)

    def test_missing_modal_image_is_an_error(self):
        import tempfile
        from pathlib import Path
        parsed = gse.parse_story(""":: config
{"title":"M","start":"a","charStats":{},"globals":{}, "characters":{},
 "modals":{"map":{"title":"The City","image":"city_map.png","text":"..."}}}
:: a
[open:modal.map]view[/open]
""")
        story = gse.build_story(parsed)
        with tempfile.TemporaryDirectory() as td:
            res = gse.validate(story, media_dir=Path(td))
            self.assertTrue(any('Modal "map" references missing image "city_map.png"' in e
                                for e in res["errors"]))


class CliTests(unittest.TestCase):
    def _cli(self, *argv, root=None):
        env = dict(os.environ)
        if root:
            env["GSE_APP_ROOT"] = str(root)
        base = [sys.executable, os.path.join(gse.ROOT, "cli", "gse.py")]
        return subprocess.run(base + list(argv), capture_output=True, text=True, env=env)

    def test_validate_cli(self):
        r = self._cli("validate", str(DEMO))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_build_smoke(self):
        import shutil
        with tempfile.TemporaryDirectory() as td:
            shutil.copytree(str(gse.ROOT / "engine"), str(Path(td) / "engine"))
            r = self._cli("build", str(DEMO), root=td)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            index = Path(td) / "build" / "demo" / "index.html"
            self.assertTrue(index.is_file())
            html = index.read_text(encoding="utf-8")
            self.assertIn("The Staple Pitch", html)

    def test_selftest(self):
        r = self._cli("selftest")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("selftest PASS", r.stdout)

    def test_edit_api_roundtrip(self):
        """Exercise editor REST endpoints against an isolated app root."""
        r = self._cli("selftest")
        self.assertEqual(r.returncode, 0)
        self.assertIn("POST /api/story saves story", r.stdout)

    def test_download_cli(self):
        """`gse download` fetches a remote URL into media/ and rewrites the story."""
        import base64
        import http.server
        import threading
        import socket
        tiny_png = base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk"
            "+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==")

        class Srv(http.server.BaseHTTPRequestHandler):
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
            port = s.getsockname()[1]
        srv = http.server.ThreadingHTTPServer(("127.0.0.1", port), Srv)
        t = threading.Thread(target=srv.serve_forever, daemon=True)
        t.start()
        try:
            with tempfile.TemporaryDirectory() as td:
                dl = Path(td) / "dl" / "dl.gse"
                dl.parent.mkdir(parents=True)
                dl.write_text(''':: config
{
  "title": "Download Test",
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
Hello.
''' % port, encoding="utf-8")
                r = self._cli("download", str(dl))
                self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
                self.assertIn("1 downloaded", r.stdout)
                final = dl.read_text(encoding="utf-8")
                self.assertNotIn("http://", final)
                media = list((dl.parent / "media").iterdir())
                self.assertEqual(len(media), 1)
                self.assertEqual(media[0].read_bytes(), tiny_png)
        finally:
            srv.shutdown()
            t.join(timeout=5)


class ImageGenerationTests(unittest.TestCase):
    """Offline checks for the Pollinations.ai generation helpers."""

    def test_slug(self):
        self.assertEqual(gse._slug("A plain desk, ready!"), "a-plain-desk-ready")
        self.assertEqual(gse._slug("  ...  "), "image")
        self.assertEqual(gse._slug("Upper CASE 1"), "upper-case-1")

    def test_pollination_url(self):
        url = gse._pollination_url("a quiet street", width=768, height=512)
        self.assertTrue(url.startswith(gse.POLLINATION_URL + "a%20quiet%20street"))
        self.assertIn("width=768", url)
        self.assertIn("height=512", url)
        self.assertIn("nologo=true", url)
        self.assertNotIn("seed", url)
        url2 = gse._pollination_url("a", seed=42, model="turbo")
        self.assertIn("seed=42", url2)
        self.assertIn("model=turbo", url2)

    def test_next_name_collision(self):
        with tempfile.TemporaryDirectory() as td:
            md = Path(td)
            (md / "scene.png").write_bytes(b"x")
            self.assertEqual(gse._next_name(md, "scene"), "scene-2.png")
            self.assertEqual(gse._next_name(md, "other"), "other.png")

    def test_plan_generations(self):
        with tempfile.TemporaryDirectory() as td:
            md = Path(td)
            story = {
                "config": {"characters": {}},
                "passages": {
                    "start": {"media": [
                        {"path": "desk.png", "caption": "A plain desk."},
                        {"path": "ready-to-go.png", "caption": ""},
                        {"path": "https://example.com/remote.png", "caption": "skip me"},
                        {"path": "nested/room.png", "caption": "skip me too"},
                    ]},
                },
            }
            plans = gse._plan_generations(story, md)
            self.assertEqual(len(plans), 2)
            self.assertEqual(plans[0]["orig"], "desk.png")
            self.assertEqual(plans[0]["prompt"], "A plain desk.")
            self.assertEqual(plans[1]["prompt"], "start")
            (md / "desk.png").write_bytes(b"x")
            self.assertEqual(len(gse._plan_generations(story, md)), 1)
            self.assertEqual(len(gse._plan_generations(story, md, force=True)), 2)

    def test_image_url_encoding_of_prompt(self):
        url = gse._pollination_url("a & b? c", seed=3)
        self.assertIn("a%20%26%20b%3F%20c", url)
        self.assertIn("seed=3", url)

    def test_horde_body(self):
        body = gse._horde_body("a sunny meadow", width=768, height=512)
        self.assertEqual(body["params"]["width"], 768)
        self.assertEqual(body["params"]["height"], 512)
        self.assertEqual(body["params"]["steps"], 25)
        self.assertIn("models", body)
        self.assertIn("prompt", body)
        self.assertEqual(body["prompt"], "a sunny meadow")
        body2 = gse._horde_body("a", seed=9, model="JuggernautXL")
        self.assertEqual(body2["params"]["seed"], 9)
        self.assertEqual(body2["models"], ["JuggernautXL"])

    def test_provider_dispatch(self):
        calls = []
        def fake_horde(prompt, width, height, seed, model, timeout):
            calls.append(("horde", prompt, width, height, seed, model, timeout))
            return b"horde-bytes"
        def fake_poll(prompt, width, height, seed, model, timeout):
            calls.append(("pollinations", prompt, width, height, seed, model, timeout))
            return b"poll-bytes"
        def fake_cf(prompt, width, height, seed, model, timeout):
            calls.append(("cf", prompt, width, height, seed, model, timeout))
            return b"cf-bytes"
        orig_h, orig_p, orig_c = gse._horde_image, gse._pollinations_image, gse._cf_image
        gse._horde_image, gse._pollinations_image, gse._cf_image = fake_horde, fake_poll, fake_cf
        try:
            self.assertEqual(gse._generate_image("x"), b"cf-bytes")
            self.assertEqual(calls[0][0], "cf")
            self.assertEqual(gse._generate_image("x", provider="pollinations"), b"poll-bytes")
            self.assertEqual(gse._generate_image("x", provider="horde"), b"horde-bytes")
        finally:
            gse._horde_image, gse._pollinations_image, gse._cf_image = orig_h, orig_p, orig_c

    def test_sniff_ext(self):
        self.assertEqual(gse._sniff_ext(b"\xff\xd8\xff\xe0\x00\x10JFIF"), ".jpg")
        self.assertEqual(gse._sniff_ext(b"\x89PNG\r\n\x1a\n\x00\x00\x00\r"), ".png")
        self.assertEqual(gse._sniff_ext(b"RIFF\x00\x00\x00\x00WEBP"), ".webp")
        self.assertEqual(gse._sniff_ext(b"GIF89a"), ".gif")
        self.assertEqual(gse._sniff_ext(b"nope"), "")

    def test_cf_body(self):
        body = gse._cf_body("a sunny meadow", width=768, height=512)
        self.assertEqual(body["width"], 768)
        self.assertEqual(body["height"], 512)
        self.assertEqual(body["prompt"], "a sunny meadow")
        self.assertNotIn("seed", body)
        body2 = gse._cf_body("a", seed=99)
        self.assertEqual(body2["seed"], 99)
        bare = gse._cf_body("a sunny meadow", None, None)
        self.assertNotIn("width", bare)
        self.assertNotIn("height", bare)
        self.assertNotIn("steps", bare)

    def test_cf_mp_multipart(self):
        payload, boundary = gse._cf_mp({"prompt": "a", "steps": 4})
        self.assertIn(boundary.encode(), payload)
        self.assertTrue(payload.startswith(b"--" + boundary.encode()))
        self.assertTrue(payload.rstrip().endswith(b"--" + boundary.encode() + b"--"))
        self.assertIn(b'name="json"', payload)
        self.assertIn(b'"prompt": "a"', payload)

    def test_load_cred_env_override_and_file(self):
        import unittest.mock as mock
        with tempfile.TemporaryDirectory() as td:
            cred_file = Path(td) / "creds.json"
            cred_file.write_text(json.dumps({"CF_API_TOKEN": "file-token",
                                             "CF_ACCOUNT_ID": "file-account"}), encoding="utf-8")
            with mock.patch.dict(os.environ, {"GSE_CREDENTIALS": str(cred_file)}, clear=False):
                self.assertEqual(gse._load_cred("CF_ACCOUNT_ID"), "file-account")
            with mock.patch.dict(os.environ, {"GSE_CREDENTIALS": str(cred_file), "CF_API_TOKEN": "env-token"}, clear=False):
                self.assertEqual(gse._load_cred("CF_API_TOKEN"), "env-token")
            self.assertEqual(gse._load_cred("UNKNOWN_KEY"), "")


if __name__ == "__main__":
    unittest.main(verbosity=2)
