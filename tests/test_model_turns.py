"""How each turn talks to the model: cheap in-turn retries and continuing one conversation across turns.

Both rely on every new request repeating the previous one exactly and only adding to its end, which is
what lets LM Studio reuse its cached processing instead of reading the whole prompt again."""
import asyncio
import types

import pytest

import agent_browser as ab
from localagentbrowser import agent
from fakes import discoveries, fake_client, reply

URL, URL2 = "https://docs.example/a", "https://docs.example/b"


@pytest.fixture
def scrapes(monkeypatch):
    """Replaces page scraping with a fixed environment and records each scrape (a fresh full prompt)."""
    calls = []

    async def fake_environment(page, url, page_state, visited=frozenset(), shown_links=None):
        calls.append(url)
        return "Page Text (part 1 of 2):\nfirst part\n[Page continues: READ_MORE shows part 2 of 2]\n\nLinks On Page:\n- x -> " + URL2

    monkeypatch.setattr(agent, "build_environment", fake_environment)
    return calls


def page_state():
    return {"url": URL, "parts": ["first part", "second part"], "index": 0, "seen_parts": {0}, "visit_discoveries": 1}


def turn(client, journal=None, current_url=URL, state=None, conversation=None, tried=None):
    return asyncio.run(ab.run_agent_turn(client, None, "objective", journal if journal is not None else [], current_url,
                                         tried or {"queries": [], "urls": [URL]}, state or page_state(),
                                         conversation if conversation is not None else ab.new_conversation()))


class TestCheapRetries:
    def test_refused_reply_is_corrected_in_the_same_turn(self, scrapes):
        client, model = fake_client([reply("DISCOVER", ""), reply("DISCOVER", "something new")])
        decision, refused = turn(client)
        assert decision["parameter"] == "something new" and len(refused) == 1
        first, second = model.calls
        assert second[:2] == first  # the original prompt is repeated exactly...
        assert second[2]["role"] == "assistant" and second[3]["content"].startswith("REFUSED:")  # ...then only appended to

    def test_first_correction_lists_the_alternatives(self, scrapes):
        client, model = fake_client([reply("NAVIGATE", URL), reply("DISCOVER", "a new fact")])
        turn(client)
        correction = model.calls[1][-1]["content"]
        assert "READ_MORE to see part 2 of 2" in correction and "NAVIGATE to another page" in correction

    def test_gives_up_after_the_retry_limit_and_escalates(self, scrapes):
        duplicate = "already recorded"
        client, model = fake_client([reply("DISCOVER", duplicate)] * 3)
        decision, refused = turn(client, journal=discoveries(duplicate))
        assert len(model.calls) == ab.MAX_CHEAP_RETRIES + 1 and len(refused) == ab.MAX_CHEAP_RETRIES
        assert decision["parameter"] == duplicate  # handed back to the main loop, which refuses it for the turn
        assert "Choose a different action" in model.calls[-1][-1]["content"]

    def test_invalid_json_gets_a_correction_not_the_identical_prompt(self, scrapes):
        client, model = fake_client(["not json", reply("DISCOVER", "a new fact")])
        decision, _ = turn(client)
        assert decision["action"] == "DISCOVER" and "not a valid JSON object" in model.calls[1][-1]["content"]

    def test_cut_off_reply_asks_for_a_short_thought_process_without_repeating_it(self, scrapes):
        cut = '{"thought_process": "' + "long reasoning " * 40
        client, model = fake_client([(cut, "length"), reply("DISCOVER", "a new fact")])
        decision, _ = turn(client)
        assert decision["action"] == "DISCOVER" and len(model.calls) == 2
        first, second = model.calls
        assert second[:len(first)] == first and len(second) == len(first) + 1  # the cut reply is not shown again
        assert "cut off at 600 tokens" in second[-1]["content"] and "3 to 10 words" in second[-1]["content"]

    def test_reply_cut_off_inside_a_search_query_searches_with_its_start(self, scrapes):
        cut = (chr(123) + chr(10) + "  \"thought_process\": \"I will search.\"," + chr(10) + "  \"action\": \"SEARCH\"," + chr(10)
               + "  \"parameter\": \"how to make cold brew " + "tips tricks hacks " * 60)
        client, model = fake_client([(cut, "length")])
        decision, _ = turn(client)
        assert len(model.calls) == 1 and decision["action"] == "SEARCH"
        assert decision["parameter"] == "how to make cold brew tips tricks hacks"  # cut where the loop began
        long_query = '{"thought_process": "t", "action": "SEARCH", "parameter": "' + " ".join(f"word{i}" for i in range(40))
        assert len(ab.cut_decision(long_query)["parameter"].split()) == ab.MAX_QUERY_WORDS

    def test_reply_cut_off_inside_a_looping_discover_is_asked_again_not_recorded(self, scrapes):
        loop = "cold brew coffee making at home with quantities steps fridge shelf life official sources "
        cut = '{"thought_process": "I can now DISCOVER.", "action": "DISCOVER", "parameter": "' + loop * 30
        client, model = fake_client([(cut, "length"), reply("SEARCH", "fresh query")])
        decision, _ = turn(client)
        assert ab.cut_decision(cut) is None
        assert ab.cut_decision(cut, on_search_page=True)["action"] == "DISCOVER"  # refused there, never recorded
        assert len(model.calls) == 2 and decision["action"] == "SEARCH" and "cut off" in model.calls[1][-1]["content"]

    def test_valid_first_reply_costs_one_call(self, scrapes):
        client, model = fake_client([reply("SEARCH", "fresh query")])
        _, refused = turn(client)
        assert len(model.calls) == 1 and not refused


