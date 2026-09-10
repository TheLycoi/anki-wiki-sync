# -*- coding: utf-8 -*-

"""Standalone tests for wiki_schema.py (no Anki required).

Run: python3 test_wiki_schema.py
"""

import importlib.util
import os
import tempfile
import unittest
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "wiki_schema", os.path.join(os.path.dirname(__file__), "wiki_schema.py"))
ws = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ws)


class TestSlugify(unittest.TestCase):
    def test_basic(self):
        self.assertEqual(ws.slugify("How to Write Good Prompts"), "how-to-write-good-prompts")

    def test_punctuation_and_unicode(self):
        self.assertEqual(ws.slugify("Woźniak's 20 rules!"), "wozniak-s-20-rules")

    def test_empty(self):
        self.assertEqual(ws.slugify(""), "")
        self.assertEqual(ws.slugify("???"), "")


class TestVaultRoot(unittest.TestCase):
    def test_find_and_guard(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp) / "vault"
            (vault / ".obsidian").mkdir(parents=True)
            anki_dir = vault / "anki"
            anki_dir.mkdir()
            self.assertEqual(ws.find_vault_root(anki_dir), vault.resolve())
            self.assertTrue(ws.is_vault_root(str(vault)))
            self.assertFalse(ws.is_vault_root(str(anki_dir)))

    def test_no_vault(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(ws.find_vault_root(Path(tmp)))


class TestWikiLinkResolver(unittest.TestCase):
    def _make_vault(self, tmp):
        vault = Path(tmp) / "vault"
        (vault / ".obsidian").mkdir(parents=True)
        for rel in ("wiki/concepts", "wiki/entities", "wiki/sources"):
            (vault / rel).mkdir(parents=True)
        (vault / "wiki/concepts/spaced-repetition.md").write_text("x")
        (vault / "wiki/entities/andy-matuschak.md").write_text("x")
        (vault / "wiki/sources/how-to-write-good-prompts_a3709a.md").write_text("x")
        return vault

    def test_resolves_exact_and_hash_suffixed(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = ws.WikiLinkResolver(self._make_vault(tmp))
            self.assertEqual(r.resolve_slug("spaced-repetition"), "wiki/concepts/spaced-repetition")
            # Compiled source pages resolve with or without the hash suffix
            self.assertEqual(r.resolve_slug("how-to-write-good-prompts"),
                             "wiki/sources/how-to-write-good-prompts_a3709a")
            self.assertEqual(r.resolve_slug("how-to-write-good-prompts_a3709a"),
                             "wiki/sources/how-to-write-good-prompts_a3709a")

    def test_source_namespace_prefers_sources_folder(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault = self._make_vault(tmp)
            # Same slug exists as both a concept and a compiled source page
            (vault / "wiki/concepts/how-to-write-good-prompts.md").write_text("x")
            r = ws.WikiLinkResolver(vault)
            self.assertEqual(r.resolve_slug("how-to-write-good-prompts", namespace="source"),
                             "wiki/sources/how-to-write-good-prompts_a3709a")
            self.assertEqual(r.resolve_slug("how-to-write-good-prompts", namespace="wiki"),
                             "wiki/concepts/how-to-write-good-prompts")

    def test_links_for_tags(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = ws.WikiLinkResolver(self._make_vault(tmp))
            links = r.links_for_tags([
                "wiki::spaced-repetition",
                "wiki::andy-matuschak",
                "source::how-to-write-good-prompts",
                "wiki::spaced-repetition",   # duplicate → dropped
                "wiki::no-such-page",        # unresolvable → dropped
                "marked",                    # non-loopback tag → ignored
            ])
            self.assertEqual(links, [
                "[[wiki/concepts/spaced-repetition]]",
                "[[wiki/entities/andy-matuschak]]",
                "[[wiki/sources/how-to-write-good-prompts_a3709a]]",
            ])

    def test_no_vault_root_resolves_nothing(self):
        r = ws.WikiLinkResolver(None)
        self.assertEqual(r.links_for_tags(["wiki::spaced-repetition"]), [])


class TestFrontmatterAndFooter(unittest.TestCase):
    def test_frontmatter_wraps_and_preserves_sync_keys(self):
        base = {"anki_note_id": 42, "anki_note_mod": 100, "content_hash": "abc"}
        fm = ws.build_card_frontmatter(base, "WikiTest/Sub", ["[[wiki/concepts/x]]"], "2026-09-07")
        self.assertEqual(fm["type"], "anki-card")
        self.assertEqual(fm["schema_version"], ws.WIKI_SCHEMA_VERSION)
        self.assertEqual(fm["deck"], "WikiTest::Sub")
        self.assertEqual(fm["anki_note_id"], 42)
        self.assertEqual(fm["content_hash"], "abc")

    def test_footer(self):
        self.assertEqual(ws.build_wiki_footer([]), "")
        footer = ws.build_wiki_footer(["[[wiki/concepts/x]]"])
        self.assertIn("## Wiki", footer)
        self.assertIn("- [[wiki/concepts/x]]", footer)


class TestTopicNoteIndex(unittest.TestCase):
    def _make_vault(self, tmp):
        vault = Path(tmp) / "vault"
        (vault / ".obsidian").mkdir(parents=True)
        (vault / "classes/PHED163").mkdir(parents=True)
        (vault / "classes/Psychopathology").mkdir(parents=True)
        (vault / "classes/PHED163/Stages of Change.md").write_text(
            "---\nclass: PHED163\n---\n\nbody\n")
        (vault / "classes/Psychopathology/X.md").write_text(
            "---\nclass: PSYC\n---\n\nbody\n")
        return vault

    def test_matches_deck_segment(self):
        with tempfile.TemporaryDirectory() as tmp:
            idx = ws.TopicNoteIndex(self._make_vault(tmp))
            self.assertEqual(idx.topics_for_deck("PHED163::Module 3"),
                             ["[[classes/PHED163/Stages of Change]]"])
            self.assertEqual(idx.topics_for_deck("PSYC::Unit 1"),
                             ["[[classes/Psychopathology/X]]"])
            self.assertEqual(idx.topics_for_deck("Nothing::Here"), [])

    def test_matching_is_case_insensitive(self):
        with tempfile.TemporaryDirectory() as tmp:
            idx = ws.TopicNoteIndex(self._make_vault(tmp))
            self.assertEqual(idx.topics_for_deck("phed163::module 3"),
                             ["[[classes/PHED163/Stages of Change]]"])
            self.assertEqual(idx.topics_for_deck("PhEd163"),
                             ["[[classes/PHED163/Stages of Change]]"])

    def test_exclude_rel_skips_sync_target(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault = self._make_vault(tmp)
            (vault / "anki").mkdir()
            # A synced deck page must never be offered back as a topic.
            (vault / "anki/phed163-module-3.md").write_text(
                "---\nclass: PHED163\n---\n")
            idx = ws.TopicNoteIndex(vault, exclude_rel=["anki"])
            self.assertEqual(idx.topics_for_deck("PHED163::Module 3"),
                             ["[[classes/PHED163/Stages of Change]]"])

    def test_skips_dot_folders(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault = self._make_vault(tmp)
            (vault / ".trash").mkdir()
            (vault / ".trash/old.md").write_text("---\nclass: PHED163\n---\n")
            idx = ws.TopicNoteIndex(vault)
            self.assertEqual(idx.topics_for_deck("PHED163"),
                             ["[[classes/PHED163/Stages of Change]]"])

    def test_ignores_files_without_frontmatter_or_class(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault = self._make_vault(tmp)
            (vault / "plain.md").write_text("no frontmatter, just text\n")
            (vault / "other.md").write_text("---\ntitle: X\n---\n")
            idx = ws.TopicNoteIndex(vault)
            self.assertEqual(idx.topics_for_deck("PHED163"),
                             ["[[classes/PHED163/Stages of Change]]"])
            self.assertEqual(idx.topics_for_deck("X"), [])

    def test_unreadable_file_is_ignored(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault = self._make_vault(tmp)
            bad = vault / "bad.md"
            bad.write_bytes(b"---\nclass: \xff\xfe PHED163\n---\n")
            # Must not raise; the good note still resolves.
            idx = ws.TopicNoteIndex(vault)
            self.assertIn("[[classes/PHED163/Stages of Change]]",
                          idx.topics_for_deck("PHED163"))

    def test_no_vault_root(self):
        self.assertEqual(ws.TopicNoteIndex(None).topics_for_deck("PHED163"), [])

    def test_quoted_class_value(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault = self._make_vault(tmp)
            (vault / "quoted.md").write_text('---\nclass: "BIOL200"\n---\n')
            idx = ws.TopicNoteIndex(vault)
            self.assertEqual(idx.topics_for_deck("BIOL200"), ["[[quoted]]"])

    def test_note_for_source_tag(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault = self._make_vault(tmp)
            (vault / "wiki/concepts").mkdir(parents=True)
            (vault / "wiki/concepts/stages-of-change.md").write_text("x")
            (vault / "classes/Psychopathology/Stages of Change.md").write_text(
                "---\nclass: PSYC\n---\n")
            idx = ws.TopicNoteIndex(vault)
            # Unique stem resolves outright.
            self.assertEqual(idx.note_for_source_tag("x"), "classes/Psychopathology/X")
            # Three notes share the stem: the deck's class breaks the tie.
            self.assertEqual(idx.note_for_source_tag("stages-of-change", "PHED163::M3"),
                             "classes/PHED163/Stages of Change")
            self.assertEqual(idx.note_for_source_tag("Stages of Change", "PSYC"),
                             "classes/Psychopathology/Stages of Change")
            # No class match and two non-wiki candidates: refuse to guess.
            self.assertIsNone(idx.note_for_source_tag("stages-of-change", "Other"))
            self.assertIsNone(idx.note_for_source_tag("no-such-note"))
            self.assertTrue(idx.has_note("classes/PHED163/Stages of Change"))
            self.assertFalse(idx.has_note("classes/PHED163/Missing"))

    def test_wiki_page_loses_tie_to_vault_note(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault = self._make_vault(tmp)
            (vault / "wiki/concepts").mkdir(parents=True)
            (vault / "wiki/concepts/stages-of-change.md").write_text("x")
            idx = ws.TopicNoteIndex(vault)
            self.assertEqual(idx.note_for_source_tag("stages-of-change", "Other"),
                             "classes/PHED163/Stages of Change")


class TestObsidianSourceLink(unittest.TestCase):
    RAW = ('<div class="recall-source">Source: <a href="obsidian://open?vault=academic%20wiki'
           '&file=classes%2FPHED163%2FStages%20of%20Change%23%5Erecall-ab12">Stages</a></div>')
    MD = "Source: [Stages](obsidian://open?vault=academic%20wiki&file=classes%2FPHED163%2FStages%20of%20Change%23%5Erecall-ab12)"

    def test_raw_href(self):
        self.assertEqual(ws.parse_obsidian_source_link({"Back Extra": self.RAW}),
                         ("classes/PHED163/Stages of Change", "recall-ab12"))

    def test_markdown_link_and_escaped_amp(self):
        self.assertEqual(ws.parse_obsidian_source_link({"Text": "q", "Back Extra": self.MD}),
                         ("classes/PHED163/Stages of Change", "recall-ab12"))
        escaped = self.MD.replace("&", "&amp;")
        self.assertEqual(ws.parse_obsidian_source_link({"Back Extra": escaped}),
                         ("classes/PHED163/Stages of Change", "recall-ab12"))

    def test_page_anchor_and_no_anchor(self):
        pdf = "obsidian://open?vault=v&file=sources%2Fch3.pdf%23page%3D3"
        self.assertEqual(ws.parse_obsidian_source_link({"E": pdf}), ("sources/ch3.pdf", ""))
        plain = "obsidian://open?vault=v&file=classes%2FA.md"
        self.assertEqual(ws.parse_obsidian_source_link({"E": plain}), ("classes/A", ""))

    def test_absent(self):
        self.assertIsNone(ws.parse_obsidian_source_link({"Front": "q", "Back": "a"}))
        self.assertIsNone(ws.parse_obsidian_source_link({}))
        self.assertIsNone(ws.parse_obsidian_source_link(None))
        self.assertIsNone(ws.parse_obsidian_source_link({"E": "obsidian://open?vault=v"}))


class TestSourceLinkForCard(unittest.TestCase):
    def _vault(self, tmp):
        vault = Path(tmp) / "vault"
        (vault / ".obsidian").mkdir(parents=True)
        (vault / "classes/PHED163").mkdir(parents=True)
        (vault / "classes/PHED163/Stages of Change.md").write_text("---\nclass: PHED163\n---\n")
        (vault / "anki").mkdir()
        (vault / "anki/phed163.md").write_text("---\ntype: deck\n---\n")
        return vault

    def test_prefers_obsidian_link_with_block(self):
        with tempfile.TemporaryDirectory() as tmp:
            idx = ws.TopicNoteIndex(self._vault(tmp), exclude_rel=["anki"])
            fields = {"Back Extra": TestObsidianSourceLink.MD}
            self.assertEqual(
                ws.source_link_for_card(fields, ["recall", "source::other"], "PHED163", idx),
                "[[classes/PHED163/Stages of Change#^recall-ab12|Stages of Change]]")

    def test_falls_back_to_tag(self):
        with tempfile.TemporaryDirectory() as tmp:
            idx = ws.TopicNoteIndex(self._vault(tmp), exclude_rel=["anki"])
            self.assertEqual(
                ws.source_link_for_card({"Front": "q"}, ["recall", "source::stages-of-change"], "PHED163", idx),
                "[[classes/PHED163/Stages of Change|Stages of Change]]")

    def test_missing_or_excluded_target_gives_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            idx = ws.TopicNoteIndex(self._vault(tmp), exclude_rel=["anki"])
            gone = {"E": "obsidian://open?vault=v&file=classes%2FGone"}
            self.assertEqual(ws.source_link_for_card(gone, [], "PHED163", idx), "")
            # A capture from a synced deck page must never link back into anki/.
            deck = {"E": "obsidian://open?vault=v&file=anki%2Fphed163"}
            self.assertEqual(ws.source_link_for_card(deck, ["source::phed163"], "PHED163", idx), "")
            self.assertEqual(ws.source_link_for_card({}, ["marked"], "PHED163", idx), "")


if __name__ == "__main__":
    unittest.main()
