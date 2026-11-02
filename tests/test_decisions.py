"""Rules the agent enforces before acting on a decision: repeats, impossible actions, revisits and the
confirm-before-leaving check."""
import asyncio

import pytest

import agent_browser as ab
from localagentbrowser import pages
from fakes import discoveries, fake_client, reply

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

    def test_a_discover_the_page_does_not_say_is_refused_even_when_repeated(self):
        page = self.page()
        invented = ("1. Gently remove the plant from its pot. 2. Loosen any tangled roots and trim dead ones. "
                    "3. Place fresh potting mix in the bottom of the new pot.")
        for _ in range(2):
            result = decide("DISCOVER", invented, page, GUIDE)
            assert result[0] == "BLOCKED_NOT_ON_PAGE" and "comes from your own knowledge" in result[1]

    def test_a_discover_copied_from_the_page_passes(self):
        copied = "Choose a pot one to two inches wider than the current one, with drainage holes."
        assert decide("DISCOVER", copied, self.page(), GUIDE) is None

    def test_short_discoveries_are_not_checked(self):
        assert decide("DISCOVER", "Use a bigger pot.", self.page(), GUIDE) is None

    def test_real_advice_mixed_with_invented_steps_is_refused(self):
        mixed = ("Choose a pot one to two inches wider than the current one, with drainage holes. The steps are: "
                 "loosen and inspect the roots, prune any dead roots, then fill with fresh potting mix.")
        assert decide("DISCOVER", mixed, self.page(), GUIDE)[0] == "BLOCKED_NOT_ON_PAGE"

    VENV = ("Save dependencies with pip freeze > requirements.txt and restore them with pip install -r requirements.txt.\n"
            "Activate it before installing anything: source <venv>/bin/activate on macOS/Linux, venv\\Scripts\\activate "
            "on Windows.")

    def test_a_command_the_page_never_gives_is_refused_even_when_repeated(self):
        # The venv case: an invented command among copied ones, whose words scored as on the page
        page = unrecorded_page(GUIDE, parts=(self.VENV, "b"))
        invented = ("To install packages into the virtual environment, use `pip install package_name` inside the "
                    "activated venv folder. To capture installed packages, run `pip freeze > requirements.txt`.")
        for _ in range(2):
            result = decide("DISCOVER", invented, page, GUIDE)
            assert result[0] == "BLOCKED_CODE_NOT_ON_PAGE" and "never gives `pip install package_name`" in result[1]
            assert ('what the parts shown do say with it: "Save dependencies with pip freeze > requirements.txt and '
                    'restore them with pip install -r requirements.txt."') in result[1]

    TABLE = ("Shell\nCommand to activate virtual environment\nPOSIX\nbash/zsh\n$ source <venv>/bin/activate\npwsh\n"
             "$ <venv>/bin/Activate.ps1\nWindows\ncmd.exe\nC:\\> <venv>\\Scripts\\activate.bat\nPowerShell\n"
             "PS C:\\> <venv>\\Scripts\\Activate.ps1")

    def test_a_swapped_path_outside_backticks_is_refused_and_the_page_line_quoted(self):
        # The 7B's copy of the official activation table, in two runs, with "bin" and "Scripts" swapped
        page = unrecorded_page(GUIDE, parts=(self.TABLE, "b"))
        swapped = ("POSIX\\nbash/zsh\n$ source <venv>/bin/activate\nWindows\\ncmd.exe\nC:\\> <venv>\\bin\\activate.bat\n"
                   "PowerShell\nPS C:\\> <venv>\\Scripts\\Activate.ps1")
        assert ab.invented_code(swapped, [self.TABLE]) == ["<venv>\\bin\\activate.bat"]
        result = decide("DISCOVER", swapped, page, GUIDE)
        assert result[0] == "BLOCKED_CODE_NOT_ON_PAGE" and '"C:\\> <venv>\\Scripts\\activate.bat"' in result[1]

    def test_prose_slashes_and_web_addresses_are_not_paths(self):
        text = ("On macOS/Linux, see https://docs.python.org/3/library/venv.html or www.example.com/a/b/c, "
                "or docs.python.org/3/library/venv.html, and/or 1/4 cup.")
        assert ab.invented_code(text, ["nothing here"]) == []

    def test_commands_copied_from_the_page_pass_whatever_their_case_spacing_or_placeholder_brackets(self):
        page = unrecorded_page(GUIDE, parts=(self.VENV, "b"))
        copied = ("Restore them with `pip install -r  requirements.txt`. Activate it: `source venv/bin/activate` on "
                  "macOS/Linux, `venv\\Scripts\\Activate` on Windows.")
        assert ab.invented_code(copied, [self.VENV]) == []
        assert decide("DISCOVER", copied, page, GUIDE) is None