def test_fresh_prompt_shortens_long_discoveries(scrapes):
    long_fact = "A long recorded finding. " * 40
    client, model = fake_client([reply("DISCOVER", "a new fact")])
    journal = discoveries(long_fact)
    turn(client, journal=journal)
    prompt = model.calls[0][1]["content"]
    assert long_fact.strip() not in prompt and "A long recorded finding." in prompt and "..." in prompt
    assert journal[0]["extracted_data"] == long_fact, "the journal keeps the full text"


class TestComposeFinalResult:
    def test_sees_only_the_objective_and_discoveries(self):
        client, model = fake_client(["Plain final text."])
        journal = discoveries("fact one", "fact two")
        text = asyncio.run(ab.compose_final_result(client, "the objective", journal))
        assert text == "Plain final text."
        system, user = model.calls[0]
        assert system == {"role": "system", "content": ab.COMPOSE_SYSTEM} and len(model.calls[0]) == 2
        assert "### OBJECTIVE\nthe objective" in user["content"]
        assert "[1] (from an unknown page)\nfact one" in user["content"] and "[2] (from an unknown page)\nfact two" in user["content"]
        assert "Use only the information in the DISCOVERIES" in user["content"] and "'My view:'" in user["content"]
        assert "unless the objective explicitly asks for your view" in user["content"] and "Do not fill gaps" in user["content"]
        assert "Never list the same item twice" in user["content"]
        assert "describes that situation, not the one in the OBJECTIVE" in user["content"]
        assert "Copy commands, code, file paths and names exactly" in user["content"]
        assert "DRAFT" not in user["content"], "the agent's unverified draft is not shown"

    def test_unwraps_a_json_reply(self):
        client, _ = fake_client([reply("FINISH", "My view: supported by [1].")])
        assert asyncio.run(ab.compose_final_result(client, "objective", discoveries("x"))) == "My view: supported by [1]."

    def test_adds_a_missing_view_when_the_objective_asks_for_one(self):
        client, model = fake_client(["Owls mean wisdom in Greece [1].", "Their night habits invite mystery."])
        text = asyncio.run(ab.compose_final_result(client, "Find views on owls, then give your own opinion.",
                                                   discoveries("Greece: wisdom")))
        assert text == "Owls mean wisdom in Greece [1].\n\nMy view: Their night habits invite mystery."
        assert "does not give it yet" in model.calls[1][-1]["content"]

    def test_no_extra_call_when_no_view_is_asked_for(self):
        client, model = fake_client(["Three items [1]."])
        asyncio.run(ab.compose_final_result(client, "Name three items.", discoveries("x")))
        assert len(model.calls) == 1


