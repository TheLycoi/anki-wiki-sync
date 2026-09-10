# -*- coding: utf-8 -*-

"""
Page rendering for Anki Wiki Sync (schema_version 3).

Pure formatting layer: turns deck and card records into the exact markdown
text written into the vault. Imports nothing from `aqt` or `anki`, so every
function here is unit-testable standalone with `python3 test_wiki_format.py`.

The rendering is deterministic. Given the same records, the same slug map,
the same topics and the same date, every function returns byte-identical
text, so a sync with nothing changed rewrites nothing.
"""

import hashlib
import json
import os
import re
import sys
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

try:
    import yaml
except ImportError:  # pragma: no cover - exercised only without a system PyYAML
    _vendor_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "vendor")
    if _vendor_path not in sys.path:
        sys.path.insert(0, _vendor_path)
    import yaml

try:
    from .wiki_schema import slugify
except ImportError:  # standalone (tests, no package context)
    from wiki_schema import slugify

SCHEMA_VERSION = 3

# Tags that carry sync machinery or Anki state rather than subject matter.
# Excluded from the `tags` frontmatter list and from card grouping.
HIDDEN_TAG_PREFIXES = ("wiki::", "source::")
HIDDEN_TAGS = ("marked", "leech")

# Section heading for cards with no display tag. Always sorted last.
UNTAGGED = "untagged"

_DEF_LAPSE_THRESHOLD = 2

# Characters that force double quoting in hand-written YAML scalars.
_YAML_NEEDS_QUOTES = (":", "[", "]", "{", "}", "#", "&", "*", "!", "|", ">",
                      "%", "@", "`", '"', "'", ",")

# YAML reads an unquoted date-shaped scalar as a datetime.date.
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}")


@dataclass
class CardRecord:
    """One Anki card, with its fields already converted to markdown.

    `fields_md` maps the note's field name to converted markdown (cloze
    markup has already become `==text==` upstream). Field order is the
    note type's own order, which `render_card_inline` preserves.
    """

    nid: int
    cid: int
    note_type: str
    fields_md: Dict[str, str]
    tags: List[str]
    reps: int = 0
    lapses: int = 0
    ease: int = 0
    queue: int = 0
    mod: int = 0
    # Ready-made `[[note#^block|Note]]` to the vault note the card came from.
    source_link: str = ""


@dataclass
class DeckRecord:
    """One Anki deck and the cards that sit directly in it.

    `subdeck_names` and `parent_name` reference synced decks only, by full
    Anki name; an unsynced parent leaves `parent_name` None.
    """

    name: str
    deck_id: int
    cards: List[CardRecord] = field(default_factory=list)
    subdeck_names: List[str] = field(default_factory=list)
    parent_name: Optional[str] = None


# --------------------------------------------------------------------------
# Slugs and tags
# --------------------------------------------------------------------------

def deck_slug(name):
    # type: (str) -> str
    """Filename stem for a deck: `PHED163::Module 3` -> `phed163-module-3`."""
    return slugify(name.replace("::", " "))


def assign_slugs(decks):
    # type: (List[DeckRecord]) -> Dict[str, str]
    """Map full deck name -> unique slug, deterministically.

    Decks are processed sorted by full name, so on a collision the first
    name in sort order keeps the bare slug and each later one gets
    `-<deck_id>` appended. The same deck set always yields the same map.
    """
    slugs = {}  # type: Dict[str, str]
    taken = set()  # type: set
    for deck in sorted(decks, key=lambda d: d.name):
        base = deck_slug(deck.name) or "deck"
        candidate = base
        if candidate in taken:
            candidate = "%s-%d" % (base, deck.deck_id)
            # Two decks sharing a name and an id cannot exist in Anki, but
            # never hand back a duplicate even so.
            suffix = 2
            while candidate in taken:
                candidate = "%s-%d-%d" % (base, deck.deck_id, suffix)
                suffix += 1
        taken.add(candidate)
        slugs[deck.name] = candidate
    return slugs


def display_tags(tags):
    # type: (List[str]) -> List[str]
    """Subject-matter tags only, sorted and deduplicated.

    Drops the sync's own `wiki::`/`source::` namespaces and Anki's `marked`
    and `leech` flags, which describe card state rather than content.
    """
    out = set()
    for tag in tags or ():
        clean = (tag or "").strip()
        if not clean:
            continue
        lowered = clean.lower()
        if lowered in HIDDEN_TAGS:
            continue
        if any(lowered.startswith(p) for p in HIDDEN_TAG_PREFIXES):
            continue
        out.add(clean)
    return sorted(out)