def test_recorded_parts_are_remembered_across_visits(monkeypatch):
    import asyncio

    async def parts(page):
        return ["part one", "part two"], [["One"], ["Two"]], ["One", "Two"]

    async def links(page, shown_links=None):
        return ""

    monkeypatch.setattr(pages, "extract_page_parts", parts)
    monkeypatch.setattr(pages, "extract_page_links", links)
    state = {"url": None, "parts": [], "index": 0}
    asyncio.run(ab.build_environment(None, GUIDE, state))
    state["recorded_parts"].add(0)  # what the main loop does on DISCOVER
    asyncio.run(ab.build_environment(None, DOCS, state))
    assert decide("READ_MORE", "", state, DOCS)[0] == "CONFIRM_READ_ON"
    asyncio.run(ab.build_environment(None, GUIDE, state))
    assert decide("READ_MORE", "", state, GUIDE) is None, "part 1 was recorded on the earlier visit"


def test_a_different_ungrounded_discover_is_refused_again():
    page = TestGroundingCheck().page()
    first = ("1. Gently remove the plant from its pot. 2. Loosen any tangled roots and trim dead ones before you "
             "start. 3. Place fresh potting mix in the bottom of the new pot.")
    second = ("During the first two weeks water your plant sparingly and avoid fertilizing it until it settles in. "
              "Keep it out of direct sun and check the soil every few days.")
    assert decide("DISCOVER", first, page, GUIDE)[0] == "BLOCKED_NOT_ON_PAGE"
    assert decide("DISCOVER", second, page, GUIDE)[0] == "BLOCKED_NOT_ON_PAGE"


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
        "associated with shapeshifters, spiritual messages and supernatural signs. In Christianity, owls symbolize "
        "wisdom and appear in several places.",))
    finding = ("Owls have played a crucial role in Native American cultures since time immemorial, with sightings often "
               "associated with shapeshifters, spiritual messages and supernatural signs. In Christianity, owls symbolize "
               "wisdom and are often depicted alongside other wise figures like the prophet Samuel.")
    first = decide("DISCOVER", finding, page, GUIDE)
    assert first[0] == "CONFIRM_GROUNDED" and "DISCOVER again with only what the page actually says in part 1" in first[1]
    assert decide("DISCOVER", finding, page, GUIDE) is None, "repeating a borderline one goes through"


def test_finish_goes_through_once_a_search_is_exhausted():
    tried = {"queries": ["schemas"], "urls": []}
    page = recorded_page(DOCS)
    for _ in range(2):
        assert ab.check_decision("SEARCH", "schemas", [], tried, page, DOCS)[0] == "BLOCKED_SEARCH"
    assert ab.check_decision("FINISH", "result", [], tried, page, DOCS, "Schemas: not covered") is None


class TestPageHint:
    PARTS = ["Lightly water a few hours before repotting to help remove the plant from its pot. "
             "After repotting, water again to help settle the potting mix.",
             "This symbolism is particularly prevalent in some Native American and African cultures."]

    def test_quotes_the_one_closest_sentence(self):
        hint = ab.page_hint("Do not water the repotted plant again for weeks after repotting.", self.PARTS,
                            "How do I care for a plant after repotting?")
        assert 'What the page does say about it: "After repotting, water again to help settle the potting mix."' in hint
        assert "DISCOVER exactly that now" in hint

    def test_names_the_page_never_mentions_instead_of_quoting(self):
        hint = ab.page_hint("In Japan, owls bring good fortune. Among the Navajo, owls are sacred in some cultures.",
                            self.PARTS)
        assert hint == " The parts shown never mention Japan, Navajo."

    def test_nothing_close_gives_no_hint(self):
        assert ab.page_hint("Completely unrelated words here.", self.PARTS) == ""


