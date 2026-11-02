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
        assert asyncio.run(ab.compose_final_result(client, "objective", discoveries("x"))) == "Kilimanjaro, 5,895 m [1]."

    def test_a_tool_call_or_the_prompt_repeated_is_asked_for_again(self):
        # What Llama 3.1 8B and Qwen 3B wrote instead of a final result in the 2026-10-25 model tests
        tool_call = json.dumps({"name": "get_mountain_info", "parameters": {"height_in_meters": 5895}})
        echoed = json.dumps({"result": "### OBJECTIVE\nHow tall is it?\n### DISCOVERIES\n[1] x"})
        for first in (tool_call, echoed):
            client, model = fake_client([first, json.dumps({"result": "It is 5,895 m tall [1]."})])
            assert asyncio.run(ab.compose_final_result(client, "objective", discoveries("x"))) == "It is 5,895 m tall [1]."
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
        result, model = gaps(["Give your own opinion on why", "What comes out"], ["not json"])
        assert result == [] and len(model.calls) == 1


class TestCoverageRefusal:
    def test_sent_back_once_for_an_item_nothing_answers(self):
        client, _ = fake_client([verdict(evidence="", missing="oxygen")])
        tried = {"checklist": checklist("What comes out")}
        refusal = asyncio.run(ab.coverage_refusal(client, "objective", tried, discoveries("x")))
        assert refusal[0] == "BLOCKED_COVERAGE"
        assert "- Item 1 (What comes out): nothing in DISCOVERIES SO FAR answers it" in refusal[1]
        assert asyncio.run(ab.coverage_refusal(client, "objective", tried, discoveries("x"))) is None

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
                                               discoveries("PostgreSQL handles many writers with MVCC.")))
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