def card_block_id(nid):
    # type: (int) -> str
    """Obsidian block id for a card: stable across syncs because nids are."""
    return "n%d" % nid


# --------------------------------------------------------------------------
# Card lines
# --------------------------------------------------------------------------

_TABLE_RE = re.compile(r"<table.*?</table>", re.DOTALL | re.IGNORECASE)
_WS_RE = re.compile(r"[ \t]+")
_BULLET_RE = re.compile(r"(?m)^[ \t]*[-*][ \t]+")


def _is_cloze(note_type):
    # type: (str) -> bool
    return "cloze" in (note_type or "").lower()


def _collapse_inline(text):
    # type: (str) -> str
    """Flatten converted markdown into one line fit for a list item.

    Tables keep their HTML (Obsidian renders it inline) but lose their
    internal newlines; remaining paragraph breaks become `<br>`; markdown
    bullets become middle dots so they do not start a nested list.
    """
    if not text:
        return ""

    # Tables first: collapse all whitespace inside them to single spaces, so
    # the later newline handling cannot inject <br> into the markup.
    def _flatten_table(match):
        return _WS_RE.sub(" ", match.group(0).replace("\n", " ")).strip()

    text = _TABLE_RE.sub(_flatten_table, text)

    # Bullets become a middle dot before newlines turn into <br>, so the
    # marker is matched at a real line start.
    text = _BULLET_RE.sub("• ", text)

    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"\n{2,}", "<br>", text)
    text = text.replace("\n", "<br>")
    text = _WS_RE.sub(" ", text)
    return text.strip()


def render_card_inline(card):
    # type: (CardRecord) -> str
    """One-line rendering of a card's content, with no link or block id.

    Cloze notes render as `Text` plus ` · Extra: ...`; basic notes as
    `Front → Back`; anything else as `Name: value` per non-empty field
    joined by ` · `, with Extra moved to the end.
    """
    fields = card.fields_md or {}
    # Preserve the note type's field order, which dicts keep from 3.7 on.
    items = [(name, (value or "").strip()) for name, value in fields.items()]
    non_empty = [(name, value) for name, value in items if value]

    if _is_cloze(card.note_type):
        text = ""
        for key in ("Text", "Content"):
            if fields.get(key, "").strip():
                text = fields[key].strip()
                break
        extra = fields.get("Extra", "").strip()
        parts = []
        if text:
            parts.append(_collapse_inline(text))
        if extra:
            parts.append("Extra: " + _collapse_inline(extra))
        return " · ".join(p for p in parts if p)

    names = set(fields.keys())
    if "Front" in names and "Back" in names:
        front = _collapse_inline(fields.get("Front", "").strip())
        back = _collapse_inline(fields.get("Back", "").strip())
        if front and back:
            return "%s → %s" % (front, back)
        return front or back

    # Generic: label every non-empty field, Extra last.
    ordered = [(n, v) for n, v in non_empty if n != "Extra"]
    ordered += [(n, v) for n, v in non_empty if n == "Extra"]
    parts = []
    for name, value in ordered:
        collapsed = _collapse_inline(value)
        if collapsed:
            parts.append("%s: %s" % (name, collapsed))
    return " · ".join(parts)


def _card_link_suffix(nid):
    # type: (int) -> str
    return " [↗](anki://x-callback-url/search?query=nid:%d) ^%s" % (nid, card_block_id(nid))


# --------------------------------------------------------------------------
# YAML frontmatter, written by hand so key order and quoting are exact
# --------------------------------------------------------------------------

def _yaml_scalar(value):
    # type: (Any) -> str
    """Render one scalar for hand-written frontmatter.

    Strings are double-quoted with JSON escaping when they carry YAML-special
    characters or could be read back as another type; ints pass through.
    """
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    text = str(value)
    if text == "":
        return '""'
    needs_quotes = (
        any(ch in text for ch in _YAML_NEEDS_QUOTES)
        or text.strip() != text
        or text[0] in "-?"
        or text.lower() in ("true", "false", "null", "yes", "no", "on", "off", "~")
        # A bare 2026-09-07 loads back as a datetime.date, not a string.
        or _DATE_RE.match(text) is not None
    )
    if not needs_quotes:
        try:
            float(text)
            needs_quotes = True
        except ValueError:
            pass
    if needs_quotes:
        return json.dumps(text, ensure_ascii=False)
    return text


