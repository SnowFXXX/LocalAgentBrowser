"""Compatibility test: whether a model works with LocalAgentBrowser, quickly and cheaply.

Usage:
    python tools/compat.py MODEL [MODEL ...] [--no-live] [--minutes N]
    python tools/compat.py --models FILE [--no-live] [--minutes N]
FILE has one model per line ("#" starts a comment). In LM Studio, a line is the model's key as "lms ls" lists it, or a
Hugging Face download ("https://huggingface.co/<repo>@<quantization>"), which is downloaded for the test and deleted
after it (models already on the PC are kept).

Each model is tested in a process of its own, with the agent's own code, in two parts:
1. Quick checks, about ten model calls and no browser, with a fixed page for every model: split the objective into a
   checklist; choose an action on a search results page; DISCOVER the answer from a page shown (it must pass the
   grounding check); check a checklist item's coverage; write a cited final result whose numbers the discoveries give;
   write the "My view:" paragraph; and a reply forced to stop at its length limit, retried (the model server must
   accept every request).
2. One live task with the browser: the regression suite's factual task (Mount Kilimanjaro's height in metres and feet).

Only a hard failure ends the test before the live task: an error, a reply with no valid JSON, no final result, or a
refused retry. The result is "compatible" when every check passes and the live task finishes with a cited result of
the model's own; "compatible, with limits" when the live task passes but a quick check failed (it is named: the agent
works, with that step weaker); and "not compatible" when a hard failure happened or the live task did not pass, with
that named. What the live result says (a height missing) is a note in its detail, not a failure. Results go to LAB_COMPAT_DIR (default
C:\\LocalAgentBrowser-compat-runs), a new folder per test run: summary.md and results.json for the list, and a folder
per model with its result.json, console.txt and the live task's run.
"""
# cloud:start
# With --cloud, the models are OpenRouter's (tools/compat_models_cloud.txt lists the tested ones), with the key from the
# OPENROUTER_API_KEY environment variable, never printed or stored. Each model's cost is read from OpenRouter before and
# after it; the list stops when the budget (--budget, default $5) is spent, or when one model costs more than OVERRUN
# times its estimate.
# cloud:end
import argparse
import asyncio
import contextlib
import datetime
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))
import agent_browser as ab  # noqa: E402
import regression as rg  # noqa: E402
from localagentbrowser import agent  # noqa: E402

RESULTS_DIR = os.environ.get("LAB_COMPAT_DIR", r"C:\LocalAgentBrowser-compat-runs")
LMS = next((p for p in (os.path.expanduser(r"~\.lmstudio\bin\lms.exe"), shutil.which("lms") or "") if p and os.path.exists(p)), "lms")
LMS_MODELS = os.path.expanduser(r"~\.lmstudio\models")
LIVE_MINUTES = 30
CUT_OFF_TOKENS = 40  # The forced cut-off check's reply limit
REMOTE_CALL_SECONDS = 300  # Longest wait for one quick-check call to a server that is not on this PC

# The fixed task every model gets: the regression suite's factual question, on a page that answers it
OBJECTIVE = "What is the tallest mountain in Africa? Give its height in meters and in feet."
SEARCH_URL = "https://html.duckduckgo.com/html/?q=tallest+mountain+in+africa"
PAGE_URL = "https://en.wikipedia.org/wiki/Mount_Kilimanjaro"
SEARCH_RESULTS = [
    ("Mount Kilimanjaro - Wikipedia", PAGE_URL,
     "Mount Kilimanjaro is a large dormant stratovolcano in Tanzania. It is the highest mountain in Africa and the "
     "highest free-standing mountain above sea level in the world."),
    ("List of highest major summits of Africa - Wikipedia", "https://en.wikipedia.org/wiki/List_of_highest_major_summits_of_Africa",
     "The highest African mountain is Kilimanjaro, which has three peaks, named Kibo, Mawenzi and Shira."),
    ("The Tallest Mountains in Africa - WorldAtlas", "https://www.worldatlas.com/articles/what-is-the-tallest-mountain-of-africa.html",
     "Learn about the highest peaks in Africa, including Kilimanjaro, Kenya, Ruwenzori, and more."),
]
PAGE_TEXT = ("Mount Kilimanjaro\n\nMount Kilimanjaro is a large dormant stratovolcano in Tanzania. It is the highest "
             "mountain in Africa and the highest free-standing mountain above sea level in the world, at 5,895 m "
             "(19,341 ft) above sea level and about 4,900 m (16,100 ft) above its plateau base.\n\nKilimanjaro has three "
             "volcanic cones: Kibo, the highest; Mawenzi, at 5,149 m (16,893 ft); and Shira, the lowest, at 4,005 m "
             "(13,140 ft).\n\nThe first people known to have reached its summit were Hans Meyer and Ludwig Purtscheller "
             "in 1889.")
