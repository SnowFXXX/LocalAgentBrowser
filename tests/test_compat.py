"""The compatibility test (tools/compat.py): its verdicts, its summary, and the checks it runs with a model."""
import asyncio
import importlib.util
import json
import os

from conftest import TESTS_DIR
from fakes import fake_client

ROOT = os.path.dirname(TESTS_DIR)
spec = importlib.util.spec_from_file_location("compat", os.path.join(ROOT, "tools", "compat.py"))
compat = importlib.util.module_from_spec(spec)
spec.loader.exec_module(compat)


def check(name, passed):
    return {"check": name, "passed": passed, "optional": name in compat.OPTIONAL}


class TestVerdict:
    def test_every_check_passed_is_compatible(self):
        assert compat.verdict([check("split", True), check("discover", True), check("live task", True)]) == "compatible"

    def test_only_checks_the_agent_goes_on_without_failed_is_compatible_with_limits(self):
        assert compat.verdict([check("split", False), check("view", False), check("discover", True)]) == \
            "compatible, with limits (split, view failed)"

    def test_a_check_the_agent_needs_failed_is_not_compatible_and_named(self):
        assert compat.verdict([check("split", False), check("discover", False), check("live task", True)]) == \
            "not compatible (discover failed)"


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


def test_a_model_list_skips_comments_and_blank_lines(tmp_path, monkeypatch):
    listed = tmp_path / "models.txt"
    listed.write_text("# a comment\nmodel-a\n\nmodel-b   # note\n", encoding="utf-8")
    seen = []
    monkeypatch.setattr(compat, "run_list", lambda models, args: seen.extend(models))
    monkeypatch.setattr("sys.argv", ["compat.py", "--models", str(listed)])
    compat.main()
    assert seen == ["model-a", "model-b"]
