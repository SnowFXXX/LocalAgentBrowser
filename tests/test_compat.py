"""The compatibility test (tools/compat.py): its verdicts, its summary, and the checks it runs with a model."""
import asyncio
import importlib.util
import json
import os
import types

from conftest import TESTS_DIR
from fakes import fake_client

ROOT = os.path.dirname(TESTS_DIR)
spec = importlib.util.spec_from_file_location("compat", os.path.join(ROOT, "tools", "compat.py"))
compat = importlib.util.module_from_spec(spec)
spec.loader.exec_module(compat)


def check(name, passed, detail="", error=False):
    return {"check": name, "passed": passed, "detail": detail, "error": error}


class TestVerdict:
    def test_every_check_passed_is_compatible(self):
        assert compat.verdict([check("split", True), check("discover", True), check("live task", True)]) == "compatible"

    def test_a_quick_check_chosen_differently_with_the_live_task_passed_is_compatible_with_limits(self):
        # Mistral Small went straight to FINISH on the page shown, then finished the live task
        assert compat.verdict([check("split", False), check("discover", False, "[FINISH] Kilimanjaro; not a DISCOVER"),
                               check("live task", True)]) == "compatible, with limits (split, discover failed)"

    def test_an_error_is_not_compatible_and_named(self):
        assert compat.verdict([check("split", True), check("discover", False, "NoReply: no reply", error=True)]) == \
            "not compatible (discover failed)"

    def test_the_live_task_failed_is_not_compatible(self):
        assert compat.verdict([check("view", False), check("live task", False, "did not finish")]) == \
            "not compatible (live task failed)"

    def test_hard_failures_are_errors_invalid_json_no_result_and_a_refused_retry(self):
        assert compat.is_hard(check("discover", False, "ValueError: x", error=True))
        assert compat.is_hard(check("search page", False, "No valid JSON in the reply"))
        assert compat.is_hard(check("final result", False, "The model did not ... (no result, no citation)"))
        assert compat.is_hard(check("cut-off retry", False, "BadRequestError: roles must alternate"))
        assert not compat.is_hard(check("final result", False, "Kilimanjaro is 5,895 m [1] (a height missing)"))
        assert not compat.is_hard(check("discover", False, "[FINISH] ...; not a DISCOVER"))
        assert not compat.is_hard(check("cut-off retry", True))


def test_the_summary_lists_each_model_with_its_checks_live_task_and_cost():
    rows = [{"model": "a", "verdict": "compatible", "minutes": 3.5, "cost": 0.0012,
             "checks": [check("split", True), check("discover", True), dict(check("live task", True))]},
            {"model": "b", "verdict": "not tested (download failed)"}]
    text = compat.summary_markdown(rows, "local", budget=5)
    assert "| a | compatible | 2/2 | pass | 3.5 | $0.0012 |" in text
    assert "| b | not tested (download failed) | - | - | - | - |" in text
    assert "Spent $0.0012 of the $5 budget." in text


def test_the_cut_off_check_passes_when_every_retry_is_accepted():
    # The fake refuses any request whose turns don't alternate, as Gemma 3 4B's chat template did
    cut = ('{"thought_process": "I will read this page carefully and then', "length")
    client, model = fake_client([cut] * 3)
    ok, detail = asyncio.run(compat.check_cut_off(client, [{"text": "the height", "discoveries": []}]))
    assert ok and "retries accepted" in detail
    assert len(model.calls) == 3 and model.limits == [compat.CUT_OFF_TOKENS] * 3
    assert compat.agent.TURN_MAX_TOKENS != compat.CUT_OFF_TOKENS, "the limit is put back"


def test_the_final_result_check_wants_both_heights_cited_and_none_cut():
    good = json.dumps({"result": "Kilimanjaro is 5,895 m (19,341 ft) high [1]."})
    client, _ = fake_client([good])
    ok, detail = asyncio.run(compat.check_result(client, [{"text": "the height", "discoveries": [1]}]))
    assert ok, detail
    missing = json.dumps({"result": "Kilimanjaro is 5,895 m high [1]."})
    client, _ = fake_client([missing])
    ok, detail = asyncio.run(compat.check_result(client, [{"text": "the height", "discoveries": [1]}]))
    assert not ok and "a height missing" in detail


def test_the_live_task_passes_on_a_cited_result_and_notes_what_it_lacks(tmp_path, monkeypatch):
    found = {"turn": 2, "action": "DISCOVER", "source": compat.PAGE_URL, "extracted_data": compat.FOUND}

    def live(result):
        journal = [found, {"turn": 3, "action": "FINISH", "finding": result}]
        monkeypatch.setattr(compat.rg, "run_task", lambda *args: None)
        monkeypatch.setattr(compat.rg, "load_run", lambda folder: ("", journal, "", None))
        return compat.live_task(str(tmp_path), 1, "model")
    # GPT-5.4 nano named the summit cone: the model's answer, a note, not a failure of the agent with the model
    kibo = live("Kibo is the highest peak, at 5,895 m [1].")
    assert kibo["passed"] and "result lacks" in kibo["detail"]
    assert live("Kilimanjaro is 5,895 m (19,341 ft) high [1].")["detail"] == "finished with both heights, cited"
    uncited = live("Kilimanjaro is 5,895 m (19,341 ft) high.")
    assert not uncited["passed"] and uncited["detail"].startswith("no citation")
    assert not live(compat.ab.NO_RESULT + "\n\n[1] " + compat.FOUND)["passed"]


def test_the_coverage_check_wants_the_item_judged_answered_but_not_the_shared_words_rule(monkeypatch):
    item = "Item 1 (the height): "
    for gaps, passed in ((([], [], []), True), (([item + "nothing in DISCOVERIES SO FAR answers it"], [1], []), False),
                         (([compat.ab.shared_gap(1, "the height")], [1], []), True)):
        async def coverage_gaps(*args, gaps=gaps, **kwargs):
            return gaps
        monkeypatch.setattr(compat.ab, "coverage_gaps", coverage_gaps)
        ok, _ = asyncio.run(compat.check_coverage(None, [{"text": "the height", "discoveries": []}]))
        assert ok == passed, gaps


def test_after_two_checks_the_server_failed_the_rest_are_not_run():
    class Down:
        def __init__(self):
            self.calls = 0
            self.client = types.SimpleNamespace(chat=types.SimpleNamespace(completions=self))
            self.prompt_tokens = self.completion_tokens = 0

        async def create(self, **kwargs):
            self.calls += 1
            if kwargs.get("response_format", {}).get("json_schema", {}).get("name") == "checklist":
                return types.SimpleNamespace(usage=None, choices=[types.SimpleNamespace(
                    message=types.SimpleNamespace(content='{"parts": ["the height"]}'), finish_reason="stop")])
            raise compat.agent.NoReply("no reply in it")
    down = Down()
    results = asyncio.run(compat.quick_checks(down))
    after = [r for r in results if r["detail"].startswith("not run")]
    assert [r["check"] for r in results][:3] == ["split", "search page", "discover"]
    assert len(after) == len(results) - 3 and all(r["error"] for r in after)
    assert down.calls == 3  # the split, then one call per failed check


def test_a_model_list_skips_comments_and_blank_lines(tmp_path, monkeypatch):
    listed = tmp_path / "models.txt"
    listed.write_text("# a comment\nmodel-a\n\nmodel-b   # note\n", encoding="utf-8")
    seen = []
    monkeypatch.setattr(compat, "run_list", lambda models, args: seen.extend(models))
    monkeypatch.setattr("sys.argv", ["compat.py", "--models", str(listed)])
    compat.main()
    assert seen == ["model-a", "model-b"]
