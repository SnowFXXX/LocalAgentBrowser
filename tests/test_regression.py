"""Scoring in tools/regression.py, which turns a run's journal into the measurements compared between versions."""
import importlib.util
import json
import os

import pytest

import agent_browser as ab

from conftest import TESTS_DIR

spec = importlib.util.spec_from_file_location("regression", os.path.join(os.path.dirname(TESTS_DIR), "tools", "regression.py"))
rg = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rg)

TASK = {"id": "list", "kind": "List of items", "objective": "Name three free, open-source office suites.",
        "expect": [["libreoffice"], ["onlyoffice", "openoffice"]], "forbid": ["freeoffice"]}


def journal(result, *found, source="https://www.libreoffice.org/about"):
    entries = [{"turn": 1, "action": "SEARCH"}]
    entries += [{"turn": 2, "action": "DISCOVER", "source": source, "extracted_data": f} for f in found]
    entries.append({"turn": 3, "action": "REFUSED_IN_TURN", "check": "CONFIRM_READ_ON", "reason": "r"})
    if result is not None:
        entries.append({"turn": 4, "action": "FINISH", "finding": result})
    return entries


class TestCitations:
    def test_ranges_and_groups(self):
        assert rg.cited_numbers("a [1] b [2, 4] c [5-7] d [9;10]") == {1, 2, 4, 5, 6, 7, 9, 10}

    def test_citation_ranges_are_not_read_as_dates(self):
        assert rg.cited_numbers("in [2020-2024]") == set()


class TestUncitedLines:
    def test_paragraph_cited_once_at_its_end_counts_as_cited(self):
        text = "LibreOffice is a full office suite with many programs. It is best for documents [1]."
        assert rg.uncited_lines(text) == []

    def test_uncited_paragraph_is_found(self):
        text = "LibreOffice is cited here as a full suite [1].\nOnlyOffice is said to be best for compatibility with Word files."
        assert rg.uncited_lines(text) == ["OnlyOffice is said to be best for compatibility with Word files."]

    def test_a_citation_line_cites_the_list_above_it(self):
        text = ("#### 1. Setup\n- SQLite databases are single files with no separate server process.\n"
                "- Backups are as simple as copying the database file somewhere safe.\n\n[2]\n\n"
                "#### 2. Users\n- PostgreSQL handles many more simultaneous users than SQLite does.")
        assert rg.uncited_lines(text) == ["- PostgreSQL handles many more simultaneous users than SQLite does."]

    def test_a_cited_heading_cites_the_list_under_it(self):
        text = ("#### GIMP [1]\n- Wide range of hardware support for pressure-sensitive tablets and more\n\n"
                "#### Krita\n- Layer management support with a variety of brushes and drawing tools")
        assert rg.uncited_lines(text) == ["- Layer management support with a variety of brushes and drawing tools"]

    def test_view_code_headings_and_gaps_are_left_out(self):
        text = ("Steps:\n```\npython -m venv venv and then several more words here\n```\n"
                "No care instructions beyond the first watering were found in the pages read.\n"
                "My view: this is an opinion written without any citations at all.")
        assert rg.uncited_lines(text) == []


class TestScoreRun:
    def test_finished_run(self):
        score = rg.score_run(TASK, journal("LibreOffice is best for documents [1]. OnlyOffice for Word files [2].",
                                           "LibreOffice ...", "OnlyOffice ..."))
        assert score["finished"] and score["turns"] == 4 and score["discoveries"] == 2
        assert score["checks"] == {"CONFIRM_READ_ON": 1}
        assert score["sources"] == ["libreoffice.org"]
        assert score["problems"] == []

    def test_missed_condition_missing_item_and_bad_citation(self):
        score = rg.score_run(TASK, journal("LibreOffice [1] and FreeOffice [3].", "LibreOffice ..."))
        assert score["forbidden"] == ["freeoffice"]
        assert score["missing"] == ["onlyoffice/openoffice"]
        assert score["bad_citations"] == [3]
        assert len(score["problems"]) == 3

    def test_unfinished_run_and_model_time(self):
        console = "   (model call 60.0s, prompt=1 tok, completion=1 tok)\n   (model call 30.0s, ...)\nTEST TIME LIMIT REACHED"
        score = rg.score_run(TASK, journal(None, "LibreOffice ..."), console)
        assert not score["finished"] and score["model_minutes"] == 1.5 and score["model_calls"] == 2
        assert score["problems"] == ["did not finish (time limit)"]

    def test_a_run_stopped_while_writing_the_final_result_is_not_a_finish(self):
        entries = journal("LibreOffice is best for documents [1].", "LibreOffice ...")
        entries[-1]["writing"] = True
        score = rg.score_run(TASK, entries, "TEST TIME LIMIT REACHED")
        assert not score["finished"]

    def test_named_source_and_missing_view(self):
        task = {"id": "docs", "kind": "k", "objective": "Using the official docs, recommend one.",
                "expect": [], "source": r"(^|\.)python\.org$"}
        score = rg.score_run(task, journal("Use venv [1].", "venv ...", source="https://realpython.com/venv"))
        assert score["off_source_discoveries"] == 1 and score["view_missing"]


