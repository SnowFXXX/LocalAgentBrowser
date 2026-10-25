"""Golden prompts: every message the agent sends to the model in a set of scripted runs, compared character for
character with a recorded copy (tests/golden/prompts.json), together with the run's journal and report.

The prompts are everything the model ever sees, so a change that leaves them identical cannot change what the
model decides. Restructuring the code must keep this test passing unchanged; a deliberate change to a prompt is
recorded again with GOLDEN_UPDATE=1 (python -m pytest tests/test_golden_prompts.py) and the diff of
prompts.json reviewed like any other change."""
import asyncio
import builtins
import json
import os
import urllib.parse

import pytest

import agent_browser as ab
from localagentbrowser import agent
from fakes import FakeSite, fake_client, reply
from test_main_loop import CHECKLIST_REPLY, SCRIPT, SITE

GOLDEN = os.path.join(os.path.dirname(os.path.abspath(__file__)), "golden", "prompts.json")
UPDATE = os.environ.get("GOLDEN_UPDATE") == "1"

# A long comparison page whose outline names the items: parts are about 2,500 characters, one section each
LONG = "https://compare.example/sqlite-vs-postgresql"
SECTION_TEXT = {
    "SQLite vs PostgreSQL: an overview": "Both are relational databases that store data in tables. ",
    "History of both projects": "SQLite was first released in 2000 and PostgreSQL grew out of a university project. ",
    "Licenses": "SQLite is in the public domain and PostgreSQL uses a permissive license of its own. ",
    "Administration": "SQLite needs no server and no administration: the database is a single file. ",
    "Features": "PostgreSQL supports JSON columns, window functions and full-text search. ",
    "Community": "Both projects have active communities and long release histories. ",
}
LONG_TEXT = "\n\n".join(f"[[[H]]]{heading}\n" + text * (2300 // len(text)) for heading, text in SECTION_TEXT.items())
LONG_SITE = FakeSite(
    pages={LONG: (LONG_TEXT, [("Home", "https://compare.example/"), ("Databases compared in depth", LONG + "/more")])},
    results=[{"title": "SQLite vs PostgreSQL", "href": "//duckduckgo.com/l/?uddg=" + urllib.parse.quote(LONG),
              "snippet": "setup, administration and features compared"}],
)
LONG_CHECKLIST = json.dumps({"parts": ["Compare SQLite and PostgreSQL setup and administration",
                                       "Compare SQLite and PostgreSQL features",
                                       "Name three features PostgreSQL has", "Recommend one for a hobby project"]})
ADMIN = "SQLite needs no server and no administration: the database is a single file."
FEATURES = "PostgreSQL supports JSON columns, window functions and full-text search."


def linked(action, parameter, items):
    return json.dumps({"thought_process": "t", "action": action, "parameter": parameter, "checklist_items": items})


def verdict(evidence="", missing="", conditions=(), things=()):
    return json.dumps({"evidence": evidence, "missing": missing, "conditions": list(conditions),
                       "things": [{"name": n, "proof": p} for n, p in things]})


LONG_SCRIPT = [
    LONG_CHECKLIST,
    reply("SEARCH", "sqlite vs postgresql"),
    reply("NAVIGATE", LONG),
    reply("READ_MORE", ""),             # nothing recorded from part 1: the read-on check names the outline's part 4
    reply("READ_MORE", ""),             # repeated plainly: reads part 4 (Administration), not part 2
    linked("DISCOVER", "SQLite is a server that needs a database administrator for every install.", [1]),  # not on the page
    linked("DISCOVER", ADMIN, [1]),     # the page's own words
    reply("READ_MORE", "5"),
    linked("DISCOVER", FEATURES, [2, 3]),
    reply("FINISH", "Use PostgreSQL."),  # the finish check
    reply("FINISH", "Use PostgreSQL."),  # repeated: the coverage check of each item
    verdict("[1] " + ADMIN),
    verdict("[2] " + FEATURES),
    verdict("[2]", things=[("JSON columns", "JSON columns"), ("window functions", "window functions")]),  # 2 of 3
    reply("READ_MORE", "6"),            # sent back: the next turn starts from a fresh prompt
    reply("FINISH", "Use PostgreSQL."),  # the finish check again
    reply("FINISH", "Use PostgreSQL."),  # repeated: the coverage check ran once, so the final result is written
    "PostgreSQL has JSON columns and window functions [2]; SQLite needs no administration [1]. My view: PostgreSQL.",
] + [reply("FINISH", "Use PostgreSQL.")] * 4


def run_scenario(monkeypatch, tmp_path, site, script, objective, objective_check_every=None):
    client, model = fake_client(list(script), prompt_tokens=None)
    real_sleep = asyncio.sleep
    monkeypatch.setattr(agent, "async_playwright", site.playwright)
    monkeypatch.setattr(agent, "AsyncOpenAI", lambda **kwargs: client)
    monkeypatch.setattr(ab.asyncio, "sleep", lambda seconds, *a, **k: real_sleep(0, *a, **k))
    monkeypatch.setattr(builtins, "input", lambda prompt="": objective)
    monkeypatch.chdir(tmp_path)
    if objective_check_every:
        monkeypatch.setattr(agent, "OBJECTIVE_CHECK_EVERY", objective_check_every)
    asyncio.run(ab.main())
    log = (tmp_path / "research_log.txt").read_text(encoding="utf-8")
    report = (tmp_path / ab.REPORT_FILE).read_text(encoding="utf-8")
    return {"calls": model.calls, "research_log": log, "report": report}


SCENARIOS = {
    "full_run": lambda mp, tmp: run_scenario(mp, tmp, SITE, [CHECKLIST_REPLY] + SCRIPT, "test objective"),
    "objective_checks": lambda mp, tmp: run_scenario(mp, tmp, SITE, [CHECKLIST_REPLY] + SCRIPT, "test objective", 3),
    "error_page_and_send_back": lambda mp, tmp: run_scenario(mp, tmp, SITE, [CHECKLIST_REPLY] + [
        reply("SEARCH", "docs a"), reply("NAVIGATE", "https://docs.example/guessed-page"),
        reply("NAVIGATE", "https://docs.example/a"), reply("FINISH", "a"), reply("FINISH", "a"), reply("FINISH", "a"),
        reply("FINISH", "a"), "Final."], "test objective"),
    "stopped_early": lambda mp, tmp: run_scenario(mp, tmp, SITE, [CHECKLIST_REPLY] + SCRIPT[:3], "test objective"),
    "long_page_outline_coverage": lambda mp, tmp: run_scenario(
        mp, tmp, LONG_SITE, LONG_SCRIPT,
        "Compare SQLite and PostgreSQL: setup and administration, and features, naming three features PostgreSQL "
        "has. Then recommend one for a hobby project."),
}


def load_golden():
    if not os.path.exists(GOLDEN):
        return {}
    with open(GOLDEN, encoding="utf-8") as f:
        return json.load(f)


@pytest.mark.parametrize("name", sorted(SCENARIOS))
def test_prompts_match_the_recorded_copy(name, monkeypatch, tmp_path):
    got = SCENARIOS[name](monkeypatch, tmp_path)
    golden = load_golden()
    if UPDATE:
        golden[name] = got
        os.makedirs(os.path.dirname(GOLDEN), exist_ok=True)
        with open(GOLDEN, "w", encoding="utf-8", newline="\n") as f:
            json.dump(dict(sorted(golden.items())), f, ensure_ascii=False, indent=1)
            f.write("\n")
        return
    assert name in golden, f"no recorded copy of '{name}': record it with GOLDEN_UPDATE=1"
    want = golden[name]
    assert len(got["calls"]) == len(want["calls"]), "a different number of model calls"
    for number, (call, recorded) in enumerate(zip(got["calls"], want["calls"], strict=True)):
        assert call == recorded, f"model call {number} differs from the recorded prompt"
    assert got["research_log"] == want["research_log"]
    assert got["report"] == want["report"]
