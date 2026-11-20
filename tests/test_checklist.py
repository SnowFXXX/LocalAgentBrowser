"""The objective checklist: the objective split once into the things the result must contain, with the
discoveries recorded for each, used by every check instead of a fresh split from memory."""
import asyncio
import json

import agent_browser as ab
from fakes import discoveries, fake_client

DOCS = "https://docs.example/page"


def split(reply):
    client, model = fake_client([reply])
    return asyncio.run(ab.split_objective(client, "Name three free tools and say what each is best at.")), model


class TestSplitObjective:
    def test_parts_become_items(self):
        checklist, model = split(json.dumps({"parts": ["Name three free tools", " ", "Say what each is best at"]}))
        assert [i["text"] for i in checklist] == ["Name three free tools", "Say what each is best at"]
        assert all(i["discoveries"] == [] for i in checklist)
        assert "do not add anything the objective does not ask for" in model.calls[0][-1]["content"]

    def test_a_failed_split_keeps_the_whole_objective(self):
        for bad in ["not json", json.dumps({"parts": []}), json.dumps({"parts": "one string"}), json.dumps([1])]:
            checklist, _ = split(bad)
            assert [i["text"] for i in checklist] == ["Name three free tools and say what each is best at."], bad

    def test_long_splits_are_cut(self):
        checklist, _ = split(json.dumps({"parts": [f"part {n}" for n in range(20)]}))
        assert len(checklist) == ab.MAX_CHECKLIST_ITEMS


def checklist(*texts):
    return [{"text": t, "discoveries": []} for t in texts]


class TestLinking:
    def test_only_valid_item_numbers_are_linked(self):
        items = checklist("a", "b")
        assert ab.link_discovery(items, [2, 2, 5, 0, True, "1", 1], 3) == [2, 1]
        assert items[0]["discoveries"] == [3] and items[1]["discoveries"] == [3]
        assert ab.link_discovery(items, None, 4) == []

    def test_lines_show_the_discoveries_for_each_item(self):
        items = checklist("Name three tools", "Say what each is best at")
        ab.link_discovery(items, [1], 1)
        ab.link_discovery(items, [1], 2)
        assert ab.checklist_lines(items) == ("1. Name three tools (discoveries for it: [1], [2])\n"
                                            "2. Say what each is best at (discoveries for it: none yet)")
        assert ab.open_items(items) == "2. Say what each is best at"


class TestChecksUseTheChecklist:
    def test_finish_check_goes_through_the_fixed_items(self):
        tried = {"queries": [], "urls": [], "checklist": checklist("Name three tools", "Say what each is best at")}
        reason = ab.check_decision("FINISH", "done", discoveries("x"), tried, {"url": None, "parts": [], "index": 0}, None)[1]
        assert "OBJECTIVE CHECKLIST below, which lists every part of the OBJECTIVE (do not add other parts)" in reason
        assert "1. Name three tools (discoveries for it: none yet)" in reason
        assert "covered only when that many different ones are recorded" in reason

    def test_without_a_checklist_the_objective_is_split_in_the_check(self):
        tried = {"queries": [], "urls": []}
        reason = ab.check_decision("FINISH", "done", discoveries("x"), tried, {"url": None, "parts": [], "index": 0}, None)[1]
        assert ab.CHECK_PARTS in reason

    def test_read_on_check_names_the_open_items(self):
        tried = {"queries": [], "urls": [], "checklist": checklist("What goes in", "Where it happens")}
        ab.link_discovery(tried["checklist"], [2], 1)
        page = {"url": DOCS, "parts": ["a", "b"], "index": 0, "seen_parts": {0}}
        reason = ab.check_decision("READ_MORE", "", discoveries("x"), tried, page, DOCS)[1]
        assert "serves the OBJECTIVE (above all the checklist items with nothing recorded yet: 1. What goes in), or write 'none'" in reason
        assert "Where it happens" not in reason

    def test_read_on_check_points_to_the_outline_part_naming_an_open_item(self):
        tried = {"queries": [], "urls": [], "checklist": checklist("Compare SQLite and PostgreSQL setup and administration",
                                                                   "Compare SQLite and PostgreSQL features")}
        outline = ["SQLite vs PostgreSQL / Why", "What is SQLite? / What is PostgreSQL?", "SQLite vs PostgreSQL: Similarities",
                   "SQLite vs PostgreSQL: Data types", "SQLite / PostgreSQL / Licenses / Administration",
                   "Performance / Features", "Scalability"]
        page = {"url": DOCS, "parts": ["x"] * 7, "outline": outline, "index": 0, "seen_parts": {0}}
        reason = ab.check_decision("READ_MORE", "", discoveries("x"), tried, page, DOCS)[1]
        assert ("If there are none, repeat your READ_MORE: it will show part 5, not the next part, because the Page "
                "Outline names 'administration' there") in reason
        # Repeated plainly, the READ_MORE reads the part named; a part number the model gives is kept
        assert ab.read_more_target("", page) == 4 and ab.read_more_target("2", page) == 1
        assert ab.read_more_target("", dict(page, index=1)) == 2
        # Words in many labels ("sqlite") and item verbs ("compare") point nowhere
        assert ab.outline_pointer(dict(page, seen_parts={0, 4, 5}), tried["checklist"]) is None
        # A label taken from the first line of code is not a heading
        code = dict(page, parts=["x"] * 4 + ["```\n    Administration of the pool\n"] + ["x"] * 2)
        assert ab.outline_pointer(code, tried["checklist"]) == (6, "features")

    def test_read_on_check_asks_for_finish_once_every_item_has_its_discoveries(self):
        tried = {"queries": [], "urls": [], "checklist": checklist("What goes in", "Where it happens")}
        ab.link_discovery(tried["checklist"], [1], 1)
        ab.link_discovery(tried["checklist"], [2], 2)
        page = {"url": DOCS, "parts": ["a", "b"], "index": 0, "seen_parts": {0}}
        reason = ab.check_decision("READ_MORE", "", discoveries("x", "y"), tried, page, DOCS)[1]
        assert "already has the discoveries it asks for. Choose FINISH now" in reason
        assert "repeat your READ_MORE" not in reason


class TestFinalResult:
    def test_checklist_is_given_with_a_rule_to_cover_it(self):
        client, model = fake_client(["Result [1]."])
        items = checklist("Create it", "Activate it")
        asyncio.run(ab.compose_final_result(client, "objective", discoveries("x"), items))
        request = model.calls[0][-1]["content"]
        assert "### CHECKLIST (the parts of the OBJECTIVE)\n1. Create it\n2. Activate it\n\n### DISCOVERIES" in request
        assert "6. Cover every CHECKLIST item; for an item no discovery supports, say plainly that it was not found." in request
        assert '7. Reply with JSON: {"result": "..."}' in request

    def test_a_single_item_adds_nothing(self):
        client, model = fake_client(["Result [1]."])
        asyncio.run(ab.compose_final_result(client, "objective", discoveries("x"), checklist("objective")))
        request = model.calls[0][-1]["content"]
        assert "CHECKLIST" not in request and '6. Reply with JSON: {"result": "..."}' in request

    def test_the_result_is_read_from_its_field(self):
        client, model = fake_client([json.dumps({"result": "Kilimanjaro, 5,895 m [1]."})])
        assert asyncio.run(ab.compose_final_result(client, "objective", discoveries("5,895 m"))) == "Kilimanjaro, 5,895 m [1]."

    def test_a_tool_call_or_the_prompt_repeated_is_asked_for_again(self):
        # What Llama 3.1 8B and Qwen 3B wrote instead of a final result in the 2026-10-25 model tests
        tool_call = json.dumps({"name": "get_mountain_info", "parameters": {"height_in_meters": 5895}})
        echoed = json.dumps({"result": "### OBJECTIVE\nHow tall is it?\n### DISCOVERIES\n[1] x"})
        for first in (tool_call, echoed):
            client, model = fake_client([first, json.dumps({"result": "It is 5,895 m tall [1]."})])
            assert asyncio.run(ab.compose_final_result(client, "objective", discoveries("5,895 m"))) == "It is 5,895 m tall [1]."
            assert model.calls[1][-1]["content"].startswith("That reply was not the final result")

    def test_two_unusable_replies_are_reported_as_no_result(self):
        tool_call = json.dumps({"name": "get_mountain_info", "parameters": {}})
        client, model = fake_client([tool_call, tool_call])
        assert asyncio.run(ab.compose_final_result(client, "objective", discoveries("x"))) == ab.NO_RESULT

    def test_a_reply_cut_off_at_the_token_limit_still_gives_its_text(self):
        assert ab.result_text('{"result": "Step 1. Grind the coffee.') == "Step 1. Grind the coffee."
        assert ab.result_text("Plain text from a server that ignores the format.") == (
            "Plain text from a server that ignores the format.")


def verdict(evidence="[1] x", missing="", conditions=(), things=()):
    return json.dumps({"evidence": evidence, "missing": missing, "conditions": list(conditions),
                       "things": [{"name": n, "proof": p} for n, p in things]})


def gaps(items, replies, found=("LibreOffice is free and open source.", "WPS Office has a familiar interface.")):
    client, model = fake_client(list(replies))
    result, _, _ = asyncio.run(ab.coverage_gaps(client, "objective", checklist(*items), discoveries(*found)))
    return result, model


