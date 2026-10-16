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
    return {"url": url, "parts": list(parts), "index": index, "seen_parts": {index}, "visit_discoveries": 1,
            "recorded_parts": {index}}


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
    ("FINISH", "summary", "CONFIRM_FINISH"),
    ("JUMP", "x", "UNKNOWN_JUMP"),
])
def test_check_decision(action, parameter, expected):
    page = recorded_page(DOCS, parts=("a", "b"), index=1)
    result = decide(action, parameter, page, DOCS, queries=["playwright agents"], urls=[DOCS],
                    journal=discoveries("already recorded fact"))
    assert (result[0] if result else None) == expected


def test_repeated_blocked_search_suggests_finishing_with_the_gap():
    tried = {"queries": ["parameter schemas"], "urls": []}
    page = recorded_page(DOCS)
    first = ab.check_decision("SEARCH", "Parameter  Schemas", [], tried, page, DOCS)
    assert first[0] == "BLOCKED_SEARCH" and "already searched" in first[1]
    second = ab.check_decision("SEARCH", "parameter schemas", [], tried, page, DOCS)
    assert "2 times" in second[1] and "FINISH and say in the final result that it could not be found" in second[1]


def test_going_back_to_an_earlier_page_is_allowed():
    assert decide("NAVIGATE", DOCS, recorded_page(GUIDE), GUIDE, urls=[DOCS, GUIDE]) is None


def test_navigating_to_the_current_page_is_refused():
    result = decide("NAVIGATE", GUIDE, recorded_page(GUIDE), GUIDE, urls=[DOCS, GUIDE])
    assert result[0] == "BLOCKED_NAVIGATE" and "already on this page" in result[1]


def test_refusals_name_the_current_page():
    page = recorded_page(GUIDE, parts=("a", "b", "c"), index=2)
    result = decide("READ_MORE", "4 of 4", page, GUIDE)
    assert result[0] == "BLOCKED_READ_MORE" and f"You are on {GUIDE} (part 3 of 3)" in result[1]
    assert f"You are on {GUIDE}" in decide("NAVIGATE", GUIDE, page, GUIDE, urls=[GUIDE])[1]


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


@pytest.mark.parametrize("parameter", [DOCS + "#comparison", "#comparison"])
def test_jumping_to_a_section_of_the_current_page_points_to_read_more(parameter):
    result = decide("NAVIGATE", parameter, recorded_page(DOCS, parts=("a", "b")), DOCS)
    assert result[0] == "BLOCKED_NAVIGATE" and "READ_MORE with that section's part number" in result[1]


def test_finish_check_asks_once_then_lets_finish_through():
    tried = {"queries": [], "urls": []}
    page = recorded_page(DOCS)
    first = ab.check_decision("FINISH", "my answer", [], tried, page, DOCS)
    assert first[0] == "CONFIRM_FINISH"
    assert "separate parts" in first[1] and "'not covered'" in first[1] and "repeat FINISH" in first[1]
    assert "'written at FINISH'" in first[1] and "never as a DISCOVER" in first[1]
    assert "say in the final result what could not be found" in first[1]
    assert ab.check_decision("FINISH", "my answer", [], tried, page, DOCS) is None


def test_finish_check_lists_the_discoveries_numbered():
    long_fact = "The Calvin cycle occurs in the stroma. " * 10
    journal = discoveries("Light reactions happen in the thylakoids.", long_fact)
    check = ab.check_decision("FINISH", "answer", journal, {"queries": [], "urls": []}, recorded_page(DOCS), DOCS)[1]
    assert "[1] Light reactions happen in the thylakoids." in check and "[2] The Calvin cycle occurs" in check
    assert long_fact.strip() not in check, "shortened in the check"
    assert "'covered by [2]'" in check


def test_rereading_a_part_is_confirmed_once():
    page = recorded_page(DOCS, parts=[f"p{i}" for i in range(11)], index=9)
    page["seen_parts"] = {0, 1, 2, 3, 9}
    first = decide("READ_MORE", "3", page, DOCS)
    assert first[0] == "CONFIRM_REREAD" and "already read part 3" in first[1]
    assert decide("READ_MORE", "3", page, DOCS) is None, "repeating it goes through"
    assert decide("READ_MORE", "11", page, DOCS) is None, "unread parts need no confirmation"