FOUND = ("Mount Kilimanjaro is a large dormant stratovolcano in Tanzania. It is the highest mountain in Africa and the "
         "highest free-standing mountain above sea level in the world, at 5,895 m (19,341 ft) above sea level.")
VIEW_OBJECTIVE = ("Compare SQLite and PostgreSQL for a small web application, then recommend one for a hobby project "
                  "with a few hundred users.")
VIEW_FOUND = ("SQLite is an embedded database: the whole database is one file, and it needs no server. It allows one "
              "writer at a time.",
              "PostgreSQL is a database server. It uses multiversion concurrency control (MVCC), so many users can "
              "write at the same time.")


def search_environment():
    lines = ["Search Results:"]
    for i, (title, url, snippet) in enumerate(SEARCH_RESULTS, 1):
        lines += [f"[{i}] {title}", f"    URL: {url}", f"    {snippet}", ""]
    return "\n".join(lines)


def page_environment():
    return f"Page Text (part 1 of 1):\n{PAGE_TEXT}\n\nLinks On Page:\n- Mawenzi -> https://en.wikipedia.org/wiki/Mawenzi"


def discovery(text, items):
    return {"turn": 3, "action": "DISCOVER", "source": PAGE_URL, "extracted_data": text, "checklist_items": items}


class Counted:
    """The model client, counting calls and tokens."""

    def __init__(self, client):
        self.calls = self.prompt_tokens = self.completion_tokens = 0
        create = client.chat.completions.create

        async def counted(*args, **kwargs):
            response = await create(*args, **kwargs)
            usage = getattr(response, "usage", None)
            self.calls += 1
            self.prompt_tokens += getattr(usage, "prompt_tokens", 0) or 0
            self.completion_tokens += getattr(usage, "completion_tokens", 0) or 0
            return response
        client.chat.completions.create = counted
        self.client = client


@contextlib.contextmanager
def fixed_page(environment):
    """The agent shown a fixed page (no browser), for the quick checks; its own page reader is put back after."""
    async def fixed_environment(page, url, page_state, visited=frozenset(), shown_links=None):
        return environment
    real, agent.build_environment = agent.build_environment, fixed_environment
    try:
        yield
    finally:
        agent.build_environment = real


async def turn(client, current_url, environment, state, journal, checklist):
    tried = {"queries": ["tallest mountain in africa"], "urls": [] if current_url == SEARCH_URL else [current_url],
             "checklist": checklist, "objective": OBJECTIVE}
    with fixed_page(environment):
        decision, refused = await ab.run_agent_turn(client, None, OBJECTIVE, journal, current_url, tried, state,
                                                    ab.new_conversation())
    action = str(decision.get("action", "")).upper()
    parameter = ab.without_control_characters(decision.get("parameter", "")).strip()
    still_refused = ab.check_decision(action, parameter, journal, tried, state, current_url,
                                      decision.get("thought_process", ""))
    return action, parameter, refused, still_refused