def test_load_run_and_summary(tmp_path):
    entries = journal("LibreOffice [1]; OnlyOffice [1].", "LibreOffice and OnlyOffice ...")
    items = [{"text": "three suites", "discoveries": [1]}, {"text": "what each is best at", "discoveries": []}]
    (tmp_path / "research_log.txt").write_text(f"OBJECTIVE: obj\nCHECKLIST: {json.dumps(items)}\n" + json.dumps(entries),
                                               encoding="utf-8")
    objective, loaded, console, checklist = rg.load_run(str(tmp_path))
    assert objective == "obj" and loaded == entries and console == "" and checklist == items

    meta = {"name": "1.53_x", "version": "1.53", "commit": "abc", "started": "now"}
    now = rg.score_run(TASK, loaded, console, checklist)
    assert now["finish_attempts"] == 1 and now["checklist_items_with_discoveries"] == 1
    before = dict(now, finished=False, model_minutes=now["model_minutes"] + 2, problems=["did not finish"])
    text = rg.summary_markdown(meta, [now], {"meta": {"name": "1.52_x", "version": "1.52"}, "scores": [before]})
    assert "| list | yes | 4 | 0.0 (-2.0) | 1 | 1/2 items found, 1 finish attempt(s) |" in text
    assert "- list: finished now; gone: did not finish" in text


def test_repeated_failure_on_the_same_site_is_flagged():
    meta = {"name": "b", "version": "1", "commit": "c", "started": "now"}
    now = rg.score_run(TASK, journal(None, "LibreOffice ..."))
    before = dict(now)
    text = rg.summary_markdown(meta, [now], {"meta": {"name": "a", "version": "0"}, "scores": [before]})
    assert "- list: failed again on the same site as before (libreoffice.org)" in text


def test_task_ids_are_unique_and_every_kind_has_a_core_task():
    ids = [t["id"] for t in rg.TASKS]
    assert len(ids) == len(set(ids))
    assert {t["kind"] for t in rg.TASKS} == {t["kind"] for t in rg.TASKS if t["core"]}


def test_a_caveated_forbidden_item_is_not_a_missed_condition():
    result = "1. LibreOffice [1]\n2. OnlyOffice [1]\n3. **FreeOffice** (not shown in the pages read to be free and open-source)"
    assert rg.score_run(TASK, journal(result, "LibreOffice and OnlyOffice ..."))["forbidden"] == []
    assert rg.score_run(TASK, journal("3. FreeOffice [1]", "x"))["forbidden"] == ["freeoffice"]


def test_run_names_and_summaries_say_which_model_was_measured():
    import datetime
    started = datetime.datetime(2026, 10, 25, 7, 18)
    assert rg.run_name("1.62", "lmstudio-community/qwen2.5-3b", started) == "1.62_lmstudio-community-qwen2.5-3b_20261025-0718"
    meta = {"name": "n", "version": "1.62", "commit": "c", "started": "s", "model": "qwen2.5-3b-instruct",
            "quantization": "Q4_K_M", "context": 8192}
    earlier = {"meta": {"name": "old", "version": "1.61"}, "scores": []}  # recorded before the model switch
    text = rg.summary_markdown(meta, [], earlier)
    assert "model qwen2.5-3b-instruct (Q4_K_M, context 8192)" in text
    assert "Compared with old (version 1.61, model qwen2.5-7b-instruct):" in text


class TestPrepareModel:
    def serve(self, monkeypatch, info, ids=("qwen2.5-3b-instruct",)):
        def get_json(url, data=None, timeout=10):
            return {"data": [{"id": i} for i in ids]} if url.endswith("/models") else {}
        monkeypatch.setattr(rg, "get_json", get_json)
        monkeypatch.setattr(rg, "lmstudio_model", lambda model: info)

    def test_a_model_the_server_does_not_have_is_refused(self, monkeypatch):
        self.serve(monkeypatch, None)
        with pytest.raises(SystemExit, match="has no model 'qwen2.5-14b'. It has: qwen2.5-3b-instruct"):
            rg.prepare_model("qwen2.5-14b")

    def test_another_context_length_is_refused(self, monkeypatch):
        self.serve(monkeypatch, {"loaded_context_length": 4096, "quantization": "Q4_K_M"})
        with pytest.raises(SystemExit, match="context length of 4096, not 8192"):
            rg.prepare_model("qwen2.5-3b-instruct")

    def test_the_same_conditions_are_recorded(self, monkeypatch):
        self.serve(monkeypatch, {"loaded_context_length": 8192, "quantization": "Q4_K_M"})
        assert rg.prepare_model("qwen2.5-3b-instruct") == {"quantization": "Q4_K_M", "context": 8192}

    def test_the_model_list_gets_a_longer_wait_and_one_more_try(self, monkeypatch):
        calls = []

        def get_json(url, data=None, timeout=10):
            calls.append(timeout)
            if len(calls) == 1:
                raise TimeoutError("timed out")
            return {"data": [{"id": "qwen2.5-3b-instruct"}]}
        monkeypatch.setattr(rg, "get_json", get_json)
        monkeypatch.setattr(rg.time, "sleep", lambda s: None)
        assert rg.model_list() == ["qwen2.5-3b-instruct"]
        assert calls == [rg.MODEL_LIST_TIMEOUT, rg.MODEL_LIST_TIMEOUT]

    def test_a_model_list_that_fails_twice_ends_the_run(self, monkeypatch):
        def get_json(url, data=None, timeout=10):
            raise TimeoutError("timed out")
        monkeypatch.setattr(rg, "get_json", get_json)
        monkeypatch.setattr(rg.time, "sleep", lambda s: None)
        with pytest.raises(TimeoutError):
            rg.model_list()