def test_a_refusal_for_text_not_on_the_page_is_not_wrapped_in_do_not_repeat():
    page = {"url": GUIDE, "parts": ["Water the plant lightly before repotting it into a bigger pot."], "index": 0,
            "seen_parts": {0}, "visit_discoveries": 1}
    invented = ("Repotting steps: first remove the old soil completely, then soak the roots in a fungicide bath for "
                "an hour, then trim every brown root with sterile scissors before planting it again.")
    client, model = fake_client([reply("DISCOVER", invented), reply("SEARCH", "repotting steps")])
    asyncio.run(ab.request_decision(client, [{"role": "user", "content": "x"}], [], {"queries": [], "urls": []}, page, GUIDE))
    correction = model.calls[1][-1]["content"]
    assert correction.startswith("REFUSED: That DISCOVER is mostly not in the page text") and "Do NOT choose" not in correction


def test_a_finding_from_the_search_results_page_is_refused():
    search = "https://html.duckduckgo.com/html/?q=free+office+suites"
    page = {"url": search, "parts": ["LibreOffice is a free and open source office suite."], "index": 0}
    refusal = ab.check_decision("DISCOVER", "LibreOffice is a free and open source office suite.", [],
                                {"queries": ["free office suites"], "urls": []}, page, search)
    assert refusal[0] == "BLOCKED_DISCOVER_SEARCH" and "NAVIGATE to the result" in refusal[1]
    assert not ab.is_search_page("https://www.libreoffice.org/about-us/")


def test_a_search_result_title_and_description_recorded_on_the_page_are_refused():
    results = ("[1] How to Make Cold Brew Coffee: A Complete Guide (ALREADY VISITED)\n    URL: https://brew.example/cold\n"
               "    Learn how to make cold brew at home, the ratio and how to store it.")
    page = recorded_page("https://brew.example/cold", ["How to Make Cold Brew Coffee: A Complete Guide\nUse 1 cup..."])
    page["snippets"] = ab.snippet_lines(results)
    copied = ("How to Make Cold Brew Coffee: A Complete Guide\nURL: https://brew.example/cold\n"
              "Learn how to make cold brew at home, the ratio and how to store it.")
    tried = {"queries": [], "urls": []}
    refusal = ab.check_decision("DISCOVER", copied, [], tried, page, "https://brew.example/cold")
    assert refusal[0] == "BLOCKED_DISCOVER_SNIPPET"
    own = "How to Make Cold Brew Coffee: A Complete Guide\nUse 1 cup of coarse coffee to 4 cups of water."
    assert not ab.is_snippet_discovery(own, page), "a line from the page itself makes it a real finding"


def test_a_search_query_that_runs_on_is_cut_to_its_first_words():
    long_query = "how to make cold brew coffee at home " + "official documentation site only no repeats " * 5
    assert len(ab.search_query(long_query).split()) == ab.MAX_QUERY_WORDS
    assert ab.search_query(long_query).startswith("how to make cold brew coffee at home official")
    eighteen = "how to activate a virtual environment with venv on windows and macos or linux in official python documentation"
    assert ab.search_query(eighteen) == eighteen


def test_a_discovery_that_is_only_a_web_address_is_refused():
    page = recorded_page(GUIDE, ["Use 1 cup of coarse coffee to 4 cups of water."])
    for bare in (GUIDE, "URL: " + GUIDE):
        refusal = ab.check_decision("DISCOVER", bare, [], {"queries": [], "urls": [], "objective": "cold brew coffee to water"}, page, GUIDE)
        assert refusal[0] == "BLOCKED_DISCOVER_URL" and "does not open or look up" in refusal[1]
        assert "Use 1 cup of coarse coffee to 4 cups of water." in refusal[1], "a page sentence to copy is quoted"