def _yaml_block(pairs):
    # type: (List[Tuple[str, Any]]) -> List[str]
    """Frontmatter lines for (key, value) pairs, lists as block lists."""
    lines = []
    for key, value in pairs:
        if isinstance(value, (list, tuple)):
            if not value:
                lines.append("%s: []" % key)
            else:
                lines.append("%s:" % key)
                for item in value:
                    lines.append("  - %s" % _yaml_scalar(item))
        else:
            lines.append("%s: %s" % (key, _yaml_scalar(value)))
    return lines


def parse_frontmatter(text):
    # type: (str) -> Dict[str, Any]
    """Frontmatter of a page as a dict; {} when absent or unparseable.

    Used by the sync to read back a page's `sync_signature` without
    re-rendering it, so a malformed file must degrade to "rewrite me"
    rather than raise.
    """
    if not text:
        return {}
    normalized = text.replace("\r\n", "\n")
    if not normalized.startswith("---\n"):
        return {}
    end = normalized.find("\n---", 3)
    if end == -1:
        return {}
    block = normalized[4:end + 1]
    try:
        data = yaml.safe_load(block)
    except Exception:
        return {}
    if not isinstance(data, dict):
        return {}
    return data


# --------------------------------------------------------------------------
# Signature
# --------------------------------------------------------------------------

def compute_signature(deck, topics):
    # type: (DeckRecord, List[str]) -> str
    """Content hash of everything a deck page renders from.

    Sorted throughout, so reordering the same cards leaves the signature
    unchanged while any edit to a card's mod, lapses or ease, to the topic
    list, to the synced subdecks or to the parent changes it.
    """
    # source_link depends on vault state, not the card, so it is hashed too.
    card_keys = sorted(
        "%d:%d:%d:%d:%s" % (c.nid, c.mod, c.lapses, c.ease, c.source_link)
        for c in deck.cards
    )
    payload = "\n".join([
        str(SCHEMA_VERSION),
        deck.name,
        str(deck.deck_id),
        "|".join(card_keys),
        "|".join(sorted(topics or ())),
        "|".join(sorted(deck.subdeck_names or ())),
        deck.parent_name or "",
    ])
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------
# Deck page
# --------------------------------------------------------------------------

def _deck_title(name):
    # type: (str) -> str
    """Human heading for a deck: `PHED163::Module 3` -> `PHED163 > Module 3`."""
    return " › ".join(part.strip() for part in name.split("::"))


def _link(slug, alias=None):
    # type: (str, Optional[str]) -> str
    if alias:
        return "[[%s|%s]]" % (slug, alias)
    return "[[%s]]" % slug


def _group_cards(cards):
    # type: (List[CardRecord]) -> List[Tuple[str, List[Tuple[str, CardRecord]]]]
    """Bucket cards under their first display tag, `untagged` last.

    Each card lands in exactly one section. Sections sort alphabetically
    with `untagged` forced to the end; cards inside a section sort by their
    rendered text, then nid, so the page is stable.
    """
    buckets = {}  # type: Dict[str, List[Tuple[str, CardRecord]]]
    for card in cards:
        tags = display_tags(card.tags)
        section = tags[0] if tags else UNTAGGED
        buckets.setdefault(section, []).append((render_card_inline(card), card))

    def section_key(name):
        return (1, "") if name == UNTAGGED else (0, name.lower())

    out = []
    for name in sorted(buckets.keys(), key=section_key):
        rows = sorted(buckets[name], key=lambda pair: (pair[0], pair[1].nid))
        out.append((name, rows))
    return out


def _weak_spot_lines(cards, threshold):
    # type: (List[CardRecord], int) -> List[str]
    """Cards at or over the lapse threshold, worst first."""
    weak = [c for c in cards if c.lapses >= threshold]
    if not weak:
        return ["- No card in this deck has %d or more lapses." % threshold]
    ranked = sorted(
        weak,
        key=lambda c: (-c.lapses, c.ease, render_card_inline(c), c.nid),
    )
    lines = []
    for card in ranked:
        text = render_card_inline(card)[:80]
        lines.append("- %d lapses · [[#^%s|%s]]" % (
            card.lapses, card_block_id(card.nid), text))
    return lines


