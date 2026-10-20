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


class TestFinalResult:
    def test_checklist_is_given_with_a_rule_to_cover_it(self):
        client, model = fake_client(["Result [1]."])
        items = checklist("Create it", "Activate it")
        asyncio.run(ab.compose_final_result(client, "objective", discoveries("x"), items))
        request = model.calls[0][-1]["content"]
        assert "### CHECKLIST (the parts of the OBJECTIVE)\n1. Create it\n2. Activate it\n\n### DISCOVERIES" in request
        assert "6. Cover every CHECKLIST item; for an item no discovery supports, say plainly that it was not found." in request
        assert "7. Write plain text, not JSON." in request

    def test_a_single_item_adds_nothing(self):
        client, model = fake_client(["Result [1]."])
        asyncio.run(ab.compose_final_result(client, "objective", discoveries("x"), checklist("objective")))
        request = model.calls[0][-1]["content"]
        assert "CHECKLIST" not in request and "6. Write plain text, not JSON." in request


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
        assert result == ["Item 1: WPS Office: no words in DISCOVERIES SO FAR show it is free and open-source. SEARCH for "
                          "'WPS Office free and open-source' to check it, or replace it with one that is",
                          "Item 1: OnlyOffice: no words in DISCOVERIES SO FAR show it is free and open-source. SEARCH for "
                          "'OnlyOffice free and open-source' to check it, or replace it with one that is"]

    def test_long_descriptions_are_not_conditions(self):
        reply = verdict(conditions=["in different cultures or religions"], things=[("Western cultures", "")])
        assert gaps(["How at least three different cultures or religions viewed owls"], [reply])[0] == []

    def test_conditions_the_item_does_not_state_are_ignored(self):
        reply = verdict(conditions=["open-source"], things=[("WPS Office", "")])
        assert gaps(["What each one is best at"], [reply])[0] == []

    def test_opinion_items_are_not_checked_and_failed_calls_report_nothing(self):
        result, model = gaps(["Give your own opinion on why", "What comes out"], ["not json"])
        assert result == [] and len(model.calls) == 1


class TestCoverageRefusal:
    def test_sent_back_once_with_the_gaps(self):
        client, _ = fake_client([verdict(missing="oxygen")])
        tried = {"checklist": checklist("What comes out")}
        refusal = asyncio.run(ab.coverage_refusal(client, "objective", tried, discoveries("x")))
        assert refusal[0] == "BLOCKED_COVERAGE" and "- Item 1 (What comes out): missing oxygen" in refusal[1]
        assert asyncio.run(ab.coverage_refusal(client, "objective", tried, discoveries("x"))) is None

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

    def test_a_discovery_number_alone_counts_as_evidence(self):
        client, _ = fake_client([verdict(evidence="[2]")])
        assert asyncio.run(ab.coverage_gaps(client, "o", checklist("a"), discoveries("x")))[1] == []

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
        assert missing == [] and unproven == ["WPS Office (not shown to be open-source)"]

        client, model = fake_client(["1. LibreOffice [1]"])
        text = asyncio.run(ab.compose_final_result(client, "o", found, items, [], unproven))
        assert "### NOT SHOWN TO MEET THE CONDITIONS (do not list these" in model.calls[0][-1]["content"]
        assert text.endswith("Left out, not shown to meet the conditions in the pages read:\n- WPS Office (not shown to be open-source)")


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
