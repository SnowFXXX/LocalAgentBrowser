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


@pytest.mark.skipif(sys.platform != "win32", reason="the tray is Windows only")
def test_the_tray_icon_opens_the_window_when_clicked():
    import ctypes
    from localagentbrowser import tray
    commands = []
    icon = tray.Tray("LocalAgentBrowser test", lambda command, argument=None: commands.append((command, argument)))
    if not icon.start():
        pytest.skip("no taskbar to put an icon in (a service session)")
    try:
        ctypes.windll.user32.SendMessageW(icon.hwnd, tray.CALLBACK, 1, tray.WM_LBUTTONUP)
    finally:
        icon.stop()
    assert commands == [("open", None)] and not icon.thread.is_alive()


class TestTrayMenu:
    RECENT = [("2026-11-20 090000 · Kilimanjaro", "C:\\Runs\\a"), ("2026-11-19 101500 · Owls", "C:\\Runs\\b")]

    def test_new_research_waits_for_the_run_and_stop_needs_one(self):
        from localagentbrowser import tray
        idle = {item[2]: item[3] for item in tray.menu_items(False, []) if item and not isinstance(item[1], list)}
        busy = {item[2]: item[3] for item in tray.menu_items(True, []) if item and not isinstance(item[1], list)}
        assert idle["New research"] and not idle["Stop the run"]
        assert not busy["New research"] and busy["Stop the run"] and busy["Open LocalAgentBrowser"]

    def test_recent_reports_are_a_submenu_greyed_when_there_are_none(self):
        from localagentbrowser import tray
        submenu = next(item for item in tray.menu_items(False, self.RECENT) if item and isinstance(item[1], list))
        assert submenu[0] == "Recent reports" and submenu[2]
        assert submenu[1][0] == ("recent", "C:\\Runs\\a", "2026-11-20 090000 · Kilimanjaro", True)
        empty = next(item for item in tray.menu_items(False, []) if item and isinstance(item[1], list))
        assert empty == ("Recent reports", [], False)
        assert tray.shorten_label("word " * 30, 20) == "word word word word…"

    @pytest.mark.skipif(sys.platform != "win32", reason="the tray is Windows only")
    def test_a_recent_report_chosen_from_the_real_menu_reaches_the_ui(self, monkeypatch):
        from localagentbrowser import tray
        commands = []
        icon = tray.Tray("LocalAgentBrowser test", lambda command, argument=None: commands.append((command, argument)),
                         recent=lambda: self.RECENT)
        if not icon.start():
            pytest.skip("no taskbar to put an icon in (a service session)")
        try:
            # The menu is built for real (a submenu's handle in a 64-bit id); only the person's click is played:
            # numbered in order, Open 1, New research 2, the first recent report 3
            monkeypatch.setattr(tray.user32, "TrackPopupMenu", lambda *args: 3)
            icon.menu()
        finally:
            icon.stop()
        assert commands == [("recent", "C:\\Runs\\a")]


class TestTrayCommands:
    def ui(self, monkeypatch, tmp_path, running):
        (tmp_path / "research_report.md").write_text("# Research report\n\n## Final result\n\nOwls [1].\n", encoding="utf-8")
        app = winui.App(page=None)
        app.run = object() if running else None
        sent, opened = [], []

        async def send(kind, **data):
            sent.append((kind, data))
        monkeypatch.setattr(app, "send", send)
        monkeypatch.setattr(winui, "open_outside", opened.append)
        monkeypatch.setattr(winui.windows, "show", lambda hwnd: None)
        return app, sent, opened

    def test_a_recent_report_shows_in_the_window_when_no_run_is_going(self, monkeypatch, tmp_path):
        app, sent, opened = self.ui(monkeypatch, tmp_path, running=False)
        asyncio.run(app.tray_command("recent", str(tmp_path)))
        assert sent[0][0] == "report" and sent[0][1]["past"] and not opened

    def test_during_a_run_it_opens_as_a_page_and_the_live_view_stays(self, monkeypatch, tmp_path):
        app, sent, opened = self.ui(monkeypatch, tmp_path, running=True)
        asyncio.run(app.tray_command("recent", str(tmp_path)))
        asyncio.run(app.tray_command("new"))  # Waits for the run to end
        assert sent == [] and opened == [os.path.join(str(tmp_path), "research_report.html")]

    def test_new_research_brings_back_the_start_screen(self, monkeypatch, tmp_path):
        app, sent, opened = self.ui(monkeypatch, tmp_path, running=False)
        asyncio.run(app.tray_command("new"))
        assert sent == [("new_research", {})]


class TestClassicColours:
    LINES = ("[TURN 3/100] Active reasoning cycle executing...\nChosen Action: [DISCOVER] -> Parameter: 5,895 m\n"
             "Warning: The model server failed the call.\nAction refused: Already on this page.\n")

    def test_known_line_starts_are_coloured_and_the_text_is_unchanged(self):
        import io
        import re
        from localagentbrowser import colors
        out = io.StringIO()
        colors.Colored(out).write(self.LINES)
        coloured = out.getvalue()
        assert colors.paint(colors.MINT, "[DISCOVER]", bold=True) in coloured
        assert colors.paint(colors.AMBER, "Warning:", bold=True) in coloured
        assert re.sub(r"\x1b\[[0-9;]*m", "", coloured) == self.LINES

    def test_piped_output_and_no_color_stay_plain(self, monkeypatch):
        import io
        from localagentbrowser import colors

        class Console(io.StringIO):
            def isatty(self):
                return True
        monkeypatch.setattr(colors, "terminal_takes_colours", lambda: True)
        assert not colors.wanted(io.StringIO())  # A file or a pipe (console.txt, the UI's runs)
        assert colors.wanted(Console())
        monkeypatch.setenv("NO_COLOR", "1")
        assert not colors.wanted(Console())
