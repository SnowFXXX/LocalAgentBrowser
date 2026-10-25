"""Runs the real main() loop against a fake website and a scripted model, exercising every kind of turn
end to end in about a second."""
import asyncio
import builtins
import json
import urllib.parse

import agent_browser as ab
from localagentbrowser import agent
from fakes import FakeSite, fake_client, reply

A, B = "https://docs.example/a", "https://docs.example/b"
SITE = FakeSite(
    pages={
        A: ("Intro paragraph.\n\n" + "Long paragraph about setup. " * 120 + "\n\nFinal words.",
            [("Menu", "https://docs.example/menu"), ("Page B", B)]),
        B: ("Page B text with a fact.", [("Menu", "https://docs.example/menu"), ("Back to A", A)]),
    },
    results=[{"title": "Doc A", "href": "//duckduckgo.com/l/?uddg=" + urllib.parse.quote(A), "snippet": "about a"}],
)
COVERED = json.dumps({"evidence": "[1] fact from a", "missing": "", "conditions": [], "things": []})
SCRIPT = [
    reply("SEARCH", "docs a"),         # turn 1: fresh prompt
    reply("NAVIGATE", A),              # turn 2: search results arrive as a follow-up note
    reply("DISCOVER", "fact from a"),  # turn 3: page A arrives as a follow-up note
    reply("NAVIGATE", A),              # turn 4: already on A -> cheap retry
    reply("NAVIGATE", A),              #         again -> cheap retry 2
    reply("NAVIGATE", A),              #         again -> refused for the turn
    reply("READ_MORE", "2 of 3"),      # turn 5
    reply("NAVIGATE", B),              # turn 6: A had a discovery, so no leave check
    reply("NAVIGATE", A),              # turn 7: nothing recorded on B -> leave check
    reply("NAVIGATE", A),              #         repeated -> goes through (going back to A is allowed)
    reply("FINISH", "done"),           # turn 8: finish check
    reply("FINISH", "final answer"),   #         repeated with the final result -> finished
    COVERED, COVERED,                  # the coverage check of each checklist item finds no gaps
    COVERED, COVERED,                  # no item has a linked discovery, so both are checked once more before writing
    "Composed final result citing [1].",  # the separate call that writes the final result (plain text)
]


CHECKLIST_REPLY = json.dumps({"parts": ["the setup", "the fact"]})  # the objective split, before turn 1


def run_agent(monkeypatch, tmp_path, objective_check_every=None, script=SCRIPT):
    client, model = fake_client([CHECKLIST_REPLY] + script, prompt_tokens=None)
    real_sleep = asyncio.sleep
    monkeypatch.setattr(agent, "async_playwright", SITE.playwright)
    monkeypatch.setattr(agent, "AsyncOpenAI", lambda **kwargs: client)
    monkeypatch.setattr(ab.asyncio, "sleep", lambda seconds, *a, **k: real_sleep(0, *a, **k))
    monkeypatch.setattr(builtins, "input", lambda prompt="": "test objective")
    monkeypatch.chdir(tmp_path)
    if objective_check_every:
        monkeypatch.setattr(agent, "OBJECTIVE_CHECK_EVERY", objective_check_every)
    asyncio.run(ab.main())
    journal = ab.read_research_log(tmp_path / "research_log.txt")[2]
    return model.calls[1:], journal  # Without the checklist call, so call numbers match the script


def last_message(call):
    return call[-1]["content"]


def test_full_run(monkeypatch, tmp_path):
    calls, journal = run_agent(monkeypatch, tmp_path)
    actions = [e["action"] for e in journal]

    assert len(calls) == len(SCRIPT)
    last = len(calls) - 1  # the final result is written from its own fresh prompt
    assert [i for i, c in enumerate(calls) if len(c) == 2] == [0, 12, 13, 14, 15, last], \
        "only the first call, the coverage checks (and their recheck) and the final result are fresh"
    assert all(calls[i][:len(calls[i - 1])] == calls[i - 1] for i in range(1, 12)), "each browsing call must extend the previous"
    assert "Search Results" in last_message(calls[1])
    assert "This is the first turn: SEARCH for the whole OBJECTIVE" in calls[0][1]["content"]
    assert "Page Text (part 1 of" in last_message(calls[2]) and "Page B" in last_message(calls[2])
    assert "choose FINISH now" in last_message(calls[3]), "after a discovery the model is asked whether it is done"
    assert "1. the setup (discoveries for it: none yet)" in calls[0][1]["content"], "the checklist is in the fresh prompt"
    assert "recorded as discovery [1] (linked to no checklist item)" in last_message(calls[3])
    assert "OBJECTIVE CHECKLIST now:\n1. the setup" in last_message(calls[3])
    assert actions.count("REFUSED_IN_TURN") == 4 and "BLOCKED_NAVIGATE" in actions
    assert not any("done (visit" in last_message(c) for c in calls[4:7]), "a refused NAVIGATE must not report 'done'"
    assert "part 2 of" in last_message(calls[7]).lower()
    assert f"You are on {A} (part 2 of" in last_message(calls[7]), "notes must say which page is current"
    assert "(1 links already listed" in last_message(calls[8])
    assert "CHECK: You are about to leave" in last_message(calls[9]) and "Do NOT" not in last_message(calls[9])
    assert "visit 2 of 3" in last_message(calls[10])
    assert "PROGRESS CHECK" in last_message(calls[10])
    assert not any("PROGRESS CHECK" in last_message(c) for c in calls[:10])
    assert not any("OBJECTIVE CHECK:" in last_message(c) for c in calls), "default schedule should not fire in 8 turns"
    finish_check = last_message(calls[11])
    assert "Before finishing" in finish_check and "Do NOT" not in finish_check
    assert f"Your only discovery comes from {A}" in finish_check
    assert "The item to check: ITEM 2" in last_message(calls[13])
    compose_request = last_message(calls[last])
    assert len(calls[last]) == 2, "the final result is written from a fresh prompt, not the browsing conversation"
    assert f"[1] (from {A})\nfact from a" in compose_request and "final answer" not in compose_request
    assert actions[-1] == "FINISH", "FINISH must be saved to the log"
    assert journal[-1]["finding"] == "Composed final result citing [1]." and journal[-1]["draft"] == "final answer"

    report = (tmp_path / ab.REPORT_FILE).read_text(encoding="utf-8")
    assert "**Objective:** test objective" in report and "finished by the agent on turn 8" in report
    assert "## Final result\n\nComposed final result citing [1]." in report
    assert "## Discoveries (1)" in report and f"Turn 3, from {A}" in report and "fact from a" in report


