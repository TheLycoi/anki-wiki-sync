# -*- coding: utf-8 -*-

"""Standalone tests for wiki_format.py (no Anki required).

Run: python3 test_wiki_format.py
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import wiki_format as wf  # noqa: E402

try:
    import yaml  # noqa: E402
except ImportError:  # pragma: no cover
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "vendor"))
    import yaml  # noqa: E402

TODAY = "2026-09-07"


def card(nid, fields, tags=None, note_type="Basic", lapses=0, ease=2500, mod=100):
    return wf.CardRecord(
        nid=nid, cid=nid * 10, note_type=note_type, fields_md=fields,
        tags=list(tags or []), reps=1, lapses=lapses, ease=ease, queue=2, mod=mod)


class TestSlugs(unittest.TestCase):
    def test_deck_slug(self):
        self.assertEqual(wf.deck_slug("PHED163::Module 3"), "phed163-module-3")

    def test_assign_slugs_collision(self):
        # Both slug to "a-b"; the first by full name keeps the bare slug.
        d1 = wf.DeckRecord(name="A::B", deck_id=11, cards=[])
        d2 = wf.DeckRecord(name="A B", deck_id=22, cards=[])
        slugs = wf.assign_slugs([d2, d1])
        self.assertEqual(slugs["A B"], "a-b")
        self.assertEqual(slugs["A::B"], "a-b-11")
        # Deterministic regardless of input order.
        self.assertEqual(wf.assign_slugs([d1, d2]), slugs)

    def test_assign_slugs_no_collision(self):
        decks = [wf.DeckRecord(name="PHED163", deck_id=1, cards=[]),
                 wf.DeckRecord(name="PHED163::Module 3", deck_id=2, cards=[])]
        self.assertEqual(wf.assign_slugs(decks),
                         {"PHED163": "phed163", "PHED163::Module 3": "phed163-module-3"})


class TestTagsAndIds(unittest.TestCase):
    def test_display_tags(self):
        self.assertEqual(
            wf.display_tags(["phed163", "wiki::x", "source::y", "marked", "leech", "stages"]),
            ["phed163", "stages"])

    def test_display_tags_dedup_and_empty(self):
        self.assertEqual(wf.display_tags([]), [])
        self.assertEqual(wf.display_tags(["b", "a", "b", "  "]), ["a", "b"])

    def test_card_block_id(self):
        self.assertEqual(wf.card_block_id(17), "n17")


class TestRenderCardInline(unittest.TestCase):
    def test_cloze_with_extra(self):
        c = card(1, {"Text": "Text", "Extra": "extra"}, note_type="Cloze")
        self.assertEqual(wf.render_card_inline(c), "Text · Extra: extra")
        self.assertNotIn("\n", wf.render_card_inline(c))

    def test_cloze_without_extra(self):
        c = card(1, {"Text": "Just text", "Extra": ""}, note_type="Cloze")
        self.assertEqual(wf.render_card_inline(c), "Just text")

    def test_basic_front_back(self):
        c = card(2, {"Front": "Front", "Back": "Back"})
        self.assertEqual(wf.render_card_inline(c), "Front → Back")

    def test_generic_type_skips_empty_and_moves_extra_last(self):
        c = card(3, {"A": "a", "Extra": "e", "B": ""}, note_type="Custom")
        self.assertEqual(wf.render_card_inline(c), "A: a · Extra: e")

    def test_generic_extra_moved_to_end(self):
        c = card(4, {"Extra": "e", "A": "a"}, note_type="Custom")
        self.assertEqual(wf.render_card_inline(c), "A: a · Extra: e")


class TestInlineCollapsing(unittest.TestCase):
    def test_newlines_become_br(self):
        c = card(1, {"Text": "one\n\ntwo\nthree"}, note_type="Cloze")
        out = wf.render_card_inline(c)
        self.assertEqual(out, "one<br>two<br>three")
        self.assertNotIn("\n", out)

    def test_bullet_becomes_middle_dot(self):
        c = card(1, {"Text": "intro\n- first\n- second"}, note_type="Cloze")
        out = wf.render_card_inline(c)
        self.assertEqual(out, "intro<br>• first<br>• second")
        self.assertNotIn("\n", out)

    def test_table_whitespace_collapsed(self):
        html = "<table>\n  <tr>\n    <td>a</td>\n  </tr>\n</table>"
        c = card(1, {"Text": html}, note_type="Cloze")
        out = wf.render_card_inline(c)
        self.assertNotIn("\n", out)
        self.assertNotIn("<br>", out)
        self.assertIn("<table> <tr> <td>a</td> </tr> </table>", out)

    def test_no_newline_survives_any_field(self):
        c = card(1, {"A": "x\ny", "B": "p\n\nq"}, note_type="Custom")
        self.assertNotIn("\n", wf.render_card_inline(c))


class TestDeckPage(unittest.TestCase):
    def _deck(self):
        cards = [
            card(1001, {"Text": "Alpha text", "Extra": "note"}, tags=["phed163"],
                 note_type="Cloze", lapses=3, ease=1900, mod=500),
            card(1002, {"Front": "Q", "Back": "A"}, tags=["stages", "phed163"], mod=600),
            card(1003, {"Front": "Untagged q", "Back": "Untagged a"}, tags=[], mod=700),
            card(1004, {"Front": "S", "Back": "T"}, tags=["stages"], mod=800),
        ]
        return wf.DeckRecord(name="PHED163::Module 3", deck_id=1712345678, cards=cards,
                             subdeck_names=["PHED163::Module 3::Quiz"],
                             parent_name="PHED163")

    def _slugs(self):
        return {"PHED163": "phed163",
                "PHED163::Module 3": "phed163-module-3",
                "PHED163::Module 3::Quiz": "phed163-module-3-quiz"}

    def test_frontmatter_keys_and_parse(self):
        page = wf.render_deck_page(self._deck(), self._slugs(),
                                   ["[[classes/PHED163/Stages of Change]]"], TODAY)
        fm = wf.parse_frontmatter(page)
        self.assertEqual(list(fm.keys()), [
            "type", "deck", "deck_id", "parent", "subdecks", "cards", "notes",
            "lapsing", "tags", "topics", "schema_version", "updated", "sync_signature"])
        self.assertEqual(fm["type"], "deck")
        self.assertEqual(fm["deck"], "PHED163::Module 3")
        self.assertEqual(fm["deck_id"], 1712345678)
        self.assertEqual(fm["parent"], "[[phed163]]")
        self.assertEqual(fm["subdecks"], ["[[phed163-module-3-quiz]]"])
        self.assertEqual(fm["cards"], 4)
        self.assertEqual(fm["notes"], 4)
        self.assertEqual(fm["lapsing"], 1)
        self.assertEqual(fm["tags"], ["phed163", "stages"])
        self.assertEqual(fm["schema_version"], 2)
        self.assertEqual(fm["updated"], TODAY)

    def test_parent_omitted_when_not_synced(self):
        deck = self._deck()
        deck.parent_name = None
        fm = wf.parse_frontmatter(wf.render_deck_page(deck, self._slugs(), [], TODAY))
        self.assertNotIn("parent", fm)

    def test_empty_lists_render_as_brackets(self):
        deck = wf.DeckRecord(name="Solo", deck_id=5, cards=[])
        page = wf.render_deck_page(deck, {"Solo": "solo"}, [], TODAY)
        self.assertIn("subdecks: []", page)
        self.assertIn("topics: []", page)
        self.assertEqual(wf.parse_frontmatter(page)["subdecks"], [])

    def test_card_grouping(self):
        page = wf.render_deck_page(self._deck(), self._slugs(), [], TODAY)
        # A card tagged ["stages","phed163"] files under its first sorted tag.
        self.assertIn("### phed163", page)
        self.assertIn("### stages", page)
        self.assertIn("### untagged", page)
        self.assertEqual(page.count("^n1002"), 1)
        body = page.split("## Cards", 1)[1]
        self.assertLess(body.index("### phed163"), body.index("### untagged"))
        self.assertLess(body.index("### stages"), body.index("### untagged"))
        # Three sections, with untagged last.
        headings = [ln for ln in body.split("\n") if ln.startswith("### ")]
        self.assertEqual(headings, ["### phed163", "### stages", "### untagged"])
        # The card tagged ["stages","phed163"] sits under its first sorted
        # tag (phed163), not under stages.
        phed_block = body[body.index("### phed163"):body.index("### stages")]
        self.assertIn("^n1002", phed_block)
        stages_block = body[body.index("### stages"):body.index("### untagged")]
        self.assertIn("^n1004", stages_block)
        self.assertNotIn("^n1002", stages_block)

    def test_block_ids_present_and_unique(self):
        page = wf.render_deck_page(self._deck(), self._slugs(), [], TODAY)
        card_lines = [ln for ln in page.split("\n")
                      if ln.startswith("- ") and "anki://" in ln]
        self.assertEqual(len(card_lines), 4)
        ids = []
        for line in card_lines:
            nid = line.rsplit("^n", 1)[1]
            self.assertTrue(line.endswith(
                " [↗](anki://x-callback-url/search?query=nid:%s) ^n%s" % (nid, nid)))
            ids.append(nid)
        self.assertEqual(len(ids), len(set(ids)))

    def test_weak_spots(self):
        page = wf.render_deck_page(self._deck(), self._slugs(), [], TODAY)
        section = page.split("## Weak spots", 1)[1].split("##", 1)[0]
        self.assertIn("- 3 lapses · [[#^n1001|", section)

    def test_weak_spots_ordering(self):
        cards = [
            card(1, {"Front": "a", "Back": "a"}, lapses=2, ease=2500),
            card(2, {"Front": "b", "Back": "b"}, lapses=5, ease=2500),
            card(3, {"Front": "c", "Back": "c"}, lapses=2, ease=1300),
        ]
        deck = wf.DeckRecord(name="D", deck_id=1, cards=cards)
        section = wf.render_deck_page(deck, {"D": "d"}, [], TODAY)
        section = section.split("## Weak spots", 1)[1].split("## Cards", 1)[0]
        order = [ln.split("^", 1)[1].split("|", 1)[0] for ln in section.strip().split("\n")]
        # Most lapses first, then lowest ease.
        self.assertEqual(order, ["n2", "n3", "n1"])

    def test_weak_spots_empty_state(self):
        deck = wf.DeckRecord(name="D", deck_id=1,
                             cards=[card(1, {"Front": "a", "Back": "b"}, lapses=0)])
        page = wf.render_deck_page(deck, {"D": "d"}, [], TODAY)
        self.assertIn("- No card in this deck has 2 or more lapses.", page)

    def test_notes_counts_distinct_nids(self):
        cards = [card(7, {"Front": "a", "Back": "b"}), card(7, {"Front": "c", "Back": "d"})]
        deck = wf.DeckRecord(name="D", deck_id=1, cards=cards)
        fm = wf.parse_frontmatter(wf.render_deck_page(deck, {"D": "d"}, [], TODAY))
        self.assertEqual(fm["cards"], 2)
        self.assertEqual(fm["notes"], 1)

    def test_topics_section(self):
        topics = ["[[classes/PHED163/Stages of Change]]", "[[wiki/concepts/x]]"]
        page = wf.render_deck_page(self._deck(), self._slugs(), topics, TODAY)
        self.assertIn("## Topics\n- [[classes/PHED163/Stages of Change]]\n- [[wiki/concepts/x]]",
                      page)
        self.assertEqual(wf.parse_frontmatter(page)["topics"], topics)


class TestDeterminism(unittest.TestCase):
    def _cards(self):
        return [
            card(1, {"Front": "a", "Back": "b"}, tags=["x"], lapses=1, mod=10),
            card(2, {"Front": "c", "Back": "d"}, tags=["y"], lapses=3, mod=20),
            card(3, {"Front": "e", "Back": "f"}, tags=[], lapses=0, mod=30),
        ]

    def test_card_order_does_not_change_page_or_signature(self):
        cards = self._cards()
        d1 = wf.DeckRecord(name="D", deck_id=1, cards=list(cards))
        d2 = wf.DeckRecord(name="D", deck_id=1, cards=list(reversed(cards)))
        slugs = {"D": "d"}
        self.assertEqual(wf.render_deck_page(d1, slugs, [], TODAY),
                         wf.render_deck_page(d2, slugs, [], TODAY))
        self.assertEqual(wf.compute_signature(d1, []), wf.compute_signature(d2, []))

    def test_signature_sensitive_to_mod_lapses_topics(self):
        base = wf.DeckRecord(name="D", deck_id=1, cards=self._cards())
        sig = wf.compute_signature(base, ["[[t]]"])

        changed_mod = wf.DeckRecord(name="D", deck_id=1, cards=self._cards())
        changed_mod.cards[0].mod = 999
        self.assertNotEqual(sig, wf.compute_signature(changed_mod, ["[[t]]"]))

        changed_lapses = wf.DeckRecord(name="D", deck_id=1, cards=self._cards())
        changed_lapses.cards[0].lapses = 42
        self.assertNotEqual(sig, wf.compute_signature(changed_lapses, ["[[t]]"]))

        changed_ease = wf.DeckRecord(name="D", deck_id=1, cards=self._cards())
        changed_ease.cards[0].ease = 1234
        self.assertNotEqual(sig, wf.compute_signature(changed_ease, ["[[t]]"]))

        self.assertNotEqual(sig, wf.compute_signature(base, ["[[t]]", "[[u]]"]))

    def test_signature_sensitive_to_structure(self):
        base = wf.DeckRecord(name="D", deck_id=1, cards=self._cards())
        sig = wf.compute_signature(base, [])
        with_sub = wf.DeckRecord(name="D", deck_id=1, cards=self._cards(),
                                 subdeck_names=["D::S"])
        self.assertNotEqual(sig, wf.compute_signature(with_sub, []))
        with_parent = wf.DeckRecord(name="D", deck_id=1, cards=self._cards(),
                                    parent_name="P")
        self.assertNotEqual(sig, wf.compute_signature(with_parent, []))

    def test_repeat_render_is_byte_identical(self):
        deck = wf.DeckRecord(name="D", deck_id=1, cards=self._cards())
        first = wf.render_deck_page(deck, {"D": "d"}, ["[[t]]"], TODAY)
        second = wf.render_deck_page(deck, {"D": "d"}, ["[[t]]"], TODAY)
        self.assertEqual(first, second)


class TestIndex(unittest.TestCase):
    def test_nesting_and_counts(self):
        decks = [
            wf.DeckRecord(name="PHED163", deck_id=1, cards=[]),
            wf.DeckRecord(name="PHED163::Module 1", deck_id=2,
                          cards=[card(1, {"Front": "a", "Back": "b"}, lapses=2)],
                          parent_name="PHED163"),
            # Parent "Orphan" is not synced, so this sits at the top level.
            wf.DeckRecord(name="Orphan::Child", deck_id=3,
                          cards=[card(2, {"Front": "c", "Back": "d"})],
                          parent_name="Orphan"),
        ]
        slugs = wf.assign_slugs(decks)
        page = wf.render_index(decks, slugs, TODAY)
        fm = wf.parse_frontmatter(page)
        self.assertEqual(fm["type"], "deck-index")
        self.assertEqual(fm["decks"], 3)
        self.assertEqual(fm["cards"], 2)
        self.assertEqual(fm["lapsing"], 1)
        self.assertEqual(fm["schema_version"], 2)

        body = page.split("---", 2)[2]
        self.assertIn("- [[phed163|PHED163]] (0 cards)", body)
        self.assertIn("  - [[phed163-module-1|PHED163::Module 1]] (1 card, 1 lapsing)", body)
        # Unsynced parent means top level: no indent.
        self.assertIn("\n- [[orphan-child|Orphan::Child]] (1 card)", body)

    def test_empty_index(self):
        page = wf.render_index([], {}, TODAY)
        fm = wf.parse_frontmatter(page)
        self.assertEqual(fm["decks"], 0)
        self.assertEqual(fm["cards"], 0)


class TestStaticPages(unittest.TestCase):
    def test_schema_page(self):
        page = wf.render_schema_page("anki")
        self.assertIn("path:anki", page)
        self.assertIn("decks.base", page)
        self.assertIn("^n", page)
        self.assertIn("schema_version: 2", page)
        self.assertEqual(wf.parse_frontmatter(page)["type"], "sync-schema")

    def test_base(self):
        text = wf.render_base("anki")
        data = yaml.safe_load(text)
        conds = data["views"][0]["filters"]["and"]
        self.assertIn('file.folder == "anki"', conds)
        self.assertIn('type == "deck"', conds)
        self.assertEqual(data["views"][0]["type"], "table")
        self.assertEqual(data["views"][0]["sort"][0]["property"], "deck")


class TestLog(unittest.TestCase):
    def test_creates_then_appends(self):
        line1 = "- 2026-09-07T23:41 sync: 3 page(s) written, 0 deleted, 61 cards, 12 assets copied"
        out = wf.append_log_line("", line1)
        self.assertTrue(out.startswith("---\ntype: sync-log"))
        self.assertIn("# Sync log", out)
        self.assertTrue(out.rstrip("\n").endswith(line1))
        self.assertEqual(wf.parse_frontmatter(out)["type"], "sync-log")

        line2 = "- 2026-09-08T10:00 sync: 1 page(s) written, 0 deleted, 61 cards, 0 assets copied"
        out2 = wf.append_log_line(out, line2)
        self.assertEqual(out2, out + line2 + "\n")
        self.assertEqual(out2.count("# Sync log"), 1)


class TestParseFrontmatter(unittest.TestCase):
    def test_returns_dict_for_page(self):
        self.assertEqual(wf.parse_frontmatter("---\ntype: deck\ncards: 3\n---\n\nbody"),
                         {"type": "deck", "cards": 3})

    def test_empty_for_missing_or_invalid(self):
        self.assertEqual(wf.parse_frontmatter(""), {})
        self.assertEqual(wf.parse_frontmatter("no frontmatter here"), {})
        self.assertEqual(wf.parse_frontmatter("---\ntype: [unclosed\n---\n"), {})
        # Frontmatter that is not a mapping is not usable either.
        self.assertEqual(wf.parse_frontmatter("---\n- a\n- b\n---\n"), {})

    def test_quoting_roundtrip(self):
        deck = wf.DeckRecord(name="A::B: tricky [x]", deck_id=9, cards=[])
        page = wf.render_deck_page(deck, {"A::B: tricky [x]": "ab"}, [], TODAY)
        self.assertEqual(wf.parse_frontmatter(page)["deck"], "A::B: tricky [x]")


if __name__ == "__main__":
    unittest.main(verbosity=2)
