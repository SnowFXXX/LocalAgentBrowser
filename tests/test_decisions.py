"""Rules the agent enforces before acting on a decision: repeats, impossible actions, revisits and the
confirm-before-leaving check."""
import pytest

import agent_browser as ab
from fakes import discoveries

DOCS = "https://docs.example/agents"
GUIDE = "https://guide.example/agents"
SEARCH_PAGE = "https://html.duckduckgo.com/html/?q=x"


def recorded_page(url, parts=("x",), index=0):
    """A content page the agent has already recorded something from (so no leave check applies)."""
    return {"url": url, "parts": list(parts), "index": index, "seen_parts": {index}, "visit_discoveries": 1}


def unrecorded_page(url, parts=("p1", "p2", "p3")):
    return {"url": url, "parts": list(parts), "index": 0, "seen_parts": {0}, "visit_discoveries": 0,
            "leave_confirmed": False}


def decide(action, parameter, page_state, current_url, queries=(), urls=(), journal=()):
    return ab.check_decision(action, parameter, list(journal), {"queries": list(queries), "urls": list(urls)},
                             page_state, current_url)


@pytest.mark.parametrize("action, parameter, expected", [
    ("SEARCH", "Playwright  Agents", "BLOCKED_SEARCH"),
    ("SEARCH", "", "BLOCKED_SEARCH"),
    ("SEARCH", "new query", None),
    ("NAVIGATE", DOCS + "#section", "BLOCKED_NAVIGATE"),
    ("NAVIGATE", "", "BLOCKED_NAVIGATE"),
    ("NAVIGATE", "https://example.com", None),
    ("READ_MORE", "", "BLOCKED_READ_MORE"),
    ("READ_MORE", "1", None),
    ("READ_MORE", "9", "BLOCKED_READ_MORE"),
    ("DISCOVER", "", "BLOCKED_DISCOVER"),
    ("DISCOVER", "already recorded fact", "DUPLICATE_DISCOVER"),
    ("DISCOVER", "something new", None),
    ("FINISH", "", None),
    ("JUMP", "x", "UNKNOWN_JUMP"),
])
def test_check_decision(action, parameter, expected):
    page = recorded_page(DOCS, parts=("a", "b"), index=1)
    result = decide(action, parameter, page, DOCS, queries=["playwright agents"], urls=[DOCS],
                    journal=discoveries("already recorded fact"))
    assert (result[0] if result else None) == expected


def test_going_back_to_an_earlier_page_is_allowed():
    assert decide("NAVIGATE", DOCS, recorded_page(GUIDE), GUIDE, urls=[DOCS, GUIDE]) is None


def test_navigating_to_the_current_page_is_refused():
    result = decide("NAVIGATE", GUIDE, recorded_page(GUIDE), GUIDE, urls=[DOCS, GUIDE])
    assert result[0] == "BLOCKED_NAVIGATE" and "already on this page" in result[1]


def test_visit_cap():
    page = recorded_page(GUIDE)
    assert decide("NAVIGATE", DOCS, page, GUIDE, urls=[DOCS, GUIDE, DOCS, GUIDE]) is None  # 3rd visit allowed
    result = decide("NAVIGATE", DOCS, page, GUIDE, urls=[DOCS, GUIDE, DOCS, GUIDE, DOCS])
    assert result[0] == "BLOCKED_NAVIGATE" and str(ab.MAX_VISITS_PER_URL) in result[1]


def test_visited_summary_counts_each_url_once():
    assert ab.visited_summary([DOCS, GUIDE, DOCS + "/"]) == f'["{DOCS} (visited 2 times)", "{GUIDE}"]'
    assert ab.visited_summary([]) == "[None]"


@pytest.mark.parametrize("parameter, expected_part", [("", 2), ("3", 3), ("2 of 2", 2), ("part 4", 4), ("4 of 5", 4)])
def test_read_more_target(parameter, expected_part):
    page = {"url": "u", "parts": list("abcde"), "index": 0}
    assert ab.read_more_target(parameter, page) + 1 == expected_part


class TestLeaveCheck:
    def test_leaving_with_nothing_recorded_asks_once_then_proceeds(self):
        page = unrecorded_page(GUIDE)
        first = decide("NAVIGATE", DOCS, page, GUIDE, urls=[DOCS, GUIDE])
        assert first[0] == "CONFIRM_LEAVE"
        assert "2 of its 3 parts are still unread" in first[1] and "repeat your NAVIGATE" in first[1]
        assert decide("NAVIGATE", DOCS, page, GUIDE, urls=[DOCS, GUIDE]) is None

    def test_no_check_after_recording_something(self):
        assert decide("SEARCH", "new q", recorded_page(GUIDE), GUIDE) is None

    def test_no_check_when_leaving_a_search_results_page(self):
        assert decide("NAVIGATE", DOCS, unrecorded_page(GUIDE), SEARCH_PAGE, urls=[DOCS, GUIDE]) is None

    def test_fully_read_page_has_no_unread_note(self):
        page = unrecorded_page(GUIDE)
        page["seen_parts"] = {0, 1, 2}
        message = decide("SEARCH", "q9", page, GUIDE)[1]
        assert "unread" not in message and "repeat your SEARCH" in message
