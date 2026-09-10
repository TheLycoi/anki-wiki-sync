# -*- coding: utf-8 -*-

"""
Wiki-schema layer for the Loopback fork of Obsidian Sync.

Adapts the mirror output to the academic-wiki vault's conventions:
- type: anki-card frontmatter on every mirrored note
- lowercase-with-hyphens (slugified) filenames
- resolution of Loopback's `wiki::<slug>` / `source::<slug>` Anki tags into
  full-path wikilinks against wiki/concepts, wiki/entities, wiki/sources

This module must stay importable without Anki (no aqt/anki imports) so its
logic can be unit-tested standalone.
"""

import html
import re
import unicodedata
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple
from urllib.parse import parse_qs, urlsplit

# Bumped whenever the frontmatter/body schema changes; mirrored notes carrying
# an older number are rewritten on the next sync even if the card is unchanged.
WIKI_SCHEMA_VERSION = 1

# Folders (relative to the vault root) searched when resolving wiki:: tags.
WIKI_PAGE_DIRS = ("wiki/concepts", "wiki/entities", "wiki/sources")

LOOPBACK_TAG_RE = re.compile(r"^(wiki|source)::(.+)$")

# Recall puts `obsidian://open?vault=…&file=<path>#^<block>` in a card's
# extra field. Stops before quote, whitespace and the `)` of a markdown link.
_OBSIDIAN_OPEN_RE = re.compile(r"obsidian://open\?[^\s\"'<>)\]]+")


def parse_obsidian_source_link(fields_md):
    # type: (Optional[Dict[str, str]]) -> Optional[Tuple[str, str]]
    """(vault-relative note path without .md, block id without ^) from the
    first `obsidian://open` link found in any field, or None.

    Works on both the raw `href="…"` and the markdownified `[…](…)` form,
    and on HTML-escaped `&amp;`. A `#page=N` anchor yields an empty block id.
    """
    for value in (fields_md or {}).values():
        if not value:
            continue
        match = _OBSIDIAN_OPEN_RE.search(html.unescape(value))
        if not match:
            continue
        query = parse_qs(urlsplit(match.group(0)).query)
        target = (query.get("file") or [""])[0].strip()
        if not target:
            continue
        path, _, anchor = target.partition("#")
        path = path.strip("/")
        if path.lower().endswith(".md"):
            path = path[:-3]
        if not path:
            continue
        block = anchor[1:] if anchor.startswith("^") else ""
        return path, block
    return None


def slugify(text: str) -> str:
    """Vault naming convention: lowercase-with-hyphens, ASCII-ish, no junk."""
    if not text:
        return ""
    text = unicodedata.normalize("NFKD", text)
    text = text.encode("ascii", "ignore").decode("ascii")
    text = text.lower()
    text = re.sub(r"[^a-z0-9]+", "-", text)
    return text.strip("-")


def find_vault_root(sync_path: Path) -> Optional[Path]:
    """Walk up from the sync target to the folder containing `.obsidian`."""
    current = Path(sync_path).resolve()
    for candidate in (current, *current.parents):
        if (candidate / ".obsidian").is_dir():
            return candidate
    return None


def is_vault_root(path: str) -> bool:
    """True when *path* is itself an Obsidian vault root (contains .obsidian)."""
    try:
        return (Path(path) / ".obsidian").is_dir()
    except OSError:
        return False


