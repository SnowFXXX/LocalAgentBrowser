"""The Windows UI's page (winui.html) in a real headless Chromium: the start screen, and a whole run's events drawn as
the live view and the report. Skipped where Playwright has no Chromium (the tests otherwise use a fake browser)."""
import asyncio

import pytest

from localagentbrowser import winui

A = "https://en.wikipedia.org/wiki/Mount_Kilimanjaro"
SETUP = {"edition": "LocalAgentBrowser", "ready": True, "models": ["qwen2.5-7b-instruct", "gemma-3-4b-it"],
         "note": "", "model": "gemma-3-4b-it", "minutes": 30, "folder": "C:\\Runs", "remote": None,
         "remote_chosen": False, "recent": [{"folder": "C:\\Runs\\2026-11-20_090000", "objective": "Owls",
                                             "when": "2026-11-20 09:00"}]}
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
                              "model": "gemma-3-4b-it", "minutes": 30, "folder": "C:\\Runs", "remote": False,
                              "remote_model": ""}
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


async def follow_on_a_page():
    from playwright.async_api import async_playwright
    from localagentbrowser import follow
    async with async_playwright() as p:
        try:
            browser = await p.chromium.launch()
        except Exception as err:
            pytest.skip(f"no Chromium for Playwright ({str(err)[:80]})")
        page = await browser.new_page(viewport={"width": 800, "height": 600})
        filler = "<p>" + "Unrelated filler text. " * 400 + "</p>"
        await page.set_content(f"<html><body>{filler}<h2>Geology</h2><p>Kilimanjaro rises to <b>5,895 m</b> "
                               f"(19,341 ft).</p><p>Its summit is Uhuru Peak.</p>{filler}</body></html>")
        before = await page.evaluate("document.body.innerText")
        found = await follow.show_discovery(page, '(Under the heading "Geology") Kilimanjaro rises to 5,895 m '
                                                  '(19,341 ft). Its summit is Uhuru Peak.')
        await page.wait_for_timeout(600)  # The smooth scroll
        state = {"found": found, "scrolled": await page.evaluate("window.scrollY"),
                 "ranges": await page.evaluate("CSS.highlights.get('lab-found').size"),
                 "same_text": await page.evaluate("document.body.innerText") == before,
                 "missing": await follow.show_discovery(page, "Words that are nowhere on this page at all.")}
        await browser.close()
        return state


def test_the_browser_highlights_a_discovery_across_elements_and_keeps_the_page_text():
    state = asyncio.run(follow_on_a_page())
    assert state["found"] and state["scrolled"] > 500  # Scrolled down to it, past the filler
    assert state["ranges"] == 1  # The whole quote at once, across the bold text and the paragraph break
    assert state["same_text"]  # What the agent reads is unchanged
    assert state["missing"] is False


async def follow_by_sentence():
    from playwright.async_api import async_playwright
    from localagentbrowser import follow
    async with async_playwright() as p:
        try:
            browser = await p.chromium.launch()
        except Exception as err:
            pytest.skip(f"no Chromium for Playwright ({str(err)[:80]})")
        page = await browser.new_page()
        await page.set_content("<p>Kibo is the highest cone.</p><p>Shira is the lowest.</p><p>It rises to 5,895 m.</p>")
        # Two sentences the page has apart, joined in one discovery: each is highlighted where it is
        found = await follow.show_discovery(page, "Kibo is the highest cone. It rises to 5,895 m.")
        ranges = await page.evaluate("CSS.highlights.get('lab-found').size")
        await browser.close()
        return found, ranges


def test_a_discovery_joining_sentences_is_highlighted_sentence_by_sentence():
    assert asyncio.run(follow_by_sentence()) == (True, 2)


KILIMANJARO = ("<p>Mount Kilimanjaro is a dormant volcano in Tanzania. It is the highest mountain in Africa and the "
               "highest free-standing mountain above sea level in the world, at 5,895 m (19,341 ft) above sea level and "
               "4,900 m (16,100 ft) above its plateau base.</p><p>It is also the highest volcano in the Eastern "
               "Hemisphere.</p>")


