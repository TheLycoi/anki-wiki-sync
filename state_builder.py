# -*- coding: utf-8 -*-

"""
Builds representations of the Anki collection state and the Obsidian
filesystem state for the deck-centric wiki sync.

The Anki side is a map of sanitized deck path -> deck entry (id, names,
notes, subdeck paths). The Obsidian side is a map of deck-page slug ->
what that page currently says on disk, read from its frontmatter, plus the
assets it holds and every other file in the folder, which the sync never
touches.
"""

import re
from typing import Any, Dict, Set
from pathlib import Path

from anki.collection import Collection
from anki.notes import Note
from aqt import mw

# Dependency Check
try:
    import yaml
    YAML_AVAILABLE = True
except ImportError:
    yaml = None
    YAML_AVAILABLE = False

from .config import get_excluded_decks, get_include_decks
from .wiki_format import parse_frontmatter

# Constants
INVALID_FILENAME_CHARS = r'[<>:"/\\|?*\x00-\x1f]|(?<!^)\.$|\s$'
REPLACEMENT_CHAR = "_"
MAX_FILENAME_LENGTH = 100
ASSETS_FOLDER = "assets"

def sanitize_filename(name: str) -> str:
    if not name:
        name = "Untitled Anki Note"
    sanitized = re.sub(INVALID_FILENAME_CHARS, REPLACEMENT_CHAR, name)
    sanitized = re.sub(f'{REPLACEMENT_CHAR}+', REPLACEMENT_CHAR, sanitized)
    sanitized = sanitized.strip(REPLACEMENT_CHAR)
    if len(sanitized) > MAX_FILENAME_LENGTH:
        sanitized = sanitized[:MAX_FILENAME_LENGTH].strip().rstrip(REPLACEMENT_CHAR)
    return sanitized or "anki_note"

def get_note_media(note: Note) -> Set[str]:
    media = set()
    img_regex = re.compile(r'<img.*?src=["\'](.*?)["\']', re.IGNORECASE)
    audio_regex = re.compile(r'\[sound:([^\]]+)\]', re.IGNORECASE)
    video_regex = re.compile(r'<video.*?src=["\'](.*?)["\']', re.IGNORECASE | re.DOTALL)
    paste_img_regex = re.compile(r'(paste-[a-f0-9]+\.(?:jpg|jpeg|png|gif|webp|svg))', re.IGNORECASE)
    general_media_regex = re.compile(r'src=["\'](.*?\.(?:jpg|jpeg|png|gif|webp|svg|mp3|mp4|wav|ogg|webm))["\']', re.IGNORECASE)
    
    for field_value in note.values():
        if field_value:
            for match in img_regex.finditer(field_value):
                src = match.group(1)
                if src and not src.startswith(('http:', 'https:', 'data:')): media.add(src)
            for match in audio_regex.finditer(field_value):
                media.add(match.group(1))
            for match in video_regex.finditer(field_value):
                src = match.group(1)
                if src and not src.startswith(('http:', 'https:', 'data:')): media.add(src)
            for match in paste_img_regex.finditer(field_value):
                media.add(match.group(1))
            for match in general_media_regex.finditer(field_value):
                src = match.group(1)
                if src and not src.startswith(('http:', 'https:', 'data:')): media.add(src)
    return media