async def check_search_page(client, checklist):
    state = {"url": ab.normalize_url(SEARCH_URL), "parts": [], "index": 0, "seen_parts": set(),
             "results": [(t, u) for t, u, _ in SEARCH_RESULTS]}
    action, parameter, refused, still = await turn(client, SEARCH_URL, search_environment(), state, [], checklist)
    ok = action in ("NAVIGATE", "SEARCH", "READ_MORE", "DISCOVER", "FINISH") and not (still and still[0].startswith("BLOCKED"))
    return ok, f"[{action}] {parameter[:100]}" + (f"; {len(refused)} refused first" if refused else "")


async def check_discover(client, checklist):
    state = {"url": ab.normalize_url(PAGE_URL), "parts": [PAGE_TEXT], "index": 0, "seen_parts": {0}, "visit_discoveries": 0}
    action, parameter, refused, still = await turn(client, PAGE_URL, page_environment(), state, [], checklist)
    has_height = bool(re.search(r"5,?895|19,?341", parameter))
    accepted = not still or still[0].startswith("CONFIRM")
    ok = action == "DISCOVER" and has_height and accepted
    why = "" if ok else ("; not a DISCOVER" if action != "DISCOVER" else "; no height" if not has_height else f"; refused: {still[0]}")
    return ok, f"[{action}] {parameter[:120]}{why}" + (f"; {len(refused)} refused first" if refused else "")


async def check_coverage(client, checklist):
    journal = [discovery(FOUND, list(range(1, len(checklist) + 1)))]
    for item in checklist:
        item["discoveries"] = [1]
    unchecked = set()
    gaps = await ab.coverage_gaps(client, OBJECTIVE, checklist, journal, evidence={}, unchecked=unchecked)
    return not unchecked, f"{len(checklist)} item(s) checked; gaps {gaps or 'none'}; unchecked {sorted(unchecked) or 'none'}"


async def check_result(client, checklist):
    journal = [discovery(FOUND, list(range(1, len(checklist) + 1)))]
    text = await ab.compose_final_result(client, OBJECTIVE, journal, checklist)
    cited = bool(ab.CITATION.search(text))
    both = bool(re.search(r"5,?895", text) and re.search(r"19,?34[01]", text))
    cut = bool(ab.LEFT_OUT_NOTE.search(text))
    ok = text != ab.NO_RESULT and cited and both and not cut
    why = [w for w, bad in (("no result", text == ab.NO_RESULT), ("no citation", not cited),
                            ("a height missing", not both), ("numbers cut by the checks", cut)) if bad]
    return ok, (" ".join(text.split())[:200] + (f" ({', '.join(why)})" if why else ""))


async def check_view(client):
    journal = [discovery(VIEW_FOUND[0], [1]), discovery(VIEW_FOUND[1], [1])]
    text = await ab.compose_final_result(client, VIEW_OBJECTIVE, journal)
    at = text.lower().find("my view:")
    return at >= 0, (" ".join(text[at:].split())[:200] if at >= 0 else "no 'My view:' paragraph")


async def check_cut_off(client, checklist):
    """Every reply stopped at a tiny limit: the cut-off retries must be accepted by the server (Gemma 3 4B's chat
    template refused two user messages in a row, and every task that hit it ended). Running out of retries is fine."""
    state = {"url": ab.normalize_url(PAGE_URL), "parts": [PAGE_TEXT], "index": 0, "seen_parts": {0}}
    tried = {"queries": [], "urls": [PAGE_URL], "checklist": checklist, "objective": OBJECTIVE}
    with fixed_page(page_environment()):
        messages = await agent.build_full_prompt(None, OBJECTIVE, [], PAGE_URL, tried, state, "")
    limit, agent.TURN_MAX_TOKENS = agent.TURN_MAX_TOKENS, CUT_OFF_TOKENS
    try:
        await agent.request_decision(client, messages, [], tried, state, PAGE_URL)
        return True, "answered within the forced limit"
    except ValueError as err:
        return True, f"retries accepted ({err})"
    finally:
        agent.TURN_MAX_TOKENS = limit


