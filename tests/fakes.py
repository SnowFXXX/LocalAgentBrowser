"""Stand-ins for the language model and the browser, so the agent can be tested in seconds without
LM Studio, Chromium or network access."""
import asyncio
import json
import types


def reply(action, parameter=""):
    """A model reply in the agent's JSON format."""
    return json.dumps({"thought_process": "t", "action": action, "parameter": parameter})


def discoveries(*texts):
    """A journal holding the given texts as recorded discoveries."""
    return [{"action": "DISCOVER", "extracted_data": t} for t in texts]


class FakeCompletions:
    """Scripted replacement for client.chat.completions: returns the given replies in order and records
    the full message list of every call."""

    def __init__(self, replies, prompt_tokens=1500):
        self.replies, self.calls, self.prompt_tokens = list(replies), [], prompt_tokens

    async def create(self, **kwargs):
        self.calls.append([dict(m) for m in kwargs["messages"]])
        if self.prompt_tokens is None:  # Estimate from the prompt size, like a real server would report
            prompt_tokens = sum(len(m["content"]) for m in kwargs["messages"]) // 4
        else:
            prompt_tokens = self.prompt_tokens
        usage = types.SimpleNamespace(prompt_tokens=prompt_tokens, completion_tokens=40)
        message = types.SimpleNamespace(content=self.replies.pop(0))
        return types.SimpleNamespace(usage=usage, choices=[types.SimpleNamespace(message=message)])


def fake_client(replies, prompt_tokens=1500):
    completions = FakeCompletions(replies, prompt_tokens)
    return types.SimpleNamespace(chat=types.SimpleNamespace(completions=completions)), completions


class FakeLinksPage:
    """Only answers the link-extraction query."""

    def __init__(self, url, links):
        self.url, self.links = url, links

    async def evaluate(self, js, *args):
        return self.links


class FakeSite:
    """A tiny website plus a DuckDuckGo results page, served to the agent through a fake Playwright API.

    pages: {url: (page_text, [(link_text, href), ...])}
    results: DuckDuckGo results as {"title", "href", "snippet"} dicts
    """

    def __init__(self, pages, results):
        self.pages, self.results = pages, results

    def playwright(self):
        site = self

        class Locator:
            def __init__(self, page):
                self.page = page

            async def count(self):
                return len(site.results) if "duckduckgo" in self.page.url else 0

        class Page:
            url = "about:blank"

            async def goto(self, url, **kwargs):
                self.url = url

            async def reload(self, **kwargs):
                pass

            async def wait_for_load_state(self, *args, **kwargs):
                pass

            def locator(self, selector):
                return Locator(self)

            async def evaluate(self, js, *args):
                text, links = site.pages.get(self.url.split("#")[0].rstrip("/"), ("", []))
                if "result__a" in js:
                    return site.results if "duckduckgo" in self.url else []
                if "blockStart" in js:
                    return text
                return [{"text": t, "href": h} for t, h in links]

        class Context:
            async def add_init_script(self, script):
                pass

            async def new_page(self):
                return Page()

        class Browser:
            async def new_context(self, **kwargs):
                return Context()

            async def close(self):
                pass

        class Playwright:
            chromium = types.SimpleNamespace(launch=lambda **kwargs: asyncio.sleep(0, result=Browser()))

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                pass

        return Playwright()