async def follow_in_own_words():
    from playwright.async_api import async_playwright
    from localagentbrowser import follow
    async with async_playwright() as p:
        try:
            browser = await p.chromium.launch()
        except Exception as err:
            pytest.skip(f"no Chromium for Playwright ({str(err)[:80]})")
        page = await browser.new_page()
        state = {}
        for name, finding in (
                # As Qwen2.5 7B recorded it (live, Nov 21): the checks accepted it; nothing was marked
                ("own words", "Mount Kilimanjaro is the highest mountain in Africa, at an elevation of 5,895 m "
                              "(19,341 ft) above sea level."),
                ("other number", "Mount Kilimanjaro is the highest mountain in Africa, at 5,896 m above sea level."),
                ("other words", "Paris is the capital and largest city of France, with many famous museums.")):
            await page.set_content(KILIMANJARO)
            await page.evaluate("() => CSS.highlights.clear()")  # The last case's marks outlive set_content
            found = await follow.show_discovery(page, finding)
            marked = await page.evaluate("() => { const h = CSS.highlights.get('lab-found'); "
                                         "return h ? [...h].map(r => r.toString()) : []; }")
            state[name] = (found, marked)
        await browser.close()
        return state


def test_a_discovery_in_the_model_s_own_words_marks_the_closest_page_sentence():
    state = asyncio.run(follow_in_own_words())
    found, marked = state["own words"]
    assert found and marked == ["It is the highest mountain in Africa and the highest free-standing mountain above sea "
                                "level in the world, at 5,895 m (19,341 ft) above sea level and 4,900 m (16,100 ft) "
                                "above its plateau base."]
    assert state["other number"] == (False, [])  # A number the page sentence doesn't give: not that sentence
    assert state["other words"] == (False, [])


async def watch_mode():
    from playwright.async_api import async_playwright
    async with async_playwright() as p:
        try:
            browser = await p.chromium.launch()
        except Exception as err:
            pytest.skip(f"no Chromium for Playwright ({str(err)[:80]})")
        page = await browser.new_page()
        await page.expose_binding("labSend", lambda source, message: None)
        with open(winui.PAGE_FILE, encoding="utf-8") as f:
            await page.set_content(f.read())

        async def receive(kind, data):
            await page.evaluate("e => lab.receive(e)", {"kind": kind, **data})
        await receive("watch", {"title": "Test suite · gemma-3-4b-it"})
        state = {"waiting": await page.inner_text("#watch-line"), "start": await page.is_visible("#start"),
                 "classic": await page.is_visible("#classic")}
        await receive("setup", SETUP)  # The page asks for its setup on load; a watch window keeps its own view
        await receive("started", {"objective": "factual", "folder": "C:\\Runs\\x", "model": "gemma-3-4b-it",
                                  "watch": {"label": "factual", "n": 2, "of": 7}})
        await receive("objective", {"objective": "What is the tallest mountain in Africa?"})
        await receive("note", {"label": "QUICK CHECK", "text": "PASS split: 3 item(s)", "tone": "mint"})
        await receive("watch_done", {})
        state.update(line=await page.inner_text("#watch-line"), objective=await page.inner_text("#live-objective"),
                     stop=await page.is_visible("#stop"), start_after=await page.is_visible("#start"),
                     labels=await page.eval_on_selector_all("#timeline .card .label", "ls => ls.map(l => l.textContent)"),
                     state=await page.inner_text("#state"))
        await browser.close()
        return state


REMOTE = {"label": "Use the second kind of model", "model": "openai/gpt-4o-mini", "ready": True, "note": "",
          "warning": "The prompts go out.", "other_hint": "vendor/model", "other_note": "Not tested.",
          "models": [{"id": "microsoft/phi-4", "group": "Recommended", "note": "The default."},
                     {"id": "openai/gpt-4o-mini", "group": "Recommended", "note": "All 7 tasks."},
                     {"id": "qwen/qwen3-32b", "group": "Passed the compatibility test", "note": "Every check."}]}


