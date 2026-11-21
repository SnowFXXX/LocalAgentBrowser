"""SimpleQA baseline for LocalAgentBrowser: a fixed random sample, run as ordinary objectives, graded by hand.

SimpleQA (OpenAI, MIT license) is 4,326 short factual questions, each with one reference answer. Its grades are
CORRECT, INCORRECT and NOT_ATTEMPTED (the answer says it could not find out): a direct measure of "a verifiable answer
or honestly can't". The dataset file is not in the repository; download it to data/ (see README.md).

  python benchmarks/simpleqa/run.py sample                  draws the sample (seed and size recorded in sample.json)
  python benchmarks/simpleqa/run.py sample --size 200 --simple-evals --out sample-200.json
                                                            the 200 questions simple-evals runs with --examples 200
  python benchmarks/simpleqa/run.py run --part 1 --of 2     runs this PC's share (questions 1, 3, 5, ... of the sample)
  python benchmarks/simpleqa/run.py sheet                   writes grading.md from every run folder, for grading

Every question runs through tools/live_test.py with the same code as any objective, the sites that republish
SimpleQA blocked (LAB_BLOCKED_SITES), and a time limit. Grades go in grades.json, one per question, by hand, with a
note for every INCORRECT and every judgement call.
"""
import argparse
import csv
import datetime
import json
import os
import random
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, ROOT)
from localagentbrowser import watch  # noqa: E402  (the watch window beside each question's browser)
DATA = os.path.join(HERE, "data", "simple_qa_test_set.csv")
DATA_SHA256 = "feee3f7e7db3617e94e8fcf1977b756ec420ef8568f4e0fcbbe0e92e9d5fc032"  # As downloaded 2026-11-06 (MD5 = server's)
SAMPLE = os.path.join(HERE, "sample.json")
RUNS = os.path.join(HERE, "runs")
SEED, SIZE = 20261106, 50
MINUTES = 60
# Pages that republish SimpleQA's questions with their answers
BLOCKED = ["huggingface.co/datasets", "github.com/openai/simple-evals", "openaipublic.blob.core.windows.net",
           "kaggle.com/datasets", "paperswithcode.com", "openai.com/index/introducing-simpleqa",
           "cdn.openai.com/papers/simpleqa", "arxiv.org/abs/2411.04368", "arxiv.org/pdf/2411.04368"]


def load():
    import hashlib
    with open(DATA, "rb") as f:
        raw = f.read()
    if hashlib.sha256(raw).hexdigest() != DATA_SHA256:
        raise SystemExit(f"{DATA} is not the file this baseline was drawn from (SHA-256 differs)")
    return list(csv.DictReader(raw.decode("utf-8").splitlines()))


def sample(size=SIZE, simple_evals=False, out_path=SAMPLE):
    """The first sample (sample.json): 50 questions, seed 20261106, in dataset order. With simple_evals, the subset
    OpenAI's simple-evals runs with --examples N: random.Random(0).sample over the dataset rows, in the order drawn, so
    anyone running simple-evals on the same N gets the same questions."""
    rows = load()
    if simple_evals:
        picked, seed, method = random.Random(0).sample(range(len(rows)), size), 0, "simple-evals: random.Random(0).sample(rows, size)"
    else:
        picked, seed, method = sorted(random.Random(SEED).sample(range(len(rows)), size)), SEED, "random.Random(seed).sample, sorted"
    out = {"seed": seed, "size": size, "method": method, "dataset_sha256": DATA_SHA256,
           "drawn": datetime.date.today().isoformat(),
           "questions": [{"n": i + 1, "index": k, "problem": rows[k]["problem"], "answer": rows[k]["answer"],
                          "topic": re.search(r"'topic': '([^']*)'", rows[k]["metadata"]).group(1)}
                         for i, k in enumerate(picked)]}
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"{size} questions drawn ({method}) -> {out_path}")