def render_deck_page(deck, slugs, topics, today, lapse_threshold=_DEF_LAPSE_THRESHOLD):
    # type: (DeckRecord, Dict[str, str], List[str], str, int) -> str
    """Full markdown for one deck page, frontmatter included."""
    topics = sorted(set(topics or ()))
    subdeck_links = [
        _link(slugs[name]) for name in sorted(deck.subdeck_names or ())
        if name in slugs
    ]

    all_tags = set()
    for card in deck.cards:
        all_tags.update(display_tags(card.tags))
    tags = sorted(all_tags)

    nids = set(c.nid for c in deck.cards)
    lapsing = sum(1 for c in deck.cards if c.lapses >= lapse_threshold)

    fm = [
        ("type", "deck"),
        ("deck", deck.name),
        ("deck_id", deck.deck_id),
    ]
    if deck.parent_name and deck.parent_name in slugs:
        fm.append(("parent", _link(slugs[deck.parent_name])))
    fm.extend([
        ("subdecks", subdeck_links),
        ("cards", len(deck.cards)),
        ("notes", len(nids)),
        ("lapsing", lapsing),
        ("tags", tags),
        ("topics", topics),
        ("schema_version", SCHEMA_VERSION),
        ("updated", today),
        ("sync_signature", compute_signature(deck, topics)),
    ])

    slug = slugs.get(deck.name, deck_slug(deck.name))
    lines = ["---"]
    lines.extend(_yaml_block(fm))
    lines.append("---")
    lines.append("")
    lines.append("# %s" % _deck_title(deck.name))
    lines.append("")
    lines.append("> [!info] Machine-managed by Anki Wiki Sync")
    # Example block id: the lowest nid, so the callout does not depend on the
    # order the cards arrived in.
    example_id = card_block_id(min(c.nid for c in deck.cards)) if deck.cards else "n0"
    lines.append(
        "> Rebuilt on every sync from the Anki deck named above. Do not edit "
        "this file; link to it. Every card is a block: `[[%s#^%s]]`."
        % (slug, example_id)
    )

    if topics:
        lines.append("")
        lines.append("## Topics")
        for topic in topics:
            # Topics arrive as "[[path]]" links already.
            lines.append("- %s" % topic)

    lines.append("")
    lines.append("## Weak spots")
    lines.extend(_weak_spot_lines(deck.cards, lapse_threshold))

    lines.append("")
    lines.append("## Cards")
    for section, rows in _group_cards(deck.cards):
        lines.append("### %s" % section)
        for text, card in rows:
            origin = " ← %s" % card.source_link if card.source_link else ""
            lines.append("- %s%s%s" % (text, origin, _card_link_suffix(card.nid)))

    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------
# Index page
# --------------------------------------------------------------------------

def render_index(decks, slugs, today):
    # type: (List[DeckRecord], Dict[str, str], str) -> str
    """Nested tree of every synced deck, with per-deck counts."""
    by_name = {d.name: d for d in decks}
    children = {}  # type: Dict[Optional[str], List[str]]
    for deck in decks:
        # A deck whose parent is not synced sits at the top level.
        parent = deck.parent_name if (deck.parent_name in by_name) else None
        children.setdefault(parent, []).append(deck.name)

    total_cards = sum(len(d.cards) for d in decks)
    total_lapsing = sum(
        1 for d in decks for c in d.cards if c.lapses >= _DEF_LAPSE_THRESHOLD)

    fm = [
        ("type", "deck-index"),
        ("decks", len(decks)),
        ("cards", total_cards),
        ("lapsing", total_lapsing),
        ("schema_version", SCHEMA_VERSION),
        ("updated", today),
    ]

    lines = ["---"]
    lines.extend(_yaml_block(fm))
    lines.append("---")
    lines.append("")
    lines.append("# Anki decks")
    lines.append("")
    lines.append("> [!info] Machine-managed by Anki Wiki Sync")
    lines.append("> One page per synced deck. See [[schema]] for how to read "
                 "and query these pages.")
    lines.append("")

    def emit(name, depth):
        deck = by_name[name]
        count = len(deck.cards)
        lapsing = sum(1 for c in deck.cards if c.lapses >= _DEF_LAPSE_THRESHOLD)
        label = "%d cards" % count if count != 1 else "1 card"
        if lapsing:
            label += ", %d lapsing" % lapsing
        lines.append("%s- %s (%s)" % (
            "  " * depth, _link(slugs.get(name, deck_slug(name)), name), label))
        for child in sorted(children.get(name, ())):
            emit(child, depth + 1)

    for name in sorted(children.get(None, ())):
        emit(name, 0)

    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------
# Static pages
# --------------------------------------------------------------------------