class TestCoverageGaps:
    def test_missing_content_is_reported_per_item(self):
        result, model = gaps(["What comes out"], [verdict(missing="oxygen")])
        assert result == ["Item 1 (What comes out): missing oxygen"]
        assert model.calls[0][-1]["content"].endswith("The item to check: ITEM 1. What comes out")

    def test_a_condition_needs_words_from_the_discoveries_that_state_it(self):
        reply = verdict(conditions=["free", "open-source"], things=[
            ("LibreOffice", "LibreOffice is free and open source"),      # copied and states both
            ("WPS Office", "WPS Office has a familiar interface"),       # copied, states neither
            ("OnlyOffice", "OnlyOffice is free and open source")])       # states both, but not in the discoveries
        result, _ = gaps(["Name three free, open-source suites"], [reply])
        assert result == ["Item 1 (Name three free, open-source suites): only 2 of 3 found (LibreOffice, WPS Office)",
                          "Item 1: WPS Office: no words in DISCOVERIES SO FAR show it is free and open-source. SEARCH for "
                          "'WPS Office free and open-source' to check it, or replace it with one that is",
                          "Item 1: OnlyOffice: no words in DISCOVERIES SO FAR show it is free and open-source. SEARCH for "
                          "'OnlyOffice free and open-source' to check it, or replace it with one that is"]

    def test_long_descriptions_are_not_conditions(self):
        reply = verdict(conditions=["in different cultures or religions"],
                        things=[("Western cultures", ""), ("Hopi", ""), ("Greece", "")])
        found = ("In Western cultures owls stand for wisdom.", "The Hopi feared owls.", "In Greece the owl was Athena's bird.")
        assert gaps(["How at least three different cultures or religions viewed owls"], [reply], found)[0] == []

    def test_a_short_count_is_found_without_a_condition(self):
        reply = verdict(things=[("Dakota Hidatsa Indians", "the Dakota Hidatsa"), ("Hopis Indians", "the Hopis")])
        found = ("The Dakota Hidatsa saw the owl as a protector.", "The Hopis feared the owl.")
        assert gaps(["How at least three different cultures viewed owls"], [reply], found)[0] == [
            "Item 1 (How at least three different cultures viewed owls): only 2 of 3 found (Dakota Hidatsa Indians, "
            "Hopis Indians)"]

    def test_conditions_the_item_does_not_state_are_ignored(self):
        reply = verdict(conditions=["open-source"], things=[("WPS Office", "")])
        assert gaps(["What each one is best at"], [reply])[0] == []

    def test_opinion_items_are_not_checked_and_failed_calls_report_nothing(self):
        # A reply that isn't JSON is asked for once more; failing again, the item is not checked and reports no gap
        result, model = gaps(["Give your own opinion on why", "What comes out"], ["not json", "not json"])
        assert result == [] and len(model.calls) == 2


class TestCoverageRefusal:
    def test_sent_back_once_for_an_item_nothing_answers(self):
        client, _ = fake_client([verdict(evidence="", missing="oxygen")])
        tried = {"checklist": checklist("What comes out")}
        refusal = asyncio.run(ab.coverage_refusal(client, "objective", tried, discoveries("x")))
        assert refusal[0] == "BLOCKED_COVERAGE"
        assert "- Item 1 (What comes out): nothing in DISCOVERIES SO FAR answers it" in refusal[1]
        assert asyncio.run(ab.coverage_refusal(client, "objective", tried, discoveries("x"))) is None

    def test_sent_back_when_no_discovery_of_an_item_gives_the_amounts_it_asks_for(self):
        client, _ = fake_client([verdict(missing="quantities")])
        items = [{"text": "List what I need with the quantities", "discoveries": [1]}]
        refusal = asyncio.run(ab.coverage_refusal(client, "objective", {"checklist": items},
                                                  discoveries("You need coffee beans, water and a jar.")))
        assert refusal[0] == "BLOCKED_COVERAGE" and "gives the amounts it asks for" in refusal[1]
        client, _ = fake_client([verdict(missing="")])
        assert asyncio.run(ab.coverage_refusal(client, "objective", {"checklist": items},
                                               discoveries("1 cup (113 grams) whole coffee beans"))) is None

    def test_the_model_s_other_verdicts_do_not_send_back(self):
        reply = verdict(missing="official documentation", conditions=["free"],
                        things=[("ONLYOFFICE", ""), ("LibreOffice", ""), ("OpenOffice", "")])
        client, _ = fake_client([reply])
        tried = {"checklist": checklist("Name three free suites")}
        found = discoveries("ONLYOFFICE is open source.", "LibreOffice is a full suite.", "OpenOffice is the original suite.")
        assert asyncio.run(ab.coverage_refusal(client, "objective", tried, found)) is None
        assert tried["unproven_items"] == [1], "the unmet condition still counts for the final result"

    def test_skipped_after_an_exhausted_search(self):
        client, model = fake_client([])
        assert asyncio.run(ab.coverage_refusal(client, "o", {"checklist": checklist("a"), "search_exhausted": True},
                                               discoveries("x"))) is None
        assert model.calls == []

    def test_finishing_with_nothing_recorded_is_sent_back_once(self):
        client, model = fake_client([])
        tried = {"checklist": checklist("a")}
        refusal = asyncio.run(ab.coverage_refusal(client, "o", tried, []))
        assert refusal[0] == "BLOCKED_COVERAGE" and "Nothing has been recorded for any checklist item yet" in refusal[1]
        assert asyncio.run(ab.coverage_refusal(client, "o", tried, [])) is None and model.calls == []


class TestCountedItems:
    def test_asked_count(self):
        assert ab.asked_count("Name three free, open-source alternatives") == 3
        assert ab.asked_count("Find at least 4 examples") == 4
        assert ab.asked_count("How tall is it in meters?") is None
        assert ab.asked_count("Say how to care for the plant during the first two weeks") is None
        assert ab.asked_count("Name two drinks made with 2 cups of water") == 2

    def test_numbers_that_name_or_date_something_are_not_counts(self):
        # From the 200-question SimpleQA run, where each of these ended a right answer with "ten more (only none of
        # the ten were found)" or the like
        for item in ["What is the name of track 10 on disc 2 of the soundtrack?", "The title of Episode 12, Season 3",
                     "For what career did Eddie Marsan leave school at 16?", "The final score on February 6, 2022",
                     "Who influenced 'Rhythm 2' by Marina Abramovic?", "The maximum angle of the Rattler at Six Flags",
                     "When was Sony's PlayStation 5 announced?", "The museum in Two Rivers, Wisconsin"]:
            assert ab.asked_count(item) is None, item
        assert ab.asked_count("What two football teams were mentioned in the case?") == 2
        assert ab.asked_count("The names of the two streets at the intersection") == 2
        assert ab.asked_count("Three free, open-source alternatives to Adobe Photoshop") == 3
        assert ab.asked_count("Find out how at least three different cultures have viewed owls") == 3

    def test_fewer_different_things_than_asked_is_a_gap(self):
        reply = verdict(conditions=["open-source"], things=[("LibreOffice", "LibreOffice is free and open source"),
                                                            ("LibreOffice", "LibreOffice is free and open source")])
        result, _ = gaps(["Name three free, open-source suites"], [reply])
        assert result == ["Item 1 (Name three free, open-source suites): only 1 of 3 found (LibreOffice)"]