def build_anki_state(col: Collection) -> Dict[str, Any]:
    anki_state = {"_root_": {"anki_deck_id": None, "anki_deck_name": "Anki Collection",
                             "anki_deck_full_name": "", "notes": {}, "subdeck_paths": set()}}
    deck_map = {}
    deck_parents = {}
    
    excluded_decks = get_excluded_decks()
    include_decks = get_include_decks()
    all_decks = col.decks.all_names_and_ids()

    def is_excluded(deck_name: str) -> bool:
        # Allowlist mode (this fork's default): only listed decks sync.
        # A bare name includes that deck and its subdecks; an empty list, or a
        # list matching nothing, syncs nothing: containment by default.
        if include_decks is not None:
            for inc in include_decks:
                if deck_name == inc or deck_name.startswith(inc + "::"):
                    return False
            return True
        for ex in excluded_decks:
            # Exact match: exclude only this deck (children remain exportable).
            if deck_name == ex:
                return True
            # Explicit subtree wildcard "Parent::" excludes the whole subtree,
            # but a bare "Parent" no longer blocks its subdecks.
            if ex.endswith("::") and deck_name.startswith(ex):
                return True
        return False

    for deck in all_decks:
        # Always register the deck path so notes can resolve their location,
        # even for excluded decks (their non-excluded children still need it).
        parts = deck.name.split("::")
        current_path_parts = []
        parent_id = None
        excluded = is_excluded(deck.name)
        
        for i, part_name in enumerate(parts):
            sanitized_part_name = sanitize_filename(part_name)
            current_path_parts.append(sanitized_part_name)
            sanitized_path = "/".join(current_path_parts)
            
            partial_name = "::".join(parts[:i+1])
            current_deck_id = next((d.id for d in all_decks if d.name == partial_name), None)
            
            if current_deck_id is not None:
                deck_map[current_deck_id] = sanitized_path
                if parent_id is not None: 
                    deck_parents[current_deck_id] = parent_id
                # Only exportable (non-excluded) decks become folders in Obsidian.
                if not excluded and sanitized_path not in anki_state:
                     anki_state[sanitized_path] = {
                        "anki_deck_id": current_deck_id, "anki_deck_name": part_name,
                        # The full Anki name with :: separators is what the deck
                        # pages, their slugs and the parent/subdeck links are
                        # built from, so carry it alongside the leaf name.
                        "anki_deck_full_name": partial_name,
                        "sanitized_deck_name": sanitized_part_name, "notes": {},
                        "subdeck_paths": set()}
                # Link subdecks only between non-excluded decks. If the parent is
                # excluded (not in anki_state), promote the deck to the root level
                # so it stays reachable in the index tree.
                if not excluded:
                    parent_path = deck_map.get(parent_id) if parent_id is not None else None
                    if parent_path and parent_path in anki_state:
                        anki_state[parent_path]["subdeck_paths"].add(sanitized_path)
                    else:
                        anki_state["_root_"]["subdeck_paths"].add(sanitized_path)
                parent_id = current_deck_id

    note_ids = col.find_notes("")
    processed_note_ids = set()
    total_notes = len(note_ids)
    
    mw.progress.start(label="Building Anki State...", max=total_notes, immediate=True)

    for i, nid in enumerate(note_ids):
        if nid in processed_note_ids: continue
        try:
            note = col.get_note(nid)
            note_type = note.note_type()
            if not note_type: continue
            card_ids = note.card_ids()
            if not card_ids: continue
            
            card0 = col.get_card(card_ids[0])
            deck_id = card0.did
            deck_path = deck_map.get(deck_id)

            # Only process if deck hasn't been excluded
            if deck_path and deck_path in anki_state:
                relevant_fields = {f['name']: note[f['name']] for f in note_type['flds']}

                anki_state[deck_path]["notes"][nid] = {
                    "note_id": nid, "card_id": card_ids[0], "note_mod_time": note.mod,
                    "note_type_name": note_type['name'], "relevant_fields": relevant_fields,
                    "required_images": get_note_media(note),
                    "card_ids": card_ids,
                    # Card scheduling metadata, read from the note's first card.
                    "tags": list(note.tags),
                    "card_reps": card0.reps,
                    "card_lapses": card0.lapses,
                    "card_ivl": card0.ivl,
                    "card_due": card0.due,
                    "card_ease": card0.factor,
                    "card_queue": card0.queue,
                }
                processed_note_ids.add(nid)
        except Exception:
            pass
            
        if i % 100 == 0: mw.progress.update(value=i)

    mw.progress.finish()
    return anki_state

def build_obsidian_state(target_dir_str: str) -> Dict[str, Any]:
    """Read what the target folder currently holds.

    Only the top level is scanned. A `.md` file is one of our deck pages
    when its frontmatter says `type: deck`; its slug is the filename stem
    and its `sync_signature` is what the diff compares against. Every other
    top-level file (README, index, schema, log, the base, anything the user
    dropped in) is recorded in `other_files` and is never written or
    deleted. Only `assets/` is descended into.
    """
    base_path = Path(target_dir_str).resolve()
    state = {
        "base_path": base_path,
        "pages": {},
        "asset_files": set(),
        "other_files": set(),
        "assets_folder_rel": ASSETS_FOLDER,
    }  # type: Dict[str, Any]
    if not base_path.is_dir():
        return state

    for entry in sorted(base_path.iterdir()):
        if entry.is_dir():
            continue
        if entry.suffix == ".md":
            frontmatter = {}
            try:
                with open(entry, 'r', encoding='utf-8') as f:
                    frontmatter = parse_frontmatter(f.read())
            except (OSError, UnicodeError):
                frontmatter = {}
            if frontmatter.get("type") == "deck":
                state["pages"][entry.stem] = {
                    "deck": frontmatter.get("deck"),
                    "sync_signature": frontmatter.get("sync_signature"),
                    "schema_version": frontmatter.get("schema_version"),
                    "abs_path": entry,
                }
                continue
        state["other_files"].add(entry.name)

    assets_dir = base_path / ASSETS_FOLDER
    if assets_dir.is_dir():
        for asset in assets_dir.iterdir():
            if asset.is_file():
                state["asset_files"].add(asset.name)

    return state
