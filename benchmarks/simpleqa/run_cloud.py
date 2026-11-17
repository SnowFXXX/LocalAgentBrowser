"""SimpleQA with LocalAgentBrowser on a cloud model through OpenRouter: every question of a sample run as an ordinary
objective, with the sites that republish SimpleQA blocked and the same time limit as the local run (run.py).

  python benchmarks/simpleqa/run_cloud.py MODEL [--sample sample-200.json] [--label 1.66]

The agent is the code in this checkout (tools/live_test.py). Cloud settings are the regression suite's: providers that
honour the JSON format and don't store or train on prompts, plus OpenRouter's app attribution. The key is read from
OPENROUTER_API_KEY and passed on in the environment only; it is never printed or written to a file. Results go to
results/<label>_<model>_<date-time>/, one folder per question, with run_info.json; earlier results are never touched.
"""
import argparse
import datetime
import json
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import run as simpleqa  # noqa: E402  (blocked sites, time limit and result reader shared with the local run)

RULES = {"provider": {"require_parameters": True, "data_collection": "deny"}}
HEADERS = {"HTTP-Referer": "https://snowfxxx.github.io/LocalAgentBrowser/", "X-OpenRouter-Title": "LocalAgentBrowser"}


def git(*args):
    try:
        return subprocess.run(["git", "-C", simpleqa.ROOT, *args], capture_output=True, text=True).stdout.strip()
    except OSError:
        return ""


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("model", help="the model as OpenRouter lists it, e.g. microsoft/phi-4")
    ap.add_argument("--sample", default="sample.json", help="sample file in this folder (default: the first 50)")
    ap.add_argument("--label", default="", help="a short label for the results folder, e.g. the version")
    args = ap.parse_args()
    key = os.environ.get("OPENROUTER_API_KEY")
    if not key:
        sys.exit("OPENROUTER_API_KEY is not set.")
    with open(os.path.join(simpleqa.HERE, args.sample), encoding="utf-8") as f:
        questions = json.load(f)["questions"]
    name = "_".join(p for p in (args.label, args.model.replace("/", "_"),
                                datetime.datetime.now().strftime("%Y%m%d-%H%M")) if p)
    out = os.path.join(simpleqa.HERE, "results", name)
    os.makedirs(out, exist_ok=True)
    info = {"model": args.model, "sample": args.sample, "questions": len(questions), "commit": git("rev-parse", "HEAD"),
            "uncommitted_changes": bool(git("status", "--porcelain", "--", "localagentbrowser", "tools", "agent_browser.py")),
            "minutes_per_question": simpleqa.MINUTES, "blocked_sites": simpleqa.BLOCKED,
            "started": datetime.datetime.now().isoformat(timespec="seconds")}
    env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1", LMSTUDIO_MODEL=args.model,
               LMSTUDIO_BASE_URL="https://openrouter.ai/api/v1", LMSTUDIO_API_KEY=key,
               LMSTUDIO_EXTRA_BODY=json.dumps(RULES), LMSTUDIO_EXTRA_HEADERS=json.dumps(HEADERS),
               LAB_BLOCKED_SITES=",".join(simpleqa.BLOCKED))
    start = time.time()
    print(f"SimpleQA: {len(questions)} questions from {args.sample}, {args.model} on OpenRouter, "
          f"commit {info['commit'][:7]}.\nResults: {out}\n", flush=True)
    for q in questions:
        folder = os.path.join(out, f"q{q['n']:03d}")
        os.makedirs(folder, exist_ok=True)
        t = time.time()
        print(f"===== q{q['n']:03d} ({datetime.datetime.now():%H:%M}): {q['problem']}", flush=True)
        with open(os.path.join(folder, "console.txt"), "w", encoding="utf-8") as console:
            p = subprocess.Popen([sys.executable, "-u", os.path.join(simpleqa.ROOT, "tools", "live_test.py"), q["problem"],
                                  "--minutes", str(simpleqa.MINUTES), "--out", folder],
                                 cwd=simpleqa.ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env, text=True,
                                 encoding="utf-8", errors="replace")
            for line in p.stdout:
                console.write(line)
                console.flush()
            p.wait()
        answer = " ".join((simpleqa.final_result(folder) or "(no final result)").split())
        print(f"     {time.time() - t:.0f} s   answer:    {answer[:600]}\n     reference: {q['answer']}\n", flush=True)
    info.update(finished=datetime.datetime.now().isoformat(timespec="seconds"),
                minutes=round((time.time() - start) / 60, 1))
    with open(os.path.join(out, "run_info.json"), "w", encoding="utf-8") as f:
        json.dump(info, f, indent=1)
    print(f"Done in {info['minutes']} min. Results in {out}. The cost is on your OpenRouter activity page.", flush=True)


if __name__ == "__main__":
    main()
