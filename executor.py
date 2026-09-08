# -*- coding: utf-8 -*-

"""
Turns the Anki state into deck pages and writes them into the target folder.

Two halves. `build_deck_records` converts the raw collection state into the
`DeckRecord`/`CardRecord` shapes `wiki_format` renders from, resolving each
deck's topics once per sync. `execute_sync` performs the actions the diff
asked for: delete stale pages, reconcile assets, write changed deck pages,
then refresh the index, schema and base only when their text actually
differs, and append one line to the log.

Every path written or deleted is inside the configured target folder.
"""

import shutil
from pathlib import Path
from datetime import date, datetime
from typing import Any, Dict, List, Optional, Set, Tuple

# Anki imports
from aqt import mw

# Local imports
from .html_converter import convert_html_to_markdown
from . import wiki_format
from .wiki_format import CardRecord, DeckRecord
from .wiki_schema import WikiLinkResolver, TopicNoteIndex, find_vault_root

ASSETS_FOLDER = "assets"
INDEX_FILENAME = "index.md"
SCHEMA_FILENAME = "schema.md"
LOG_FILENAME = "log.md"
BASE_FILENAME = "decks.base"


# --- Helpers ---

def ensure_dir_exists(dir_path: Path):
    """Creates a directory if it doesn't exist."""
    dir_path.mkdir(parents=True, exist_ok=True)


def today_iso() -> str:
    """The local date, as the `updated` frontmatter value."""
    return date.today().isoformat()


def compute_target_rel(base_path: Path) -> str:
    """The target folder relative to its vault root, in posix form.

    Used by the schema page and the base view to scope their queries. With
    no vault root above the folder, its own name is the best available
    answer.
    """
    vault_root = find_vault_root(base_path)
    if vault_root is None:
        return base_path.name
    try:
        return base_path.resolve().relative_to(vault_root).as_posix().strip("/")
    except ValueError:
        return base_path.name


def copy_required_media(
    required_media: Set[str], media_to_copy_set: Set[str],
    anki_media_path: str, obsidian_assets_path: Path):
    """Copies all required media files (images, audio, video, etc.) to Obsidian assets folder."""
    if not required_media: return
    ensure_dir_exists(obsidian_assets_path)
    for media_filename in required_media:
        if media_filename not in media_to_copy_set: continue
        source_path = Path(anki_media_path) / media_filename
        dest_path = obsidian_assets_path / media_filename
        if source_path.is_file():
            if not dest_path.exists():
                try:
                    shutil.copy2(source_path, dest_path)
                    print(f"Copied media file: {media_filename}")
                except Exception as e:
                    print(f"Error copying media {media_filename}: {e}")
            media_to_copy_set.discard(media_filename)
        else:
            print(f"Warning: Source media not found in Anki media: {source_path}")
            media_to_copy_set.discard(media_filename)


# Backwards compatibility alias
copy_required_images = copy_required_media


def _is_inside(base_path: Path, candidate: Path) -> bool:
    """True when `candidate` resolves to something under `base_path`.

    The sync owns its target folder and nothing else, so every delete is
    gated on this. A symlink pointing out of the folder resolves out of it
    and is refused.
    """
    try:
        candidate.resolve().relative_to(base_path.resolve())
        return True
    except (ValueError, OSError):
        return False


def _write_if_different(path: Path, text: str) -> bool:
    """Write `text` to `path` only when it would change the file.

    Keeps a no-op sync byte-identical on disk, mtimes included, which is
    what makes "nothing changed" observable from the vault side.
    """
    try:
        if path.is_file():
            with open(path, "r", encoding="utf-8") as fh:
                if fh.read() == text:
                    return False
    except (OSError, UnicodeError):
        pass
    ensure_dir_exists(path.parent)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    return True


# --- Record building ---

def build_deck_records(anki_state: Dict[str, Any], vault_root: Optional[Path],
                       target_rel: str) -> Tuple[List[DeckRecord], Dict[str, Any], Dict[str, str]]:
    """Convert the collection state into renderable deck records.

    Field HTML is converted per field so `wiki_format` can lay the fields
    out itself; the note type's field order is preserved. The wiki link
    resolver and the topic index each scan the vault, so both are built
    once here and shared across every deck.

    Returns the deck records, the per-slug render plan the diff compares
    against, and the deck name to slug map the pages link through.
    """
    entries = []
    for deck_path, deck_data in anki_state.items():
        if deck_path == "_root_":
            continue
        full_name = deck_data.get("anki_deck_full_name")
        if not full_name:
            continue
        entries.append((full_name, deck_data))

    synced_names = set(name for name, _ in entries)

    resolver = WikiLinkResolver(vault_root)
    topic_index = TopicNoteIndex(vault_root, exclude_rel=[target_rel])

    decks = []  # type: List[DeckRecord]
    media_by_name = {}  # type: Dict[str, Set[str]]
    tags_by_name = {}  # type: Dict[str, List[str]]

    for full_name, deck_data in entries:
        cards = []
        required_media = set()
        deck_tags = []
        for note_data in deck_data.get("notes", {}).values():
            fields_md = {}
            for field_name, value in note_data.get("relevant_fields", {}).items():
                fields_md[field_name] = convert_html_to_markdown(value)
            tags = list(note_data.get("tags", []))
            deck_tags.extend(tags)
            required_media.update(note_data.get("required_images", set()))
            cards.append(CardRecord(
                nid=note_data["note_id"],
                cid=note_data["card_id"],
                note_type=note_data.get("note_type_name", ""),
                fields_md=fields_md,
                tags=tags,
                reps=note_data.get("card_reps", 0),
                lapses=note_data.get("card_lapses", 0),
                ease=note_data.get("card_ease", 0),
                queue=note_data.get("card_queue", 0),
                mod=note_data.get("note_mod_time", 0),
            ))

        # Synced children, by full name: one :: segment deeper than this deck.
        subdeck_names = sorted(
            name for name in synced_names
            if name.startswith(full_name + "::")
            and "::" not in name[len(full_name) + 2:]
        )
        parent_name = None
        if "::" in full_name:
            candidate = full_name.rsplit("::", 1)[0]
            if candidate in synced_names:
                parent_name = candidate

        decks.append(DeckRecord(
            name=full_name,
            deck_id=deck_data.get("anki_deck_id"),
            cards=cards,
            subdeck_names=subdeck_names,
            parent_name=parent_name,
        ))
        media_by_name[full_name] = required_media
        tags_by_name[full_name] = deck_tags

    slugs = wiki_format.assign_slugs(decks)

    expected = {}
    for deck in decks:
        topics = set(resolver.links_for_tags(tags_by_name[deck.name]))
        topics.update(topic_index.topics_for_deck(deck.name))
        # Sorted and deduplicated here so the signature matches the one
        # `render_deck_page` recomputes from the same list.
        topic_list = sorted(topics)
        expected[slugs[deck.name]] = {
            "deck": deck,
            "topics": topic_list,
            "signature": wiki_format.compute_signature(deck, topic_list),
            "required_media": media_by_name[deck.name],
        }

    return decks, expected, slugs