class WikiLinkResolver:
    """Resolves Loopback tag slugs to wiki page paths.

    Scans the vault's wiki page folders once per sync run and matches slugs
    exactly, or by prefix for compiled source pages whose filenames carry a
    hash suffix (e.g. `how-to-write-good-prompts_a3709a.md`).
    """

    def __init__(self, vault_root: Optional[Path]):
        self.vault_root = vault_root
        # per-folder slug indexes, so a `source::` tag can prefer wiki/sources
        # even when a concept/entity page shares the same slug
        self._by_dir: Dict[str, Dict[str, str]] = {d: {} for d in WIKI_PAGE_DIRS}
        if vault_root is not None:
            self._scan()

    def _scan(self):
        for rel_dir in WIKI_PAGE_DIRS:
            page_dir = self.vault_root / rel_dir
            if not page_dir.is_dir():
                continue
            index = self._by_dir[rel_dir]
            for md_file in page_dir.glob("*.md"):
                rel_path = f"{rel_dir}/{md_file.stem}"
                index[md_file.stem] = rel_path
                # Compiled source pages: also index without the _hash suffix
                base = re.sub(r"_[0-9a-f]{6}$", "", md_file.stem)
                if base != md_file.stem:
                    index.setdefault(base, rel_path)

    def resolve_slug(self, slug: str, namespace: str = "wiki") -> Optional[str]:
        """Return the vault-relative page path for *slug*, or None.

        `source::` tags search wiki/sources first; `wiki::` tags search
        concepts, then entities, then sources.
        """
        if namespace == "source":
            search_order = ("wiki/sources", "wiki/concepts", "wiki/entities")
        else:
            search_order = WIKI_PAGE_DIRS
        for rel_dir in search_order:
            index = self._by_dir[rel_dir]
            hit = index.get(slug) or index.get(slugify(slug))
            if hit:
                return hit
        return None

    def links_for_tags(self, tags: List[str]) -> List[str]:
        """Map Loopback `wiki::`/`source::` tags to full-path wikilinks.

        Unresolvable slugs are skipped — a dangling link would create phantom
        pages in the graph, and the raw tag is still preserved in `anki-tags`.
        """
        links = []
        for tag in tags:
            m = LOOPBACK_TAG_RE.match(tag.strip())
            if not m:
                continue
            page = self.resolve_slug(m.group(2), namespace=m.group(1))
            if page and f"[[{page}]]" not in links:
                links.append(f"[[{page}]]")
        return links


def build_card_frontmatter(base: Dict, deck_path: str, wiki_links: List[str],
                           updated_iso: str) -> Dict:
    """Wrap the sync frontmatter in the vault's anki-card schema.

    `base` keys (anki_note_id, anki_note_mod, content_hash, …) are preserved
    because the differential sync reads them back from disk.
    """
    fm = {
        "type": "anki-card",
        "schema_version": WIKI_SCHEMA_VERSION,
        "deck": deck_path.replace("/", "::"),
        "wiki": wiki_links,
        "updated": updated_iso,
    }
    fm.update(base)
    return fm


def build_wiki_footer(wiki_links: List[str]) -> str:
    """Body section linking back to the wiki pages this card was drawn from."""
    if not wiki_links:
        return ""
    lines = ["", "## Wiki", ""]
    lines.extend(f"- {link}" for link in wiki_links)
    return "\n".join(lines)


