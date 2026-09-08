# -*- coding: utf-8 -*-

"""Stub-runtime smoke test for the deck-centric sync (no Anki required).

Fakes the `aqt` and `anki` modules, feeds a fixture Anki state through the
real pipeline into a temporary vault, and asserts the resulting folder.

Run: python3 test_sync_stub.py
"""

import importlib.util
import os
import shutil
import sys
import tempfile
import types
import unittest

REPO = os.path.dirname(os.path.abspath(__file__))
VENDOR = os.path.join(REPO, "vendor")
if VENDOR not in sys.path:
    sys.path.insert(0, VENDOR)

MEDIA_DIR = tempfile.mkdtemp(prefix="anki-wiki-sync-media-")


# --------------------------------------------------------------------------
# Fake Anki runtime, installed before the package is imported
# --------------------------------------------------------------------------

class _FakeProgress(object):
    def start(self, *args, **kwargs):
        pass

    def update(self, *args, **kwargs):
        pass

    def finish(self, *args, **kwargs):
        pass


class _FakeMedia(object):
    def dir(self):
        return MEDIA_DIR


class _FakeDB(object):
    def scalar(self, *args, **kwargs):
        return None


class _FakeCol(object):
    def __init__(self):
        self.media = _FakeMedia()
        self.db = _FakeDB()


class _FakeForm(object):
    class _Menu(object):
        def addMenu(self, *args, **kwargs):
            pass

    def __init__(self):
        self.menuTools = self._Menu()


class _FakeMW(object):
    def __init__(self):
        self.progress = _FakeProgress()
        self.col = _FakeCol()
        self.form = _FakeForm()


class _FakeAction(object):
    def __init__(self, *args, **kwargs):
        self.triggered = None


class _FakeMenu(object):
    def __init__(self, *args, **kwargs):
        pass

    def addAction(self, *args, **kwargs):
        pass


def _install_fakes():
    aqt = types.ModuleType("aqt")
    aqt.mw = _FakeMW()

    aqt_qt = types.ModuleType("aqt.qt")
    aqt_qt.QAction = _FakeAction
    aqt_qt.QMenu = _FakeMenu
    aqt_qt.qconnect = lambda *args, **kwargs: None
    # config_ui imports a wide set of Qt names at module scope.
    for name in ("QDialog", "QVBoxLayout", "QHBoxLayout", "QLabel", "QLineEdit",
                 "QPushButton", "QFileDialog", "QListWidget", "QCheckBox",
                 "QComboBox", "QWidget", "QGroupBox", "QListWidgetItem",
                 "QAbstractItemView", "Qt", "QSizePolicy", "QMessageBox",
                 "QTextEdit", "QSpinBox", "QFormLayout", "QDialogButtonBox"):
        setattr(aqt_qt, name, type(name, (object,), {}))

    aqt_utils = types.ModuleType("aqt.utils")
    aqt_utils.showInfo = lambda *a, **k: None
    aqt_utils.showWarning = lambda *a, **k: None
    aqt_utils.showCritical = lambda *a, **k: None
    aqt_utils.tooltip = lambda *a, **k: None
    aqt.utils = aqt_utils
    aqt.qt = aqt_qt

    anki = types.ModuleType("anki")
    anki_collection = types.ModuleType("anki.collection")
    anki_collection.Collection = type("Collection", (object,), {})
    anki_notes = types.ModuleType("anki.notes")
    anki_notes.Note = type("Note", (object,), {})
    anki.collection = anki_collection
    anki.notes = anki_notes

    sys.modules["aqt"] = aqt
    sys.modules["aqt.qt"] = aqt_qt
    sys.modules["aqt.utils"] = aqt_utils
    sys.modules["anki"] = anki
    sys.modules["anki.collection"] = anki_collection
    sys.modules["anki.notes"] = anki_notes
    return aqt


FAKE_AQT = _install_fakes()


def _load_package():
    """Import the repo as a package so its relative imports resolve."""
    spec = importlib.util.spec_from_file_location(
        "anki_wiki_sync", os.path.join(REPO, "__init__.py"),
        submodule_search_locations=[REPO])
    module = importlib.util.module_from_spec(spec)
    sys.modules["anki_wiki_sync"] = module
    spec.loader.exec_module(module)
    return module


PKG = _load_package()
from anki_wiki_sync import diff_calculator, executor, state_builder, wiki_format  # noqa: E402


# --------------------------------------------------------------------------
# Fixture
# --------------------------------------------------------------------------