class TestReadOnCheck:
    def test_reading_on_from_an_unrecorded_part_asks_once_then_proceeds(self):
        page = unrecorded_page(DOCS)
        first = decide("READ_MORE", "", page, DOCS)
        assert first[0] == "CONFIRM_READ_ON" and "recorded nothing from part 1" in first[1]
        assert "write 'none'" in first[1] and "choose FINISH instead of reading on" in first[1]
        assert "not about the OBJECTIVE at all, leave it" in first[1]
        assert decide("READ_MORE", "", page, DOCS) is None, "repeating it goes through"

    def test_no_check_after_recording_from_the_shown_part(self):
        assert decide("READ_MORE", "", recorded_page(DOCS, parts=("a", "b")), DOCS) is None

    def test_asked_again_for_the_next_unrecorded_part(self):
        page = unrecorded_page(DOCS)
        page["read_on_confirmed"] = {0}
        page["index"] = 1
        page["seen_parts"] = {0, 1}
        assert decide("READ_MORE", "", page, DOCS)[0] == "CONFIRM_READ_ON"

    def test_no_check_after_a_duplicate_from_the_shown_part(self):
        page = unrecorded_page(DOCS)
        journal = discoveries("already recorded fact")
        assert decide("DISCOVER", "already recorded fact", page, DOCS, journal=journal)[0] == "DUPLICATE_DISCOVER"
        assert decide("READ_MORE", "", page, DOCS, journal=journal) is None

    def test_going_back_to_an_earlier_part_is_not_reading_on(self):
        page = unrecorded_page(DOCS, parts=("a", "b", "c", "d", "e"))
        page.update(index=4, seen_parts={0, 1, 2, 3, 4}, reread_confirmed={2})
        assert decide("READ_MORE", "3", page, DOCS) is None

    def test_asking_for_the_part_already_shown_means_the_next_one(self):
        assert ab.read_more_target("1", unrecorded_page(DOCS)) == 1
        assert decide("READ_MORE", "1", recorded_page(DOCS, parts=("a", "b")), DOCS) is None
        last = recorded_page(DOCS, parts=("a", "b"), index=1)
        assert ab.read_more_target("2", last) == 1, "on the last part it stays a request for that part"
        assert "last part" in decide("READ_MORE", "2", last, DOCS)[1]


def test_outline_marks_parts_already_read():
    page = {"parts": ["a"] * 6, "outline": [f"Section {i}" for i in range(6)], "index": 2, "seen_parts": {0, 2}}
    outline = ab.render_outline(page)
    assert "  1. Section 0 (read)" in outline and "  3. Section 2 (read)" in outline and "  2. Section 1\n" in outline


def test_finish_check_mentions_unread_parts_of_the_current_page():
    page = recorded_page(DOCS, parts=("a", "b", "c", "d", "e", "f"), index=2)
    page["seen_parts"] = {0, 1, 2}
    message = ab.check_decision("FINISH", "answer", [], {"queries": [], "urls": []}, page, DOCS)[1]
    assert "still has 3 unread part(s)" in message


def test_finish_check_points_long_pages_to_the_outline():
    page = recorded_page(DOCS, parts=[f"p{i}" for i in range(8)], index=2)
    page["seen_parts"] = {0, 1, 2}
    message = ab.check_decision("FINISH", "answer", [], {"queries": [], "urls": []}, page, DOCS)[1]
    assert "5 unread parts: use its outline" in message


def test_finish_is_sent_back_when_its_own_check_lists_a_gap():
    tried = {"queries": [], "urls": [], "finish_confirmed": True}  # part-by-part check already done this turn
    page = recorded_page(DOCS)
    gap = "Performance: covered by [1]. Available libraries: Not covered. Written at FINISH."
    first = ab.check_decision("FINISH", "answer", [], tried, page, DOCS, gap)
    assert first[0] == "BLOCKED_FINISH" and "'not covered'" in first[1]
    assert ab.check_decision("FINISH", "answer", [], tried, page, DOCS, "Every part is covered by [1] and [2].") is None
    assert ab.check_decision("FINISH", "answer", [], tried, page, DOCS, gap)[0] == "BLOCKED_FINISH"
    assert ab.check_decision("FINISH", "answer", [], tried, page, DOCS, gap) is None, \
        f"after {ab.GAP_REFUSAL_LIMIT} refusals a gap that cannot be filled no longer blocks finishing"