class TopicNoteIndex:
    """Indexes vault notes by their frontmatter `class:` value.

    A deck like `PHED163::Module 3` should link to every note declaring
    `class: PHED163`, so the wiki pages point back at the course material.
    Matching is case-insensitive against each `::` segment of the deck name.

    Only the frontmatter block of each file is read, and any file that
    cannot be read or has no frontmatter is skipped, so a single bad note
    never breaks a sync.
    """

    # Folders never worth scanning: plugin data, VCS, caches, deleted notes.
    SKIP_DIRS = (".obsidian", ".git", "node_modules", ".smart-env", ".trash")

    def __init__(self, vault_root: Optional[Path], exclude_rel=()):
        self.vault_root = Path(vault_root) if vault_root is not None else None
        # The sync target is always excluded: its own pages are not topics.
        self.exclude_rel = set()
        for rel in exclude_rel or ():
            cleaned = str(rel).strip("/").strip()
            if cleaned:
                self.exclude_rel.add(cleaned)
        # lowercased class value -> sorted vault-relative paths (no .md)
        self._by_class: Dict[str, List[str]] = {}
        # slugified filename stem -> vault-relative paths (no .md), for
        # resolving Recall's `source::<stem-slug>` tags to the note itself
        self._by_stem: Dict[str, List[str]] = {}
        # vault-relative path (no .md) -> lowercased class value
        self._class_of: Dict[str, str] = {}
        self._notes: Set[str] = set()
        if self.vault_root is not None and self.vault_root.is_dir():
            self._scan()

    def _is_excluded(self, rel_path: str) -> bool:
        parts = rel_path.split("/")
        for part in parts[:-1]:
            if part in self.SKIP_DIRS:
                return True
        for excluded in self.exclude_rel:
            if rel_path == excluded or rel_path.startswith(excluded + "/"):
                return True
        return False

    def _scan(self):
        for md_file in sorted(self.vault_root.rglob("*.md")):
            try:
                rel_path = md_file.relative_to(self.vault_root).as_posix()
            except ValueError:
                continue
            if self._is_excluded(rel_path):
                continue
            page = rel_path[:-3] if rel_path.endswith(".md") else rel_path
            self._notes.add(page)
            stem = slugify(md_file.stem)
            if stem:
                self._by_stem.setdefault(stem, []).append(page)
            class_value = self._read_class(md_file)
            if not class_value:
                continue
            key = class_value.strip().lower()
            if not key:
                continue
            self._class_of[page] = key
            self._by_class.setdefault(key, []).append(page)

    @staticmethod
    def _read_class(md_file: Path) -> Optional[str]:
        """The frontmatter `class:` value, or None. Never raises."""
        try:
            with md_file.open("r", encoding="utf-8", errors="replace") as fh:
                first = fh.readline()
                if first.strip() != "---":
                    return None
                for _ in range(200):  # frontmatter is small; do not read bodies
                    line = fh.readline()
                    if not line:
                        return None
                    stripped = line.strip()
                    if stripped in ("---", "..."):
                        return None
                    if stripped.lower().startswith("class:"):
                        value = stripped.split(":", 1)[1].strip()
                        # Tolerate quoted scalars.
                        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                            value = value[1:-1].strip()
                        return value or None
        except (OSError, UnicodeError, ValueError):
            return None
        return None

    def topics_for_deck(self, deck_name: str) -> List[str]:
        """Wikilinks to every note whose `class:` matches a deck segment."""
        if not deck_name or not self._by_class:
            return []
        hits = set()
        for segment in deck_name.split("::"):
            key = segment.strip().lower()
            if not key:
                continue
            for page in self._by_class.get(key, ()):
                hits.add("[[%s]]" % page)
        return sorted(hits)

    def has_note(self, page):
        # type: (str) -> bool
        """True when *page* (vault-relative, no .md) is an indexed note."""
        return page in self._notes

    def note_for_source_tag(self, slug, deck_name=""):
        # type: (str, str) -> Optional[str]
        """The one vault note whose filename stem slugifies to *slug*.

        Several notes can share a stem. Ties break on a `class:` matching
        a deck segment, then on not being a wiki page; still ambiguous
        means None rather than a guess.
        """
        candidates = self._by_stem.get(slugify(slug), [])
        if len(candidates) == 1:
            return candidates[0]
        if not candidates:
            return None
        segments = set(s.strip().lower() for s in (deck_name or "").split("::") if s.strip())
        by_class = [p for p in candidates if self._class_of.get(p) in segments]
        if len(by_class) == 1:
            return by_class[0]
        pool = by_class or candidates
        non_wiki = [p for p in pool if not p.startswith("wiki/")]
        if len(non_wiki) == 1:
            return non_wiki[0]
        return None


def source_link_for_card(fields_md, tags, deck_name, topic_index):
    # type: (Optional[Dict[str, str]], List[str], str, TopicNoteIndex) -> str
    """Wikilink to the note (and highlight block) a card was captured from.

    Prefers the exact `obsidian://` link Recall stores in the extra field,
    then falls back to the card's `source::`/`wiki::` tag. Empty when the
    target is not an existing vault note outside the sync folder.
    """
    parsed = parse_obsidian_source_link(fields_md)
    if parsed:
        page, block = parsed
        if topic_index.has_note(page):
            anchor = "#^%s" % block if block else ""
            return "[[%s%s|%s]]" % (page, anchor, page.rsplit("/", 1)[-1])
    for tag in tags or ():
        m = LOOPBACK_TAG_RE.match((tag or "").strip())
        if not m:
            continue
        page = topic_index.note_for_source_tag(m.group(2), deck_name)
        if page:
            return "[[%s|%s]]" % (page, page.rsplit("/", 1)[-1])
    return ""