def test_a_discovery_that_repeats_the_objective_is_refused_but_an_answer_in_its_words_is_not():
    objective = ("How do I make cold brew coffee at home? List what I need with the quantities, give the steps in "
                 "order, and say how long it keeps in the fridge.")
    page = recorded_page(GUIDE, ["Cold brew keeps in the fridge for up to two weeks. Use 1 cup of coarse coffee "
                                 "to 4 cups of water and steep it for 16 hours."])
    tried = {"queries": [], "urls": [], "objective": objective}
    refusal = ab.check_decision("DISCOVER", objective, [], tried, page, GUIDE)
    assert refusal[0] == "BLOCKED_DISCOVER_OBJECTIVE"
    assert not ab.echoes_objective("Cold brew keeps in the fridge for up to two weeks.", page, objective)


def test_a_discovery_that_is_only_a_heading_is_refused_but_a_heading_with_its_facts_is_not():
    page = recorded_page(GUIDE, ["Method\nPut the coffee in the jar.", "How long to keep cold brew in the fridge?\nUp to two weeks."])
    page["outline"] = ["Method", "How long to keep cold brew in the fridge? / Storage"]
    tried = {"queries": [], "urls": []}
    for heading in ("Method", "How long to keep cold brew in the fridge?", "Method (read)"):
        assert ab.check_decision("DISCOVER", heading, [], tried, page, GUIDE)[0] == "BLOCKED_DISCOVER_HEADING"
    assert not ab.is_heading_discovery("Method\nPut the coffee in the jar.", page)
    page["outline"] = ["Cold brew keeps for up to two weeks."]  # a part with no heading is labelled by its first line
    assert not ab.is_heading_discovery("Cold brew keeps for up to two weeks.", page)


def test_navigate_opens_the_first_url_when_given_several_or_a_title_before_it():
    several = "https://brew.example/cold https://cafe.example/guide https://news.example/recipe"
    assert ab.determine_url("NAVIGATE", several) == "https://brew.example/cold"
    titled = "How to Make Cold Brew Coffee: A Complete Guide https://brew.example/cold."
    assert ab.determine_url("NAVIGATE", titled) == "https://brew.example/cold"
    assert ab.determine_url("NAVIGATE", "docs.python.org/3/library/venv.html") == "https://docs.python.org/3/library/venv.html"


def test_an_empty_discovery_with_facts_in_the_reasoning_is_told_only_the_parameter_is_recorded():
    page = recorded_page(GUIDE, ["Start with a 1:5 ratio."])
    thought = "The page recommends starting with a 1:5 ratio. I should DISCOVER these details."
    refusal = ab.check_decision("DISCOVER", "", [], {"queries": [], "urls": []}, page, GUIDE, thought)
    assert refusal[0] == "BLOCKED_DISCOVER" and "only the parameter is recorded" in refusal[1]
    assert "READ_MORE or SEARCH instead" in refusal[1]


def test_a_short_discovery_adding_nothing_to_the_objective_is_refused_but_a_new_name_or_number_is_not():
    objective = ("How do I make cold brew coffee at home? List what I need with the quantities, give the steps in "
                 "order, and say how long it keeps in the fridge.")
    page = recorded_page(GUIDE, ["Use 1 cup of coarse coffee to 4 cups of water. Cold brew keeps in the fridge for two weeks."])
    for label in ("What you need for cold brew coffee at home",
                  "How to make cold brew coffee at home with the given quantities and in order"):
        assert ab.echoes_objective(label, page, objective), label
    assert not ab.echoes_objective("Cold brew keeps in the fridge for two weeks.", page, objective)
    office = "Find free and open source alternatives to Microsoft Office"
    office_page = recorded_page(GUIDE, ["LibreOffice is free and open source."])
    assert not ab.echoes_objective("LibreOffice is free and open source.", office_page, office)


def test_read_more_on_the_results_page_says_to_navigate_to_a_result():
    refusal = ab.check_decision("READ_MORE", "2", [], {"queries": ["x"], "urls": []}, unrecorded_page(GUIDE), SEARCH_PAGE)
    assert refusal[0] == "BLOCKED_READ_MORE" and "NAVIGATE to its URL" in refusal[1]