class TestNotFound:
    def test_empty_evidence_means_unanswered(self):
        client, _ = fake_client([verdict(evidence="", missing="")])
        result, unanswered, _ = asyncio.run(ab.coverage_gaps(client, "o", checklist("Care in the first two weeks"),
                                                          discoveries("Repot in spring.")))
        assert unanswered == [1] and result == ["Item 1 (Care in the first two weeks): nothing in DISCOVERIES SO FAR answers it"]

    def test_a_discovery_number_alone_is_asked_for_its_words_and_counts_when_they_are_there(self):
        client, model = fake_client([verdict(evidence="[1]"), verdict(evidence="[1] PostgreSQL handles many connections well")])
        found = discoveries("PostgreSQL handles many connections well, using one process per connection.")
        assert asyncio.run(ab.coverage_gaps(client, "o", checklist("Handling of many users"), found))[1] == []
        assert len(model.calls) == 2 and "only discovery numbers" in model.calls[1][-1]["content"]

    def test_evidence_quoting_several_discoveries_counts_when_each_part_is_in_one(self):
        found = discoveries("If an owl flew over Greek soldiers before a battle, they took it as a sign of victory.",
                            "In English folklore, hearing an owl's call meant imminent death.")
        quoted = ("[1] If an owl flew over Greek soldiers before a battle, they took it as a sign of victory.\n"
                  "[2] In English folklore, hearing an owl's call meant imminent death.")
        client, _ = fake_client([verdict(evidence="[1], [2]"), verdict(evidence=quoted)])
        assert asyncio.run(ab.coverage_gaps(client, "o", checklist("How two cultures viewed owls"), found))[1] == []

    def test_numbers_given_twice_lead_to_a_yes_or_no_question_about_the_cited_discovery(self):
        items = checklist("Compare setup and administration", "Compare handling of many simultaneous users")
        no = json.dumps({"answer": "no", "quote": ""})
        intro = "SQLite and PostgreSQL sit at opposite ends of the relational database spectrum."
        client, model = fake_client([verdict(evidence="[1]"), verdict(evidence="[1]"), no])
        assert asyncio.run(ab.coverage_gaps(client, "o", items, discoveries(intro), only=[2]))[1] == [2]
        assert "### DISCOVERY\n" + intro in model.calls[2][1]["content"]
        locking = "SQLite locks the whole file on writes, so many simultaneous users wait on each other."
        yes = json.dumps({"answer": "yes", "quote": "SQLite locks the whole file on writes"})
        client, _ = fake_client([verdict(evidence="[1]"), verdict(evidence="[1]"), yes])
        assert asyncio.run(ab.coverage_gaps(client, "o", items, discoveries(locking), only=[2]))[1] == []
        invented = json.dumps({"answer": "yes", "quote": "PostgreSQL handles thousands of users"})
        client, _ = fake_client([verdict(evidence="[1]"), verdict(evidence="[1]"), invented])
        assert asyncio.run(ab.coverage_gaps(client, "o", items, discoveries(intro), only=[2]))[1] == [2]

    def test_a_no_still_counts_when_the_cited_discovery_has_the_items_own_words(self):
        # The 7B said no 3 times out of 3 for a discovery placing the light-dependent reactions in the thylakoids
        items = checklist("Detail the light-dependent reactions and their location in the plant cell",
                          "Detail the Calvin cycle and its location in the plant cell")
        no = json.dumps({"answer": "no", "quote": ""})
        found = discoveries("The light-dependent reactions, which take place in the thylakoids, produce ATP and NADPH.")
        client, _ = fake_client([verdict(evidence="[1]"), verdict(evidence="[1]"), no])
        assert asyncio.run(ab.coverage_gaps(client, "o", items, found, only=[1]))[1] == []
        # "location", "plant" and "cell" are shared with the other item, so they alone do not count
        found = discoveries("The plant cell has a location for everything, and the Calvin cycle runs in the stroma.")
        client, _ = fake_client([verdict(evidence="[1]"), verdict(evidence="[1]"), no])
        assert asyncio.run(ab.coverage_gaps(client, "o", items, found, only=[1]))[1] == [1]

    def test_a_discovery_number_with_no_real_words_behind_it_means_unanswered(self):
        client, _ = fake_client([verdict(evidence="[1]"), verdict(evidence="[1] SQLite cannot handle many users")])
        found = discoveries("SQLite and PostgreSQL sit at opposite ends of the relational database spectrum.")
        result, unanswered, _ = asyncio.run(ab.coverage_gaps(client, "o", checklist("Handling of many users"), found))
        assert unanswered == [1] and "nothing in DISCOVERIES SO FAR answers it" in result[0]

    def test_unanswered_items_are_rechecked_before_the_final_result(self):
        items = checklist("Steps", "Care")
        ab.link_discovery(items, [1, 2], 1)
        client, model = fake_client([verdict(evidence="")])
        tried = {"checklist": items, "unanswered": [2]}
        assert asyncio.run(ab.not_found_items(client, "o", tried, discoveries("x"))) == ([2], [])
        assert len(model.calls) == 1 and "The item to check: ITEM 2" in model.calls[0][-1]["content"]

    def test_items_nothing_is_linked_to_are_not_found_without_discoveries(self):
        client, model = fake_client([])
        tried = {"checklist": checklist("Steps", "Give your own opinion")}
        assert asyncio.run(ab.not_found_items(client, "o", tried, [])) == ([1], []) and model.calls == []

    def test_the_final_result_is_not_asked_about_them_and_the_code_reports_them(self):
        client, model = fake_client(["Result [1]."])
        text = asyncio.run(ab.compose_final_result(client, "objective", discoveries("x"), checklist("Steps", "Care"), [2]))
        request = model.calls[0][-1]["content"]
        assert "### CHECKLIST (the parts of the OBJECTIVE)\n1. Steps\n\n### NOT FOUND" in request
        assert "Write nothing about the NOT FOUND items" in request
        assert text == "Result [1].\n\nNot found in the pages read:\n- Care"

    def test_an_item_the_result_answers_from_a_cited_discovery_is_not_also_listed_as_not_found(self):
        # Phi-4 gave both dates from discovery [1], and the code then listed both as not found
        said = "He served as the 17th Chief Justice of India from 12 July 1985 until his retirement on 20 December 1986 [1]."
        client, _ = fake_client([said])
        items = checklist("Find the start date of his tenure as the Chief Justice of India",
                          "Find the end date of his tenure as the Chief Justice of India", "Find his birthplace")
        found = discoveries("The 17th Chief Justice of India, from 12 July 1985 until his retirement on 20 December 1986.")
        text = asyncio.run(ab.compose_final_result(client, "objective", found, items, [1, 2, 3]))
        assert text == said + "\n\nNot found in the pages read:\n- Find his birthplace"
        # A cited sentence that says the thing was not found answers nothing
        assert not ab.answered_in_result("Find his birthplace", "His birthplace was not found [1].")
        assert not ab.answered_in_result("Find his birthplace", "His birthplace was Bombay.")


class TestConditionsOnlyOnCountedItems:
    def test_a_question_s_own_wording_is_not_a_condition(self):
        reply = verdict(conditions=["in the fridge"], things=[("cold brew", "")])
        assert gaps(["Say how long it keeps in the fridge"], [reply])[0] == []

    def test_items_asking_for_a_number_of_things_keep_their_conditions(self):
        reply = verdict(conditions=["open-source"], things=[("WPS Office", "WPS Office has a familiar interface")])
        result, _ = gaps(["Name three free, open-source suites"], [reply])
        assert any(g.startswith("Item 1: WPS Office: no words in DISCOVERIES SO FAR show it is open-source") for g in result)


class TestUnprovenThings:
    def test_things_still_unproven_before_the_final_result_are_left_out_and_reported(self):
        items = checklist("Name three free, open-source suites", "What each is best at")
        ab.link_discovery(items, [1, 2], 1)
        reply = verdict(conditions=["open-source"], things=[("LibreOffice", "LibreOffice is free and open source"),
                                                            ("WPS Office", "")])
        client, _ = fake_client([reply])
        tried = {"checklist": items, "unproven_items": [1]}
        found = discoveries("LibreOffice is free and open source. WPS Office has a familiar interface.")
        missing, unproven = asyncio.run(ab.not_found_items(client, "o", tried, found))
        assert missing == [] and unproven == [("WPS Office", "open-source")]

        client, model = fake_client(["1. **LibreOffice** [1]\n2. **WPS Office**"])
        text = asyncio.run(ab.compose_final_result(client, "o", found, items, [], unproven))
        assert "- WPS Office: not shown to be open-source" in model.calls[0][-1]["content"]
        assert text == "1. **LibreOffice** [1]\n2. **WPS Office** (not shown in the pages read to be open-source)"

    def test_unnamed_things_are_listed_after_the_result_and_notes_are_not_doubled(self):
        text = ab.with_caveats("LibreOffice [1]. ONLYOFFICE (not shown in the pages read to be free).",
                               [("ONLYOFFICE", "free"), ("WPS Office", "open-source")])
        assert text == ("LibreOffice [1]. ONLYOFFICE (not shown in the pages read to be free).\n\n"
                        "Not shown in the pages read to meet the conditions:\n- WPS Office: not shown in the pages read "
                        "to be open-source")

    def test_things_with_the_same_condition_are_each_marked(self):
        note = "(not shown in the pages read to be free and open-source)"
        text = ab.with_caveats("1. **OnlyOffice** [1]\n2. **WPS Office** [2]",
                               [("OnlyOffice", "free and open-source"), ("WPS Office", "free and open-source")])
        assert text == f"1. **OnlyOffice** {note} [1]\n2. **WPS Office** {note} [2]"


def test_opinion_items_are_shown_as_written_at_finish():
    items = checklist("Find how three cultures viewed owls", "Give your own opinion on why")
    lines = ab.checklist_lines(items)
    assert "2. Give your own opinion on why (written by you at FINISH, not found on a page)" in lines
    assert ab.open_items(items) == "1. Find how three cultures viewed owls"


class TestReallyMissing:
    FOUND = discoveries("6CO2 + 6H2O + Light Energy -> C6H12O6 + 6O2. Water comes from the soil.")

    def test_short_claims_the_discoveries_already_answer_are_dropped(self):
        assert ab.really_missing("H2O from the soil", self.FOUND) == ""
        assert ab.really_missing("O2", self.FOUND) == ""

    def test_absent_words_and_long_claims_are_kept(self):
        assert ab.really_missing("glucose and chlorophyll", self.FOUND) == "glucose, chlorophyll"
        long = "what happens during the light dependent reactions"
        assert ab.really_missing(long, discoveries("what happens during the light dependent reactions")) == long

    def test_co2_does_not_count_as_o2(self):
        assert ab.really_missing("O2", discoveries("Plants take in CO2.")) == "O2"


def test_descriptions_are_not_things():
    reply = verdict(conditions=["open-source"], things=[
        ("LibreOffice", "LibreOffice is free and open source"),
        ("OnlyOffice", ""),
        (".docx/.xlsx/.pptx compatibility is genuinely excellent\nBuilt-in real-time collaboration", "")])
    client, _ = fake_client([reply])
    result, _, unproven = asyncio.run(ab.coverage_gaps(client, "o", checklist("Name three free, open-source suites"),
                                                       discoveries("LibreOffice is free and open source.")))
    assert "Item 1 (Name three free, open-source suites): only 1 of 3 found (LibreOffice)" in result  # OnlyOffice: named in none
    assert unproven == [("OnlyOffice", "open-source")]


class TestAfterDiscoveryAdvice:
    def test_says_finish_when_every_item_has_what_it_asks_for(self):
        items = checklist("Find how at least three cultures viewed owls", "Give your own opinion on why")
        for n in (1, 2):
            ab.link_discovery(items, [1], n)
        assert "If DISCOVERIES SO FAR now cover" in ab.after_discovery_advice(items), "two of three is not enough"
        ab.link_discovery(items, [1], 3)
        assert ab.after_discovery_advice(items).startswith("Every item of the OBJECTIVE CHECKLIST now has the discoveries")

    def test_an_item_without_discoveries_keeps_the_usual_advice(self):
        items = checklist("Setup", "Features")
        ab.link_discovery(items, [1], 1)
        assert not ab.every_item_has_discoveries(items)


def test_one_discovery_linked_to_every_item_is_not_enough():
    items = checklist("What goes in", "What comes out", "Light reactions", "Calvin cycle")
    ab.link_discovery(items, [1, 2, 3, 4], 1)
    assert not ab.every_item_has_discoveries(items)
    for n in (2, 3, 4):
        ab.link_discovery(items, [n], n)
    assert ab.every_item_has_discoveries(items)