async def quick_checks(counted):
    client, results = counted.client, []

    async def run(name, check):
        calls, started, error = counted.calls, time.time(), False
        try:
            ok, detail = await check
        except Exception as err:  # A failed check is a result, not the end of the test
            ok, detail, error = False, f"{type(err).__name__}: {str(err)[:300]}", True
        results.append({"check": name, "passed": ok, "detail": detail, "error": error,
                        "calls": counted.calls - calls, "seconds": round(time.time() - started, 1)})
        print(f"   {'PASS' if ok else 'FAIL'} {name}: {detail}", flush=True)

    started = time.time()
    checklist = await ab.split_objective(client, OBJECTIVE)
    texts = [i["text"] for i in checklist]
    results.append({"check": "split", "passed": texts != [OBJECTIVE], "calls": counted.calls,
                    "seconds": round(time.time() - started, 1),
                    "detail": f"{len(texts)} item(s): " + " | ".join(t[:80] for t in texts)})
    print(f"   {'PASS' if results[0]['passed'] else 'FAIL'} split: {results[0]['detail']}", flush=True)
    await run("search page", check_search_page(client, [dict(i, discoveries=[]) for i in checklist]))
    await run("discover", check_discover(client, [dict(i, discoveries=[]) for i in checklist]))
    await run("coverage", check_coverage(client, [dict(i, discoveries=[]) for i in checklist]))
    await run("final result", check_result(client, [dict(i, discoveries=[1]) for i in checklist]))
    await run("view", check_view(client))
    await run("cut-off retry", check_cut_off(client, [dict(i, discoveries=[]) for i in checklist]))
    return results


def live_task(folder, minutes, model):
    task = next(t for t in rg.TASKS if t["id"] == "factual")
    os.makedirs(folder, exist_ok=True)
    rg.run_task(task, folder, minutes, model)
    _, journal, console, checklist = rg.load_run(folder)
    score = rg.score_run(task, journal, console, checklist)
    result = next((str(e.get("finding", "")) for e in reversed(journal)
                   if e.get("action") == "FINISH" and not e.get("writing")), "")
    # Compatible means the agent works with the model: it finished with a result of its own, cited. What the result
    # says (a height missing, a peak named instead of the mountain) is the model's answer, kept as a note
    why = ("did not finish" if not score["finished"] else "no result written" if result.startswith(ab.NO_RESULT)
           else "no citation" if not rg.cited_numbers(result) else "")
    return {"check": "live task", "passed": not why, "minutes": minutes,
            "detail": "; ".join(([why] if why and why != "did not finish" else []) + score["problems"])
            or "finished with both heights, cited",
            "model_minutes": score["model_minutes"], "model_calls": score["model_calls"]}


def is_hard(result):
    """A failure that means the model or its server does not work with the agent: an error, no valid JSON, no final
    result, or a retry refused. These end the test before the live task; a model that only chose differently in a
    quick check (a FINISH where the check expected a DISCOVER) still gets the live task, which decides."""
    detail = str(result.get("detail", ""))
    return not result["passed"] and (result.get("error") or "No valid JSON" in detail
                                     or result["check"] == "cut-off retry"
                                     or (result["check"] == "final result" and "(no result" in detail))


def verdict(results):
    failed = [r for r in results if not r["passed"]]
    if not failed:
        return "compatible"
    hard = [r["check"] for r in failed if is_hard(r) or r["check"] == "live task"]
    if hard:
        return "not compatible (" + ", ".join(hard) + " failed)"
    return "compatible, with limits (" + ", ".join(r["check"] for r in failed) + " failed)"