class TestConversation:
    def test_turns_continue_one_conversation(self, scrapes):
        client, model = fake_client([reply("DISCOVER", "fact one"), reply("NAVIGATE", URL2), reply("DISCOVER", "fact two")])
        journal, conversation = [], ab.new_conversation()
        turn(client, journal=journal, conversation=conversation)
        assert len(model.calls[0]) == 2 and len(scrapes) == 1  # turn 1: full prompt

        ab.set_note(conversation, "RESULT: Your DISCOVER was recorded.", URL)
        turn(client, journal=journal, conversation=conversation)
        assert model.calls[1][:2] == model.calls[0] and model.calls[1][3]["content"].startswith("RESULT: Your DISCOVER")
        assert len(scrapes) == 1  # same page: no re-scrape

        ab.set_note(conversation, "RESULT: NAVIGATE done.\n### CURRENT ENVIRONMENT\nnew page text", URL2)
        turn(client, journal=journal, current_url=URL2, conversation=conversation)
        assert model.calls[2][:4] == model.calls[1] and "new page text" in model.calls[2][-1]["content"]

    def test_note_for_a_different_page_starts_fresh(self, scrapes):
        client, model = fake_client([reply("DISCOVER", "a"), reply("SEARCH", "q")])
        conversation = ab.new_conversation()
        turn(client, conversation=conversation)
        ab.set_note(conversation, "RESULT: stale", URL)
        turn(client, current_url=URL2, conversation=conversation)
        assert len(model.calls[-1]) == 2

    def test_no_note_starts_fresh(self, scrapes):
        client, model = fake_client([reply("DISCOVER", "a"), reply("SEARCH", "q")])
        conversation = ab.new_conversation()
        turn(client, conversation=conversation)
        turn(client, conversation=conversation)
        assert len(model.calls[-1]) == 2

    def test_follow_up_limit_starts_fresh(self, scrapes):
        client, model = fake_client([reply("DISCOVER", f"n{i}") for i in range(ab.MAX_FOLLOWUPS + 2)])
        conversation = ab.new_conversation()
        turn(client, conversation=conversation)
        for _ in range(ab.MAX_FOLLOWUPS + 1):
            ab.set_note(conversation, "RESULT: x", URL)
            turn(client, conversation=conversation)
        assert len(model.calls[-1]) == 2 and conversation["followups"] == 0

    @pytest.mark.parametrize("note, continues", [("x" * 3000, False), ("RESULT: short", True)],
                             ids=["note would exceed the budget", "note fits"])
    def test_token_budget_counts_the_incoming_note(self, scrapes, note, continues):
        client, model = fake_client([reply("DISCOVER", "a"), reply("DISCOVER", "b")],
                                    prompt_tokens=ab.MAX_CONVERSATION_TOKENS - 500)
        conversation = ab.new_conversation()
        turn(client, conversation=conversation)
        ab.set_note(conversation, note, URL)
        turn(client, conversation=conversation)
        assert (len(model.calls[-1]) == 4) is continues


def test_fresh_prompt_numbers_the_discoveries(scrapes):
    client, model = fake_client([reply("DISCOVER", "a new fact")])
    turn(client, journal=discoveries("first fact", "second fact"))
    prompt = model.calls[0][1]["content"]
    assert "### DISCOVERIES SO FAR\n[1] first fact\n[2] second fact\n" in prompt


