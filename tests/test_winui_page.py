"""The Windows UI's page (winui.html) in a real headless Chromium: the start screen, and a whole run's events drawn as
the live view and the report. Skipped where Playwright has no Chromium (the tests otherwise use a fake browser)."""
import asyncio

import pytest

from localagentbrowser import winui

A = "https://en.wikipedia.org/wiki/Mount_Kilimanjaro"
SETUP = {"edition": "LocalAgentBrowser", "ready": True, "models": ["qwen2.5-7b-instruct", "gemma-3-4b-it"],
         "note": "", "model": "gemma-3-4b-it", "minutes": 30, "folder": "C:\\Runs", "remote": None,
         "remote_chosen": False, "recent": [{"folder": "C:\\Runs\\2026-11-20_090000", "objective": "Owls",
                                             "when": "2026-11-20 090000"}]}
CHECKLIST = [{"text": "the tallest mountain", "discoveries": []}, {"text": "its height", "discoveries": []}]
RUN = [
    ("started", {"objective": "What is the tallest mountain in Africa?", "folder": "C:\\Runs\\x", "model": "gemma-3-4b-it"}),
    ("checklist", {"items": CHECKLIST}),
    ("turn", {"number": 1, "of": 100}), ("waiting", {}), ("replied", {"seconds": 2.0}),
    ("decision", {"action": "SEARCH", "parameter": "tallest mountain in africa", "thought": "Search first."}),
    ("page", {"url": "https://html.duckduckgo.com/html/?q=x", "search": True, "part": 1, "parts": 1, "outline": [],
              "results": [["Mount Kilimanjaro - Wikipedia", A]]}),
    ("decision", {"action": "NAVIGATE", "parameter": A, "thought": ""}),
    ("page", {"url": A, "search": False, "part": 1, "parts": 12, "outline": ["Intro"], "results": []}),
    ("decision", {"action": "READ_MORE", "parameter": "2", "thought": ""}),
    ("read_more", {"part": 2, "parts": 12}),
    ("retried", {"action": "DISCOVER", "parameter": "x", "reason": "Not on the page."}),  # Before the turn's decision
    ("decision", {"action": "DISCOVER", "parameter": "Kilimanjaro is 5,895 m.", "thought": ""}),
    ("discovery", {"number": 1, "text": '(Under the heading "Geology") Kilimanjaro is 5,895 m.', "source": A, "items": [1],
                   "checklist": [{"text": "the tallest mountain", "discoveries": [1]}, {"text": "its height", "discoveries": []}]}),
    ("decision", {"action": "NAVIGATE", "parameter": A, "thought": ""}),
    ("refused", {"action": "NAVIGATE", "parameter": A, "reason": "Already on this page.", "check": "BLOCKED_REVISIT"}),
    ("decision", {"action": "FINISH", "parameter": "done", "thought": ""}),
    ("checking", {}), ("not_found", {"items": [2]}), ("writing", {}),
    ("result", {"text": "Kilimanjaro [1].", "html": "<p>Kilimanjaro <span class=\"cite\">[1]</span>.</p>"}),
    ("stopped", {"reason": None, "report": "C:\\Runs\\x\\research_report.md"}),
    ("report", {"page": f'<html><body><h1>Research report</h1><a href="{A}">source</a></body></html>',
                "folder": "C:\\Runs\\x", "past": False}),
    ("exited", {"code": 0, "folder": "C:\\Runs\\x", "report": True}),
]


async def drive():
    from playwright.async_api import async_playwright
    async with async_playwright() as p:
        try:
            browser = await p.chromium.launch()
        except Exception as err:  # No Chromium installed for Playwright
            pytest.skip(f"no Chromium for Playwright ({str(err)[:80]})")
        page = await browser.new_page()
        sent = []
        await page.expose_binding("labSend", lambda source, message: sent.append(message))
        with open(winui.PAGE_FILE, encoding="utf-8") as f:
            await page.set_content(f.read())
        state = {"hello": [m["type"] for m in sent]}

        async def receive(kind, data):
            await page.evaluate("e => lab.receive(e)", {"kind": kind, **data})
        await receive("setup", SETUP)
        state["model"] = await page.input_value("#model")
        state["start_disabled_empty"] = await page.is_disabled("#go")
        await page.fill("#objective", "What is the tallest mountain in Africa?")
        state["start_disabled_typed"] = await page.is_disabled("#go")
        await page.click("#go")
        state["start"] = sent[-1]
        state["recent"] = await page.inner_text("#recent")
        await receive("server", {"ready": False, "models": [], "note": "LM Studio is not answering."})
        state["retry_shown"] = await page.is_visible("#retry")
        await receive("server", {"ready": True, "models": SETUP["models"], "note": ""})
        for kind, data in RUN:
            await receive(kind, data)
        state["labels"] = await page.eval_on_selector_all("#timeline .card .label", "ls => ls.map(l => l.textContent)")
        state["chips"] = await page.eval_on_selector_all("#chips .chip", "cs => cs.map(c => c.className)")
        state["discovery"] = await page.eval_on_selector("#timeline .card.mint", "c => c.textContent")
        state["state"] = await page.inner_text("#state")
        state["done"] = await page.is_visible("#done")
        state["report"] = await page.get_attribute("#report", "srcdoc")
        state["retried"] = await page.eval_on_selector("#timeline", "t => t.textContent")
        state["steps_open"] = await page.eval_on_selector("#steps", "d => d.open")  # Folded once the report shows
        await page.click("#steps-summary")
        await page.click("#timeline a")  # A link opens outside the UI window, never in it
        state["link"] = sent[-1]
        state["url"] = page.url
        await browser.close()
        return state


def test_the_page_draws_the_start_screen_and_a_whole_run():
    state = asyncio.run(drive())
    assert state["hello"] == ["hello"]  # The page asks for its setup as soon as it loads
    assert state["model"] == "gemma-3-4b-it"  # The saved choice
    assert state["start_disabled_empty"] and not state["start_disabled_typed"]
    assert state["start"] == {"type": "start", "objective": "What is the tallest mountain in Africa?",
                              "model": "gemma-3-4b-it", "minutes": 30, "folder": "C:\\Runs", "remote": False}
    assert "Owls" in state["recent"] and state["retry_shown"]
    assert state["labels"] == ["START", "SEARCH", "NAVIGATE", "READ MORE", "ASKED AGAIN", "DISCOVER", "NAVIGATE",
                               "REFUSED", "FINISH", "CHECKING", "WRITING", "RESULT"]
    assert state["chips"] == ["chip found", "chip missing"]  # Item 1 found; item 2 reported not found
    assert "[1]" in state["discovery"] and "Geology" in state["discovery"] and "wikipedia.org" in state["discovery"]
    assert "DISCOVER refused by the code: Not on the page." in state["retried"]
    assert state["state"] == "DONE" and state["done"] and "Research report" in state["report"]
    assert not state["steps_open"]
    assert state["link"] == {"type": "open", "url": A}  # The first link: the search result
    assert state["url"] == "about:blank"  # Still the UI page