@pytest.mark.parametrize("sources, expected", [
    ([], "You have no discoveries yet."),
    ([DOCS], f"Your only discovery comes from {DOCS}."),
    ([DOCS, DOCS + "/", DOCS + "#part"], f"All 3 of your discoveries come from a single page ({DOCS})."),
    ([DOCS, GUIDE, DOCS], "Your 3 discoveries come from 2 different pages."),
], ids=["none", "one discovery", "one page", "two pages"])
def test_source_note(sources, expected):
    journal = [{"action": "DISCOVER", "source": s, "extracted_data": f"fact {i}"} for i, s in enumerate(sources)]
    assert ab.source_note(journal).startswith(expected)
    finish = ab.check_decision("FINISH", "answer", journal, {"queries": [], "urls": []}, recorded_page(DOCS), DOCS)
    assert finish[1].startswith(expected)


class TestLeaveCheck:
    def test_leaving_with_nothing_recorded_asks_once_then_proceeds(self):
        page = unrecorded_page(GUIDE)
        first = decide("NAVIGATE", DOCS, page, GUIDE, urls=[DOCS, GUIDE])
        assert first[0] == "CONFIRM_LEAVE"
        assert "2 of its 3 parts are still unread" in first[1] and "repeat your NAVIGATE" in first[1]
        assert decide("NAVIGATE", DOCS, page, GUIDE, urls=[DOCS, GUIDE]) is None

    def test_no_check_when_something_was_recorded_here_on_an_earlier_visit(self):
        journal = [{"action": "DISCOVER", "source": GUIDE + "/", "extracted_data": "fact"}]
        assert ab.check_decision("NAVIGATE", DOCS, journal, {"queries": [], "urls": [DOCS, GUIDE]},
                                 unrecorded_page(GUIDE), GUIDE) is None

    def test_no_check_after_recording_something(self):
        assert decide("SEARCH", "new q", recorded_page(GUIDE), GUIDE) is None

    def test_no_check_when_leaving_a_search_results_page(self):
        assert decide("NAVIGATE", DOCS, unrecorded_page(GUIDE), SEARCH_PAGE, urls=[DOCS, GUIDE]) is None

    def test_long_page_points_to_the_outline_instead_of_the_unread_count(self):
        page = unrecorded_page(GUIDE, parts=[f"p{i}" for i in range(33)])
        message = decide("NAVIGATE", DOCS, page, GUIDE, urls=[DOCS, GUIDE])[1]
        assert "long page (33 parts, 32 unread)" in message and "outline" in message
        assert "32 of its 33 parts are still unread" not in message

    def test_fully_read_page_has_no_unread_note(self):
        page = unrecorded_page(GUIDE)
        page["seen_parts"] = {0, 1, 2}
        message = decide("SEARCH", "q9", page, GUIDE)[1]
        assert "unread" not in message and "repeat your SEARCH" in message


class TestGroundingCheck:
    PAGE = ("Repotting guide. Choose a pot one to two inches wider than the current one, with drainage holes. "
            "Now it is time to repot. Just follow these six easy steps.")

    def page(self):
        page = unrecorded_page(GUIDE, parts=(self.PAGE, "Step 1: Water the plant the day before."))
        return page

    def test_a_discover_the_page_does_not_say_is_confirmed_once(self):
        page = self.page()
        invented = ("1. Gently remove the plant from its pot. 2. Loosen any tangled roots and trim dead ones. "
                    "3. Place fresh potting mix in the bottom of the new pot.")
        first = decide("DISCOVER", invented, page, GUIDE)
        assert first[0] == "CONFIRM_GROUNDED" and "does not match the page text" in first[1]
        assert "DISCOVER again with only what the page actually says in part 1" in first[1]
        assert decide("DISCOVER", invented, page, GUIDE) is None, "repeating it goes through"

    def test_a_discover_copied_from_the_page_passes(self):
        copied = "Choose a pot one to two inches wider than the current one, with drainage holes."
        assert decide("DISCOVER", copied, self.page(), GUIDE) is None

    def test_short_discoveries_are_not_checked(self):
        assert decide("DISCOVER", "Use a bigger pot.", self.page(), GUIDE) is None

    def test_real_advice_mixed_with_invented_steps_is_confirmed(self):
        mixed = ("Choose a pot one to two inches wider than the current one, with drainage holes. The steps are: "
                 "loosen and inspect the roots, prune any dead roots, then fill with fresh potting mix.")
        assert decide("DISCOVER", mixed, self.page(), GUIDE)[0] == "CONFIRM_GROUNDED"


