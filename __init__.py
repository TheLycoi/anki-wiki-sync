# -*- coding: utf-8 -*-

"""
Anki Wiki Sync

Syncs Anki decks into an Obsidian vault as a compact, queryable wiki: one
page per deck, every card a linkable block. The add-on owns its target
folder and never writes outside it, and never modifies the collection.
"""

import os
import sys
import time
import traceback

addon_path = os.path.dirname(__file__)
vendor_path = os.path.join(addon_path, "vendor")
if vendor_path not in sys.path: sys.path.insert(0, vendor_path)

missing_deps = []
try: import yaml
except ImportError: missing_deps.append("PyYAML")
try: from markdownify import markdownify
except ImportError: missing_deps.append("markdownify")

if missing_deps:
    from aqt.utils import showCritical
    showCritical(f"Missing Dependencies: {', '.join(missing_deps)}")

from aqt import mw
from aqt.qt import QAction, QMenu, qconnect
from aqt.utils import showInfo, showWarning

from .config import get_obsidian_path
from .state_builder import build_anki_state, build_obsidian_state
from .diff_calculator import calculate_diff
from .executor import (
    build_deck_records, execute_sync, compute_target_rel, today_iso,
    INDEX_FILENAME, SCHEMA_FILENAME, BASE_FILENAME,
)
from .wiki_schema import find_vault_root
from .config_ui import show_config_dialog


def sync_to_obsidian():
    obsidian_path = get_obsidian_path()
    if not obsidian_path:
        showWarning("Sync target folder not configured. Please set it via Tools > Anki Wiki Sync > Configure...")
        return

    start_time = time.time()
    mw.progress.start(label="Starting Anki Wiki Sync...", immediate=True)

    try:
        anki_state = build_anki_state(mw.col)
        obsidian_state = build_obsidian_state(obsidian_path)
        base_path = obsidian_state["base_path"]

        target_rel = compute_target_rel(base_path)
        decks, expected, slugs = build_deck_records(
            anki_state, find_vault_root(base_path), target_rel)

        actions = calculate_diff(anki_state, obsidian_state, expected)

        total_cards = sum(len(d.cards) for d in decks)
        # The static pages are not part of the diff, so a folder missing one
        # of them still needs a run even when every deck page is current.
        statics_present = all(
            (base_path / name).is_file()
            for name in (INDEX_FILENAME, SCHEMA_FILENAME, BASE_FILENAME))

        if not actions["changed"] and statics_present:
            mw.progress.finish()
            showInfo(
                f"Anki Wiki Sync: nothing changed. "
                f"{len(decks)} deck(s), {total_cards} card(s)."
            )
            return

        counts = execute_sync(actions, expected, decks, slugs, obsidian_state,
                              target_rel, today_iso())

        mw.progress.finish()
        showInfo(
            f"Anki Wiki Sync finished in {time.time() - start_time:.2f} seconds.\n\n"
            f"{len(decks)} deck(s), {counts['cards']} card(s).\n"
            f"Pages: {counts['pages_written']} written, "
            f"{counts['pages_deleted']} deleted.\n"
            f"Assets copied: {counts['assets_copied']}."
        )
    except Exception as e:
        mw.progress.finish()
        print(traceback.format_exc())
        showWarning(f"Anki Wiki Sync failed.\nError: {e}\n\nSee console or debug log for details.")


def add_menu_items():
    if not hasattr(mw, "menuAnkiWikiSync"):
        mw.menuAnkiWikiSync = QMenu("Anki Wiki Sync", mw)
        mw.form.menuTools.addMenu(mw.menuAnkiWikiSync)

    sync_action = QAction("Sync Now", mw)
    qconnect(sync_action.triggered, sync_to_obsidian)
    mw.menuAnkiWikiSync.addAction(sync_action)

    config_action = QAction("Configure...", mw)
    qconnect(config_action.triggered, show_config_dialog)
    mw.menuAnkiWikiSync.addAction(config_action)


add_menu_items()
