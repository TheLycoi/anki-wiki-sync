"""Create the WikiTest deck with fixture notes over AnkiConnect.

Standard library only. Python 3.9 compatible. Talks to AnkiConnect
(http://localhost:8765 by default) to build a small, idempotent fixture
deck for end to end testing of the Anki Wiki Sync add-on.

Usage:
    python3 tools/make_test_deck.py --dry-run
    python3 tools/make_test_deck.py
"""

import argparse
import json
import urllib.request
from urllib.error import URLError

DECK_NAME = "WikiTest"
FIXTURE_TAG = "wikitest-fixture"

# Each fixture is (model, tags, fields). Cloze fields use the placeholder
# key "__extra__" for the model's second field name, resolved at runtime
# from modelFieldNames since the stock Cloze model's second field is
# named "Back Extra".
FIXTURES = [
    {
        "model": "Basic",
        "tags": ["phed163", "stages", "wiki::stages-of-change"],
        "fields": {
            "Front": "What defines the precontemplation stage?",
            "Back": (
                "The client has no intention of changing in the "
                "foreseeable future and is often uninformed of the "
                "consequences."
            ),
        },
    },
    {
        "model": "Cloze",
        "tags": ["phed163", "stages", "wiki::stages-of-change"],
        "fields": {
            "Text": (
                "Contemplation: the client intends to change within the "
                "next {{c1::six months}} and weighs pros and cons."
            ),
            "__extra__": "Ambivalence is the key element of this stage.",
        },
    },
    {
        "model": "Cloze",
        "tags": ["phed163", "stages"],
        "fields": {
            "Text": (
                "Preparation: the client intends to change within the "
                "next {{c1::month}} and has made plans to start."
            ),
            "__extra__": "",
        },
    },
    {
        "model": "Basic",
        "tags": ["phed163", "stages"],
        "fields": {
            "Front": "Which stage is the most difficult to maintain and why?",
            "Back": (
                "Action, because commitment is fragile while the change "
                "is new."
            ),
        },
    },
    {
        "model": "Basic",
        "tags": ["phed163", "decisional-balance"],
        "fields": {
            "Front": "Define decisional balance.",
            "Back": (
                "An individual's relative weighing of the benefits of "
                "changing (pros) versus the costs of changing (cons)."
            ),
        },
    },
    {
        "model": "Cloze",
        "tags": ["phed163", "decisional-balance"],
        "fields": {
            "Text": (
                "In precontemplation the {{c1::cons}} outweigh the "
                "{{c2::pros}}; by action and maintenance the pros "
                "outweigh the cons."
            ),
            "__extra__": "",
        },
    },
    {
        "model": "Basic",
        "tags": ["phed163", "self-efficacy"],
        "fields": {
            "Front": "Why does self-efficacy matter to a health coach?",
            "Back": (
                "It is a fairly good predictor of future behavior "
                "change, so supporting it is essential to the "
                "client-coach relationship."
            ),
        },
    },
    {
        "model": "Basic",
        "tags": ["phed163"],
        "fields": {
            "Front": (
                "What four areas of lifestyle medicine does a health "
                "coach understand?"
            ),
            "Back": "Nutrition, physical activity, sleep, and stress management.",
        },
    },
    {
        "model": "Basic",
        "tags": [],
        "fields": {
            "Front": "Who is the expert in the client's own life?",
            "Back": "The client.",
        },
    },
    {
        "model": "Basic",
        "tags": ["phed163", "stages"],
        "fields": {
            "Front": "Stage timelines",
            "Back": (
                "<table><tr><th>Stage</th><th>Timeline</th></tr>"
                "<tr><td>Contemplation</td><td>within 6 months</td></tr>"
                "<tr><td>Preparation</td><td>within 1 month</td></tr>"
                "</table>"
            ),
        },
    },
]


def invoke(base_url, action, params=None, dry_run=False, dry_run_log=None):
    """Send one AnkiConnect request and return its result.

    Raises RuntimeError if the response carries a non-null error.
    """
    payload = {"action": action, "version": 6}
    if params is not None:
        payload["params"] = params

    if dry_run_log is not None:
        dry_run_log.append(payload)

    if dry_run:
        return None

    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        base_url, data=body, headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(request) as response:
            raw = response.read()
    except URLError as exc:
        raise RuntimeError(
            "Could not reach AnkiConnect at {}: {}".format(base_url, exc)
        )

    parsed = json.loads(raw.decode("utf-8"))
    if "error" not in parsed or "result" not in parsed:
        raise RuntimeError(
            "Unexpected AnkiConnect response shape: {}".format(parsed)
        )
    if parsed["error"] is not None:
        raise RuntimeError(
            "AnkiConnect action {} failed: {}".format(action, parsed["error"])
        )
    return parsed["result"]


