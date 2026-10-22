"""Scoring in tools/regression.py, which turns a run's journal into the measurements compared between versions."""
import importlib.util
import json
import os

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
        console = "   (model call 60.0s, prompt=1 tok, completion=1 tok)\n   (model call 30.0s, ...)\n⏹️ TEST TIME LIMIT REACHED"
        score = rg.score_run(TASK, journal(None, "LibreOffice ..."), console)
        assert not score["finished"] and score["model_minutes"] == 1.5 and score["model_calls"] == 2
        assert score["problems"] == ["did not finish (time limit)"]

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