class TestHonesty:
    RESULT = ("1. **LibreOffice** (not shown in the pages read to be free and open-source) [1]\n"
              "2. **OnlyOffice** [1]\n"
              "How long it keeps was not found in the pages read.\n"
              "The text does not specify the ratio, but a common recommendation is 1 part coffee to 4 parts water.\n"
              "WPS Office looks like Microsoft Office, but it is not open source [2].\n"
              "My view: it is generally recommended to start with LibreOffice.")

    def test_gaps_filled_from_the_model_are_found_and_honest_gaps_are_not(self):
        assert rg.outside_knowledge(self.RESULT) == [
            "The text does not specify the ratio, but a common recommendation is 1 part coffee to 4 parts water."]

    def test_the_honesty_table_counts_each_measure(self):
        score = rg.score_run(TASK, journal(self.RESULT, "LibreOffice ...", "WPS Office ..."))
        assert rg.honesty(score) == {"said not found": 1, "caveats": 1, "outside knowledge": 1,
                                     "numbers not in discoveries": 2, "commands not in discoveries": 0,
                                     "uncited lines": 1, "cites missing discoveries": 0}
        assert "1 gap(s) filled from the model's own knowledge" in score["problems"]
        meta = {"name": "n", "version": "1.62", "commit": "c", "started": "s"}
        text = rg.summary_markdown(meta, [score])
        assert "| Task | Said not found | Caveats | Outside knowledge |" in text
        assert "| list | 1 | 1 | 1 | 2 | 0 | 1 | 0 |" in text and "| **All** | **1** | **1** | **1** |" in text

    def test_runs_scored_before_the_measures_existed_have_none(self):
        assert rg.honesty({"gap_statements": []}) is None


def test_a_bold_label_is_not_an_uncited_paragraph():
    text = ("**1. Creating a virtual environment with venv from official Python documentation:**\n"
            "Virtual environments are created by executing the venv module [1].")
    assert rg.uncited_lines(text) == []


def test_a_left_out_note_is_neither_an_uncited_line_nor_an_unverified_command():
    # BB2's v11 docs result: the invented activate commands were cut, and the note naming them was then scored as an
    # uncited paragraph and as two commands not in discoveries
    text = ("To install packages, use pip [4].\n\nLeft out, because no discovery gives them (the pages read may still "
            "have them): `/path/to/new/virtual/environment/bin/activate`.")
    found = [{"extracted_data": "python -m pip install package_name"}]
    assert rg.uncited_lines(text) == []
    assert rg.ab.unverified_code(text, found) == []
    assert rg.ab.unverified_code(text + "\n\nRun `source venv/bin/activate` [4].", found) == ["source venv/bin/activate"]


class TestRunsFolder:
    """The installed test suite keeps its results in Documents (LAB_RUNS_DIR); a clone keeps regression_runs/."""

    def test_default_is_the_projects_regression_runs(self, monkeypatch):
        monkeypatch.delenv("LAB_RUNS_DIR", raising=False)
        assert rg.runs_folder() == os.path.join(rg.ROOT, "regression_runs")

    def test_lab_runs_dir_moves_it(self, monkeypatch, tmp_path):
        monkeypatch.setenv("LAB_RUNS_DIR", str(tmp_path))
        assert rg.runs_folder() == str(tmp_path)


