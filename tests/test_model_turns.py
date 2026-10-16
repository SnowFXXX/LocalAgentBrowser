"""How each turn talks to the model: cheap in-turn retries and continuing one conversation across turns.

Both rely on every new request repeating the previous one exactly and only adding to its end, which is
what lets LM Studio reuse its cached processing instead of reading the whole prompt again."""
import asyncio

import pytest

import agent_browser as ab
from fakes import discoveries, fake_client, reply

URL, URL2 = "https://docs.example/a", "https://docs.example/b"


@pytest.fixture
def scrapes(monkeypatch):
    """Replaces page scraping with a fixed environment and records each scrape (a fresh full prompt)."""
    calls = []

    async def fake_environment(page, url, page_state, visited=frozenset(), shown_links=None):
        calls.append(url)
        return "Page Text (part 1 of 2):\nfirst part\n[Page continues: READ_MORE shows part 2 of 2]\n\nLinks On Page:\n- x -> " + URL2

    monkeypatch.setattr(ab, "build_environment", fake_environment)
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
        assert "give exactly that many" in user["content"]
        assert "DRAFT" not in user["content"], "the agent's unverified draft is not shown"

    def test_unwraps_a_json_reply(self):
        client, _ = fake_client([reply("FINISH", "My view: supported by [1].")])
        assert asyncio.run(ab.compose_final_result(client, "objective", discoveries("x"))) == "My view: supported by [1]."


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