def render_schema_page(target_rel_folder):
    # type: (str) -> str
    """Static reference page: how to read and query this folder."""
    folder = target_rel_folder.strip("/")
    fm = [
        ("type", "sync-schema"),
        ("schema_version", SCHEMA_VERSION),
    ]
    lines = ["---"]
    lines.extend(_yaml_block(fm))
    lines.append("---")
    lines.extend([
        "",
        "# Anki wiki schema",
        "",
        "> [!info] Machine-managed by Anki Wiki Sync",
        "> This folder is written by the Anki add-on. Do not edit these files "
        "by hand; every sync rebuilds them.",
        "",
        "## What a deck page is",
        "",
        "One page per synced Anki deck, named after the deck slug "
        "(`PHED163::Module 3` becomes `phed163-module-3.md`). The page lists "
        "every card in that deck as a single line under a heading for its "
        "first tag, or under `untagged`. Subdecks get their own pages and are "
        "linked from the parent.",
        "",
        "## Frontmatter fields",
        "",
        "- `type`: always `deck` on a deck page. The index is `deck-index`, "
        "this page is `sync-schema`, the log is `sync-log`.",
        "- `deck`: the full Anki deck name, with `::` separators.",
        "- `deck_id`: the Anki deck id.",
        "- `parent`: link to the parent deck page. Present only when the "
        "parent deck is itself synced.",
        "- `subdecks`: links to synced child deck pages. May be empty.",
        "- `cards`: number of cards on this page.",
        "- `notes`: number of distinct Anki notes behind those cards.",
        "- `lapsing`: how many of those cards are at or over the lapse "
        "threshold.",
        "- `tags`: every subject tag used by the deck's cards, sorted. Sync "
        "namespaces (`wiki::`, `source::`) and Anki flags (`marked`, `leech`) "
        "are excluded.",
        "- `topics`: vault notes this deck relates to, as full-path links.",
        "- `schema_version`: bumped when this format changes; pages with an "
        "older number are rewritten on the next sync.",
        "- `updated`: the local date of the last write.",
        "- `sync_signature`: content hash. Unchanged signature means the page "
        "is already current and is not rewritten.",
        "",
        "## Card blocks",
        "",
        "Every card line ends with `^n<nid>`, where `<nid>` is the Anki note "
        "id. These are stable block anchors: the same card keeps the same "
        "anchor across syncs, across edits, and across deck renames. Link to "
        "one card with `[[phed163-module-3#^n1725000000001]]`, or embed it "
        "with a leading `!`.",
        "",
        "The `↗` link on each line opens that note in Anki.",
        "",
        "A `← [[note#^block|Note]]` before it points at the vault note the "
        "card was captured from, and at the exact highlight block when the "
        "card carries one (Recall cloze cards do). Open the backlinks pane on "
        "that note to see every card drawn from it. Never put links to this "
        "folder inside `wiki/` pages: the LLM wiki linter treats them as dead "
        "and replaces them with stubs.",
        "",
        "## How to query",
        "",
        "- Obsidian search: `path:%s term` searches only this folder." % folder,
        "- `decks.base` is a Bases table over every page with `type == "
        "\"deck\"` in this folder, sortable by deck, cards, lapsing and "
        "updated.",
        "- Weak spots: each deck page has a `## Weak spots` section listing "
        "its most-lapsed cards, worst first.",
        "- An LLM assistant reading the vault should answer from this folder "
        "and cite the card it used as `[[slug#^nid]]`.",
        "",
        "## Rules",
        "",
        "- These files are machine-managed. Anything you type into them is "
        "lost on the next sync. Link to them from your own notes instead.",
        "- The sync writes only inside this folder and never deletes anything "
        "from Anki.",
        "- Do not capture flashcards from these pages. Anki is the source of "
        "truth; this folder is a read-only view of it.",
    ])
    return "\n".join(lines) + "\n"


def render_base(target_rel_folder):
    # type: (str) -> str
    """Obsidian Bases table over the deck pages in the target folder."""
    folder = target_rel_folder.strip("/")
    lines = [
        "views:",
        "  - type: table",
        "    name: Decks",
        "    filters:",
        "      and:",
        '        - file.folder == "%s"' % folder,
        '        - type == "deck"',
        "    order:",
        "      - file.name",
        "      - deck",
        "      - cards",
        "      - lapsing",
        "      - updated",
        "    sort:",
        "      - property: deck",
        "        direction: ASC",
    ]
    return "\n".join(lines) + "\n"


def append_log_line(existing, line):
    # type: (str, str) -> str
    """Append one sync line, creating the log's header when it is empty."""
    entry = line.rstrip("\n")
    if not existing or not existing.strip():
        header = "\n".join([
            "---",
            "type: sync-log",
            "schema_version: %d" % SCHEMA_VERSION,
            "---",
            "",
            "# Sync log",
            "",
            "> [!info] Machine-managed by Anki Wiki Sync",
            "> One line per sync that changed something. Append-only.",
            "",
        ])
        return header + entry + "\n"
    body = existing if existing.endswith("\n") else existing + "\n"
    return body + entry + "\n"