async def remote_picker():
    from playwright.async_api import async_playwright
    async with async_playwright() as p:
        try:
            browser = await p.chromium.launch()
        except Exception as err:
            pytest.skip(f"no Chromium for Playwright ({str(err)[:80]})")
        page = await browser.new_page()
        sent = []
        await page.expose_binding("labSend", lambda source, message: sent.append(message))
        with open(winui.PAGE_FILE, encoding="utf-8") as f:
            await page.set_content(f.read())
        await page.evaluate("e => lab.receive(e)", {"kind": "setup", **dict(SETUP, remote=REMOTE, remote_chosen=True)})
        await page.fill("#objective", "How tall is Kilimanjaro?")
        state = {"shown": await page.is_visible("#remote-pick"), "picked": await page.input_value("#remote-model"),
                 "note": await page.inner_text("#remote-model-note"),
                 "groups": await page.eval_on_selector_all("#remote-model optgroup", "gs => gs.map(g => g.label)"),
                 "lm_studio_off": await page.is_disabled("#model")}
        await page.select_option("#remote-model", "__other__")
        state["other_shown"] = await page.is_visible("#remote-other")
        await page.fill("#remote-other", "not a model")
        state["bad_blocks"] = await page.is_disabled("#go")
        await page.fill("#remote-other", "mistralai/mistral-small-2603")
        state["good_allows"] = not await page.is_disabled("#go")
        await page.click("#go")
        state["start"] = sent[-1]
        await page.uncheck("#remote")
        state["hidden_unticked"] = not await page.is_visible("#remote-pick")
        await browser.close()
        return state


def test_the_second_kind_of_model_is_picked_from_our_list_or_typed_in():
    state = asyncio.run(remote_picker())
    assert state["shown"] and state["picked"] == "openai/gpt-4o-mini" and state["note"] == "All 7 tasks."
    assert state["groups"] == ["Recommended", "Passed the compatibility test"] and state["lm_studio_off"]
    assert state["other_shown"] and state["bad_blocks"] and state["good_allows"]
    assert state["start"]["remote"] and state["start"]["remote_model"] == "mistralai/mistral-small-2603"
    assert state["hidden_unticked"]


LONG_REASON = ("Your only discovery comes from https://en.wikipedia.org/wiki/Eiffel_Tower. Consider whether the "
               "objective needs more. The current page still has 23 unread parts: use its outline. " * 3)


async def long_run():
    from playwright.async_api import async_playwright
    async with async_playwright() as p:
        try:
            browser = await p.chromium.launch()
        except Exception as err:
            pytest.skip(f"no Chromium for Playwright ({str(err)[:80]})")
        page = await browser.new_page(viewport={"width": 720, "height": 800})
        await page.expose_binding("labSend", lambda source, message: None)
        with open(winui.PAGE_FILE, encoding="utf-8") as f:
            await page.set_content(f.read())

        async def receive(kind, data):
            await page.evaluate("e => lab.receive(e)", {"kind": kind, **data})
        await receive("started", {"objective": "How often is the Eiffel Tower repainted?", "folder": "C:\\Runs\\x",
                                  "model": "microsoft/phi-4"})
        for n in range(30):  # A timeline several windows long
            await receive("decision", {"action": "READ_MORE", "parameter": str(n + 2), "thought": "Reading on. " * 20})
        await receive("retried", {"action": "FINISH", "parameter": "x", "reason": LONG_REASON})
        await page.evaluate("() => window.scrollTo(0, document.documentElement.scrollHeight)")
        await asyncio.sleep(0.3)
        state = {"bar_top": await page.evaluate("() => document.querySelector('.bar').getBoundingClientRect().top"),
                 "scrolled": await page.evaluate("() => window.scrollY"),
                 "card": await page.inner_text("#timeline .card:last-child .main"),
                 "whole_shown": await page.is_visible("#timeline .card:last-child details.whole > div")}
        await page.click("#timeline .card:last-child details.whole > summary")
        state["whole"] = await page.inner_text("#timeline .card:last-child details.whole > div")
        await browser.close()
        return state