class TestCheckedArithmetic:
    """Ages and lengths of time worked out by the code from the discoveries' dates (the three SimpleQA answers per
    run the number check used to cut), and nothing else."""

    @staticmethod
    def found(text):
        return [{"extracted_data": text, "source": "https://example.org"}]

    def test_an_age_from_two_full_dates_is_accepted_with_its_working(self):
        found = self.found("César Lattes was born on 11 July 1924 and died on 8 March 2005.")
        objective = "How old was the Brazilian physicist Cesar Lattes when he died?"
        assert ab.unverified_numbers("He was 80 years old when he died [1].", found, objective) == []
        assert "80 years and 240 days" in ab.span_working("He was 80 years old [1].", found, objective)

    def test_a_wrong_age_is_still_cut(self):
        found = self.found("César Lattes was born on 11 July 1924 and died on 8 March 2005.")
        assert ab.unverified_numbers("He was 79 years old [1].", found, "How old was Cesar Lattes when he died?") == ["79"]

    def test_years_and_days_are_counted_from_the_anniversary(self):
        # Phi-4's own working counted 30-day months and got 158 days; the right count is 161
        found = self.found("He served as Chief Justice of India from 12 July 1985 until his retirement on 20 December 1986.")
        objective = "What was the length of his tenure as the Chief Justice of India, in years and days?"
        assert ab.unverified_numbers("1 year and 161 days [1].", found, objective) == []
        assert ab.unverified_numbers("1 year and 158 days [1].", found, objective) == ["158"]

    def test_with_only_a_year_both_possible_ages_are_given(self):
        found = self.found("Josiah Willard Gibbs was born on February 11, 1839, and published his first work in 1873.")
        objective = "How old was Josiah Willard Gibbs when he published his first work?"
        assert "33 or 34 years" in ab.span_section(found, objective)
        assert ab.unverified_numbers("He was 34 [1].", found, objective) == []

    def test_other_questions_get_no_worked_out_numbers(self):
        found = self.found("Kilimanjaro was first climbed in 1889 and resurveyed in 1999.")
        objective = "What is the tallest mountain in Africa?"
        assert ab.span_section(found, objective) == ""
        assert ab.unverified_numbers("It was resurveyed 110 years later [1].", found, objective) == ["110"]


class TestConditionsAndSources:
    """The false "not shown to be free and open-source" caution, and comparisons or views resting on one website."""

    def test_open_source_meets_free(self):
        from localagentbrowser.coverage import states
        assert states("GIMP is open source software", "free")
        assert states("GIMP is open-source", "free, open-source") is True
        assert not states("Photopea is a free online editor", "open-source")

    def test_the_address_of_an_open_source_list_states_it_for_each_program(self):
        from localagentbrowser.coverage import page_states
        page = "https://itsfoss.com/open-source-photoshop-alternatives/"
        assert page_states(page, "open-source") and page_states(page, "free")
        assert not page_states("https://itsfoss.com/photoshop-alternatives/", "open-source")
        assert not page_states("https://opensource.example/", "open-source")  # a site's name is not a claim

    def test_a_comparison_from_one_website_is_named(self):
        from localagentbrowser.coverage import one_site
        one = [{"action": "DISCOVER", "source": "https://www.example.org/a"},
               {"action": "DISCOVER", "source": "https://example.org/b"}]
        two = one + [{"action": "DISCOVER", "source": "https://other.example/c"}]
        assert one_site("Compare SQLite and PostgreSQL.", one) == "example.org"
        assert one_site("Compare SQLite and PostgreSQL.", two) is None
        assert one_site("What is the tallest mountain in Africa?", one) is None
        assert one_site("Find out how owls are viewed, then give your own opinion.", one) == "example.org"

    def test_a_licence_filter_in_the_address_states_it(self):
        from localagentbrowser.coverage import page_states
        assert page_states("https://alternativeto.net/software/adobe-photoshop/?license=opensource", "free, open-source")
        assert not page_states("https://alternativeto.net/software/adobe-photoshop/?license=free", "open-source")


class TestFalseNotFound:
    """Items reported "not found" over discoveries that answer them, and a send-back with nowhere to look."""

    TENURE = [{"text": t} for t in ["Determine the start date of his tenure as Chief Justice of India.",
                                    "Determine the end date of his tenure as Chief Justice of India.",
                                    "Calculate the length of the tenure in years and days."]]

    def test_one_sentence_with_both_dates_answers_start_and_end(self):
        from localagentbrowser.coverage import shared_evidence_items
        both = "Chief Justice of India from 12 July 1985 until his retirement on 20 December 1986"
        assert shared_evidence_items({1: both, 2: both}, self.TENURE) == []

    def test_one_date_for_two_date_items_still_answers_neither(self):
        from localagentbrowser.coverage import shared_evidence_items
        assert shared_evidence_items({1: "Chief Justice from 1985", 2: "Chief Justice from 1985"}, self.TENURE) == [1, 2]

    def test_an_item_about_each_thing_found_shares_their_discoveries(self):
        from localagentbrowser.coverage import shared_evidence_items
        items = [{"text": "Find three free, open-source alternatives to Adobe Photoshop."},
                 {"text": "Summarize the main strengths of each one."}]
        features = "GIMP: The true Photoshop alternative. Key Features: Customizable Interface, Digital Retouching"
        assert shared_evidence_items({1: features, 2: features}, items) == []

    def test_an_unanswered_item_gets_a_query_to_start_from(self):
        from localagentbrowser.coverage import search_suggestions
        items = [{"text": "Using the official Python documentation, find out how to create a virtual environment"},
                 {"text": "Using the official Python documentation, find out how to install packages into a virtual environment"}]
        text = search_suggestions(items, [2])
        assert "Item 2" in text and "install packages virtual environment" in text and "Item 1" not in text
        assert search_suggestions(items, []) == ""

    def test_the_length_item_sharing_the_dates_keeps_them_too(self):
        # q33 live: all three items, the length among them, were given the same sentence
        from localagentbrowser.coverage import shared_evidence_items
        both = "Chief Justice of India from 12 July 1985 until his retirement on 20 December 1986"
        assert shared_evidence_items({1: both, 2: both, 3: both}, self.TENURE) == []
        assert shared_evidence_items({1: "from 1985", 2: "from 1985", 3: "from 1985"}, self.TENURE) == [1, 2, 3]


