"""Opens a run's research_report.md as a web page in the default browser, in front, when the run ends.

Used by LocalAgentBrowser.cmd. Windows has no program for .md files by default (opening one asks "How do you want to
open this file?"), so the report is written next to it as research_report.html, which every browser opens. Standard
library only; no network. Usage: python show_report.py <run folder>
"""
import html
import os
import sys
import webbrowser

# The program folder, for the localagentbrowser package (the renderer and the window placing)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from localagentbrowser.reportpage import page, to_html  # noqa: E402


def main(folder):
    report = os.path.join(folder, "research_report.md")
    if not os.path.exists(report):
        print("No research_report.md in", folder)
        return 1
    with open(report, encoding="utf-8") as f:
        body = to_html(f.read())
    page_file = os.path.join(folder, "research_report.html")
    title = page_title(folder)
    with open(page_file, "w", encoding="utf-8") as f:
        f.write(page(html.escape(title), body, html.escape(report)))
    # Side by side (localagentbrowser/windows.py): the console, in front now, stays on the left half, and the browser
    # window showing the report, found by the page's title, goes to the right half
    side = side_by_side()
    if side:
        side.console_window()
    webbrowser.open("file:///" + page_file.replace("\\", "/"))
    if side:
        side.place_report_right(title, wait=REPORT_WINDOW_WAIT)
    return 0


REPORT_WINDOW_WAIT = 15  # Seconds to wait at most for the browser to show the report (a cold start over Remote Desktop)


def page_title(folder):
    """The report page's title, with the run folder's name, so its browser window is told apart from the run before's
    (whose report may still be open): "Research report (2026-11-17_172719)"."""
    return f"Research report ({os.path.basename(os.path.normpath(folder))})"


def side_by_side():
    """The window-placing module from the program folder, when side-by-side windows apply; None otherwise."""
    try:
        from localagentbrowser import windows
    except Exception:
        return None
    return windows if windows.enabled() else None


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else os.getcwd()))
