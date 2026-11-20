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


class RolesDoNotAlternate(Exception):
    """What a strict chat template answers (LM Studio, Gemma 3: "400 - Conversation roles must alternate")."""


def roles_alternate(messages):
    """Refuses a request whose turns don't go system (optional), user, assistant, user, ... and end with the user,
    as Gemma's chat template does: the agent's cut-off retry once sent two user messages in a row, and every
    Gemma task that hit it ended. Checked on every fake call, so no path can do it again unnoticed."""
    roles = [m["role"] for m in messages if m["role"] != "system" or messages.index(m) > 0]
    if roles and roles[0] == "system":
        raise RolesDoNotAlternate(f"a system message after the first: {roles}")
    expected = ["user" if i % 2 == 0 else "assistant" for i in range(len(roles))]
    if roles != expected or not roles or roles[-1] != "user":
        raise RolesDoNotAlternate(f"Conversation roles must alternate user/assistant/user/assistant/...: {roles}")


class EmptyTurn(Exception):
    """What Mistral's and Cohere's services answer to a turn with no text (400 "Assistant message must have either
    content or tool_calls")."""


def no_empty_turns(messages):
    """Refuses a request with a turn that has no text, as Mistral's and Cohere's services do: the coverage retry sent
    a thinking model's empty reply back, and every such check failed. Checked on every fake call, like the roles."""
    empty = [m["role"] for m in messages if not str(m.get("content") or "").strip()]
    if empty:
        raise EmptyTurn(f"a turn with no text: {empty}")


class FakeCompletions:
    """Scripted replacement for client.chat.completions: returns the given replies in order and records
    the full message list of every call."""

    def __init__(self, replies, prompt_tokens=1500):
        self.replies, self.calls, self.prompt_tokens = list(replies), [], prompt_tokens
        self.limits = []  # max_tokens of every call

    async def create(self, **kwargs):
        roles_alternate(kwargs["messages"])
        no_empty_turns(kwargs["messages"])
        self.calls.append([dict(m) for m in kwargs["messages"]])
        self.limits.append(kwargs.get("max_tokens"))
        if self.prompt_tokens is None:  # Estimate from the prompt size, like a real server would report
            prompt_tokens = sum(len(m["content"]) for m in kwargs["messages"]) // 4
        else:
            prompt_tokens = self.prompt_tokens
        usage = types.SimpleNamespace(prompt_tokens=prompt_tokens, completion_tokens=40)
        content = self.replies.pop(0)
        content, finish_reason = content if isinstance(content, tuple) else (content, "stop")  # (text, "length") = cut off
        message = types.SimpleNamespace(content=content)
        return types.SimpleNamespace(usage=usage, choices=[types.SimpleNamespace(message=message, finish_reason=finish_reason)])


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

    def __init__(self, pages, results, statuses=None):
        self.pages, self.results = pages, results
        self.statuses = statuses or {}  # {url: HTTP status} for pages that answer with an error
        self.visits = []

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
                site.visits.append(url)
                if url in site.statuses:
                    return types.SimpleNamespace(status=site.statuses[url])
                known = "duckduckgo" in url or url.split("#")[0].rstrip("/") in site.pages
                return types.SimpleNamespace(status=200 if known else 404)

            async def reload(self, **kwargs):
                return types.SimpleNamespace(status=200)

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