def test_one(model, folder, live, minutes):
    """Runs in its own process, with LMSTUDIO_MODEL and the server's settings set before the agent was imported."""
    os.makedirs(folder, exist_ok=True)
    started = time.time()
    # A server not on this PC answers a quick check within minutes, or is hung: LFM 2.5's test waited 45 minutes with
    # nothing spent. On this PC a big model on a slow CPU may need the agent's full wait
    timeout = ab.MODEL_TIMEOUT_SECONDS if agent.is_local_server() else REMOTE_CALL_SECONDS
    counted = Counted(ab.with_retries(ab.AsyncOpenAI(base_url=ab.BASE_URL, api_key=ab.API_KEY, max_retries=0,
                                                     timeout=timeout,
                                                     # cloud:start
                                                     default_headers=ab.EXTRA_HEADERS or None,
                                                     # cloud:end
                                                     )))
    print(f"Quick checks of {model}:", flush=True)
    results = asyncio.run(quick_checks(counted))
    workarounds = []
    # cloud:start
    workarounds += [f"{p} not sent (no provider takes it)" for p in sorted(agent.UNSUPPORTED_PARAMETERS)]
    # cloud:end
    quick = {"calls": counted.calls, "prompt_tokens": counted.prompt_tokens, "completion_tokens": counted.completion_tokens,
             "minutes": round((time.time() - started) / 60, 1)}
    if live and not any(is_hard(r) for r in results):
        print(f"Live task ({minutes:g} minutes at most):", flush=True)
        results.append(live_task(os.path.join(folder, "factual"), minutes, model))
        print(f"   {'PASS' if results[-1]['passed'] else 'FAIL'} live task: {results[-1]['detail']}", flush=True)
    result = {"model": model, "verdict": verdict(results) if live else "quick checks only: " + verdict(results),
              "checks": results, "quick": quick, "workarounds": workarounds, "version": rg.project_version(),
              "server": ab.BASE_URL, "tested": datetime.datetime.now().isoformat(timespec="minutes"),
              "minutes": round((time.time() - started) / 60, 1)}
    with open(os.path.join(folder, "result.json"), "w", encoding="utf-8") as f:
        json.dump(result, f, indent=1, ensure_ascii=False)
    print(f"Result: {result['verdict']}", flush=True)


# --- The list: one process per model, LM Studio's models loaded (and downloaded) in turn ---

def lms(*args, timeout=3600):
    return subprocess.run([LMS, *args], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)


def local_models():
    out = lms("ls", "--json").stdout
    return {m["modelKey"]: m for m in json.loads(out or "[]") if m.get("type") == "llm"}


def prepare_local(line):
    """The model key to test, and the folder to delete after it if this run downloaded it."""
    if not line.startswith("https://"):
        return line, None
    # "https://huggingface.co/lmstudio-community/Qwen3.5-9B-GGUF@Q4_K_M": LM Studio keeps it under that publisher/repo
    repo = line.split("huggingface.co/", 1)[1].split("@")[0].strip("/")

    def found():
        return [m for m in local_models().values() if m["path"].replace("\\", "/").lower().startswith(repo.lower())]
    if not found():
        print(f"Downloading {line} ...", flush=True)
        done = lms("get", line, "-y", timeout=4 * 3600)
        if done.returncode != 0 or not found():
            raise RuntimeError(f"download failed: {(done.stdout + done.stderr)[-300:]}")
    folder = os.path.realpath(os.path.join(LMS_MODELS, *repo.split("/")))
    if not folder.startswith(os.path.realpath(LMS_MODELS) + os.sep) or not os.path.isdir(folder):
        raise RuntimeError(f"unexpected model folder {folder}")
    return found()[0]["modelKey"], folder


def load_local(key):
    lms("unload", "--all")
    loaded = lms("load", key, "--context-length", str(ab.CONTEXT_LENGTH), "-y", timeout=1800)
    if loaded.returncode != 0:
        raise RuntimeError(f"could not load: {(loaded.stdout + loaded.stderr)[-300:]}")


# cloud:start
CLOUD_KEY_URL = "https://openrouter.ai/api/v1/key"
COST_ESTIMATE_TOKENS = (100_000, 6_000)  # Prompt and reply tokens a model is expected to use at most, for its estimate
OVERRUN = 2.0  # A model costing more than this many times its estimate stops the list


