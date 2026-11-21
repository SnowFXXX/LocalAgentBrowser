"""The Windows UI's pieces that need no window: event lines, stopping a run, switching modes, the run folders and the
run's environment, and the saved settings. The page itself is tested in test_winui_page.py."""
import asyncio
import os
import sys

import pytest

from localagentbrowser import agent, events, settings, windows, winui


class TestEventLines:
    def test_an_event_is_printed_as_a_marked_line_and_read_back(self, capsys):
        events.print_line("discovery", {"number": 1, "text": "It is 5,895 m high.", "items": [2]})
        line = capsys.readouterr().out.rstrip("\n")
        assert line.startswith(events.MARK)
        assert events.read_line(line) == ("discovery", {"number": 1, "text": "It is 5,895 m high.", "items": [2]})

    def test_a_console_line_is_not_an_event(self):
        assert events.read_line("Chosen Action: [SEARCH] -> Parameter: kilimanjaro") is None
        assert events.read_line(events.MARK + "{not json") is None


class TestStopping:
    def test_a_stop_file_holds_the_reason(self, tmp_path, monkeypatch):
        stop = tmp_path / "stop"
        monkeypatch.setenv("LAB_STOP_FILE", str(stop))
        assert events.stop_requested() is None
        stop.write_text("the time limit (30 minutes)", encoding="utf-8")
        assert events.stop_requested() == "the time limit (30 minutes)"
        monkeypatch.delenv("LAB_STOP_FILE")
        assert events.stop_requested() is None

    def test_a_model_call_is_stopped_while_the_model_works(self, tmp_path, monkeypatch):
        # On a CPU one call can take minutes: Stop must not wait for it
        stop = tmp_path / "stop"
        stop.write_text("stopped by you", encoding="utf-8")
        monkeypatch.setenv("LAB_STOP_FILE", str(stop))
        monkeypatch.setattr(agent, "TIMER_TICK_SECONDS", 0.01)
        seen = []
        listener = events.listen(lambda kind, data: seen.append(kind))

        async def never():
            await asyncio.sleep(3600)
        try:
            with pytest.raises(events.Stopped, match="stopped by you"):
                asyncio.run(agent.ticking(never()))
        finally:
            events.stop_listening(listener)
        assert seen == ["waiting", "replied"]

    def test_a_run_asked_to_stop_writes_its_report_saying_why(self, tmp_path, monkeypatch):
        import test_main_loop
        stop = tmp_path / "stop"
        stop.write_text("stopped by you", encoding="utf-8")
        monkeypatch.setenv("LAB_STOP_FILE", str(stop))
        seen = []
        listener = events.listen(lambda kind, data: seen.append((kind, data)))
        try:
            calls, journal = test_main_loop.run_agent(monkeypatch, tmp_path)
        finally:
            events.stop_listening(listener)
        assert calls == [] and journal == []  # Stopped before the first turn
        report = (tmp_path / "research_report.md").read_text(encoding="utf-8")
        assert "stopped before the agent finished (after turn 0): stopped by you" in report
        assert seen[-1][0] == "stopped" and seen[-1][1]["reason"] == "stopped by you"


def test_typing_ui_in_classic_mode_switches_to_the_windows_ui(tmp_path, monkeypatch):
    import builtins
    from fakes import fake_client
    client, model = fake_client([])
    saved, opened = [], []
    monkeypatch.setattr(agent, "AsyncOpenAI", lambda **kwargs: client)
    monkeypatch.setattr(builtins, "input", lambda prompt="": "/UI")
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(settings, "save", lambda **changes: saved.append(changes))
    monkeypatch.setattr(settings, "open_windows_ui", lambda: opened.append(True))
    monkeypatch.chdir(tmp_path)
    asyncio.run(agent.main())
    assert saved == [{"ui": "windows"}] and opened == [True]
    assert model.calls == [] and not (tmp_path / "research_report.md").exists()  # No run: the UI opens instead


class TestRuns:
    def test_each_run_gets_a_folder_of_its_own(self, tmp_path):
        first, second = winui.new_run_folder(str(tmp_path)), winui.new_run_folder(str(tmp_path))
        assert first != second and os.path.isdir(first) and os.path.isdir(second)

    def test_a_run_is_told_it_runs_for_the_ui(self, tmp_path, monkeypatch):
        monkeypatch.setattr(settings, "APP", str(tmp_path))
        env = winui.run_environment({"model": "qwen2.5-7b-instruct"}, "C:/stop")
        assert env["LMSTUDIO_MODEL"] == "qwen2.5-7b-instruct" and env["LAB_UI"] == "windows"
        assert env["LAB_EVENTS"] == "stdout" and env["LAB_STOP_FILE"] == "C:/stop"
        assert env["PYTHONPATH"].split(os.pathsep)[0] == str(tmp_path)  # The package, from any run folder

    def test_setup_s_model_is_the_default(self, tmp_path, monkeypatch):
        (tmp_path / "settings").mkdir()
        (tmp_path / "settings" / "model.cmd").write_text('@echo off\nset "LMSTUDIO_MODEL=gemma-3-4b-it"\n', encoding="utf-8")
        monkeypatch.setattr(settings, "APP", str(tmp_path))
        assert settings.setup_choices("model.cmd") == {"LMSTUDIO_MODEL": "gemma-3-4b-it"}
        assert winui.run_environment({}, "stop")["LMSTUDIO_MODEL"] == "gemma-3-4b-it"
        assert settings.setup_choices("none.cmd") == {}

    def test_recent_reports_are_the_newest_run_folders_with_a_report(self, tmp_path):
        for name, objective in (("2026-11-19_101500", "Owls"), ("2026-11-20_090000", "Kilimanjaro")):
            (tmp_path / name).mkdir()
            (tmp_path / name / "research_report.md").write_text(f"# Research report\n\n**Objective:** {objective}\n",
                                                                 encoding="utf-8")
        (tmp_path / "2026-11-20_100000").mkdir()  # No report: left out
        recent = winui.recent_reports(str(tmp_path))
        assert [r["objective"] for r in recent] == ["Kilimanjaro", "Owls"]
        assert recent[0]["when"] == "2026-11-20 090000"

    def test_a_report_is_shown_and_saved_as_a_page(self, tmp_path):
        assert winui.report_page_for(str(tmp_path)) is None
        (tmp_path / "research_report.md").write_text("# Research report\n\n## Final result\n\nIt is 5,895 m [1].\n",
                                                     encoding="utf-8")
        whole = winui.report_page_for(str(tmp_path))
        assert '<span class="cite">[1]</span>' in whole and (tmp_path / "research_report.html").exists()


def test_settings_are_kept_between_runs(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    assert settings.load()["minutes"] == 0
    settings.save(ui="classic", minutes=30)
    assert settings.load()["ui"] == "classic" and settings.load()["minutes"] == 30


def test_a_ui_run_places_its_browser_without_a_console(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv("LAB_UI", "windows")
    monkeypatch.delenv("LAB_SIDE_BY_SIDE", raising=False)
    assert windows.enabled()  # No console (stdin is a pipe), but still placed beside the UI
    monkeypatch.setenv("LAB_SIDE_BY_SIDE", "0")
    assert not windows.enabled()