class TestCodeBlocks:
    def test_adjacent_code_blocks_are_not_merged(self):
        # Phi-4's docs result: a csh block followed by a "```powershell" block became one block with "powershell" in it
        text = "```csh\nsource <venv>/bin/activate.csh\n```\n```powershell\n<venv>/bin/Activate.ps1\n```"
        assert ab.without_empty_code_blocks(text) == text

    def test_an_emptied_block_is_removed(self):
        assert ab.without_empty_code_blocks("Run:\n```bash\n\n```\nDone.") == "Run:\nDone."

    def test_a_lead_in_whose_command_was_cut_says_so(self):
        text = "To activate it, use the following command:\n\n[2]\n\n#### 3. On macOS\nRun this command:\n```\nx\n```"
        out = ab.with_lead_ins_closed(text)
        assert "use the following command: not given in the pages read" in out
        assert "Run this command:\n```" in out


class TestInstallCommand:
    ITEM = "Using the official Python documentation, find out how to install packages into a virtual environment"

    def test_an_install_item_needs_an_install_command(self):
        from localagentbrowser.coverage import INSTALL_ASKED, INSTALL_COMMAND
        assert INSTALL_ASKED.search(self.ITEM)
        assert not INSTALL_COMMAND.search("The ExtendedEnvBuilder class installs setuptools and pip into the environment")
        for page in ("you can invoke pip install -r requirements.txt", "$ python -m pip install novas",
                     "python3 -m pip install requests", "npm install left-pad"):
            assert INSTALL_COMMAND.search(page), page

    def test_other_items_are_not_held_to_it(self):
        from localagentbrowser.coverage import INSTALL_ASKED
        for text in ("How do I make cold brew coffee at home?", "Install a ceiling fan safely",
                     "Find out how to create a virtual environment with venv"):
            assert not INSTALL_ASKED.search(text), text

    def test_the_check_sends_an_install_item_back_without_a_command(self):
        import asyncio
        import json
        from fakes import fake_client
        from localagentbrowser.coverage import coverage_gaps
        verdict = json.dumps({"evidence": "The ExtendedEnvBuilder class installs setuptools and pip", "missing": "",
                              "conditions": [], "things": []})
        journal = [{"action": "DISCOVER", "source": "https://docs.python.org/3/library/venv.html",
                    "extracted_data": "The ExtendedEnvBuilder class installs setuptools and pip into the environment."}]
        client, _ = fake_client([verdict])
        gaps, unanswered, _ = asyncio.run(coverage_gaps(client, "objective", [{"text": self.ITEM, "discoveries": [1]}], journal))
        assert unanswered == [1]


class TestAddresses:
    def test_a_wikipedia_title_keeps_its_closing_bracket(self):
        assert ab.determine_url("NAVIGATE", "https://en.wikipedia.org/wiki/Trevor_Evans_(journalist)") == \
            "https://en.wikipedia.org/wiki/Trevor_Evans_(journalist)"
        assert ab.determine_url("NAVIGATE", "Open https://en.wikipedia.org/wiki/Dark_Souls_(video_game).") == \
            "https://en.wikipedia.org/wiki/Dark_Souls_(video_game)"

    def test_punctuation_after_an_address_is_still_dropped(self):
        assert ab.determine_url("NAVIGATE", "(see https://example.org/page).") == "https://example.org/page"
        assert ab.determine_url("NAVIGATE", "https://example.org/a,") == "https://example.org/a"


class TestLookedNowhereElse:
    CHECKLIST = [{"text": "Create a virtual environment", "discoveries": [1]},
                 {"text": "Install packages into a virtual environment", "discoveries": []}]

    def tried(self):
        return {"checklist": [dict(i) for i in self.CHECKLIST], "coverage_checked": True, "coverage_at": 1,
                "unanswered": [2]}

    def test_a_finish_without_looking_elsewhere_is_sent_back_once(self):
        from localagentbrowser.coverage import looked_nowhere_else
        tried = self.tried()
        journal = [{"action": "DISCOVER"}, {"action": "READ_MORE"}, {"action": "DISCOVER"}]
        refusal = looked_nowhere_else(tried, journal)
        assert refusal and "SEARCH" in refusal[1] and "Item 2" in refusal[1]
        assert looked_nowhere_else(tried, journal) is None  # once only

    def test_a_search_since_the_send_back_is_enough(self):
        from localagentbrowser.coverage import looked_nowhere_else
        assert looked_nowhere_else(self.tried(), [{"action": "DISCOVER"}, {"action": "SEARCH"}]) is None

    def test_an_install_command_recorded_since_is_enough(self):
        from localagentbrowser.coverage import looked_nowhere_else
        journal = [{"action": "DISCOVER"}, {"action": "DISCOVER", "extracted_data": "python -m pip install novas"}]
        assert looked_nowhere_else(self.tried(), journal) is None

    def test_other_unanswered_items_are_not_pushed_twice(self):
        from localagentbrowser.coverage import looked_nowhere_else
        tried = self.tried()
        tried["checklist"][1]["text"] = "Explain what happens next"
        assert looked_nowhere_else(tried, [{"action": "DISCOVER"}, {"action": "DISCOVER"}]) is None