def build_note(fixture, extra_field_name):
    """Turn one fixture dict into an AnkiConnect addNotes note dict."""
    tags = list(fixture["tags"]) + [FIXTURE_TAG]

    if fixture["model"] == "Cloze":
        fields = {
            "Text": fixture["fields"]["Text"],
            extra_field_name: fixture["fields"]["__extra__"],
        }
    else:
        fields = dict(fixture["fields"])

    return {
        "deckName": DECK_NAME,
        "modelName": fixture["model"],
        "fields": fields,
        "tags": tags,
        "options": {"allowDuplicate": False},
    }


def main():
    parser = argparse.ArgumentParser(
        description=(
            "Create the WikiTest deck with fixture notes over AnkiConnect."
        )
    )
    parser.add_argument(
        "--base-url",
        default="http://localhost:8765",
        help="AnkiConnect base URL (default: http://localhost:8765)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help=(
            "Print every request payload that would be sent, send only "
            "read-only actions, and exit without changing anything."
        ),
    )
    args = parser.parse_args()

    if args.dry_run:
        log = []

        version = invoke(args.base_url, "version", dry_run=True, dry_run_log=log)
        deck_names = invoke(
            args.base_url, "deckNames", dry_run=True, dry_run_log=log
        )
        model_names = invoke(
            args.base_url, "modelNames", dry_run=True, dry_run_log=log
        )
        cloze_fields = invoke(
            args.base_url,
            "modelFieldNames",
            params={"modelName": "Cloze"},
            dry_run=True,
            dry_run_log=log,
        )
        found = invoke(
            args.base_url,
            "findNotes",
            params={"query": "tag:{}".format(FIXTURE_TAG)},
            dry_run=True,
            dry_run_log=log,
        )

        for payload in log:
            print(json.dumps(payload))

        print()
        print("Dry run: no write actions were sent.")
        print("Would check: is {!r} in current deckNames?".format(DECK_NAME))
        print("Would check: is 'Cloze' in current modelNames?")
        print(
            "Would resolve the Cloze model's second field name via "
            "modelFieldNames and use it for the 'extra' field."
        )
        print(
            "Would add up to {} fixture notes tagged '{}' if "
            "findNotes returns fewer than that many.".format(
                len(FIXTURES), FIXTURE_TAG
            )
        )
        return 0

    # Real mode: connectivity check.
    invoke(args.base_url, "version")

    deck_names = invoke(args.base_url, "deckNames")
    deck_created = DECK_NAME not in deck_names
    if deck_created:
        invoke(args.base_url, "createDeck", params={"deck": DECK_NAME})

    model_names = invoke(args.base_url, "modelNames")
    if "Cloze" not in model_names:
        raise RuntimeError(
            "The 'Cloze' note type is not present in this collection."
        )

    cloze_fields = invoke(
        args.base_url, "modelFieldNames", params={"modelName": "Cloze"}
    )
    if len(cloze_fields) < 2:
        raise RuntimeError(
            "Cloze model has fewer than 2 fields: {}".format(cloze_fields)
        )
    extra_field_name = cloze_fields[1]

    existing_ids = invoke(
        args.base_url,
        "findNotes",
        params={"query": "tag:{}".format(FIXTURE_TAG)},
    )

    added_ids = []
    if len(existing_ids) < len(FIXTURES):
        notes = [build_note(fixture, extra_field_name) for fixture in FIXTURES]
        result = invoke(
            args.base_url, "addNotes", params={"notes": notes}
        )
        added_ids = [nid for nid in result if nid is not None]

    final_ids = invoke(
        args.base_url,
        "findNotes",
        params={"query": "tag:{}".format(FIXTURE_TAG)},
    )

    print("Deck {!r}: {}".format(DECK_NAME, "created" if deck_created else "already existed"))
    print("Notes added this run: {}".format(len(added_ids)))
    if added_ids:
        print("Added note ids: {}".format(added_ids))
    print("Resulting fixture note ids ({} total): {}".format(len(final_ids), final_ids))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