def test_only_the_first_turn_asks_for_one_search_for_the_whole_objective(scrapes):
    client, model = fake_client([reply("SEARCH", "q"), reply("SEARCH", "q2")])
    turn(client, journal=[], current_url=None)
    turn(client, journal=[{"turn": 1, "action": "SEARCH", "requested": "q"}], current_url=None)
    assert ab.FIRST_TURN_INSTRUCTION in model.calls[0][-1]["content"]
    assert ab.FIRST_TURN_INSTRUCTION not in model.calls[1][-1]["content"]


class TestServerFailures:
    """A model call the server fails is tried again after a wait, instead of ending the run."""

    @staticmethod
    def client(outcomes, monkeypatch):
        import openai
        try:  # openai 3 is built on httpx2, openai 2 on httpx
            import httpx2 as httpx
        except ImportError:
            import httpx

        request = httpx.Request("POST", "http://localhost:1234/v1/chat/completions")
        errors = {"down": openai.APIConnectionError(request=request),
                  "500": openai.InternalServerError("model crashed", response=httpx.Response(500, request=request), body=None),
                  "400": openai.BadRequestError("bad request", response=httpx.Response(400, request=request), body=None),
                  "timeout": openai.APITimeoutError(request=request),
                  # What LM Studio answered live when the model was unloaded in the middle of a call
                  "unloaded": openai.BadRequestError("Error code: 400 - {'error': 'Model unloaded by user or API request.'}",
                                                     response=httpx.Response(400, request=request), body=None)}
        calls, waits = [], []

        async def create(**kwargs):
            calls.append(kwargs)
            outcome = outcomes.pop(0)
            if outcome in errors:
                raise errors[outcome]
            return outcome

        async def fake_sleep(seconds):
            waits.append(seconds)

        monkeypatch.setattr(agent.asyncio, "sleep", fake_sleep)
        client = types.SimpleNamespace(chat=types.SimpleNamespace(completions=types.SimpleNamespace(create=create)))
        return agent.with_retries(client), calls, waits

    def test_a_server_failure_is_tried_again_after_a_wait(self, monkeypatch):
        client, calls, waits = self.client(["down", "500", "reply"], monkeypatch)
        assert asyncio.run(client.chat.completions.create(model="m", messages=[])) == "reply"
        assert len(calls) == 3 and waits == list(ab.MODEL_RETRY_WAITS[:2])

    def test_a_model_unloaded_during_the_call_is_tried_again(self, monkeypatch):
        client, calls, waits = self.client(["unloaded", "reply"], monkeypatch)
        assert asyncio.run(client.chat.completions.create(model="m", messages=[])) == "reply"
        assert len(calls) == 2 and waits == [ab.MODEL_RETRY_WAITS[0]]

    def test_it_gives_up_after_the_last_wait(self, monkeypatch):
        import openai

        client, calls, waits = self.client(["down"] * (len(ab.MODEL_RETRY_WAITS) + 1), monkeypatch)
        with pytest.raises(openai.APIConnectionError):
            asyncio.run(client.chat.completions.create(model="m", messages=[]))
        assert waits == list(ab.MODEL_RETRY_WAITS)

    def test_a_timeout_is_not_retried(self, monkeypatch):
        # The model is still writing (a runaway reply), not lost: sending the request again only repeats it
        import openai

        client, calls, waits = self.client(["timeout", "reply"], monkeypatch)
        with pytest.raises(openai.APITimeoutError):
            asyncio.run(client.chat.completions.create(model="m", messages=[]))
        assert len(calls) == 1 and waits == []

    def test_other_errors_are_not_retried(self, monkeypatch):
        import openai

        client, calls, waits = self.client(["400"], monkeypatch)
        with pytest.raises(openai.BadRequestError):
            asyncio.run(client.chat.completions.create(model="m", messages=[]))
        assert len(calls) == 1 and waits == []