def test_a_gap_filled_from_the_models_own_knowledge_is_cut_back_to_the_gap():
    text = ("Store it in the fridge [1]. How long it keeps in the fridge is not explicitly stated but generally, it can "
            "be stored for several weeks if properly sealed and refrigerated. Serve over ice [1].")
    assert ab.without_filled_gaps(text) == ("Store it in the fridge [1]. How long it keeps in the fridge is not "
                                            "explicitly stated. Serve over ice [1].")
    ratio = "The exact ratio is not specified in the text, but a common recommendation is 1 part coffee to 4 parts water (e.g., 1 cup)."
    assert ab.without_filled_gaps(ratio) == "The exact ratio is not specified in the text."
    honest = "Steep it for 16 hours [1], but no longer than 24 hours [2]."
    assert ab.without_filled_gaps(honest) == honest


def test_a_command_in_the_result_that_no_discovery_gives_is_cut_and_named():
    # The 14B's venv result: commands in triple backticks, one of them written from memory
    found = discoveries("Create one with python -m venv /path/to/env. On POSIX bash/zsh: $ source <venv>/bin/activate")
    text = ("To create it, run ```python -m venv /path/to/env``` [1]. On Windows, run ```<venv>/Scripts/activate```.\n\n"
            "On macOS or Linux, run `source venv/bin/activate` [1].")
    assert ab.unverified_code(text, found) == ["<venv>/Scripts/activate"]
    assert ab.without_invented_code(text, found) == (
        "To create it, run ```python -m venv /path/to/env``` [1].\n\nOn macOS or Linux, run `source venv/bin/activate` "
        "[1].\n\nLeft out, because no discovery gives them (the pages read may still have them): "
        "`<venv>/Scripts/activate`.")
    assert ab.without_invented_code("Run `python -m venv /path/to/env` [1].", found) == "Run `python -m venv /path/to/env` [1]."


def test_a_code_block_line_no_discovery_gives_is_cut_and_an_emptied_block_removed():
    # The 7B's venv result: a block with "pip install package_name" under "simply use pip as usual:"
    found = discoveries("Create one:\n```\npython -m venv /path/to/env\n```",
                        "If you have a requirements file requirements.txt, you can invoke pip install -r requirements.txt.")
    text = ("To create it [1]:\n\n```\npython -m venv /path/to/env\n```\n\nTo install packages, use pip:\n\n```\n"
            "pip install package_name\n```")
    assert ab.unverified_code(text, found) == ["pip install package_name"]
    assert ab.without_invented_code(text, found) == (
        "To create it [1]:\n\n```\npython -m venv /path/to/env\n```\n\nTo install packages, use pip:\n\nLeft out, because "
        "no discovery gives them (the pages read may still have them): `pip install package_name`.")