def test_a_result_title_with_its_url_added_is_still_refused():
    results = "[1] How to Make Cold Brew Coffee: A Complete Guide\n    URL: https://brew.example/cold\n    Learn how."
    page = recorded_page("https://brew.example/cold", ["How to Make Cold Brew Coffee: A Complete Guide\nUse 1 cup..."])
    page["snippets"] = ab.snippet_lines(results)
    titled = "How to Make Cold Brew Coffee: A Complete Guide from https://brew.example/cold"
    assert ab.is_snippet_discovery(titled, page)
    assert not ab.is_snippet_discovery("Use 1 cup of coffee to 4 cups of water, from https://brew.example/cold", page)


def test_page_title_joined_to_step_headings_is_refused_as_a_heading():
    page = recorded_page(GUIDE, ["Grind about 1 cup of beans coarse, like sea salt."])
    page["headings"] = ["How to Make Cold Brew Coffee: Step-by-Step", "Step 1: Grind Your Coffee", "Step 2: Combine Your Coffee and Water"]
    for joined in ("How to Make Cold Brew Coffee: Step-by-Step Grind Your Coffee",
                   "How to Make Cold Brew Coffee: Step-by-Step Combine Your Coffee and Water"):
        assert ab.is_heading_discovery(joined, page), joined
    assert not ab.is_heading_discovery("Grind about 1 cup of beans coarse, like sea salt", page)


def test_a_discovery_describing_the_page_instead_of_giving_its_facts_is_refused():
    page = recorded_page(GUIDE, ["How to Make Cold Brew Coffee: A Complete Step-by-Step Guide. Use 1 cup of coffee."])
    described = ("How to Make Cold Brew Coffee: A Complete Step-by-Step Guide from https://brew.example/cold contains a "
                 "list of what is needed, step-by-step instructions, and information on how long it keeps in the fridge.")
    tried = {"queries": [], "urls": []}
    for _ in range(2):  # repeating it does not get it recorded
        assert ab.check_decision("DISCOVER", described, [], tried, page, GUIDE)[0] == "BLOCKED_DISCOVER_DESCRIBES"
    assert not ab.DESCRIBES_PAGE.search("Use 1 cup of coarse coffee to 4 cups of water; it keeps for two weeks.")


def test_a_discovery_that_repeats_the_models_own_search_query_is_refused():
    objective = ("How do I make cold brew coffee at home? List what I need with the quantities, give the steps in "
                 "order, and say how long it keeps in the fridge.")
    query = ("how to make cold brew coffee at home with quantities, steps, and shelf life in fridge official "
             "documentation site recipes")
    page = recorded_page(GUIDE, ["How to Make Cold Brew Coffee: A Complete Step-by-Step Guide\nThe short answer: "
                                 "mix 1 cup of coarse coffee with 4 cups of water and steep it for 16 hours."])
    tried = {"queries": [query], "urls": [], "objective": objective}
    recorded = ("How to Make Cold Brew Coffee: A Complete Step-by-Step Guide https://brew.example/cold How To Make "
                "Cold Brew Coffee at Home with quantities, steps and shelf life in fridge official documentation site "
                "recipes guides")
    assert ab.check_decision("DISCOVER", recorded, [], tried, page, GUIDE)[0] == "BLOCKED_DISCOVER_OBJECTIVE"
    real = "The short answer: mix 1 cup of coarse coffee with 4 cups of water and steep it for 16 hours."
    assert ab.check_decision("DISCOVER", real, [], tried, page, GUIDE) is None


def test_a_real_finding_once_used_as_a_search_query_is_not_refused():
    objective = "Find free open source alternatives to Microsoft Office and say what each is best at"
    fact = "Apache OpenOffice is an open-source suite that offers good compatibility with MS Office file formats."
    page = recorded_page(GUIDE, [fact])
    tried = {"queries": [fact], "urls": [], "objective": objective}
    assert ab.check_decision("DISCOVER", fact, [], tried, page, GUIDE) is None