# --- Execution ---

def execute_sync(actions: Dict[str, Any], expected: Dict[str, Any],
                 decks: List[DeckRecord], slugs: Dict[str, str],
                 obsidian_state: Dict[str, Any], target_rel: str,
                 today: str) -> Dict[str, int]:
    """Apply the diff to the target folder and report what was done."""
    base_path = obsidian_state["base_path"]
    assets_abs_path = base_path / ASSETS_FOLDER
    ensure_dir_exists(base_path)

    pages_deleted = 0
    pages_to_delete = actions.get("pages_to_delete", [])
    if pages_to_delete:
        mw.progress.start(label="Deleting stale deck pages...",
                          max=len(pages_to_delete), immediate=True)
        for i, slug in enumerate(pages_to_delete):
            page = obsidian_state.get("pages", {}).get(slug, {})
            abs_path = page.get("abs_path") or (base_path / (slug + ".md"))
            try:
                if not _is_inside(base_path, Path(abs_path)):
                    print(f"Refusing to delete outside the target folder: {abs_path}")
                elif Path(abs_path).is_file():
                    Path(abs_path).unlink()
                    pages_deleted += 1
            except OSError as e:
                print(f"Error deleting deck page {slug}: {e}")
            mw.progress.update(label=f"Deleting: {slug}", value=i + 1)
        mw.progress.finish()

    images_to_copy = set(actions.get("images_to_copy", set()))
    assets_copied = len(images_to_copy)
    if images_to_copy:
        anki_media_path = mw.col.media.dir()
        copy_required_media(set(images_to_copy), images_to_copy,
                            anki_media_path, assets_abs_path)
        assets_copied -= len(images_to_copy)

    for filename in actions.get("images_to_delete", set()):
        abs_path = assets_abs_path / filename
        try:
            if _is_inside(base_path, abs_path) and abs_path.is_file():
                abs_path.unlink()
        except OSError as e:
            print(f"Error deleting asset {filename}: {e}")

    pages_to_write = actions.get("pages_to_write", [])
    pages_written = 0
    if pages_to_write:
        mw.progress.start(label="Writing deck pages...",
                          max=len(pages_to_write), immediate=True)
        for i, slug in enumerate(pages_to_write):
            plan = expected.get(slug)
            if not plan:
                continue
            text = wiki_format.render_deck_page(
                plan["deck"], slugs, plan["topics"], today)
            try:
                _write_if_different(base_path / (slug + ".md"), text)
                pages_written += 1
            except OSError as e:
                print(f"Error writing deck page {slug}: {e}")
            mw.progress.update(label=f"Writing: {slug}", value=i + 1)
        mw.progress.finish()

    index_path = base_path / INDEX_FILENAME
    if actions.get("changed") or not index_path.is_file():
        try:
            _write_if_different(index_path, wiki_format.render_index(decks, slugs, today))
        except OSError as e:
            print(f"Error writing index: {e}")

    # Static pages: created on the first sync, rewritten on a schema bump,
    # left alone otherwise.
    try:
        _write_if_different(base_path / SCHEMA_FILENAME,
                            wiki_format.render_schema_page(target_rel))
        _write_if_different(base_path / BASE_FILENAME,
                            wiki_format.render_base(target_rel))
    except OSError as e:
        print(f"Error writing static pages: {e}")

    total_cards = sum(len(d.cards) for d in decks)
    if actions.get("changed"):
        stamp = datetime.now().strftime("%Y-%m-%dT%H:%M")
        line = ("- %s sync: %d page(s) written, %d deleted, %d cards, "
                "%d assets copied" % (stamp, pages_written, pages_deleted,
                                      total_cards, assets_copied))
        log_path = base_path / LOG_FILENAME
        existing = ""
        try:
            if log_path.is_file():
                with open(log_path, "r", encoding="utf-8") as fh:
                    existing = fh.read()
        except (OSError, UnicodeError):
            existing = ""
        try:
            with open(log_path, "w", encoding="utf-8") as fh:
                fh.write(wiki_format.append_log_line(existing, line))
        except OSError as e:
            print(f"Error writing log: {e}")

    return {
        "pages_written": pages_written,
        "pages_deleted": pages_deleted,
        "cards": total_cards,
        "assets_copied": assets_copied,
    }