def test_the_report_of_a_run_stopped_while_writing_shows_an_unchecked_draft(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    journal = [{"action": "DISCOVER", "turn": 1, "extracted_data": "LibreOffice is free."},
               {"action": "FINISH", "turn": 2, "finding": "LibreOffice, OpenOffice and WPS Office.", "writing": True}]
    ab.write_report("Name three free suites", journal)
    report = (tmp_path / ab.REPORT_FILE).read_text(encoding="utf-8")
    assert "stopped while the final result was being checked and written" in report
    assert "finished by the agent" not in report and "unchecked draft" in report


def test_what_the_checks_cut_is_reported_after_the_result_not_inside_it(tmp_path, monkeypatch):
    # OpenAI's SimpleQA grader read the cut "33 years" in "Left out ...: 33 years." as the answer
    monkeypatch.chdir(tmp_path)
    final = ("His age when he first kayaked it was not found [1].\n\n"
             "Left out, because no discovery gives these numbers (the pages read may still have them): 33 years.")
    journal = [{"action": "DISCOVER", "turn": 1, "extracted_data": "He kayaked the waterfall in 2017."},
               {"action": "FINISH", "turn": 2, "finding": final}]
    ab.write_report("How old was he?", journal)
    report = (tmp_path / ab.REPORT_FILE).read_text(encoding="utf-8")
    result = report.split("## Final result", 1)[1].split("\n## ", 1)[0]
    assert "33" not in result and ab.CHECKED_POINTER in result
    assert "## Checked by the code" in report and "gives these numbers (the pages read may still have them): 33 years." in report


def test_a_result_the_checks_left_alone_has_no_extra_section(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    journal = [{"action": "DISCOVER", "turn": 1, "extracted_data": "Kilimanjaro is 5,895 m tall."},
               {"action": "FINISH", "turn": 2, "finding": "Kilimanjaro is 5,895 m tall [1]."}]
    ab.write_report("How tall is Kilimanjaro?", journal)
    report = (tmp_path / ab.REPORT_FILE).read_text(encoding="utf-8")
    assert "Checked by the code" not in report and ab.CHECKED_POINTER not in report


def test_a_result_asked_for_steps_that_gives_none_is_flagged_in_the_report(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    journal = [{"action": "DISCOVER", "turn": 1, "extracted_data": "Stir the coffee into the water. Keep it two weeks."},
               {"action": "FINISH", "turn": 2, "finding": "You need coffee and water [1]. It keeps two weeks [1]."}]
    ab.write_report("How do I make cold brew? Give the steps in order.", journal)
    report = (tmp_path / ab.REPORT_FILE).read_text(encoding="utf-8")
    assert "## Checked by the code" in report and "asks for steps, and this result gives none" in report
    assert ab.CHECKED_POINTER in report


def test_items_asking_for_amounts_with_none_recorded_are_reported_by_the_code():
    items = checklist("List what I need with the quantities", "Say how long it keeps in the fridge")
    journal = discoveries("You need coffee beans, water, and a strainer.", "It keeps in the fridge for two weeks.")
    ab.link_discovery(items, [1], 1)
    ab.link_discovery(items, [2], 2)
    assert ab.amounts_not_found(items, journal) == [1]
    client, model = fake_client(["You need coffee beans, water and a strainer [1]. It keeps two weeks [2]."])
    text = asyncio.run(ab.compose_final_result(client, "Cold brew at home", journal, items, (), (), [1]))
    assert "NO AMOUNTS FOUND" in model.calls[0][1]["content"]
    assert text.endswith("Not found in the pages read:\n- List what I need with the quantities: the amounts it asks for")


def test_a_view_paragraph_repeating_the_whole_result_is_left_out():
    result = "SQLite suits a hobby project; PostgreSQL handles many writers with MVCC [1]. I recommend SQLite."
    client, model = fake_client([result, "My view: " + result])
    text = asyncio.run(ab.compose_final_result(client, "Compare them, then recommend one for my hobby project.",
                                               discoveries("SQLite suits small projects; PostgreSQL handles many writers with MVCC.")))
    assert len(model.calls) == 2 and text.count("I recommend SQLite") == 1 and "My view:" not in text


def test_a_caveat_finds_the_thing_by_its_distinctive_word():
    text = "1. LibreOffice is the fullest suite [1].\n2. ONLYOFFICE is best at collaboration [2]."
    marked = ab.with_caveats(text, [("ONLYOFFICE Docs", "free")])
    assert "ONLYOFFICE (not shown in the pages read to be free) is best" in marked
    assert ab.with_caveats(text, [("Apache OpenOffice", "free")]).endswith(
        "- Apache OpenOffice: not shown in the pages read to be free")


def test_a_thing_no_discovery_names_is_not_counted():  # here Chinese culture is in no discovery
    reply = verdict(evidence="[1] Owls play a role in Native American cultures", things=[("Native American cultures", "Owls play a role in Native American cultures"),
                                              ("Christianity", "In Christianity owls carry darker connotations"),
                                              ("Chinese culture", "In Chinese culture owls symbolize wisdom")])
    found = ("Owls play a role in Native American cultures.", "In Christianity owls carry darker connotations.")
    result, _ = gaps(["Find out how at least three different cultures have viewed owls"], [reply], found)
    assert any("only 2 of 3 found (Native American cultures, Christianity)" in g for g in result)


class TestUnfinishedResults:
    """Issue #11: a result that stops partway through leaves parts out without a word."""

    CUT = ("1. **What goes in:** sunlight, carbon dioxide and water [1].\n\n2. **What comes out:** the Calvin cycle uses "
           "stored chemical energy from the light-dependent reactions to")

    def test_a_result_ending_on_a_word_like_to_or_a_comma_is_unfinished(self):
        assert ab.ends_mid_sentence(self.CUT) and ab.ends_mid_sentence("It is tall,")
        assert ab.ends_mid_sentence("The thylakoids hold the [1]")

    def test_finished_sentences_lists_and_citation_lists_are_not(self):
        for text in ["It stands at 5,895 m according to the information found at [1].", "- ONLYOFFICE Docs",
                     "References: [1], [2], [4]", "[1], [2]", "Use these:", "Run `pip freeze`"]:
            assert not ab.ends_mid_sentence(text), text

    def test_the_unfinished_sentence_and_its_bare_list_number_are_cut(self):
        assert ab.complete_sentences(self.CUT) == "1. **What goes in:** sunlight, carbon dioxide and water [1]."
        assert ab.complete_sentences("A is B [1]. C is") == "A is B [1]."

    def test_an_opening_line_repeating_the_objective_is_left_out(self):
        assert ab.without_echoed_objective("Explain it.\n\nIt works [1].", "explain  it") == "It works [1]."
        assert ab.without_echoed_objective("Explain it.", "Explain it.") == "Explain it."

    def test_an_opening_sentence_of_the_objective_is_left_out(self):
        goal = "How do I make cold brew coffee at home? List what I need with the quantities."
        # Gemma's cold brew results: the objective's question first, a citation stuck to it
        assert ab.without_echoed_objective("How do I make cold brew coffee at home? [2] The steps: grind the coffee "
                                           "coarsely [2].", goal) == "The steps: grind the coffee coarsely [2]."
        assert ab.without_echoed_objective("# How do I make cold brew coffee at home?\nGrind the coffee coarsely and "
                                           "steep it [1].", goal) == "Grind the coffee coarsely and steep it [1]."
        # Nothing of substance after it: left as it is; another opening question is not the objective's
        assert ab.without_echoed_objective("How do I make cold brew coffee at home? [1]", goal) == \
            "How do I make cold brew coffee at home? [1]"
        other = "Why cold brew? It is smoother and keeps longer in the fridge [1]."
        assert ab.without_echoed_objective(other, goal) == other

    def test_an_unfinished_result_is_written_again(self):
        client, model = fake_client([self.CUT, "It takes in water and gives out glucose [1]."])
        text = asyncio.run(ab.compose_final_result(client, "objective", discoveries("x")))
        assert text == "It takes in water and gives out glucose [1]."
        assert 'stops partway through a sentence ("' in model.calls[1][-1]["content"]

    def test_written_unfinished_twice_it_is_cut_back_and_the_code_says_so(self):
        client, model = fake_client([self.CUT, self.CUT])
        text = asyncio.run(ab.compose_final_result(client, "objective", discoveries("x")))
        assert text == "1. **What goes in:** sunlight, carbon dioxide and water [1].\n\n" + ab.UNFINISHED_NOTE

    def test_a_reply_stopped_at_the_length_limit_is_written_again(self):
        client, model = fake_client([("A long result [1].", "length"), "A shorter result [1]."])
        assert asyncio.run(ab.compose_final_result(client, "objective", discoveries("x"))) == "A shorter result [1]."

    def test_a_finished_result_needs_no_second_call(self):
        client, model = fake_client(["It is 5,895 m tall [1]."])
        asyncio.run(ab.compose_final_result(client, "objective", discoveries("x")))
        assert len(model.calls) == 1


class TestFewerFoundThanAsked:
    """Issue #11: "Three free, open-source alternatives ... are LibreOffice [1] and Apache OpenOffice [2]"."""

    def test_short_counts_are_read_from_the_coverage_gaps(self):
        gaps = ["Item 1 (Name three free (and open) suites): only 2 of 3 found (LibreOffice, OpenOffice)",
                "Item 2 (Say what each is best at): missing strengths"]
        assert ab.short_counts(gaps) == {1: (2, 3)}

    def test_the_final_result_is_told_and_the_code_reports_the_shortfall(self):
        items = checklist("Name three free suites", "Say what each is best at")
        client, model = fake_client(["LibreOffice [1] and OpenOffice [1]."])
        text = asyncio.run(ab.compose_final_result(client, "objective", discoveries("x"), items, short={1: (2, 3)}))
        assert "### FEWER FOUND THAN ASKED" in model.calls[0][-1]["content"]
        assert "- Name three free suites: only 2 of 3 found" in model.calls[0][-1]["content"]
        assert text.endswith("Not found in the pages read:\n- Name three free suites: one more (only two of the three "
                             "were found)")
        assert ab.unverified_numbers(text, discoveries("x")) == []

    def test_with_none_found_the_note_says_so_plainly(self):
        items = checklist("Name three free suites", "Say what each is best at")
        client, _ = fake_client(["The page lists no suites [1]."])
        text = asyncio.run(ab.compose_final_result(client, "objective", discoveries("x"), items, short={1: (0, 3)}))
        assert text.endswith("- Name three free suites: none of the three were found")
        assert "only none" not in text

    def test_short_items_are_counted_again_before_the_final_result(self):
        items = checklist("Name three free suites", "Say what each is best at")
        ab.link_discovery(items, [1, 2], 1)
        found = discoveries("LibreOffice, OpenOffice and Calligra are free suites.")
        client, model = fake_client([verdict(evidence="[1] LibreOffice, OpenOffice and Calligra are free suites.",
                                             things=[("LibreOffice", "LibreOffice"), ("OpenOffice", "OpenOffice"),
                                                     ("Calligra", "Calligra")])])
        tried = {"checklist": items, "short_counts": {1: (2, 3)}}
        asyncio.run(ab.not_found_items(client, "o", tried, found))
        assert len(model.calls) == 1 and tried["short_counts"] == {}


def cited(answer, quote):
    return json.dumps({"answer": answer, "quote": quote})


class TestFalseGaps:
    """Issue #11: "its height above the plateau base is not specified", over a discovery that gives it."""

    KILI = ("Mount Kilimanjaro is the highest mountain in Africa, at 5,895 m (19,341 ft) above sea level and 4,900 m "
            "(16,100 ft) above its plateau base.")
    RESULT = ("It stands at 5,895 meters (19,341 feet) above sea level [1], but its height above the plateau base is "
              "not specified in the given discoveries [1].")

    def test_a_clause_saying_a_discovery_lacks_what_it_gives_is_cut(self):
        client, model = fake_client([cited("yes", "4,900 m (16,100 ft) above its plateau base")])
        text = asyncio.run(ab.without_false_gaps(client, self.RESULT, discoveries(self.KILI)))
        assert text == "It stands at 5,895 meters (19,341 feet) above sea level [1]."
        assert model.calls[0][-1]["content"].startswith("### ITEM\nits height above the plateau base")

    def test_a_quote_not_in_the_discoveries_does_not_count(self):
        client, model = fake_client([cited("yes", "the plateau base is 4,000 m high")])
        assert asyncio.run(ab.without_false_gaps(client, self.RESULT, discoveries(self.KILI))) == self.RESULT

    def test_a_no_keeps_the_claim(self):
        client, model = fake_client([cited("no", "")])
        assert asyncio.run(ab.without_false_gaps(client, self.RESULT, discoveries(self.KILI))) == self.RESULT

    def test_a_claim_sharing_one_word_with_the_quote_stays(self):
        found = discoveries("LibreOffice is a free alternative to Microsoft Office.")
        result = "LibreOffice [1]. The third alternative was not found."
        client, model = fake_client([cited("yes", "LibreOffice is a free alternative")])
        assert asyncio.run(ab.without_false_gaps(client, result, found)) == result

    def test_a_whole_sentence_claim_goes_and_the_rest_stays(self):
        found = discoveries("The light-dependent reactions take place in the thylakoids.")
        result = "Water goes in [1].\nThe location of the light-dependent reactions is not given. It needs light."
        client, model = fake_client([cited("yes", "The light-dependent reactions take place in the thylakoids")])
        assert asyncio.run(ab.without_false_gaps(client, result, found)) == "Water goes in [1].\nIt needs light."

    def test_one_word_claims_are_not_asked_about(self):
        client, model = fake_client([])
        result = "The exact duration was not found."
        assert asyncio.run(ab.without_false_gaps(client, result, discoveries("x"))) == result and model.calls == []


class TestLoopsAndBareNotFound:
    def test_a_reply_looping_on_one_character_is_not_a_result(self):
        loop = '{"result": "How \n\n#010;Do I Make 2023040116570' + "0" * 880
        assert not ab.usable_result(ab.result_text(loop))
        assert ab.usable_result("| Task | Done |\n|---|---|\n" + "-" * 60 + "\nA table [1].")

    def test_looping_twice_is_no_result(self):
        loop = '{"result": "How \n\n#010;Do I Make 2023040116570' + "0" * 880
        client, model = fake_client([(loop, "length"), (loop, "length")])
        assert asyncio.run(ab.compose_final_result(client, "objective", discoveries("x"))) == ab.NO_RESULT

    def test_cut_back_to_nothing_finished_is_no_result(self):
        client, model = fake_client(["How to make it with the", "How to make it with the"])
        assert asyncio.run(ab.compose_final_result(client, "objective", discoveries("x"))) == ab.NO_RESULT

    FOUND = discoveries("Chlorophyll captures energy from sunlight to break down water molecules.",
                        "The Calvin cycle fixes carbon dioxide into glucose.")
    ANSWERED = [("Explain what goes into photosynthesis.", "captures energy from sunlight to break down water molecules"),
                ("Explain what comes out of photosynthesis.", "fixes carbon dioxide into glucose")]

    def test_a_part_answered_only_not_found_gets_the_checked_words_of_its_item(self):
        result = "1. **What goes into photosynthesis:**\n   - Not found.\n\n2. **What comes out:** glucose [2]."
        client, model = fake_client([])
        text = asyncio.run(ab.without_false_gaps(client, result, self.FOUND, self.ANSWERED))
        assert text == ("1. **What goes into photosynthesis:**\n   - \"captures energy from sunlight to break down water "
                        "molecules\" [1]\n\n2. **What comes out:** glucose [2].") and model.calls == []

    def test_on_one_line_too(self):
        client, model = fake_client([])
        text = asyncio.run(ab.without_false_gaps(client, "**What comes out:** Not found.", self.FOUND, self.ANSWERED))
        assert text == '**What comes out:** "fixes carbon dioxide into glucose" [2]'

    def test_a_heading_matching_no_single_item_keeps_not_found(self):
        client, model = fake_client([])
        result = "**Photosynthesis:** Not found."
        assert asyncio.run(ab.without_false_gaps(client, result, self.FOUND, self.ANSWERED)) == result
        assert asyncio.run(ab.without_false_gaps(client, "**Care:** Not found.", self.FOUND, self.ANSWERED)) \
            == "**Care:** Not found."


class TestInventedNumbersAndCitations:
    FOUND = discoveries("Steep the grounds for 16 hours in 1 cup of coffee to 4 cups of water.")

    def test_a_sentence_giving_a_number_no_discovery_has_is_cut_and_the_number_named(self):
        result = ("1. Add 1 cup of coffee to 4 cups of water [1].\n2. Fill the jar about 3/4 full.\n3. Steep for 16 "
                  "hours [1]. It keeps for up to 2 weeks [1].")
        assert ab.without_invented_numbers(result, self.FOUND) == (
            "1. Add 1 cup of coffee to 4 cups of water [1].\n\n3. Steep for 16 hours [1].\n\n"
            "Left out, because no discovery gives these numbers (the pages read may still have them): 3/4, 2.")

    def test_list_numbers_and_backed_numbers_stay(self):
        result = "1. Steep for 16 hours [1].\n2. Strain it."
        assert ab.without_invented_numbers(result, self.FOUND) == result

    def test_a_filled_gap_keeps_its_gap_in_the_final_result(self):
        reply = "Steep for 16 hours [1]. How long it keeps is not specified, but it is usually 1-2 weeks [1]."
        client, model = fake_client([reply])
        text = asyncio.run(ab.compose_final_result(client, "objective", self.FOUND))
        assert text == "Steep for 16 hours [1]. How long it keeps is not specified."

    def test_citations_of_discoveries_that_do_not_exist_are_removed(self):
        text = "It is 5,895 m [2]. In feet, 19,341 [1, 3].\n  - Kept [1]"
        assert ab.without_missing_citations(text, 1) == "It is 5,895 m. In feet, 19,341 [1].\n  - Kept [1]"


def test_digits_in_names_and_code_are_not_numbers_to_check():
    found = discoveries("Photosynthesis takes in water and carbon dioxide.")
    text = "It takes in water (H2O) and CO2 [1]. Use the `sqlite3` tool or `python3 -m venv`."
    assert ab.unverified_numbers(text, found) == []
    assert ab.without_invented_numbers(text, found) == text
    assert ab.unverified_numbers("Steep 40g for 12 hours.", found) == ["40", "12"]


class TestUncitedResults:
    FOUND = discoveries("SQLite needs no server and no administration: the database is a single file.",
                        "PostgreSQL supports JSON columns, window functions and full-text search.")

    def test_a_result_citing_nothing_is_written_again(self):
        client, model = fake_client(["SQLite needs no server.", "SQLite needs no server [1]."])
        assert asyncio.run(ab.compose_final_result(client, "objective", self.FOUND)) == "SQLite needs no server [1]."
        assert model.calls[1][-1]["content"].startswith("That result cites no discovery")

    def test_cited_by_the_code_where_the_wording_matches_one_discovery(self):
        result = ("### Setup\nSQLite needs no server and no administration at all.\nPostgreSQL supports JSON columns "
                  "and window functions.\nBoth are popular choices for many developers.")
        client, model = fake_client([result, result])
        text = asyncio.run(ab.compose_final_result(client, "objective", self.FOUND))
        assert text == ("### Setup\nSQLite needs no server and no administration at all. [1]\nPostgreSQL supports JSON "
                        "columns and window functions. [2]\nBoth are popular choices for many developers.")

    def test_a_line_introducing_a_list_or_naming_another_thing_is_not_cited(self):
        found = discoveries("ONLYOFFICE Docs has editors for text documents, spreadsheets and presentations.")
        text = "Here are editors for text documents, spreadsheets:\nLibreOffice has editors for text documents, spreadsheets."
        assert ab.with_matched_citations(text, found) == text

    def test_the_writing_rules_say_to_keep_each_fact_under_its_own_part(self):
        client, model = fake_client(["Result [1]."])
        asyncio.run(ab.compose_final_result(client, "objective", self.FOUND, checklist("Setup", "Users")))
        assert "never under another part's heading" in model.calls[0][-1]["content"]


class TestNamesNoDiscoveryGives:
    FOUND = discoveries("It is also the highest free-standing mountain above sea level, at 5,895 m (19,341 ft).",
                        "Customizable Interface, Digital Retouching, Layer management support")

    def test_a_sentence_naming_what_no_discovery_names_is_cut_and_the_name_listed(self):
        text = ("The tallest mountain in Africa is Mount Kilimanjaro [1]. It is 5,895 m (19,341 ft) tall [1].\n"
                "- **GIMP**: Customizable Interface, Digital Retouching [2]")
        assert ab.without_unnamed_names(text, self.FOUND, "What is the tallest mountain in Africa?") == (
            "It is 5,895 m (19,341 ft) tall [1].\n\nLeft out, because no discovery names them (the pages read may "
            "still have them): Kilimanjaro, GIMP.")

    def test_sentence_starts_headings_labels_other_word_forms_and_acronyms_in_the_findings_are_fine(self):
        found = discoveries("Egyptian and Christianity traditions; the Hopis; uses JSON.")
        text = ("### Comparison\n**Recommendation:** Pour it. Both views: Egypt and Christian traditions, the Hopi, "
                "and JSON [1].\nMy View: Although rare, it holds.")
        assert ab.unnamed_names(text, found, "Compare views") == []

    def test_an_acronym_no_discovery_gives_is_a_name(self):
        assert ab.unnamed_names("Light reactions make ATP and NADPH [1].", self.FOUND) == ["ATP", "NADPH"]


def test_bold_numbered_labels_are_not_numbers_to_check():
    # Box 1, 2026-11-06: "**1. Explain what goes into photosynthesis:**" lost its "1" and the label was mangled
    found = discoveries("Carbon dioxide and water go in.")
    text = "**1. Explain what goes into photosynthesis:**\nCarbon dioxide and water go in [1].\n**3. Explain more:** fine."
    assert ab.unverified_numbers(text, found) == []
    assert ab.without_invented_numbers(text, found) == text


def test_only_the_clause_holding_an_unbacked_name_is_cut():
    # BB2 v5 explanation, 2026-11-06: the cited thylakoid location went with the whole sentence
    found = discoveries("The light-dependent reactions take place in the thylakoids.")
    text = ("The light-dependent reactions take place in the thylakoids [1] and convert light energy into chemical "
            "energy, which is stored in molecules like ATP and NADPH.")
    assert ab.without_unnamed_names(text, found, "Explain photosynthesis") == (
        "The light-dependent reactions take place in the thylakoids [1] and convert light energy into chemical "
        "energy.\n\nLeft out, because no discovery names them (the pages read may still have them): ATP, NADPH.")
    # A name the whole sentence rests on still takes the sentence
    assert ab.without_unnamed_names("Mount Kilimanjaro is the tallest [1].", discoveries("It is the tallest."),
                                    "Which is tallest?").startswith("Left out")


def test_do_not_specify_is_a_claim_that_something_is_missing():
    assert ab.NOT_GIVEN.search("These reactions do not specify a particular location within the plant cell.")
    assert ab.NOT_GIVEN.search("The page does not mention the steeping time.")


def test_citations_written_inside_code_are_moved_out_before_the_command_check():
    # Box 1 v5 docs, 2026-11-06: the retry put markers inside backticks, and the correct commands were cut
    found = discoveries(r"On Windows run venv\Scripts\activate and on macOS or Linux run source venv/bin/activate.")
    text = "Windows: `venv\\Scripts\\activate [1]`\nmacOS or Linux: `source venv/bin/activate [1]`"
    moved = ab.with_citations_outside_code(text)
    assert moved == "Windows: `venv\\Scripts\\activate` [1]\nmacOS or Linux: `source venv/bin/activate` [1]"
    assert ab.unverified_code(moved, found) == []
    assert ab.with_citations_outside_code("```\npython -m venv venv [1]\n```") == "```\npython -m venv venv\n```"


def test_an_unclosed_code_block_is_closed_at_its_paragraph_end():
    text = "Install:\n```\npip install requests\n\n### How to Activate it on Windows\nUse the script."
    assert ab.with_code_closed(text) == ("Install:\n```\npip install requests\n```\n\n### How to Activate it on "
                                         "Windows\nUse the script.")
    assert ab.with_code_closed("```\nx\n```") == "```\nx\n```"


def test_numbers_written_out_on_the_page_match_digits():
    # Box 1 v6 cold brew, 2026-11-06: "one cup ... four cups" on the page, "1 cup ... 4 cups" in the DISCOVER
    page = "Start with one cup of beans steeped in four cups of water, then wait about twelve hours."
    state = {"url": "https://brew.example/a", "parts": [page], "index": 0, "seen_parts": {0}}
    discover = "Start with 1 cup of beans steeped in 4 cups of water, then wait about 12 hours."
    assert ab.check_decision("DISCOVER", discover, [], {"objective": "o"}, state, "https://brew.example/a") is None
    assert ab.unverified_numbers("Use 1 cup of beans and 4 cups of water [1].", discoveries(page)) == []
    # "one of the best" is not an amount
    assert ab.unverified_numbers("Use 1 cup [1].", discoveries("It is one of the best brews.")) == ["1"]


def test_names_copied_accepts_one_name_per_line_but_not_the_items_own_words():
    from localagentbrowser.coverage import names_copied
    page = "Popular free editors include GIMP, Krita and Darktable. Photosynthesis makes carbohydrates."
    assert names_copied("GIMP\nKrita\nDarktable", page, "Find three free alternatives to Photoshop.")
    assert names_copied("carbohydrates", page, "Describe what comes out of photosynthesis.")
    assert not names_copied("Photosynthesis", page, "Describe what comes out of photosynthesis.")
    assert not names_copied("Inkscape", page, "Find three free alternatives to Photoshop.")
    assert not names_copied("free editors include GIMP", page, "Find three free alternatives to Photoshop.")


def test_a_cited_heading_naming_a_program_no_discovery_names_goes_with_its_section():
    found = discoveries("GIMP\nKey Features:\n- Layers", "PhotoSplice\nKey Features:\n- Blending modes")
    text = ("### Alternatives\n\n#### GIMP [1]\n- Layers\n\n#### PhotoScape [2]\n- Blending modes\n\n"
            "#### Setup and Administration Differences [1]\n- Simple")
    assert ab.unnamed_names(text, found, "Find free alternatives to Photoshop") == ["PhotoScape"]
    assert ab.without_unnamed_names(text, found, "Find free alternatives to Photoshop") == (
        "### Alternatives\n\n#### GIMP [1]\n- Layers\n\n#### Setup and Administration Differences [1]\n- Simple\n\n"
        "Left out, because no discovery names them (the pages read may still have them): PhotoScape.")


def test_word_forms_count_as_named_but_a_shared_start_does_not():
    from localagentbrowser.report import other_form
    assert other_form("egypt", "egyptians") and other_form("comparison", "compare")
    assert not other_form("photoscape", "photosplice")


def test_a_citation_on_a_line_of_its_own_joins_the_line_it_cites():
    from localagentbrowser.report import with_lone_citations_joined
    text = "- **SQLite**: Compiles into your application.\n  - [1]\n- **PostgreSQL**: A server.\n[2], [3]\n\nNext [4]"
    assert with_lone_citations_joined(text) == (
        "- **SQLite**: Compiles into your application. [1]\n- **PostgreSQL**: A server. [2], [3]\n\nNext [4]")


def test_an_amount_in_words_no_discovery_gives_is_cut():
    found = discoveries("Steep for 12 hours. Use one cup of beans.")
    text = "Steep for twelve hours [1]. Use one cup of beans [1]. It keeps for up to two weeks [not found]."
    assert ab.unverified_numbers(text, found) == ["two weeks"]
    assert ab.without_invented_numbers(text, found).startswith("Steep for twelve hours [1]. Use one cup of beans [1].\n")


def test_a_recommendation_section_is_labelled_as_the_models_view():
    from localagentbrowser.report import with_view_labelled
    text = "#### Features\n- Small [1]\n\n### Recommendation for a Hobby Project\nPostgreSQL is recommended.\n"
    assert with_view_labelled(text) == "#### Features\n- Small [1]\n\nMy view: PostgreSQL is recommended.\n"
    assert with_view_labelled("### Recommendation\n- **PostgreSQL**: best") == "### Recommendation\n- **PostgreSQL**: best"
    assert with_view_labelled("No heading here.") == "No heading here."
    # Numbered (Phi-4, 1.66), and a list first with the advice after it (Phi-4, 1.67)
    assert with_view_labelled("#### 4. Recommendation for a Hobby Project\n\nSQLite is recommended [3].") == \
        "My view: SQLite is recommended [3]."
    listed = ("### Recommendation for a Hobby Project\n- **SQLite**: simple [3].\n- **PostgreSQL**: scales [1].\n\n"
              "Given the requirements, **PostgreSQL** is recommended [1, 3].\n\n## Next\nOther text.")
    assert with_view_labelled(listed) == listed.replace("Given the", "My view: Given the")
    # Not a paragraph past the section's end, nor one already labelled
    assert with_view_labelled("### Recommendation\n- a\n- b\n\n## Next\nOther text.") == "### Recommendation\n- a\n- b\n\n## Next\nOther text."
    labelled = "### Recommendation\n- a\n\nMy view: b."
    assert with_view_labelled(labelled) == labelled


def test_a_time_needs_the_same_number_given_as_a_time():
    found = discoveries("Use a 2L mason jar. Refrigerate for 16 hours (minimum 12, maximum 24). Steep 12 to 24 hours.")
    text = "Refrigerate for 16 hours [1]. Steep 12 hours [1]. It keeps for up to 2 weeks [1]."
    assert ab.unverified_numbers(text, found) == ["2 weeks"]


def test_a_short_unit_is_the_same_time():
    found = discoveries("Steep for 10 mins, stir, then rest 2 hrs. It keeps 2 wks.")
    text = "Steep for 10 minutes [1], then rest 2 hours [1]. It keeps for 2 weeks [1]."
    assert ab.unverified_numbers(text, found) == []
    # "10 min" is not "10 hours", and "minimum" is not a unit
    assert ab.unverified_numbers("Steep for 10 hours [1].", discoveries("Steep for 10 min.")) == ["10 hours"]
    assert ab.unverified_numbers("Steep for 10 hours [1].", discoveries("A minimum of 10 hours.")) == []


def test_a_number_keeps_the_unit_its_discovery_gives():
    # Gemma 3 4B gave a page's "173.8 °F" as "173.8 °C"
    found = discoveries("Ethanol boils at 173.8 °F. Kilimanjaro is 5,895 m (19,341 ft) tall. Use 40 g of coffee.")
    assert ab.unverified_numbers("Ethanol boils at 173.8 °C [1].", found) == ["173.8 °C"]
    assert ab.unverified_numbers("It is 5,895 feet tall [1].", found) == ["5,895 ft"]
    assert ab.unverified_numbers("Use 40 ounces of coffee [1].", found) == ["40 oz"]
    # The same unit in other words, the other figure the page gives, and units of another kind are all fine
    assert ab.unverified_numbers("It boils at 173.8 degrees Fahrenheit [1]. It is 19,341 feet, 5,895 metres "
                                 "tall [1]. Use 40 grams [1].", found) == []
    # A number the discoveries give without a unit is not held to one
    assert ab.unverified_numbers("It is 5,895 feet tall [1].", discoveries("Kilimanjaro: 5,895.")) == []


def test_the_checks_own_left_out_notes_are_not_numbers_missing_from_the_discoveries():
    found = discoveries("Steep for 12 hours.")
    text = ("Steep for 12 hours [1].\n\nLeft out, because no discovery gives these numbers (the pages read may still "
            "have them): 33 years, 80")
    assert ab.unverified_numbers(text, found) == []


def test_control_characters_are_taken_out_of_a_result_but_not_line_breaks():
    raw = json.dumps({"result": "Use \bthe \x00venv module [1].\n\tThen\x7f activate it [1]."})
    assert ab.result_text(raw) == "Use the venv module [1].\n\tThen activate it [1]."


def test_one_sentence_given_for_several_items_counts_only_where_it_has_their_words():
    from localagentbrowser.coverage import shared_evidence_items
    checklist = [{"text": t} for t in ("Compare SQLite and PostgreSQL setup and administration",
                                       "Compare SQLite and PostgreSQL handling of many simultaneous users",
                                       "Compare SQLite and PostgreSQL features",
                                       "Recommend one for a hobby project")]
    sentence = ("I'd pick SQLite for any single-server app under roughly 10,000 daily active users, and PostgreSQL the "
                "moment concurrent writes, team growth, or multi-region deployments enter the picture.")
    assert shared_evidence_items({1: sentence, 2: sentence, 3: sentence}, checklist) == [1, 3]
    # Different words for each item: nothing shared
    assert shared_evidence_items({1: "SQLite needs no server to administer.", 3: "PostgreSQL has JSONB and full-text "
                                  "search."}, checklist) == []


def test_a_sentence_put_under_the_wrong_subject_is_cut():
    found = discoveries("SQLite databases are self-contained single files. SQLite offers minimal, zero-configuration "
                        "administration suitable for embedded databases.",
                        "PostgreSQL has advanced administrative capabilities for enterprise servers.")
    text = ("- **SQLite**: Databases are self-contained single files [1].\n"
            "- **PostgreSQL**: Has advanced administrative capabilities for enterprise servers [2]. Offers minimal, "
            "zero-configuration administration suitable for embedded databases [1].")
    assert ab.without_swapped_subjects(text, found) == (
        "- **SQLite**: Databases are self-contained single files [1].\n"
        "- **PostgreSQL**: Has advanced administrative capabilities for enterprise servers [2].\n\n"
        "Left out, because the discovery says it of another subject: PostgreSQL (the discovery says it of SQLite).")


def test_a_shared_evidence_gap_sends_the_agent_back():
    from localagentbrowser.coverage import UNANSWERED_GAP, shared_gap
    # coverage_refusal sends back only gaps that end with UNANSWERED_GAP
    assert shared_gap(2, "Compare SQLite and PostgreSQL features").endswith(UNANSWERED_GAP)


def test_one_sentence_giving_both_units_answers_both_unit_items():
    from localagentbrowser.coverage import shared_evidence_items
    checklist = [{"text": t} for t in ("What is the tallest mountain in Africa?", "How tall is it in meters?",
                                       "How tall is it in feet?")]
    sentence = ("It is also the highest free-standing mountain above sea level in the world, at 5,895 m (19,341 ft) "
                "above sea level.")
    assert shared_evidence_items({2: sentence, 3: sentence}, checklist) == []


def test_a_name_in_a_discovery_s_own_address_counts_as_given():
    found = [{"extracted_data": "File formats support, command line options, extension system, filters and blend modes.",
              "source": "https://inkscape.org/about/features/"}]
    assert ab.unnamed_names("- **Inkscape**: file formats, extensions and filters [1].", found, "Find three alternatives") == []


def test_a_concrete_missing_thing_no_discovery_names_sends_back():
    from localagentbrowser.coverage import concrete_term
    assert concrete_term("O2") and concrete_term("GA3P") and concrete_term("Step 3")
    assert not concrete_term("the specific steps of the Calvin cycle") and not concrete_term("location in the plant cell")
    client, _ = fake_client([verdict(missing="O2")])
    items = [{"text": "Describe what comes out of photosynthesis.", "discoveries": [1]}]
    paragraph = "The Calvin cycle, which takes place in the stroma, uses energy to make GA3P from CO2."
    refusal = asyncio.run(ab.coverage_refusal(client, "objective", {"checklist": items}, discoveries(paragraph)))
    assert refusal[0] == "BLOCKED_COVERAGE" and "O2: named by no discovery" in refusal[1]
    # Named by a discovery after all ("6O2" in an equation): really_missing drops it, nothing is sent back
    client, _ = fake_client([verdict(missing="O2")])
    assert asyncio.run(ab.coverage_refusal(client, "objective", {"checklist": items},
                                           discoveries("6CO2 + 6H2O -> C6H12O6 + 6O2"))) is None


def test_the_prompt_s_own_section_names_are_not_unbacked_names():
    found = discoveries("The light reactions make ATP.")
    assert ab.unnamed_names("Following the OBJECTIVE CHECKLIST, the light reactions make ATP [1].", found, "Explain it") == []


def test_a_view_at_the_end_of_a_repeated_result_is_kept():
    # GPT-4o-mini sent the whole owl result back with its view at the end; the view was dropped with the repeat
    result = "1. In Western cultures owls stood for wisdom and death [1].\n2. Mayan rulers wore owl emblems [1]."
    view = "My view: Owls are seen at night and heard more than seen, so people read very different things into them."
    client, model = fake_client([result, result + "\n\n" + view])
    text = asyncio.run(ab.compose_final_result(client, "Find how cultures viewed owls, then give your own opinion.",
                                               discoveries("Western cultures saw owls as wisdom and death; Mayan rulers wore owl emblems.")))
    assert text.count("Western cultures") == 1 and view in text


def test_a_reply_repeating_the_prompt_keeps_the_answer_after_it():
    # Llama 3.1 8B's writing-step replies, three in one suite, refused whole: the answer came after the repeat
    multi = ("### OBJECTIVE\nCompare SQLite and PostgreSQL.\n\n### CHECKLIST (the parts of the OBJECTIVE)\n1. Compare setup\n"
             "2. Recommend one\n\n### DISCOVERIES\n[1] (from https://x.org)\nSQLite needs no server.\n\n### RESULTS\n"
             "### 1. Compare setup\nSQLite needs no server [1].\n\n### MY VIEW")
    out = ab.result_text(multi)
    assert ab.usable_result(out) and out.startswith("### 1. Compare setup") and "### DISCOVERIES" not in out
    assert not out.rstrip().endswith("### MY VIEW")
    one_line = ("### OBJECTIVE: Find out how cultures viewed owls. ### CHECKLIST (the parts of the OBJECTIVE) 1. Find out "
                "how cultures viewed owls ### DISCOVERIES [1] (from https://owls.example) Egyptians linked owls with death. "
                "### 1. Find out how cultures viewed owls Egyptian records connect owls with death [1].")
    out = ab.result_text(one_line)
    assert ab.usable_result(out) and "Egyptian records connect owls with death [1]." in out and "[1] (from" not in out


def test_a_kind_of_text_label_is_not_a_subject():
    # Phi-4's photosynthesis result lost its "Process" and "Details" lines as "said of Light-dependent reactions"
    found = discoveries("Light-dependent reactions split water molecules into hydrogen ions and oxygen atoms in the thylakoids.")
    text = ("**Light-dependent reactions:** These occur in the thylakoids [1].\n"
            "- **Details:** Water molecules are split into hydrogen ions and oxygen atoms in the thylakoids [1].")
    assert ab.swapped_sentences(text, found) == []


def test_the_codes_own_count_survives_the_number_check():
    # GPT-4o-mini's owl result: "Only 2 of the 3 cultures or religions could be found", cut as no page's number
    client, _ = fake_client(["1. Western cultures linked owls with wisdom [1].\n2. Mayans linked owls with rulers [1].\n"
                             "Only 2 of the 3 cultures or religions could be found."])
    items = [{"text": "Find out how at least three different cultures or religions have viewed owls", "discoveries": [1]}]
    text = asyncio.run(ab.compose_final_result(client, "objective", discoveries("Western cultures and Mayans saw owls."),
                                               items, short={1: (2, 3)}))
    assert "Only two of the three cultures or religions could be found." in text and "Left out" not in text


def test_a_repeated_block_of_sections_is_cut_and_look_alikes_stay():
    # Llama 3.1 8B restated all four parts under "### SUMMARY", and the copies disagreed
    repeated = ("### 1. Compare setup and administration of SQLite and PostgreSQL\nSQLite needs no server [1].\n"
                "### 2. Compare handling of many simultaneous users in PostgreSQL\nMVCC handles many writers [2].\n"
                "### SUMMARY\n### 1. Compare setup and administration of SQLite and PostgreSQL\nSQLite is easy [1].\n"
                "### 2. Compare handling of many simultaneous users in SQLite and PostgreSQL\nIt was not found.\n"
                "### My view:\nSQLite for a hobby project.")
    out = ab.without_repeated_sections(repeated)
    assert "MVCC handles many writers [2]." in out and "It was not found." not in out and "SUMMARY" not in out
    assert out.count("Compare setup") == 1 and "### My view:" in out
    # Parts that look alike are not repeats: meters beside feet, one tool's strengths beside another's
    for text in ("### Tallest Mountain in Africa\nKilimanjaro [1].\n### Height of the Tallest Mountain in Africa in Meters\n"
                 "5,895 m [1].\n### Height of the Tallest Mountain in Africa in Feet\n19,341 ft [1].",
                 "## GIMP\n### Main Strengths\nLayers [1].\n## Krita\n### Main Strengths\nBrushes [2]."):
        assert ab.without_repeated_sections(text) == text


class TestCutOffCoverageReplies:
    """A coverage reply cut off at the length limit (Opus 5.5 copied long passages as evidence and broke mid-JSON) is
    asked for once more with a higher limit; failing again, the item is not checked, never counted as covered."""

    CUT = '{"evidence": "[1] LibreOffice is free and open source and has a long list of features that goes on'

    def test_a_cut_off_reply_is_asked_again_with_a_higher_limit_and_shorter_evidence(self):
        client, model = fake_client([(self.CUT, "length"), verdict(evidence="[1] LibreOffice is free and open source")])
        unchecked = set()
        result, unanswered, _ = asyncio.run(ab.coverage_gaps(client, "o", checklist("Name one office suite"),
                                                          discoveries("LibreOffice is free and open source."),
                                                          unchecked=unchecked))
        assert len(model.calls) == 2 and unanswered == [] and result == [] and unchecked == set()
        assert model.calls[1][-1]["content"] == ab.COVERAGE_SHORTER
        assert model.calls[1][-2] == {"role": "assistant", "content": self.CUT}

    def test_failing_twice_is_not_checked_not_covered_and_not_unanswered(self, capsys):
        client, model = fake_client([(self.CUT, "length"), (self.CUT, "length")])
        unchecked, items = set(), checklist("Name one office suite")
        result, unanswered, _ = asyncio.run(ab.coverage_gaps(client, "o", items, discoveries("LibreOffice is free."),
                                                          unchecked=unchecked))
        assert unchecked == {1} and unanswered == [] and result == [] and items[0]["unchecked"]
        assert "not checked (not counted as covered)" in capsys.readouterr().out
        assert "coverage check failed, not checked" in ab.checklist_lines(items, report=True)
        assert "not checked" not in ab.checklist_lines(items)  # the prompts the model reads are unchanged

    def test_a_later_check_that_works_clears_the_mark(self):
        client, _ = fake_client([(self.CUT, "length"), (self.CUT, "length"), verdict(evidence="[1] LibreOffice is free")])
        items, found = checklist("Name one office suite"), discoveries("LibreOffice is free.")
        asyncio.run(ab.coverage_gaps(client, "o", items, found))
        assert items[0]["unchecked"]
        asyncio.run(ab.coverage_gaps(client, "o", items, found))
        assert "unchecked" not in items[0]


class TestResultsCutOffAtTheLimit:
    """K3: Opus 5.5's results ran past COMPOSE_MAX_TOKENS; written again at the same limit they were cut off again, and
    2 of 4 ended "stopped partway". A result cut off at the limit is asked for more briefly, with a higher limit."""

    def test_a_result_cut_off_at_the_limit_is_asked_for_more_briefly_with_a_higher_limit(self):
        client, model = fake_client([("1. Setup: SQLite is one file [1].\n\n2. Many users: PostgreSQL uses", "length"),
                                     "1. Setup: SQLite is one file [1].\n\n2. Many users: PostgreSQL uses MVCC [2]."])
        found = discoveries("SQLite is one file.", "PostgreSQL uses MVCC for many users.")
        text = asyncio.run(ab.compose_final_result(client, "objective", found))
        assert text.endswith("PostgreSQL uses MVCC [2].")
        assert model.limits == [ab.COMPOSE_MAX_TOKENS, ab.COMPOSE_RETRY_MAX_TOKENS]
        assert "cut off at the length limit" in model.calls[1][-1]["content"] and "more briefly" in model.calls[1][-1]["content"]

    def test_a_sentence_left_unfinished_without_hitting_the_limit_keeps_the_old_request(self):
        client, model = fake_client([TestUnfinishedResults.CUT, "It takes in water and gives out glucose [1]."])
        asyncio.run(ab.compose_final_result(client, "objective", discoveries("x")))
        assert 'stops partway through a sentence ("' in model.calls[1][-1]["content"]

    def test_the_rewrite_to_add_citations_also_gets_the_higher_limit(self):
        client, model = fake_client(["SQLite is one file.", "SQLite is one file [1]."])
        asyncio.run(ab.compose_final_result(client, "objective", discoveries("SQLite is one file.")))
        assert model.limits == [ab.COMPOSE_MAX_TOKENS, ab.COMPOSE_RETRY_MAX_TOKENS]


def test_a_number_inside_a_chemical_name_is_part_of_the_name():
    # Phi-4's correct "Ribulose-1,5-bisphosphate (RuBP) is regenerated using ATP [2]" was cut as an invented "1,5"
    found = discoveries("RuBP is regenerated using ATP.")
    assert ab.unverified_numbers("Ribulose-1,5-bisphosphate (RuBP) is regenerated using ATP [1].", found) == []
    assert ab.unverified_numbers("It forms glyceraldehyde-3-phosphate.", found) == []
    # A quantity written with a hyphen is still a quantity
    assert ab.unverified_numbers("Steep it for a 16-hour period.", found) == ["16"]
    assert ab.unverified_numbers("Use a 4-cup jar.", found) == ["4"]
