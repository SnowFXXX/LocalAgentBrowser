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


def test_a_run_without_a_report_opens_nothing(tmp_path, monkeypatch):
    opened = []
    monkeypatch.setattr(show_report.webbrowser, "open", opened.append)
    assert show_report.main(str(tmp_path)) == 1 and opened == []
    (tmp_path / "research_report.md").write_text("# Research report\n", encoding="utf-8")
    assert show_report.main(str(tmp_path)) == 0
    assert (tmp_path / "research_report.html").exists() and opened[0].endswith("research_report.html")