def cloud_spent():
    """What the key has spent so far, in dollars, from OpenRouter (the key itself is never printed or stored)."""
    request = urllib.request.Request(CLOUD_KEY_URL, headers={"Authorization": f"Bearer {os.environ['OPENROUTER_API_KEY']}"})
    with urllib.request.urlopen(request, timeout=30) as response:
        return float(json.load(response)["data"]["usage"])


def settled_spent(wait=15, steady=2, most=180):
    """The key's spending once OpenRouter has added the last calls: it adds each call's cost some time after it (one
    diagnosis's $0.0033 was still missing 20 seconds after its last call), so a figure read too soon counted part of
    one model's cost to the next. Read every `wait` seconds until it holds for `steady` readings in a row, for `most`
    seconds at most; a model's cost stays approximate, the list's total is exact."""
    last, held, waited = cloud_spent(), 0, 0
    while waited < most and held < steady:
        time.sleep(wait)
        waited += wait
        now = cloud_spent()
        held = held + 1 if now == last else 0
        last = now
    return last


def cloud_estimates():
    with urllib.request.urlopen(rg.CLOUD_URL + "/models", timeout=30) as response:
        models = json.load(response)["data"]
    prompt, reply = COST_ESTIMATE_TOKENS
    return {m["id"]: float(m["pricing"]["prompt"]) * prompt + float(m["pricing"]["completion"]) * reply for m in models}


def cloud_env():
    key = os.environ.get("OPENROUTER_API_KEY")
    if not key:
        sys.exit("Cloud models need the OPENROUTER_API_KEY environment variable (set it yourself, never in a file).")
    return dict(LMSTUDIO_BASE_URL=rg.CLOUD_URL, LMSTUDIO_API_KEY=key, LMSTUDIO_EXTRA_BODY=json.dumps(rg.CLOUD_RULES),
                LMSTUDIO_EXTRA_HEADERS=json.dumps(rg.CLOUD_HEADERS))
# cloud:end


def summary_markdown(rows, where, budget=None):
    lines = [f"# Compatibility test, {where} models", "",
             f"LocalAgentBrowser {rg.project_version()}, {datetime.date.today().isoformat()}. Quick checks (about ten model "
             "calls on a fixed page) and one live task (the regression suite's factual question).", "",
             "| Model | Result | Quick checks | Live task | Minutes | Cost |", "|---|---|---|---|---|---|"]
    for r in rows:
        checks = [c for c in r.get("checks", []) if c["check"] != "live task"]
        live = next((c for c in r.get("checks", []) if c["check"] == "live task"), None)
        quick = f"{sum(c['passed'] for c in checks)}/{len(checks)}" if checks else "-"
        cost = f"${r['cost']:.4f}" if r.get("cost") is not None else "-"
        lines.append(f"| {r['model']} | {r['verdict']} | {quick} | {('pass' if live['passed'] else 'fail') if live else '-'} "
                     f"| {r.get('minutes', '-')} | {cost} |")
    if budget is not None:
        lines += ["", f"Spent ${sum(r.get('cost') or 0 for r in rows):.4f} of the ${budget:g} budget."]
    return "\n".join(lines) + "\n"