def final_result(folder):
    path = os.path.join(folder, "research_report.md")
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        text = f.read()
    if "## Final result" not in text:
        return None
    return text.split("## Final result", 1)[1].split("\n## ", 1)[0].strip()


def run(part, of, model):
    with open(SAMPLE, encoding="utf-8") as f:
        questions = json.load(f)["questions"]
    mine = [q for q in questions if (q["n"] - 1) % of == part - 1]
    env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1", LMSTUDIO_MODEL=model,
               LAB_BLOCKED_SITES=",".join(BLOCKED))
    watch.start(f"SimpleQA · {model}", total=len(mine))  # LAB_WATCH=0 for none
    for q in mine:
        folder = os.path.join(RUNS, f"q{q['n']:02d}")
        if final_result(folder) is not None:
            print(f"q{q['n']:02d}: already done", flush=True)
            continue
        os.makedirs(folder, exist_ok=True)
        print(f"===== SIMPLEQA q{q['n']:02d} ({datetime.datetime.now():%H:%M}): {q['problem']}", flush=True)
        with open(os.path.join(folder, "console.txt"), "w", encoding="utf-8") as console:
            process = subprocess.Popen([sys.executable, "-u", os.path.join(ROOT, "tools", "live_test.py"),
                                        q["problem"], "--minutes", str(MINUTES), "--out", folder],
                                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                       env=watch.task_env(env, f"q{q['n']:02d}", folder, model),
                                       encoding="utf-8", errors="replace")
            for line in process.stdout:
                console.write(line)
                console.flush()
            process.wait()
        result = final_result(folder)
        print(f"===== RESULT q{q['n']:02d}: {'finished' if result else 'no final result'}", flush=True)
    watch.finish()


def sheet():
    with open(SAMPLE, encoding="utf-8") as f:
        questions = json.load(f)["questions"]
    grades = {}
    if os.path.exists(os.path.join(HERE, "grades.json")):
        with open(os.path.join(HERE, "grades.json"), encoding="utf-8") as f:
            grades = json.load(f)
    lines = ["# SimpleQA baseline: answers to grade", "", "Grades: CORRECT, INCORRECT, NOT_ATTEMPTED (SimpleQA's own).", ""]
    for q in questions:
        result = final_result(os.path.join(RUNS, f"q{q['n']:02d}"))
        g = grades.get(str(q["n"]), {})
        lines += [f"## q{q['n']:02d} ({q['topic']})", "", f"**Question:** {q['problem']}", "",
                  f"**Reference answer:** {q['answer']}", "", "**LocalAgentBrowser:**", "",
                  result if result is not None else "_(not run yet, or no final result)_", "",
                  f"**Grade:** {g.get('grade', '—')}" + (f" — {g['note']}" if g.get("note") else ""), ""]
    with open(os.path.join(HERE, "grading.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    counted = [g["grade"] for g in grades.values() if g.get("grade")]
    if counted:
        n = len(counted)
        c, i, na = (counted.count(x) for x in ("CORRECT", "INCORRECT", "NOT_ATTEMPTED"))
        attempted = c + i
        print(f"graded {n}: correct {c} ({c / n:.0%}), incorrect {i} ({i / n:.0%}), not attempted {na} ({na / n:.0%}); "
              f"correct given attempted {c / attempted:.0%}" if attempted else f"graded {n}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="command", required=True)
    s = sub.add_parser("sample")
    s.add_argument("--size", type=int, default=SIZE)
    s.add_argument("--simple-evals", action="store_true", help="draw the subset simple-evals uses for --examples SIZE")
    s.add_argument("--out", default=SAMPLE)
    r = sub.add_parser("run")
    r.add_argument("--part", type=int, default=1)
    r.add_argument("--of", type=int, default=1)
    r.add_argument("--model", default="qwen2.5-7b-instruct")
    sub.add_parser("sheet")
    args = ap.parse_args()
    if args.command == "sample":
        sample(args.size, args.simple_evals, args.out)
    elif args.command == "sheet":
        sheet()
    else:
        run(args.part, args.of, args.model)