def test_a_refused_page_description_quotes_the_page_sentence_to_record():
    page = recorded_page(GUIDE, ["Our shop opened in 2015 near the harbour. Use a 1:6 coffee to water ratio by weight."])
    described = ("The current page mentions a 1:6 coffee to water ratio, but does not provide step-by-step "
                 "instructions. I should DISCOVER any relevant details from this page.")
    tried = {"queries": [], "urls": [], "objective": "How do I make cold brew coffee at home with the quantities?"}
    refusal = ab.check_decision("DISCOVER", described, [], tried, page, GUIDE)
    assert refusal[0] == "BLOCKED_DISCOVER_DESCRIBES" and "Use a 1:6 coffee to water ratio by weight." in refusal[1]


def test_on_the_results_page_the_navigate_refusal_comes_before_the_others():
    query = "how to make cold brew coffee at home with quantities steps and shelf life"
    tried = {"queries": [query], "urls": [], "objective": "How do I make cold brew coffee at home?"}
    page = {"url": None, "parts": [], "index": 0}
    for text in (query, "https://brew.example/cold", "How to Make Cold Brew Coffee: A Complete Guide"):
        assert ab.check_decision("DISCOVER", text, [], tried, page, SEARCH_PAGE)[0] == "BLOCKED_DISCOVER_SEARCH", text


def test_read_more_ignores_numbers_inside_a_web_address():
    page = {"url": GUIDE, "parts": ["a", "b", "c"], "index": 0, "seen_parts": {0}}
    assert ab.read_more_target("https://www.example.com/7843793/how-make-cold-brew#recipe-1026", page) == 1
    assert ab.read_more_target("part 3 of https://www.example.com/7843793/x", page) == 2


def test_the_quoted_page_sentence_is_never_a_bare_title():
    parts = ["The Right Way to Make Cold Brew Coffee, According to the Pros\nSteep coarse coffee in cold water for 8-24 hours."]
    assert ab.closest_sentence("make cold brew coffee at home", parts) == "Steep coarse coffee in cold water for 8-24 hours."


def test_the_quoted_page_sentence_is_never_one_the_describes_check_refuses():
    parts = ["This complete guide shows you exactly how to create a virtual environment with venv and activate it on "
             "Windows.\nActivate it before installing anything: venv\\Scripts\\activate on Windows."]
    assert ab.closest_sentence("create and activate a virtual environment with venv on Windows", parts) == (
        "Activate it before installing anything: venv\\Scripts\\activate on Windows.")
    assert ab.DESCRIBES_PAGE.search("This complete guide shows you how")
    faq = ["What is the basic formula for photosynthesis?\nPhotosynthesis turns carbon dioxide and water into sugar and oxygen."]
    assert ab.closest_sentence("the basic formula for photosynthesis and its sugar", faq) == (
        "Photosynthesis turns carbon dioxide and water into sugar and oxygen.")


def test_outline_labels_with_part_notes_are_refused_as_headings():
    page = recorded_page(GUIDE, ["The grind: coarse, like sea salt\nUse a burr grinder."])
    page["headings"] = ["The grind: coarse, like sea salt", "The cold brew ratio explained", "Method"]
    copied = "The grind: coarse, like sea salt (part 2), The cold brew ratio explained (part 3), Method (part 4)"
    assert ab.is_heading_discovery(copied, page)


def test_a_discovery_that_only_asks_questions_is_refused_but_a_question_with_its_answer_is_not():
    page = recorded_page(GUIDE, ["Steep 1 cup of coffee in 4 cups of water for 16 hours. It keeps for two weeks."])
    asked = ("what are the quantities needed for cold brew coffee at home, what are the steps in order to make it, "
             "and how long does it keep in the fridge")
    tried = {"queries": [], "urls": []}
    assert ab.check_decision("DISCOVER", asked, [], tried, page, GUIDE)[0] == "BLOCKED_DISCOVER_QUESTION"
    assert not ab.is_question_discovery("How to activate a virtual environment on macOS or Linux: $ source <venv>/bin/activate")
    assert not ab.is_question_discovery("Steep 1 cup of coffee in 4 cups of water for 16 hours.")


