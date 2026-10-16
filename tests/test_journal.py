"""The compact one-line journal format sent to the model."""
import json

import agent_browser as ab

ENTRIES = [
    {"turn": 1, "action": "SEARCH", "requested": "q", "destination": "https://html.duckduckgo.com/html/?q=q", "thought": "t"},
    {"turn": 2, "action": "NAVIGATE", "requested": "https://a.example/x", "destination": "https://a.example/x/", "thought": "t"},
    {"turn": 3, "action": "NAVIGATE", "requested": "https://a.example/old", "destination": "https://a.example/new"},
    {"turn": 4, "action": "DISCOVER", "source": "https://a.example/x", "extracted_data": "SECRET-LONG-TEXT", "thought": "t"},
    {"turn": 5, "action": "REFUSED_IN_TURN", "requested": "NAVIGATE https://a.example/x", "reason": "You are already on this page."},
    {"turn": 6, "action": "READ_MORE", "part": "2 of 3", "thought": "t"},
    {"turn": 7, "action": "FAILED_SEARCH", "requested": "q2", "error": "No results"},
    {"turn": 8, "action": "FINISH", "finding": "summary text"},
]


def test_each_entry_renders_on_one_line_with_its_key_facts():
    lines = [ab.journal_line(e) for e in ENTRIES]
    assert lines[1] == "T2 NAVIGATE https://a.example/x | thought: t"  # same-URL destination not repeated
    assert "(landed on https://a.example/new)" in lines[2]
    assert "SECRET-LONG-TEXT" not in lines[3] and "recorded in DISCOVERIES" in lines[3]
    assert lines[4].endswith("-> You are already on this page.")
    assert "2 of 3" in lines[5] and "No results" in lines[6] and "summary text" in lines[7]


def test_multi_line_requests_are_collapsed_and_capped():
    entry = {"turn": 9, "action": "REFUSED_IN_TURN", "requested": "DISCOVER line one\nline two\n" + "x" * 500, "reason": "dup"}
    line = ab.journal_line(entry)
    assert "\n" not in line and "..." in line and len(line) < 300


def test_compact_journal_is_much_smaller_than_indented_json():
    journal = [{"turn": i, "action": "NAVIGATE", "requested": f"https://docs.example/page{i}",
                "destination": f"https://docs.example/page{i}", "thought": "I should look at the next page for the details. " * 3}
               for i in range(1, 8)]
    old_format = json.dumps([{k: v for k, v in e.items()} for e in journal], indent=2)
    assert len(ab.compact_journal(journal)) <= 0.65 * len(old_format)


def test_unverified_numbers_flags_quantities_missing_from_discoveries():
    found = [{"extracted_data": "100 grams rye flour and 200 grams bread flour. Feed 45 grams each day for 4 days. "
                                "Height: 5,895 m (19,341 ft)."}]
    result = ("1. Mix 100 grams of rye flour [1].\n2. Add 45 grams of water (about 1/4 cup) [1].\n"
              "3. Feed daily for 4 days, twice a day from day 5. Height 5895 m or 19341 ft.")
    assert ab.unverified_numbers(result, found) == ["1/4", "5"]


def test_old_entries_are_dropped_in_batches():
    journal = [{"turn": i, "action": "READ_MORE", "part": "1 of 1"} for i in range(1, 13)]
    shown = ab.compact_journal(journal).splitlines()
    assert shown[0].startswith("T7 ") and len(shown) == 6
    assert ab.compact_journal(journal + [{"turn": 13, "action": "READ_MORE", "part": "1 of 1"}]).splitlines()[0].startswith("T7 ")


def test_unverified_numbers_ignores_grouped_citations():
    found = [{"extracted_data": "LibreOffice offers good compatibility."}]
    assert ab.unverified_numbers("LibreOffice has good compatibility [4, 5] and more [2;3].", found) == []
