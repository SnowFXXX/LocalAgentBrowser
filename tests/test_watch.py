"""The watch window of test runs (watch.py, winui.py --watch): each task's events go to a file of its own in a folder
apart from the results, the window follows the current one, and nothing opens unless a person started the run."""
import asyncio
import json
import os
import types

import pytest

from localagentbrowser import events, watch, winui


@pytest.fixture
def watched(tmp_path, monkeypatch):
    """A watch folder, as watch.start leaves it, without opening the window."""
    folder = tmp_path / "watch"
    folder.mkdir()
    monkeypatch.setenv("LAB_WATCH_DIR", str(folder))
    for name in ("LAB_WATCH_N", "LAB_WATCH_OF", "LAB_EVENTS", "LAB_UI"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setitem(watch.COUNT, "n", 0)
    watch.write_state(str(folder), {"title": "Test suite · gemma", "of": 7})
    return folder


class TestStarting:
    def test_nothing_opens_unless_a_person_started_the_run(self, monkeypatch):
        monkeypatch.delenv("LAB_WATCH_DIR", raising=False)
        assert not watch.wanted()  # Under pytest, stdin is not a console: test runs of the tests open nothing
        assert watch.start("Test suite") is None and "LAB_WATCH_DIR" not in os.environ

    def test_the_window_opens_once_for_the_run_and_lab_watch_0_turns_it_off(self, monkeypatch):
        opened = []
        monkeypatch.delenv("LAB_WATCH_DIR", raising=False)
        monkeypatch.setattr(watch, "wanted", lambda: os.environ.get("LAB_WATCH", "1") != "0")
        monkeypatch.setattr(watch.subprocess, "Popen", lambda command, **kwargs: opened.append(command))
        folder = watch.start("Test suite · gemma", total=7)
        try:
            assert opened and opened[0][-2:] == ["--watch", folder] and "localagentbrowser.winui" in opened[0]
            assert os.environ["LAB_WATCH_DIR"] == folder  # The processes running the tasks inherit it
            assert watch.read_state(folder) == {"title": "Test suite · gemma", "of": 7}
            assert watch.start("Again") is None and len(opened) == 1  # A child of a watched run opens no second one
        finally:
            monkeypatch.delenv("LAB_WATCH_DIR")
        monkeypatch.setenv("LAB_WATCH", "0")
        assert watch.start("Test suite") is None and len(opened) == 1


class TestTasks:
    def test_each_task_writes_its_events_to_a_new_file_the_window_follows(self, watched, tmp_path):
        env = watch.task_env({"PATH": "x"}, "factual", str(tmp_path / "run" / "factual"), "gemma-3-4b-it")
        state = watch.read_state(str(watched))
        assert env["LAB_EVENTS"] == state["file"] and os.path.dirname(env["LAB_EVENTS"]) == str(watched)
        assert env["LAB_UI"] == "windows" and env["PATH"] == "x"  # The browser on the right half
        assert state["label"] == "factual" and state["n"] == 1 and state["of"] == 7 and not state["done"]
        assert not (tmp_path / "run").exists()  # Nothing is added to the results
        second = watch.task_env({}, "comparison", "out", "gemma-3-4b-it")
        assert second["LAB_EVENTS"] != env["LAB_EVENTS"] and watch.read_state(str(watched))["n"] == 2

    def test_a_task_begun_already_keeps_its_file(self, watched, monkeypatch):
        # begin() sets both in this process's environment: put back after the test (they leaked into later tests'
        # child processes, which then placed windows as a UI run would)
        monkeypatch.setenv("LAB_EVENTS", "")
        monkeypatch.setenv("LAB_UI", "")
        events_file = watch.begin("qwen2.5-7b-instruct", "out", "qwen2.5-7b-instruct")
        assert os.environ["LAB_EVENTS"] == events_file
        assert watch.task_env(dict(os.environ), "factual", "out")["LAB_EVENTS"] == events_file

    def test_a_child_running_one_task_is_told_its_number(self, watched, monkeypatch):
        monkeypatch.setenv("LAB_WATCH_N", "12")
        monkeypatch.setenv("LAB_WATCH_OF", "18")
        watch.task_env({}, "phi-4", "out")
        state = watch.read_state(str(watched))
        assert state["n"] == 12 and state["of"] == 18

    def test_without_a_window_nothing_changes(self, monkeypatch):
        monkeypatch.delenv("LAB_WATCH_DIR", raising=False)
        assert watch.task_env({"A": "1"}, "factual", "out") == {"A": "1"}
        assert watch.begin("factual", "out") is None

    def test_the_run_says_when_it_is_over(self, watched):
        watch.finish()
        assert watch.read_state(str(watched))["done"] is True


def test_a_run_sends_its_events_to_the_file_and_its_output_is_unchanged(tmp_path, monkeypatch):
    import test_main_loop
    target = str(tmp_path / "events.jsonl")
    monkeypatch.setenv("LAB_EVENTS", target)
    try:
        calls, journal = test_main_loop.run_agent(monkeypatch, tmp_path)
    finally:
        events.detach(target)
    kinds = [json.loads(line)["kind"] for line in open(target, encoding="utf-8")]
    assert kinds[0] == "objective" and kinds[-1] == "stopped" and "discovery" in kinds
    monkeypatch.delenv("LAB_EVENTS")
    assert test_main_loop.run_agent(monkeypatch, tmp_path)[0] == calls  # The same run without it


def test_the_suite_gives_each_task_its_events_file(watched, tmp_path, monkeypatch):
    import importlib.util
    from conftest import TESTS_DIR
    spec = importlib.util.spec_from_file_location("regression", os.path.join(os.path.dirname(TESTS_DIR), "tools",
                                                                             "regression.py"))
    rg = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(rg)
    seen = []

    def popen(command, **kwargs):
        seen.append(kwargs["env"])
        return types.SimpleNamespace(stdout=[], wait=lambda: 0)
    monkeypatch.setattr(rg.subprocess, "Popen", popen)
    rg.run_task({"id": "factual", "objective": "x"}, str(tmp_path / "factual"), 1, "gemma-3-4b-it")
    assert seen[0]["LAB_EVENTS"] == watch.read_state(str(watched))["file"] and seen[0]["LAB_UI"] == "windows"
    monkeypatch.delenv("LAB_WATCH_DIR")
    rg.run_task({"id": "factual", "objective": "x"}, str(tmp_path / "factual"), 1, "gemma-3-4b-it")
    assert "LAB_EVENTS" not in seen[1] and "LAB_UI" not in seen[1]  # LAB_WATCH=0: the test-run browser as before


def test_the_window_follows_the_current_task_and_shows_its_report(watched, tmp_path, monkeypatch):
    output = tmp_path / "factual"
    output.mkdir()
    env = watch.task_env({}, "factual", str(output), "gemma-3-4b-it")
    with open(env["LAB_EVENTS"], "w", encoding="utf-8") as f:
        for kind, data in (("objective", {"objective": "Tallest mountain?"}), ("turn", {"number": 1, "of": 100})):
            f.write(json.dumps({"kind": kind, **data}) + "\n")
        f.write('{"kind": "stopp')  # Half a line: kept for the next read
    sent = []

    class App:
        done = asyncio.Event()

        async def send(self, kind, **data):
            sent.append((kind, data))
    monkeypatch.setattr(winui, "WATCH_POLL_SECONDS", 0.01)

    async def follow():
        app = App()
        task = asyncio.ensure_future(winui.follow_watch(app, str(watched)))
        await asyncio.sleep(0.1)
        (output / "research_report.md").write_text("# Research report\n\n## Final result\n\nKilimanjaro [1].\n",
                                                   encoding="utf-8")
        with open(env["LAB_EVENTS"], "a", encoding="utf-8") as f:
            f.write('ed", "reason": null}\n')
        watch.finish()
        await asyncio.sleep(0.1)
        app.done.set()
        await task
    asyncio.run(follow())
    kinds = [kind for kind, _ in sent]
    assert kinds == ["started", "objective", "turn", "stopped", "report", "watch_done"]
    assert sent[0][1]["watch"]["label"] == "factual" and sent[0][1]["watch"]["n"] == 1
    assert "Kilimanjaro" in sent[4][1]["page"]


def test_a_run_of_quick_checks_alone_is_shown_over_when_it_ends(watched, monkeypatch):
    # Seen live (Nov 21): compat.py --no-live sends no "stopped", and the window stayed on "Thinking" after the run
    monkeypatch.setenv("LAB_EVENTS", "")  # begin() sets them in this process: put back after the test
    monkeypatch.setenv("LAB_UI", "")
    events = watch.begin("microsoft/phi-4", "out", "microsoft/phi-4")
    with open(events, "w", encoding="utf-8") as f:
        f.write(json.dumps({"kind": "waiting"}) + "\n")
    sent = []

    class App:
        done = asyncio.Event()

        async def send(self, kind, **data):
            sent.append(kind)
    monkeypatch.setattr(winui, "WATCH_POLL_SECONDS", 0.01)

    async def follow():
        app = App()
        task = asyncio.ensure_future(winui.follow_watch(app, str(watched)))
        await asyncio.sleep(0.1)
        watch.finish()
        await asyncio.sleep(0.1)
        app.done.set()
        await task
    asyncio.run(follow())
    assert sent == ["started", "waiting", "watch_done"]