def test_steps_that_start_like_questions_are_not_refused_as_questions():
    for step in ("Do not grind the coffee too fine", "Can be kept in the fridge for up to two weeks",
                 "Is best served over ice with milk"):
        assert not ab.is_question_discovery(step), step


def test_a_repeated_search_on_an_unrecorded_page_points_to_the_page_and_never_says_give_up():
    tried = {"queries": ["venv official docs"], "urls": [DOCS]}
    page = unrecorded_page(DOCS)
    for _ in range(3):
        refusal = ab.check_decision("SEARCH", "venv official docs", [], tried, page, DOCS)
        assert refusal[0] == "BLOCKED_SEARCH" and "DISCOVER what it says" in refusal[1] and "could not be found" not in refusal[1]
    assert not tried.get("search_exhausted")


def test_an_outline_line_copied_with_its_number_is_refused_as_a_heading():
    page = recorded_page(DOCS, ["How venvs work\nA venv is a directory."])
    page["headings"] = ["How venvs work", "Creating virtual environments"]
    assert ab.is_heading_discovery("3: How venvs work (read)", page)
    assert ab.is_heading_discovery("2. Creating virtual environments", page)


def test_a_page_announcing_what_it_will_cover_is_refused():
    intro = "This comprehensive guide will compare SQLite and PostgreSQL across critical factors like performance, scalability."
    assert ab.DESCRIBES_PAGE.search(intro)
    assert not ab.DESCRIBES_PAGE.search("The Python Packaging User Guide recommends using venv for virtual environments.")


def test_the_one_page_note_is_left_out_once_every_item_has_its_discoveries():
    journal = [{"action": "DISCOVER", "source": GUIDE, "extracted_data": "Kilimanjaro is 5,895 m (19,341 ft) high."}]
    items = [{"text": "the tallest mountain in Africa", "discoveries": [1]}]
    covered = ab.finish_check({"checklist": items}, journal)
    assert covered[0] == "CONFIRM_FINISH" and "Your only discovery comes from" not in covered[1]
    items.append({"text": "its height in feet", "discoveries": []})
    assert "Your only discovery comes from" in ab.finish_check({"checklist": items}, journal)[1]


def test_the_finish_checks_own_wording_is_refused_as_a_discovery():
    page = recorded_page(GUIDE, ["Use 1 cup of coarse coffee to 4 cups of water."])
    refusal = ab.check_decision("DISCOVER", "not covered by [1, 2, 3]", [], {"queries": [], "urls": []}, page, GUIDE)
    assert refusal[0] == "BLOCKED_DISCOVER_CHECK"
    assert not ab.CHECK_WORDING.search("Cover the jar and steep it for 16 hours.")


def test_an_outline_line_with_a_section_number_is_refused_as_a_heading():
    page = recorded_page(DOCS, ["The Light-Dependent Reactions\nLight splits water."])
    page["headings"] = ["10.4: The Light-Dependent Reactions", "How the Light-Dependent Reactions Work"]
    assert ab.is_heading_discovery("10.4: The Light-Dependent Reactions / How the Light-Dependent Reactions Work (read)", page)
    assert not ab.is_heading_discovery("4 molecules of water are split in the light-dependent reactions", page)


def test_an_outline_label_with_a_page_number_is_refused_as_a_heading():
    page = recorded_page(DOCS, ["Light-independent Reactions\nThe Calvin cycle has three stages."])
    page["headings"] = ["4.1.06: Light-independent Reactions"]
    assert ab.is_heading_discovery("4.1.06: Light-independent Reactions/3", page)
    assert not ab.is_heading_discovery("The Calvin cycle has 3 stages", page)


def test_a_number_next_to_a_headings_words_is_still_a_fact():
    page = recorded_page(DOCS, ["Mount Kilimanjaro\nIt is 5,895 metres high."])
    page["headings"] = ["Mount Kilimanjaro"]
    assert not ab.is_heading_discovery("Mount Kilimanjaro 5,895", page)