def test_error_pages_are_failed_navigations(monkeypatch, tmp_path):
    missing = "https://docs.example/guessed-page"
    script = [
        reply("SEARCH", "docs a"),
        reply("NAVIGATE", missing),        # 404 -> recorded as a failed navigation
        reply("NAVIGATE", A),              # leaving the error page needs no confirmation
        reply("FINISH", "a"), reply("FINISH", "a"),  # nothing recorded: sent back once by the coverage check
        reply("FINISH", "a"), reply("FINISH", "a"), "Final.",
    ]
    calls, journal = run_agent(monkeypatch, tmp_path, script=script)
    failed = [e for e in journal if e["action"] == "FAILED_NAVIGATE"]
    assert len(failed) == 1 and "HTTP 404" in failed[0]["error"]
    assert "failed: The page returned HTTP 404" in last_message(calls[2])
    assert not any(e["action"] == "REFUSED_IN_TURN" and "leave" in e.get("reason", "") for e in journal)
    assert len(calls) == len(script)


def test_report_is_written_when_the_run_stops_early(monkeypatch, tmp_path):
    # The scripted model runs out of replies on turn 4, which ends the run like a lost LM Studio connection
    run_agent(monkeypatch, tmp_path, script=SCRIPT[:3])
    report = (tmp_path / ab.REPORT_FILE).read_text(encoding="utf-8")
    assert "stopped before the agent finished (after turn 3)" in report
    assert "## Discoveries (1)" in report and "fact from a" in report


def test_objective_check_schedule(monkeypatch, tmp_path):
    calls, _ = run_agent(monkeypatch, tmp_path, objective_check_every=3)
    fired = [i for i, c in enumerate(calls) if "OBJECTIVE CHECK:" in last_message(c)]
    assert fired == [3, 8], "after turns 3 and 6"
    assert "recorded 1 discoveries in 3 turns" in last_message(calls[3])
    assert "PROGRESS CHECK" in last_message(calls[10]) and "OBJECTIVE CHECK:" not in last_message(calls[10])


def test_discoveries_are_linked_to_checklist_items(monkeypatch, tmp_path):
    linked = json.dumps({"thought_process": "t", "action": "DISCOVER", "parameter": "fact from a", "checklist_items": [2, 9]})
    script = [reply("SEARCH", "docs a"), reply("NAVIGATE", A), linked]
    calls, journal = run_agent(monkeypatch, tmp_path, script=script)
    assert journal[-1]["checklist_items"] == [2], "item numbers outside the checklist are dropped"
    report = (tmp_path / ab.REPORT_FILE).read_text(encoding="utf-8")
    assert "fact from a" in report


def test_a_coverage_send_back_starts_the_next_turn_fresh(monkeypatch, tmp_path):
    gap = json.dumps({"evidence": "", "missing": "the second fact", "conditions": [], "things": []})
    script = [reply("SEARCH", "docs a"), reply("NAVIGATE", A), reply("DISCOVER", "fact from a"),
              reply("FINISH", "done"), reply("FINISH", "done"),  # finish check, then repeated
              gap, COVERED,                                       # coverage check: nothing answers item 1 -> sent back
              reply("FINISH", "done"), reply("FINISH", "done"),   # next turn, fresh prompt
              COVERED, COVERED,                                   # nothing linked: both items checked once more
              "Final [1]."]
    calls, journal = run_agent(monkeypatch, tmp_path, script=script)
    assert any(e["action"] == "BLOCKED_COVERAGE" for e in journal)
    next_turn = calls[7]
    assert len(next_turn) == 2 and "Your FINISH was refused. Before finishing, each checklist item" in next_turn[1]["content"]
    assert journal[-1]["action"] == "FINISH"