def test_recorded_parts_are_remembered_across_visits(monkeypatch):
    import asyncio

    async def parts(page):
        return ["part one", "part two"], [["One"], ["Two"]]

    async def links(page, shown_links=None):
        return ""

    monkeypatch.setattr(ab, "extract_page_parts", parts)
    monkeypatch.setattr(ab, "extract_page_links", links)
    state = {"url": None, "parts": [], "index": 0}
    asyncio.run(ab.build_environment(None, GUIDE, state))
    state["recorded_parts"].add(0)  # what the main loop does on DISCOVER
    asyncio.run(ab.build_environment(None, DOCS, state))
    assert decide("READ_MORE", "", state, DOCS)[0] == "CONFIRM_READ_ON"
    asyncio.run(ab.build_environment(None, GUIDE, state))
    assert decide("READ_MORE", "", state, GUIDE) is None, "part 1 was recorded on the earlier visit"


def test_a_different_ungrounded_discover_is_checked_again():
    page = TestGroundingCheck().page()
    first = ("1. Gently remove the plant from its pot. 2. Loosen any tangled roots and trim dead ones before you "
             "start. 3. Place fresh potting mix in the bottom of the new pot.")
    second = ("During the first two weeks water your plant sparingly and avoid fertilizing it until it settles in. "
              "Keep it out of direct sun and check the soil every few days.")
    assert decide("DISCOVER", first, page, GUIDE)[0] == "CONFIRM_GROUNDED"
    assert decide("DISCOVER", second, page, GUIDE)[0] == "CONFIRM_GROUNDED"
    assert decide("DISCOVER", second, page, GUIDE) is None, "repeating the same one goes through"


def test_the_objectives_wording_counts_as_grounded():
    page = unrecorded_page(DOCS, parts=("Kilimanjaro is a dormant volcano in Tanzania and the highest mountain in "
                                        "Africa, at 5,895 m (19,341 ft) above sea level and 4,900 m above its base.",))
    finding = ("The tallest mountain in Africa is Kilimanjaro, a dormant volcano in Tanzania. How tall is it in "
               "meters and in feet: 5,895 m (19,341 ft) above sea level.")
    tried = {"queries": [], "urls": []}
    assert ab.check_decision("DISCOVER", finding, [], tried, page, DOCS)[0] == "CONFIRM_GROUNDED"
    page["grounding_asked"] = []
    tried["objective"] = "What is the tallest mountain in Africa, and how tall is it in meters and in feet?"
    assert ab.check_decision("DISCOVER", finding, [], tried, page, DOCS) is None


def test_an_invented_sentence_behind_copied_ones_is_confirmed():
    page = unrecorded_page(GUIDE, parts=(
        "Owls have played a crucial role in Native American cultures since time immemorial, with sightings often "
        "associated with shapeshifters, spiritual messages and supernatural signs. In Christianity owls are mentioned "
        "in several places.",))
    finding = ("Owls have played a crucial role in Native American cultures since time immemorial, with sightings often "
               "associated with shapeshifters, spiritual messages and supernatural signs. In Christianity, owls symbolize "
               "wisdom and are often depicted alongside other wise figures like the prophet Samuel.")
    assert decide("DISCOVER", finding, page, GUIDE)[0] == "CONFIRM_GROUNDED"


def test_finish_goes_through_once_a_search_is_exhausted():
    tried = {"queries": ["schemas"], "urls": []}
    page = recorded_page(DOCS)
    for _ in range(2):
        assert ab.check_decision("SEARCH", "schemas", [], tried, page, DOCS)[0] == "BLOCKED_SEARCH"
    assert ab.check_decision("FINISH", "result", [], tried, page, DOCS, "Schemas: not covered") is None