async def following_the_newest_card():
    from playwright.async_api import async_playwright
    async with async_playwright() as p:
        try:
            browser = await p.chromium.launch()
        except Exception as err:
            pytest.skip(f"no Chromium for Playwright ({str(err)[:80]})")
        page = await browser.new_page(viewport={"width": 720, "height": 800})
        await page.expose_binding("labSend", lambda source, message: None)
        with open(winui.PAGE_FILE, encoding="utf-8") as f:
            await page.set_content(f.read())

        async def receive(kind, data):
            await page.evaluate("e => lab.receive(e)", {"kind": kind, **data})
            await page.wait_for_timeout(120)

        def at_bottom():
            return page.evaluate("() => innerHeight + scrollY >= document.documentElement.scrollHeight - 2")
        await receive("started", {"objective": "How tall is Kilimanjaro?", "folder": "C:\\Runs\\x", "model": "m"})
        await receive("checklist", {"items": [{"text": "its height", "discoveries": []}]})
        for n in range(12):
            await receive("decision", {"action": "READ_MORE", "parameter": str(n + 2), "thought": "Reading on. " * 20})
        # A card that grows after it is made: the finding gets its source and items, then a check's whole message
        await receive("decision", {"action": "DISCOVER", "parameter": "Kilimanjaro is 5,895 m.", "thought": ""})
        await receive("discovery", {"number": 1, "text": "Kilimanjaro is 5,895 m.", "source": A, "items": [1],
                                    "checklist": [{"text": "its height", "discoveries": [1]}]})
        await receive("retried", {"action": "FINISH", "parameter": "x", "reason": LONG_REASON})
        state = {"follows": await at_bottom()}
        await page.evaluate("() => window.scrollTo(0, 300)")  # The person scrolls up to read
        await page.wait_for_timeout(120)
        await receive("decision", {"action": "READ_MORE", "parameter": "20", "thought": "More."})
        state["stays"] = await page.evaluate("() => window.scrollY") == 300
        await page.evaluate("() => window.scrollTo(0, document.documentElement.scrollHeight)")  # And back down
        await page.wait_for_timeout(120)
        await receive("decision", {"action": "READ_MORE", "parameter": "21", "thought": "More. " * 30})
        state["follows_again"] = await at_bottom()
        await browser.close()
        return state


def test_the_live_view_keeps_its_newest_card_in_sight_unless_scrolled_up():
    """Seen live (Nov 21): cards that grow once made were left below the window's edge."""
    assert asyncio.run(following_the_newest_card()) == {"follows": True, "stays": True, "follows_again": True}


def test_a_long_run_keeps_the_bar_and_a_check_s_card_shows_its_reason_first():
    """Seen live (Nov 21): past a window's length the bar scrolled away (the body was the window's height), and a
    refusal's card showed the checks' whole message to the model, a page long."""
    state = asyncio.run(long_run())
    assert state["scrolled"] > 2000 and state["bar_top"] == 0
    assert state["card"].startswith("ASKED AGAIN") and state["card"].endswith(
        "FINISH refused by the code: Your only discovery comes from https://en.wikipedia.org/wiki/Eiffel_Tower.")
    assert not state["whole_shown"] and state["whole"].strip() == LONG_REASON.strip()


async def opened_window(tmp_path, monkeypatch):
    import os
    from playwright.async_api import async_playwright
    monkeypatch.setattr(winui, "server_status", lambda: (True, ["gemma-3-4b-it"], ""))
    monkeypatch.setattr(winui.settings, "settings_folder", lambda: str(tmp_path / "settings"))
    async with async_playwright() as p:
        if not os.path.exists(p.chromium.executable_path):
            pytest.skip("no Chromium for Playwright")
        app = winui.App(None)
        context = await winui.open_window(p, str(tmp_path / "profile"), app, headless=True)
        await app.page.wait_for_function("() => document.getElementById('folder').textContent", timeout=15000)
        state = {"url": app.page.url, "folder": await app.page.inner_text("#folder"),
                 "server": await app.page.inner_text("#server-text"), "model": await app.page.input_value("#model")}
        await context.close()
        return state


def test_the_real_window_opens_on_the_ui_page_and_gets_its_setup(tmp_path, monkeypatch):
    """Seen live (Nov 21): the page says hello as it loads, and the answer was lost when the app was given its page
    only after the load: the start screen stayed at "Checking LM Studio..." with no reports folder."""
    state = asyncio.run(opened_window(tmp_path, monkeypatch))
    assert state["url"].endswith("/winui.html")
    assert state["folder"] and state["server"] == "LM Studio is ready (1 model)." and state["model"] == "gemma-3-4b-it"


def test_the_watch_window_follows_tasks_and_starts_none():
    state = asyncio.run(watch_mode())
    assert "waiting for the first task" in state["waiting"]
    assert not state["start"] and not state["classic"] and not state["start_after"] and not state["stop"]
    assert state["line"] == "Test suite · gemma-3-4b-it · 2 of 7 · factual · the run is over"
    assert state["objective"] == "What is the tallest mountain in Africa?"
    assert state["labels"] == ["START", "QUICK CHECK"] and state["state"] == "FINISHED"
