# -*- coding: utf-8 -*-

"""
Compares what the sync would render against what the target folder holds
and returns the filesystem actions needed to close the gap.

Deck-centric: the unit of comparison is one deck page, identified by its
slug. A page is rewritten only when it is missing, when its recorded
`sync_signature` differs from the one just computed, or when it carries an
older `schema_version`. Files the sync does not own are never in play.
"""

from typing import Any, Dict

from . import wiki_format


def calculate_diff(anki_state: Dict[str, Any], obsidian_state: Dict[str, Any],
                   expected: Dict[str, Any]) -> Dict[str, Any]:
    """Actions to reconcile the expected deck pages with what is on disk.

    `expected` is the executor's precomputed render plan, mapping slug to
    {"deck", "topics", "signature", "required_media"}, and
    `obsidian_state["pages"]` is what the folder currently says. The index,
    schema, log and base pages are not diffed here: the executor rewrites
    those on a plain content difference.
    """
    on_disk = obsidian_state.get("pages", {})

    pages_to_write = []
    for slug in sorted(expected):
        current = on_disk.get(slug)
        if current is None:
            pages_to_write.append(slug)
            continue
        if current.get("sync_signature") != expected[slug].get("signature"):
            pages_to_write.append(slug)
            continue
        # A schema bump rewrites every page even when no card moved.
        if current.get("schema_version") != wiki_format.SCHEMA_VERSION:
            pages_to_write.append(slug)

    # Deck pages we no longer expect: the deck left the allowlist, was
    # renamed, or was deleted in Anki. Files without `type: deck` never
    # reach `pages`, so they can never be selected for deletion here.
    pages_to_delete = sorted(set(on_disk) - set(expected))

    required_media = set()
    for slug in expected:
        required_media.update(expected[slug].get("required_media", ()))

    assets = obsidian_state.get("asset_files", set())
    images_to_copy = required_media - assets
    images_to_delete = assets - required_media

    actions = {
        "pages_to_write": pages_to_write,
        "pages_to_delete": pages_to_delete,
        "images_to_copy": images_to_copy,
        "images_to_delete": images_to_delete,
    }
    actions["changed"] = bool(
        pages_to_write or pages_to_delete or images_to_copy or images_to_delete)
    return actions