def test_an_item_the_check_found_unanswered_stays_open_until_something_new_is_linked():
    from localagentbrowser.checklist import every_item_has_discoveries, link_discovery, open_items
    checklist = [{"text": "Create a virtual environment", "discoveries": [1]},
                 {"text": "Install packages into it", "discoveries": [2], "unanswered": True}]
    assert not every_item_has_discoveries(checklist) and "2. Install packages" in open_items(checklist)
    link_discovery(checklist, [2], 3)
    assert every_item_has_discoveries(checklist) and open_items(checklist) == ""


class TestPointers:
    def test_the_code_records_the_install_command_a_page_gave(self):
        from localagentbrowser.coverage import record_install_line, remember_install_lines
        url = "https://docs.python.org/3/library/venv.html"
        checklist = [{"text": "Create a virtual environment", "discoveries": [1]},
                     {"text": "Install packages into a virtual environment", "discoveries": [2], "unanswered": True}]
        tried = {"checklist": checklist}
        page = {"url": ab.normalize_url(url), "index": 0, "parts": [
            "Creating virtual environments.", "How venvs work. If you have a requirements file requirements.txt, you can "
            "invoke pip install -r requirements.txt to install all the packages needed by the environment."]}
        remember_install_lines(tried, page)
        journal = [{"action": "DISCOVER", "extracted_data": "python -m venv /path"},
                   {"action": "DISCOVER", "extracted_data": "ExtendedEnvBuilder installs setuptools and pip"}]
        entry = record_install_line(tried, journal, 9)
        assert entry["by_code"] and "pip install -r requirements.txt" in entry["extracted_data"]
        assert journal[-1] is entry and checklist[1]["discoveries"] == [2, 3] and "unanswered" not in checklist[1]
        assert record_install_line(tried, journal, 10) is None, "once one is recorded, nothing more"

    def test_nothing_is_recorded_when_no_page_gave_one(self):
        from localagentbrowser.coverage import record_install_line
        tried = {"checklist": [{"text": "Install packages into a virtual environment", "discoveries": []}],
                 "install_lines": {"docs.python.org/3/library/venv.html": None}}
        assert record_install_line(tried, [], 3) is None

    def test_a_same_name_page_is_refused_until_the_named_one_is_opened(self):
        from localagentbrowser.checks import same_name_check
        results = [("Battle of the Nations (modern tournament) - Wikipedia",
                    "https://en.wikipedia.org/wiki/Battle_of_the_Nations_(modern_tournament)"),
                   ("Battle of Leipzig - Wikipedia", "https://en.wikipedia.org/wiki/Battle_of_Leipzig")]
        tried = {"objective": "Where was the first Battle of the Nations tournament held?"}
        target = ab.normalize_url("https://en.wikipedia.org/wiki/Battle_of_Leipzig")
        refusal = same_name_check(tried, results, target)
        assert refusal[0] == "BLOCKED_SAME_NAME" and "result [1]" in refusal[1] and "tournament" in refusal[1]
        assert same_name_check(tried, results, target), "still refused: asking was not enough"
        tried["urls"] = [results[0][1]]
        assert same_name_check(tried, results, target) is None, "allowed once the named page was opened"
        right = ab.normalize_url(results[0][1])
        assert same_name_check({"objective": tried["objective"]}, results, right) is None

    def test_a_qualifier_the_question_does_not_use_is_ignored(self):
        from localagentbrowser.checks import same_name_check
        results = [("Mercury (planet)", "https://en.wikipedia.org/wiki/Mercury_(planet)"),
                   ("Mercury (element)", "https://en.wikipedia.org/wiki/Mercury_(element)")]
        tried = {"objective": "What is the boiling point of the element mercury?"}
        assert same_name_check(tried, results, ab.normalize_url(results[1][1])) is None


def test_an_install_item_with_a_recorded_command_is_answered_whatever_the_verdict():
    import asyncio
    import json
    from fakes import fake_client
    from localagentbrowser.coverage import coverage_gaps
    item = "Using the official Python documentation, find out how to install packages into a virtual environment"
    journal = [{"action": "DISCOVER", "source": "https://docs.python.org/3/library/venv.html",
                "extracted_data": "you can invoke pip install -r requirements.txt using the environment's pip"}]
    client, _ = fake_client([json.dumps({"evidence": "", "missing": "how to install packages", "conditions": [], "things": []})])
    evidence = {}
    gaps, unanswered, _ = asyncio.run(coverage_gaps(client, "objective", [{"text": item, "discoveries": [1]}], journal,
                                                    evidence=evidence))
    assert unanswered == [] and "pip install -r requirements.txt" in evidence[1]


