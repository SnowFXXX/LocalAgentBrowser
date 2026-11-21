import importlib.util
import os

from conftest import TESTS_DIR

spec = importlib.util.spec_from_file_location(
    "show_report", os.path.join(os.path.dirname(TESTS_DIR), "tools", "windows", "show_report.py"))
show_report = importlib.util.module_from_spec(spec)
spec.loader.exec_module(show_report)


def test_the_report_becomes_a_page_with_its_headings_lists_code_and_warnings():
    page = show_report.to_html("# Research report\n\n**Objective:** venv\n\n## Checklist\n\n1. Create it. (discoveries"
                               " for it: [1])\n2. Activate it.\n\nRun `python -m venv env` [1].\n\n> ⚠️ Check <this>."
                               "\n\n```\npip install x\n```")
    assert "<h1>Research report</h1>" in page and "<section>\n<h2>Checklist</h2>" in page
    assert ('<ol><li>Create it. (discoveries for it: <span class="cite">[1]</span>)</li><li>Activate it.</li></ol>'
            in page)
    assert "<code>python -m venv env</code>" in page and page.endswith("</section>")
    assert "<blockquote>⚠️ Check &lt;this&gt;.</blockquote>" in page  # Page text is escaped, never run as HTML
    assert "<pre>pip install x</pre>" in page


def test_a_numbered_list_broken_up_by_answers_goes_on_from_its_own_number():
    # As Phi-4 wrote its final result (Windows UI, Nov 21): every question was shown as "1."
    page = show_report.to_html("1. What is the tallest mountain?\n   - Kilimanjaro [1].\n\n2. How high is it?\n"
                               "   - 5,895 m [1].\n\n3. In feet?\n   - 19,341 ft [1].")
    assert [page.count(f'<ol start="{n}">') for n in (2, 3)] == [1, 1] and page.count("<ol>") == 1
    assert page.count("<ul><li>") == 3


def test_a_run_without_a_report_opens_nothing(tmp_path, monkeypatch):
    opened = []
    monkeypatch.setattr(show_report.webbrowser, "open", opened.append)
    assert show_report.main(str(tmp_path)) == 1 and opened == []
    (tmp_path / "research_report.md").write_text("# Research report\n", encoding="utf-8")
    assert show_report.main(str(tmp_path)) == 0
    assert (tmp_path / "research_report.html").exists() and opened[0].endswith("research_report.html")


def test_the_page_title_names_the_run(tmp_path, monkeypatch):
    monkeypatch.setattr(show_report.webbrowser, "open", lambda url: None)
    run = tmp_path / "2026-11-17_172719"
    run.mkdir()
    (run / "research_report.md").write_text("# Research report\n", encoding="utf-8")
    show_report.main(str(run))
    assert "<title>Research report (2026-11-17_172719)</title>" in (run / "research_report.html").read_text(encoding="utf-8")


class TestReportPlacement:
    """The report's window is found by its page title, never taken to be whatever window is in front: on BB3, over
    Remote Desktop, a minimized window of another program was in front and was moved to the right half instead."""

    def setup(self, monkeypatch, titled):
        from localagentbrowser import windows
        placed, fronted = [], []
        monkeypatch.setattr(windows, "enabled", lambda: True)
        monkeypatch.setattr(windows, "console_window", lambda: 1)
        monkeypatch.setattr(windows, "window_title_starting", lambda prefix: next(titled))
        monkeypatch.setattr(windows, "place", lambda hwnd, side: placed.append((hwnd, side)))
        monkeypatch.setattr(windows, "bring_to_front", fronted.append)
        monkeypatch.setattr(windows, "report_window_file", lambda: os.devnull)
        monkeypatch.setattr(windows.time, "sleep", lambda s: None)
        return windows, placed, fronted

    def test_the_window_showing_the_report_goes_right_once_it_appears(self, monkeypatch):
        windows, placed, fronted = self.setup(monkeypatch, iter([0, 0, 42]))
        windows.place_report_right("Research report (run)")
        assert placed == [(42, "right"), (1, "left")] and fronted == [42]

    def test_no_report_window_moves_nothing_but_the_console(self, monkeypatch):
        windows, placed, fronted = self.setup(monkeypatch, iter([0] * 100))
        windows.place_report_right("Research report (run)", wait=2, step=0.5)
        assert placed == [(1, "left")] and fronted == []
