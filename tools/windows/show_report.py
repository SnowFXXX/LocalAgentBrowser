"""Opens a run's research_report.md as a web page in the default browser, in front, when the run ends.

Used by LocalAgentBrowser.cmd. Windows has no program for .md files by default (opening one asks "How do you want to
open this file?"), so the report is written next to it as research_report.html, which every browser opens. Standard
library only; no network. Usage: python show_report.py <run folder>
"""
import html
import os
import re
import sys
import webbrowser

PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Research report</title>
<style>
/* Dark always (the user's choice, 2026-11-07), in the project website's colors */
:root {{ color-scheme: dark;
  --bg: #0b111b; --bg-2: #0e1624; --ink: #e8eef5; --sub: #b9c7d6; --muted: #8191a5;
  --mint: #5fd3a6; --mint-hi: #8ff0c8; --mint-bg: #12312b; --mint-line: #3fae86;
  --blue: #7fb3e6; --chip: #122438; --chip-line: #2f5577; --amber: #f0a860; --amber-bg: #2b2112;
  --mono: Consolas, "Cascadia Mono", "SF Mono", Menlo, monospace; }}
* {{ box-sizing: border-box; }}
body {{ background: var(--bg); color: var(--ink); font: 16px/1.65 "Segoe UI", system-ui, sans-serif; margin: 0;
       background-image: radial-gradient(900px 420px at 85% 0%, rgba(30,58,90,.75), transparent 70%); }}
main {{ max-width: 54rem; margin: 0 auto; padding: 2rem 16px 4rem; }}
h1 {{ font-size: clamp(28px, 6vw, 40px); font-weight: 800; letter-spacing: -.01em; margin: 0 0 1rem; }}
section {{ background: var(--bg-2); border: 1px solid var(--chip-line); border-left: 4px solid var(--blue);
          border-radius: 10px; padding: .4rem 1.2rem 1rem; margin: 1.4rem 0; }}
section.result {{ border-left-color: var(--mint); }}
section.discoveries {{ border-left-color: var(--amber); }}
h2 {{ font-size: 13px; letter-spacing: .2em; text-transform: uppercase; color: var(--blue); margin: .9rem 0 .6rem; }}
section.result h2 {{ color: var(--mint); }}
section.discoveries h2 {{ color: var(--amber); }}
h3 {{ font-size: 18px; color: var(--mint-hi); margin: 1.2rem 0 .3rem; }}
section.discoveries h3 {{ font-size: 14px; font-weight: 600; color: var(--sub); border-top: 1px solid var(--chip);
                        padding-top: .8rem; }}
h4 {{ color: var(--sub); }}
p, li {{ color: var(--ink); }}
strong {{ color: var(--sub); }}
code, pre {{ font-family: var(--mono); background: var(--chip); border: 1px solid var(--chip-line); border-radius: 5px; }}
code {{ font-size: .9em; padding: 1px 6px; }}
pre {{ font-size: 14px; padding: .8rem; overflow-x: auto; white-space: pre-wrap; }}
.cite {{ font-family: var(--mono); font-size: .8em; font-weight: 700; color: var(--mint-hi); background: var(--mint-bg);
        border: 1px solid var(--mint-line); border-radius: 5px; padding: 0 5px; white-space: nowrap; }}
blockquote {{ background: var(--amber-bg); border-left: 4px solid var(--amber); color: var(--ink); margin: 1rem 0;
             padding: .6rem 1rem; border-radius: 6px; }}
a {{ color: var(--blue); text-underline-offset: 3px; word-break: break-all; }}
a:hover {{ color: var(--mint-hi); }}
.source {{ color: var(--muted); font-size: 13px; word-break: break-all; }}
</style></head><body><main>
{body}
<p class="source">From {source}</p>
</main></body></html>
"""


def inline(text):
    text = html.escape(text, quote=False)
    text = re.sub(r"`([^`]+)`", r"<code>\1</code>", text)
    text = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text)
    text = re.sub(r"(?<![\w*])_(.+?)_(?![\w*])", r"<em>\1</em>", text)
    text = re.sub(r"(https?://[^\s<)]+)", r'<a href="\1">\1</a>', text)
    # Citations stand out, as on the website: "[2]", "[1, 3]"
    return re.sub(r"\[(\d+(?:\s*[-–,;]\s*\d+)*)\](?![^<]*</a>)", r'<span class="cite">[\1]</span>', text)


def in_sections(parts):
    """Each "## ..." heading and what follows it as a card, colored by kind: the final result in mint, the
    discoveries in amber, the rest in blue, so the report's parts are easy to tell apart."""
    out, open_section = [], False
    for part in parts:
        if part.startswith("<h2>"):
            if open_section:
                out.append("</section>")
            name = part.lower()
            kind = " class=\"result\"" if "final result" in name else " class=\"discoveries\"" if "discover" in name else ""
            out.append(f"<section{kind}>")
            open_section = True
        out.append(part)
    if open_section:
        out.append("</section>")
    return out


def to_html(markdown):
    """A small Markdown subset, enough for the report: headings, paragraphs, lists, quotes, code blocks."""
    out, para, items, fenced, code = [], [], None, False, []

    def flush():
        nonlocal para, items
        if para:
            out.append("<p>" + "<br>".join(inline(line) for line in para) + "</p>")
            para = []
        if items:
            tag = items[0]
            out.append(f"<{tag}>" + "".join(f"<li>{inline(i)}</li>" for i in items[1:]) + f"</{tag}>")
            items = None

    for line in markdown.splitlines():
        if line.strip().startswith("```"):
            if fenced:
                out.append("<pre>" + html.escape("\n".join(code)) + "</pre>")
                code = []
            else:
                flush()
            fenced = not fenced
            continue
        if fenced:
            code.append(line)
            continue
        heading = re.match(r"(#{1,6})\s+(.*)", line)
        bullet = re.match(r"\s*[-*]\s+(.*)", line)
        number = re.match(r"\s*\d+[.)]\s+(.*)", line)
        if heading:
            flush()
            level = len(heading.group(1))
            out.append(f"<h{level}>{inline(heading.group(2))}</h{level}>")
        elif bullet or number:
            tag = "ul" if bullet else "ol"
            if para or (items and items[0] != tag):
                flush()
            items = items or [tag]
            items.append((bullet or number).group(1))
        elif line.startswith(">"):
            flush()
            out.append(f"<blockquote>{inline(line.lstrip('> '))}</blockquote>")
        elif not line.strip():
            flush()
        else:
            if items:
                flush()
            para.append(line)
    if fenced:
        out.append("<pre>" + html.escape("\n".join(code)) + "</pre>")
    flush()
    return "\n".join(in_sections(out))


def main(folder):
    report = os.path.join(folder, "research_report.md")
    if not os.path.exists(report):
        print("No research_report.md in", folder)
        return 1
    with open(report, encoding="utf-8") as f:
        body = to_html(f.read())
    page = os.path.join(folder, "research_report.html")
    with open(page, "w", encoding="utf-8") as f:
        f.write(PAGE.format(body=body, source=html.escape(report)))
    webbrowser.open("file:///" + page.replace("\\", "/"))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else os.getcwd()))