def test_a_result_without_the_recorded_install_command_gets_it_from_the_code():
    checklist = [{"text": "Find out how to install packages into a virtual environment", "discoveries": [1]}]
    found = [{"extracted_data": "you can invoke pip install -r requirements.txt using the environment's pip"}]
    text = "Installing packages was not described.\n\nLeft out, because no discovery gives them: `pip install <package>`."
    out = ab.with_install_command(text, checklist, found)
    assert out.endswith("\"you can invoke pip install -r requirements.txt using the environment's pip\" [1]")
    kept = "Run `pip install -r requirements.txt` [1]."
    assert ab.with_install_command(kept, checklist, found) == kept
    assert ab.with_install_command(text, [{"text": "Explain photosynthesis", "discoveries": []}], found) == text


class TestRefusingSites:
    """A site that turns the browser away is not visited again in the run, and leaves the search results."""

    @pytest.fixture(autouse=True)
    def clean(self):
        from localagentbrowser import pages
        pages.REFUSING_SITES.clear()
        yield
        pages.REFUSING_SITES.clear()

    def test_a_refused_site_is_blocked_for_every_address_on_it(self):
        from localagentbrowser.pages import mark_refusing
        mark_refusing("https://alternativeto.net/software/adobe-photoshop/?tag=paint-application", "HTTP 403")
        tried = {"urls": [], "queries": [], "objective": "x"}
        state = {"url": None, "parts": [], "index": 0}
        for address in ("https://alternativeto.net/software/adobe-photoshop/?license=opensource",
                        "https://www.alternativeto.net/browse/"):
            refusal = ab.check_decision("NAVIGATE", address, [], tried, state, None)
            assert refusal[0] == "BLOCKED_NAVIGATE" and "refused this browser" in refusal[1], address
        assert ab.check_decision("NAVIGATE", "https://itsfoss.com/open-source-photoshop-alternatives/", [], tried,
                                 state, None) is None

    def test_statuses_and_bot_check_pages_count_as_refusals(self):
        import asyncio
        from localagentbrowser.pages import site_refusal

        class Page:
            def __init__(self, text):
                self.text = text

            async def evaluate(self, script):
                return self.text

        assert asyncio.run(site_refusal(Page("anything"), 403)) == "HTTP 403"
        assert asyncio.run(site_refusal(Page("Just a moment...\nChecking if the site connection is secure"), 200))
        assert asyncio.run(site_refusal(Page("Please verify you are human"), 200))
        article = "A captcha is a test. " * 200  # a long article about captchas is content
        assert asyncio.run(site_refusal(Page(article), 200)) is None
        assert asyncio.run(site_refusal(Page("Not here"), 404)) is None


def test_one_full_date_answers_the_day_month_and_year_items():
    from localagentbrowser.coverage import shared_evidence_items
    items = [{"text": f"What {p} was Rosa Whitaker Day proclaimed by Rep. Charles Rangel?"} for p in ("day", "month", "year")]
    date = "'Rosa Whitaker Day' proclaimed by Rep. Charles Rangel, July 9, 2016"
    assert shared_evidence_items({1: date, 2: date, 3: date}, items) == []
    year_only = "Rosa Whitaker Day was proclaimed by Rep. Charles Rangel in 2016"
    assert shared_evidence_items({1: year_only, 2: year_only}, items[:2]) == [1, 2]


def test_lines_are_cited_to_the_discovery_their_section_cites_when_its_words_are_there():
    found = [{"extracted_data": "Krita: Layer management support. Transformation tools help with basic editing tasks."},
             {"extracted_data": "GIMP: Customizable interface and digital retouching."}]
    text = ("2. **Krita**\n   - **Transformation Tools**: Useful for basic editing tasks.\n"
            "   - **Speed**: Renders large canvases faster than any other painting program.\n"
            "   - **Supporting Discoveries:** [1]")
    out = ab.with_section_citations(text, found)
    assert "Useful for basic editing tasks. [1]" in out
    assert "faster than any other painting program.\n" in out, "words not in the discovery stay uncited"


class TestSiteSearchPages:
    """Other sites' own search pages are lists, not sources: left out of the results and refused as a NAVIGATE."""

    def test_site_search_addresses(self):
        from localagentbrowser.pages import site_search_page
        for url in ("https://blog.wvlegislature.gov/search/Rep.%20Charles%20Rangel%20Rosa%20Whitaker%20Day",
                    "https://kingsridgecofc.org/?s=Rosa+Whitaker+Day+proclamation",
                    "https://www.arkansasonline.com/search/vertical/news.story/?q=%22Rosa%20Whitaker%20Day%22",
                    "https://en.wikipedia.org/w/index.php?search=Vaughan+Jones&title=Special%3ASearch&fulltext=1",
                    "https://docs.python.org/3/search.html?q=venv"):
            assert site_search_page(url), url
        for url in ("https://en.wikipedia.org/wiki/Rosa_Whitaker", "https://www.rugbyeurope.eu/competitions/results",
                    "https://www.youtube.com/watch?v=abc", "https://html.duckduckgo.com/html/?q=rosa+whitaker",
                    "https://docs.python.org/3/library/venv.html"):
            assert not site_search_page(url), url

    def test_a_navigate_to_a_site_search_page_is_refused(self):
        tried = {"urls": [], "queries": [], "objective": "x"}
        state = {"url": None, "parts": [], "index": 0}
        refusal = ab.check_decision("NAVIGATE", "https://kingsridgecofc.org/?s=Rosa+Whitaker+Day", [], tried, state, None)
        assert refusal[0] == "BLOCKED_NAVIGATE" and "own search page" in refusal[1]