def run_list(models, args):
    where = "local"
    env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
    # cloud:start
    where = "cloud" if args.cloud else where
    if args.cloud:
        env.update(cloud_env())
        estimates = cloud_estimates()
    # cloud:end
    folder = os.path.join(RESULTS_DIR, f"{datetime.datetime.now():%Y%m%d-%H%M}_{where}")
    os.makedirs(folder, exist_ok=True)
    rows = []
    for n, line in enumerate(models, 1):
        print(f"\n===== {n}/{len(models)}: {line}", flush=True)
        row, delete = {"model": line, "verdict": "not tested"}, None
        try:
            model = line
            if where == "local":
                model, delete = prepare_local(line)
                row["model"] = model
                load_local(model)
            # cloud:start
            spent_before = cloud_spent() if args.cloud else None
            # cloud:end
            out = os.path.join(folder, re.sub(r"[^\w.-]+", "-", model))
            os.makedirs(out, exist_ok=True)
            command = [sys.executable, "-u", os.path.abspath(__file__), "--one", model, "--out", out,
                       "--minutes", str(args.minutes)] + (["--no-live"] if args.no_live else [])
            with open(os.path.join(out, "console.txt"), "w", encoding="utf-8") as console:
                process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                           encoding="utf-8", errors="replace", env=dict(env, LMSTUDIO_MODEL=model))
                for text in process.stdout:
                    print(text, end="", flush=True)
                    console.write(text)
                    console.flush()  # The log can be watched while the model is tested
                process.wait()
            result_file = os.path.join(out, "result.json")
            if os.path.exists(result_file):
                with open(result_file, encoding="utf-8") as f:
                    row.update(json.load(f))
            else:
                row["verdict"] = "not compatible (the test stopped: see console.txt)"
            # cloud:start
            if args.cloud:
                row["cost"] = round(settled_spent() - spent_before, 5)
                row["estimate"] = round(estimates.get(model, 0), 4)
                print(f"Cost: ${row['cost']:.4f} (estimate ${row['estimate']:.4f})", flush=True)
            # cloud:end
        except Exception as err:  # A model that cannot be prepared is a result too
            row["verdict"] = f"not tested ({err})"
            print(f"Not tested: {err}", flush=True)
        finally:
            if where == "local":
                lms("unload", "--all")
            if delete:
                shutil.rmtree(delete, ignore_errors=True)
                print(f"Deleted the downloaded model ({delete})", flush=True)
        rows.append(row)
        with open(os.path.join(folder, "results.json"), "w", encoding="utf-8") as f:
            json.dump(rows, f, indent=1, ensure_ascii=False)
        with open(os.path.join(folder, "summary.md"), "w", encoding="utf-8") as f:
            f.write(summary_markdown(rows, where, getattr(args, "budget", None)))
        # cloud:start
        if args.cloud:
            spent = sum(r.get("cost") or 0 for r in rows)
            if row.get("estimate") and row.get("cost", 0) > OVERRUN * row["estimate"]:
                print(f"Stopping: {model} cost ${row['cost']:.4f}, over {OVERRUN:g} times its estimate.", flush=True)
                break
            if spent >= args.budget:
                print(f"Stopping: ${spent:.4f} spent, the budget is ${args.budget:g}.", flush=True)
                break
        # cloud:end
    print(f"\nSummary: {os.path.join(folder, 'summary.md')}", flush=True)


def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("models", nargs="*", help="models to test (or --models FILE)")
    parser.add_argument("--models", dest="models_file", help="a file with one model per line")
    parser.add_argument("--no-live", action="store_true", help="quick checks only, no live task")
    parser.add_argument("--minutes", type=float, default=LIVE_MINUTES, help=f"the live task's limit (default {LIVE_MINUTES})")
    parser.add_argument("--one", help=argparse.SUPPRESS)
    parser.add_argument("--out", help=argparse.SUPPRESS)
    # cloud:start
    parser.add_argument("--cloud", action="store_true", help="models on OpenRouter, with OPENROUTER_API_KEY")
    parser.add_argument("--budget", type=float, default=5.0, help="stop once this many dollars are spent (default 5)")
    # cloud:end
    args = parser.parse_args()
    if args.one:
        return test_one(args.one, args.out, not args.no_live, args.minutes)
    models = list(args.models)
    if args.models_file:
        with open(args.models_file, encoding="utf-8") as f:
            models += [line.split("#")[0].strip() for line in f if line.split("#")[0].strip()]
    if not models:
        parser.error("no models given")
    run_list(models, args)


if __name__ == "__main__":
    main()