BASIC_FIELDS = {
    "Front": "What is <b>decisional balance</b>?",
    "Back": "Weighing the pros against the cons of changing.",
}
CLOZE_FIELDS = {
    "Text": "Precontemplation: the client has {{c1::no intention of changing}} "
            "in the foreseeable future.",
    "Extra": "Shares benefits only with permission.",
}
TABLE_FIELDS = {
    "Front": "Compare the stages.",
    "Back": "<table>\n<tr><th>Stage</th><th>Action</th></tr>\n"
            "<tr><td>Contemplation</td><td>Weigh options</td></tr>\n</table>",
}
IMAGE_FIELDS = {
    "Front": "Identify this diagram.",
    "Back": 'The change cycle. <img src="pic.png">',
}


def _note(nid, fields, note_type, tags, mod=1000, lapses=0, ease=2500):
    return {
        "note_id": nid,
        "card_id": nid * 10,
        "note_mod_time": mod,
        "note_type_name": note_type,
        "relevant_fields": dict(fields),
        "required_images": set(["pic.png"]) if "pic.png" in str(fields) else set(),
        "card_ids": [nid * 10],
        "tags": list(tags),
        "card_reps": 3,
        "card_lapses": lapses,
        "card_ivl": 10,
        "card_due": 5,
        "card_ease": ease,
        "card_queue": 2,
    }


def build_fixture_state():
    """Three decks, six notes, shaped like `build_anki_state` output."""
    module1_notes = [
        _note(1725000000001, BASIC_FIELDS, "Basic", ["phed163"]),
        _note(1725000000002, TABLE_FIELDS, "Basic", ["phed163", "stages"]),
        _note(1725000000003, IMAGE_FIELDS, "Basic", []),
    ]
    module3_notes = [
        _note(1725000000004, CLOZE_FIELDS, "Cloze",
              ["phed163", "wiki::stages-of-change"], lapses=3),
        _note(1725000000005, BASIC_FIELDS, "Basic", ["stages"], lapses=2),
        _note(1725000000006, TABLE_FIELDS, "Basic", ["marked"]),
    ]
    return {
        "_root_": {"anki_deck_id": None, "anki_deck_name": "Anki Collection",
                   "anki_deck_full_name": "", "notes": {},
                   "subdeck_paths": set(["PHED163"])},
        "PHED163": {
            "anki_deck_id": 101, "anki_deck_name": "PHED163",
            "anki_deck_full_name": "PHED163", "sanitized_deck_name": "PHED163",
            "notes": {}, "subdeck_paths": set(["PHED163/Module 1", "PHED163/Module 3"]),
        },
        "PHED163/Module 1": {
            "anki_deck_id": 102, "anki_deck_name": "Module 1",
            "anki_deck_full_name": "PHED163::Module 1",
            "sanitized_deck_name": "Module 1",
            "notes": dict((n["note_id"], n) for n in module1_notes),
            "subdeck_paths": set(),
        },
        "PHED163/Module 3": {
            "anki_deck_id": 103, "anki_deck_name": "Module 3",
            "anki_deck_full_name": "PHED163::Module 3",
            "sanitized_deck_name": "Module 3",
            "notes": dict((n["note_id"], n) for n in module3_notes),
            "subdeck_paths": set(),
        },
    }


README_TEXT = """---
type: anki-card
deck: PHED163
---

# Hand-written note

This file predates the sync and must survive it.
"""


def run_pipeline(anki_state, target_dir):
    """The real pipeline, minus the menu and the dialogs."""
    obsidian_state = state_builder.build_obsidian_state(target_dir)
    base_path = obsidian_state["base_path"]
    target_rel = executor.compute_target_rel(base_path)
    from anki_wiki_sync.wiki_schema import find_vault_root
    decks, expected, slugs = executor.build_deck_records(
        anki_state, find_vault_root(base_path), target_rel)
    actions = diff_calculator.calculate_diff(anki_state, obsidian_state, expected)
    counts = executor.execute_sync(actions, expected, decks, slugs,
                                   obsidian_state, target_rel,
                                   executor.today_iso())
    return actions, counts


class SyncStubTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="anki-wiki-sync-vault-")
        self.vault = os.path.join(self.tmp, "vault")
        self.target = os.path.join(self.vault, "anki")
        os.makedirs(os.path.join(self.vault, ".obsidian"))
        os.makedirs(os.path.join(self.vault, "wiki", "concepts"))
        os.makedirs(os.path.join(self.vault, "classes", "PHED163"))
        os.makedirs(self.target)

        self._write(os.path.join(self.vault, "wiki", "concepts",
                                 "stages-of-change.md"),
                    "---\ntype: concept\n---\n\n# Stages of change\n")
        self._write(os.path.join(self.vault, "classes", "PHED163",
                                 "Stages of Change.md"),
                    "---\nclass: PHED163\n---\n\n# Stages of change\n")
        self._write(os.path.join(self.target, "README.md"), README_TEXT)

        with open(os.path.join(MEDIA_DIR, "pic.png"), "wb") as fh:
            fh.write(b"\x89PNG\r\n\x1a\n fake")

        self.state = build_fixture_state()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    @staticmethod
    def _write(path, text):
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)

    def _read(self, name):
        with open(os.path.join(self.target, name), "r", encoding="utf-8") as fh:
            return fh.read()

    def _snapshot(self):
        """Every file under the target folder as path -> bytes and mtime."""
        out = {}
        for root, _dirs, files in os.walk(self.target):
            for name in files:
                full = os.path.join(root, name)
                rel = os.path.relpath(full, self.target)
                with open(full, "rb") as fh:
                    out[rel] = (fh.read(), os.stat(full).st_mtime_ns)
        return out

    def test_full_lifecycle(self):
        # --- Run 1: a fresh folder gets the whole layout ---
        actions, counts = run_pipeline(self.state, self.target)
        self.assertTrue(actions["changed"])
        for name in ("phed163.md", "phed163-module-1.md", "phed163-module-3.md",
                     "index.md", "schema.md", "log.md", "decks.base",
                     os.path.join("assets", "pic.png")):
            self.assertTrue(os.path.isfile(os.path.join(self.target, name)),
                            "missing after first run: %s" % name)

        module3 = self._read("phed163-module-3.md")
        fm = wiki_format.parse_frontmatter(module3)
        self.assertEqual(fm["deck"], "PHED163::Module 3")
        self.assertEqual(fm["parent"], "[[phed163]]")
        self.assertIn("[[wiki/concepts/stages-of-change]]", fm["topics"])
        self.assertIn("[[classes/PHED163/Stages of Change]]", fm["topics"])

        # The cloze card keeps its highlight and ends with its block id.
        # Card lines carry the anki:// link; the Weak spots entry quotes the
        # same text without one, so match on the link to isolate the card.
        cloze_lines = [ln for ln in module3.splitlines()
                       if "no intention of changing" in ln and "anki://" in ln]
        self.assertEqual(len(cloze_lines), 1)
        self.assertIn("==", cloze_lines[0])
        self.assertTrue(cloze_lines[0].endswith("^n1725000000004"),
                        cloze_lines[0])

        # A table stays on one line so it cannot break the list.
        module1 = self._read("phed163-module-1.md")
        table_lines = [ln for ln in module1.splitlines()
                       if "<table>" in ln and "anki://" in ln]
        self.assertEqual(len(table_lines), 1)
        self.assertIn("</table>", table_lines[0])
        self.assertTrue(table_lines[0].endswith("^n1725000000002"))

        # A file the sync does not own is untouched.
        self.assertEqual(self._read("README.md"), README_TEXT)

        # --- Run 2: nothing changed, nothing rewritten ---
        before = self._snapshot()
        actions2, _ = run_pipeline(self.state, self.target)
        self.assertFalse(actions2["changed"])
        self.assertEqual(self._snapshot(), before,
                         "an unchanged sync rewrote something")

        # --- Run 3: a deck leaves the collection ---
        del self.state["PHED163/Module 1"]
        self.state["PHED163"]["subdeck_paths"].discard("PHED163/Module 1")
        actions3, counts3 = run_pipeline(self.state, self.target)
        self.assertTrue(actions3["changed"])
        self.assertFalse(os.path.isfile(
            os.path.join(self.target, "phed163-module-1.md")))
        self.assertEqual(counts3["pages_deleted"], 1)
        for name in ("README.md", "phed163.md", "phed163-module-3.md"):
            self.assertTrue(os.path.isfile(os.path.join(self.target, name)),
                            "run 3 removed %s" % name)
        self.assertEqual(self._read("README.md"), README_TEXT)
        self.assertNotIn("phed163-module-1", self._read("index.md"))

        log_entries = [ln for ln in self._read("log.md").splitlines()
                       if ln.startswith("- ")]
        self.assertEqual(len(log_entries), 2, log_entries)

        # --- Run 4: one edited note rewrites only its own deck page ---
        before = self._snapshot()
        self.state["PHED163/Module 3"]["notes"][1725000000004]["note_mod_time"] = 2000
        actions4, counts4 = run_pipeline(self.state, self.target)
        self.assertEqual(actions4["pages_to_write"], ["phed163-module-3"])
        self.assertEqual(counts4["pages_written"], 1)
        after = self._snapshot()
        changed = sorted(k for k in after
                         if k not in before or after[k][0] != before[k][0])
        # The deck page changes; the log gains a line. Nothing else moves.
        self.assertEqual(changed, ["log.md", "phed163-module-3.md"], changed)


if __name__ == "__main__":
    unittest.main(verbosity=2)