def test_a_number_in_a_name_is_not_a_count_of_things_asked_for():
    assert ab.asked_count("How many operators does the oscillator of the Yamaha SHS-10 (1987) have?") is None
    assert ab.asked_count("Find three free, open-source alternatives to Adobe Photoshop") == 3
    assert ab.asked_count("Name 5 tools for image editing") == 5
    assert ab.asked_count("Care during the first two weeks") is None


class TestMatchingPart:
    """Leaving a page is refused while an unread part of it gives a name the objective asks about."""
    OBJECTIVE = "What day, month, and year was Rosa Whitaker Day proclaimed by Rep. Charles Rangel?"

    def page(self, seen=None):
        return {"url": ab.normalize_url("https://en.wikipedia.org/wiki/Rosa_Whitaker"), "index": 0,
                "seen_parts": seen or {0}, "parts": [
                    "Rosa Whitaker is an American lobbyist and former trade negotiator.",
                    "Career. She served as Assistant U.S. Trade Representative for Africa.",
                    "Awards and recognition. 'Rosa Whitaker Day' proclaimed by Rep. Charles Rangel, July 9, 2016"]}

    def test_the_part_naming_the_objectives_subject_is_found(self):
        from localagentbrowser.checks import matching_part
        assert matching_part(self.OBJECTIVE, self.page()) == (2, "Rosa Whitaker Day proclaimed by Rep. Charles Rangel") \
            or matching_part(self.OBJECTIVE, self.page())[0] == 2
        assert matching_part(self.OBJECTIVE, self.page(seen={0, 2})) is None

    def test_leaving_is_refused_twice_then_allowed(self):
        from localagentbrowser.checks import matching_part_check
        state, url = self.page(), "https://en.wikipedia.org/wiki/Rosa_Whitaker"
        first = matching_part_check("NAVIGATE", self.OBJECTIVE, state, url)
        assert first[0] == "BLOCKED_LEAVE" and "READ_MORE 3" in first[1]
        assert matching_part_check("NAVIGATE", self.OBJECTIVE, state, url)
        assert matching_part_check("NAVIGATE", self.OBJECTIVE, state, url) is None, "never traps a run"

    def test_a_name_already_shown_does_not_hold_the_page(self):
        from localagentbrowser.checks import matching_part
        state = {"parts": ["Adobe Photoshop alternatives: GIMP, Krita.", "More about Adobe Photoshop pricing."],
                 "seen_parts": {0}}
        assert matching_part("Find three free alternatives to Adobe Photoshop", state) is None


def test_test_runs_never_move_windows(monkeypatch):
    # Side-by-side windows are for a person watching in a console; piped test runs keep the 1280 by 720 page
    from localagentbrowser import windows
    assert not windows.enabled() and windows.browser_args() == []
    monkeypatch.setenv("LAB_SIDE_BY_SIDE", "0")
    assert not windows.enabled()


class TestCoverageNotChecked:
    def test_items_whose_coverage_check_failed_are_counted_and_listed_as_a_problem(self):
        console = ("Warning: Coverage check of item 1 failed (Unterminated string); not checked (not counted as covered).\n"
                   f"{ab.COVERAGE_RETRY_LINE}\n"
                   "Warning: Coverage check of item 3 failed (Unterminated string); not checked (not counted as covered).\n"
                   "Warning: Coverage check of item 3 failed (Unterminated string); not checked (not counted as covered).\n")
        score = rg.score_run(TASK, journal("LibreOffice [1].", "LibreOffice ..."), console)
        assert score["coverage_unchecked"] == 2 and score["coverage_retries"] == 1
        assert "2 checklist item(s) not checked (coverage reply failed)" in score["problems"]


class TestTimeLimit:
    """Every task gets 90 minutes unless --minutes says otherwise, and each run records its limit (a 2026-11-17 run
    started without --minutes 90 was cut short by the tasks' old limits of 25 to 60 minutes)."""

    def test_every_task_gets_the_default(self):
        assert rg.DEFAULT_MINUTES == 90
        assert not any("minutes" in t for t in rg.TASKS)

    def test_the_summary_says_the_limit(self):
        meta = {"name": "n", "version": "1.67.2", "commit": "c", "started": "s", "minutes_per_task": 90}
        assert "started s, up to 90 minutes per task." in rg.summary_markdown(meta, [])

    def test_older_runs_without_a_recorded_limit_still_summarise(self):
        meta = {"name": "n", "version": "1.67", "commit": "c", "started": "s"}
        assert "started s.\n" in rg.summary_markdown(meta, [])
