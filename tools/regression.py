"""Run a fixed set of objectives, one for each kind of task the agent has been tested on, and score every run
the same way, so a change can be judged on all of them instead of on one run of one task.

Usage:
    python tools/regression.py                     run the core set (one task of each kind)
    python tools/regression.py --suite all         also run the extra tasks
    python tools/regression.py --only factual,list run some tasks
    python tools/regression.py --baseline regression_runs/<earlier run>/summary.json
    python tools/regression.py --rescore regression_runs/<run>   score an earlier run again, without the model
    python tools/regression.py --list              show the tasks

Each task runs in its own process through tools/live_test.py, with its own time limit, in
regression_runs/<version>_<date-time>/<task>/. After every task, summary.json and summary.md in that folder are
updated, so a run that is stopped early still has a summary of the tasks it finished.

The checks are deliberately simple, so they can be trusted and read: words the result must contain (the
answer, the items asked for), words that show a condition was missed (a non-open-source office suite), where
the discoveries came from (official documentation), citations, paragraphs without a citation, numbers missing
from the discoveries, and a "My view:" paragraph when the objective asks for one. They point to runs worth
reading; they do not replace reading them.
"""
import argparse
import datetime
import json
import os
import re
import subprocess
import sys
import urllib.request
from collections import Counter
from urllib.parse import urlparse

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import agent_browser as ab  # noqa: E402

# expect: groups of words the final result must contain, one alternative per group being enough
# forbid: words in the final result that show a condition of the objective was missed
# source: pattern every discovery's page should match, for objectives that name where to look
TASKS = [
    {"id": "factual", "kind": "Factual question", "core": True, "minutes": 25,
     "objective": "What is the tallest mountain in Africa, and how tall is it in meters and in feet?",
     "expect": [["kilimanjaro"], ["5,895", "5895"], ["19,341", "19341", "19,340", "19340"]]},
    {"id": "list", "kind": "List of items", "core": True, "minutes": 45,
     "objective": "Name three free, open-source alternatives to Microsoft Office, and say what each one is best at.",
     "expect": [["libreoffice"], ["onlyoffice", "openoffice", "calligra"]],
     # Free but not open source (FreeOffice was given as the third item on 1.46), or not free at all
     "forbid": ["freeoffice", "wps office", "softmaker", "google docs", "zoho", "microsoft 365", "apple iwork"]},
    {"id": "comparison", "kind": "Comparison and recommendation", "core": True, "minutes": 45,
     "objective": "Compare SQLite and PostgreSQL for a small web application: setup and administration, handling of "
                  "many simultaneous users, and features. Then recommend one for a hobby project with a few hundred users.",
     "expect": [["sqlite"], ["postgresql", "postgres"], ["concurren", "simultaneous"], ["setup", "set up", "install",
                                                                                         "administ", "configur"]]},
    # Replaced the repotting how-to in the core set on 2026-10-18: in three suite runs in a row the first search led
    # to the same guide, which has no section on care in the first two weeks, so that part could not be found there
    # whatever the agent did. Same kind of task (what you need, steps in order, and a follow-up part); a new id, so
    # its times are never compared with the repotting runs, which keep the id "howto"
    {"id": "howto-coldbrew", "kind": "Step-by-step how-to", "core": True, "minutes": 45,
     "objective": "How do I make cold brew coffee at home? List what I need with the quantities, give the steps in "
                  "order, and say how long it keeps in the fridge.",
     "expect": [["coffee"], ["water"], ["hour"], ["fridge", "refrigerat"]]},
    {"id": "howto", "kind": "Step-by-step how-to", "core": False, "minutes": 45,
     "objective": "How do I repot a houseplant? List what I need, give the steps in order, and say how to care for the "
                  "plant during the first two weeks after repotting.",
     "expect": [["pot"], ["soil", "potting mix"], ["water"], ["root"]]},
    {"id": "explanation", "kind": "Explanation of a topic", "core": True, "minutes": 60,
     "objective": "Explain how photosynthesis works: what goes in and what comes out, what happens in the "
                  "light-dependent reactions and in the Calvin cycle, and where in the plant cell each stage takes place.",
     "expect": [["carbon dioxide", "co2", "co₂"], ["oxygen"], ["glucose", "sugar", "carbohydrate", "g3p"],
                ["thylakoid"], ["stroma"]]},
    {"id": "opinion", "kind": "Opinion from several sources", "core": True, "minutes": 45,
     "objective": "Find out how at least three different cultures or religions have viewed owls (for example as wise, "
                  "unlucky or sacred), then give your own opinion on why owls attract such different meanings.",
     "expect": [["owl"], ["wis"], ["omen", "unlucky", "death", "bad luck"]]},
    # 45 minutes like the other tasks since 2026-10-18: at 30 it ran out twice while working through the official
    # page, which it reads part by part
    {"id": "docs", "kind": "Documentation research", "core": True, "minutes": 45,
     "objective": "Using the official Python documentation, find out how to create a virtual environment with venv, how "
                  "to activate it on Windows and on macOS or Linux, and how to install packages into it.",
     "expect": [["python -m venv", "python3 -m venv", "py -m venv"], ["activate"], ["pip install"]],
     "source": r"(^|\.)python\.org$"},
    {"id": "docs-strict", "kind": "Documentation research", "core": False, "minutes": 60,
     "objective": "Locate, analyze, and map out the exact implementation guidelines, parameter schemas, and terminal setup "
                  "commands for the natively integrated Playwright Test Agents (planner, generator, and healer). Your task "
                  "is divided into strict sequential milestones: 1) Use your SEARCH action to pinpoint official source "
                  "documentation. If a search layout is repetitive, pivot your query parameters immediately. 2) Use "
                  "NAVIGATE to enter deep technical pages or configuration guides. Do not rely on high-level snippets. "
                  "3) Use DISCOVER to programmatically extract and save distinct terminal commands, configuration blocks, "
                  "and file layout conventions into your rolling log. You must log at least 3 separate technical details. "
                  "4) Do not execute a FINISH action until your persistent research journal contains fully validated, "
                  "actionable setup metrics matching the current official release rules.",
     "expect": [["init-agents"], ["planner"], ["generator"], ["healer"]],
     "source": r"(^|\.)playwright\.dev$"},
    {"id": "opinion-cats", "kind": "Opinion from several sources", "core": False, "minutes": 45,
     "objective": "Research how different spiritual and religious traditions around the world have viewed cats, then "
                  "formulate your own spiritual opinion about cats, supported by what you found.",
     "expect": [["cat"], ["egypt", "islam", "buddhis", "japan", "norse", "christian"]]},
    {"id": "list-photoshop", "kind": "List of items", "core": False, "minutes": 40,
     "objective": "Find three free, open-source alternatives to Adobe Photoshop and summarize the main strengths of each one.",
     "expect": [["gimp", "krita", "darktable", "inkscape", "pinta", "rawtherapee"]],
     "forbid": ["photopea", "affinity", "pixlr", "canva", "paint.net"]},
    {"id": "howto-sourdough", "kind": "Step-by-step how-to", "core": False, "minutes": 40,
     "objective": "How do I make a sourdough starter from scratch? Give a step-by-step plan with the quantities of flour "
                  "and water and what to do on each day.",
     "expect": [["flour"], ["water"], ["day"]]},
    {"id": "comparison-rust", "kind": "Comparison and recommendation", "core": False, "minutes": 45,
     "objective": "Compare Python and Rust for writing command-line tools in terms of performance, ease of learning, and "
                  "available libraries, then recommend one for a beginner.",
     "expect": [["python"], ["rust"], ["perform", "fast", "speed"], ["librar", "crate", "package"]]},
]

MODEL_CALL = re.compile(r"\(model call ([\d.]+)s")
CITATION = re.compile(r"\[(\d+(?:\s*[-–,;]\s*\d+)*)\]")
GAP_STATEMENT = re.compile(r"\b(not (?:been )?(?:found|mentioned|covered|provided|included|available|specified|given)|"
                           r"no (?:information|details|mention)|no\b[^.]{0,80}\b(?:were|was) (?:found|given|mentioned))\b",
                           re.I)


def cited_numbers(text):
    """Every discovery number cited in the text, with ranges like [1-3] expanded."""
    numbers = set()
    for group in CITATION.findall(text):
        for piece in re.split(r"\s*[,;]\s*", group):
            ends = re.split(r"\s*[-–]\s*", piece)
            if len(ends) == 2 and ends[0].isdigit() and ends[1].isdigit() and int(ends[0]) <= int(ends[1]) <= min(int(ends[0]) + 50, 500):
                numbers.update(range(int(ends[0]), int(ends[1]) + 1))
            elif piece.isdigit():
                numbers.add(int(piece))
    return numbers


def uncited_lines(text):
    """Paragraphs and list items of the result that state something without citing any discovery.

    Counted per paragraph or list item rather than per sentence: the final result usually cites a point once,
    at its end. The opinion paragraph, headings, code blocks, short lines and statements that something was
    not found are left out, since they are not meant to carry a citation. A line holding only citations ("[2]"
    under a list) cites the lines above it, back to the previous heading.
    """
    body = re.split(r"(?im)^\s*my view:", text)[0]
    found, section, in_code = [], [], False
    for line in body.splitlines():
        line = line.strip()
        if line.startswith("```"):
            in_code = not in_code
            continue
        if line.startswith("#"):
            section = []
        if in_code or not line or line.endswith(":") or line.startswith("#"):
            continue
        if CITATION.sub("", line).strip(" ,;.") == "":
            found = [f for f in found if f not in section]
            section = []
        elif len(line.split()) >= 8 and not CITATION.search(line) and not GAP_STATEMENT.search(line):
            found.append(line)
            section.append(line)
    return found


def host(url):
    try:
        name = urlparse(url or "").hostname or ""
    except ValueError:
        return ""
    return name[4:] if name.startswith("www.") else name


def load_run(folder):
    """The objective, journal, console output and checklist of a run (each may be missing)."""
    objective, journal, console, checklist = "", [], "", None
    log_path = os.path.join(folder, "research_log.txt")
    if os.path.exists(log_path):
        try:
            objective, checklist, journal = ab.read_research_log(log_path)
        except json.JSONDecodeError:
            pass
    console_path = os.path.join(folder, "console.txt")
    if os.path.exists(console_path):
        with open(console_path, encoding="utf-8", errors="replace") as f:
            console = f.read()
    return objective, journal, console, checklist


def score_run(task, journal, console="", checklist=None):
    """Measurements and a list of problems for one run of a task."""
    finish = next((e for e in reversed(journal) if e.get("action") == "FINISH"), None)
    found = [e for e in journal if e.get("action") == "DISCOVER"]
    result = str(finish.get("finding", "")) if finish else ""
    low = result.lower()
    calls = [float(s) for s in MODEL_CALL.findall(console)]
    checks = Counter(e.get("check") or "REFUSED" for e in journal if e.get("action") == "REFUSED_IN_TURN")
    checks.update(e["action"] for e in journal if str(e.get("action", "")).startswith(("BLOCKED_", "CONFIRM_", "DUPLICATE_", "UNKNOWN_")))

    cited = cited_numbers(result)
    score = {
        "id": task["id"],
        "kind": task["kind"],
        "finished": finish is not None,
        "turns": journal[-1].get("turn", 0) if journal else 0,
        "model_minutes": round(sum(calls) / 60, 1),
        "model_calls": len(calls),
        "time_limit_reached": "TEST TIME LIMIT REACHED" in console,
        "discoveries": len(found),
        "sources": sorted({host(e.get("source")) for e in found if e.get("source")}),
        "checks": dict(sorted(checks.items())),
        "result_chars": len(result),
        "uncited_discoveries": sorted(set(range(1, len(found) + 1)) - cited) if finish else [],
        "bad_citations": sorted(n for n in cited if n > len(found) or n < 1),
        "uncited_lines": uncited_lines(result),
        "unverified_numbers": ab.unverified_numbers(result, found) if finish else [],
        "gap_statements": [m.group(0) for m in GAP_STATEMENT.finditer(result)],
        "missing": ["/".join(group[:3]) for group in task.get("expect", []) if not any(word in low for word in group)],
        "forbidden": [word for word in task.get("forbid", []) if word in low],
        # How far a run got, so one that was about to finish is not read the same as one that was stuck
        "finish_attempts": sum(1 for e in journal if e.get("action") in ("FINISH", "BLOCKED_FINISH", "CONFIRM_FINISH")
                               or str(e.get("requested", "")).startswith("FINISH")),
        "checklist_items": len(checklist) if checklist else None,
        "checklist_items_with_discoveries": sum(1 for item in checklist if item.get("discoveries")) if checklist else None,
    }
    if task.get("source"):
        off = [h for h in (host(e.get("source")) for e in found) if not re.search(task["source"], h)]
        score["off_source_discoveries"] = len(off)
    score["view_missing"] = bool(finish and ab.OPINION_REQUEST.search(task["objective"]) and "my view:" not in low)

    problems = []
    if not finish:
        problems.append("did not finish" + (" (time limit)" if score["time_limit_reached"] else ""))
    else:
        if score["missing"]:
            problems.append("result lacks " + ", ".join(score["missing"]))
        if score["forbidden"]:
            problems.append("condition missed: " + ", ".join(score["forbidden"]))
        if score["view_missing"]:
            problems.append("no 'My view:' paragraph")
        if score["bad_citations"]:
            problems.append("cites missing discoveries " + ", ".join(map(str, score["bad_citations"])))
        if score["unverified_numbers"]:
            problems.append("numbers not in discoveries: " + ", ".join(score["unverified_numbers"]))
        if score["uncited_lines"]:
            problems.append(f"{len(score['uncited_lines'])} uncited paragraph(s)")
    if score.get("off_source_discoveries"):
        problems.append(f"{score['off_source_discoveries']} of {len(found)} discoveries outside the named source")
    score["problems"] = problems
    return score


def progress(score):
    """How far a run got: checklist items with discoveries, and finish attempts (both absent in older runs)."""
    parts = []
    if score.get("checklist_items"):
        parts.append(f"{score['checklist_items_with_discoveries']}/{score['checklist_items']} items found")
    if score.get("finish_attempts") is not None:
        parts.append(f"{score['finish_attempts']} finish attempt(s)")
    return ", ".join(parts) or "-"


def summary_markdown(meta, scores, baseline=None):
    lines = [f"# Regression run {meta['name']}", "",
             f"Version {meta['version']} (commit {meta['commit']}), started {meta['started']}.", "",
             "| Task | Finished | Turns | Model min | Discoveries | Progress | Checks fired | Problems |",
             "|---|---|---|---|---|---|---|---|"]
    before = {s["id"]: s for s in (baseline or {}).get("scores", [])}
    for s in scores:
        minutes = f"{s['model_minutes']}"
        old = before.get(s["id"])
        if old:
            minutes += f" ({s['model_minutes'] - old['model_minutes']:+.1f})"
        fired = sum(s["checks"].values())
        lines.append(f"| {s['id']} | {'yes' if s['finished'] else 'no'} | {s['turns']} | {minutes} | {s['discoveries']} | "
                     f"{progress(s)} | {fired} | {'; '.join(s['problems']) or 'none'} |")
    done = [s for s in scores if s["finished"]]
    lines += ["", f"Finished {len(done)} of {len(scores)}; total model time {sum(s['model_minutes'] for s in scores):.1f} min."]
    if baseline:
        lines += ["", f"Compared with {baseline['meta']['name']} (version {baseline['meta']['version']}):"]
        for s in scores:
            old = before.get(s["id"])
            if not old:
                continue
            fixed = [p for p in old["problems"] if p not in s["problems"]]
            new = [p for p in s["problems"] if p not in old["problems"]]
            if old["finished"] != s["finished"] or fixed or new:
                lines.append(f"- {s['id']}: " + "; ".join(
                    ([f"finished {'now' if s['finished'] else 'no longer'}"] if old["finished"] != s["finished"] else [])
                    + [f"gone: {p}" for p in fixed] + [f"new: {p}" for p in new]))
            # A task that keeps failing on the same site may be failing because of the site: the repotting how-to
            # went to the same guide in three runs, and that guide had no answer for one part of the objective
            shared = sorted(set(s["sources"]) & set(old["sources"]))
            if not s["finished"] and not old["finished"] and shared:
                lines.append(f"- {s['id']}: failed again on the same site as before ({', '.join(shared)}): check "
                             "whether that site can answer the objective at all before changing the agent for it.")
    lines += ["", "Read each task's research_report.md before drawing conclusions: these checks only find words."]
    return "\n".join(lines) + "\n"


def write_summary(folder, meta, scores, baseline=None):
    with open(os.path.join(folder, "summary.json"), "w", encoding="utf-8") as f:
        json.dump({"meta": meta, "scores": scores}, f, indent=2, ensure_ascii=False)
    with open(os.path.join(folder, "summary.md"), "w", encoding="utf-8") as f:
        f.write(summary_markdown(meta, scores, baseline))


def project_version():
    with open(os.path.join(ROOT, "pyproject.toml"), encoding="utf-8") as f:
        match = re.search(r'(?m)^version\s*=\s*"([^"]+)"', f.read())
    return match.group(1) if match else "unknown"


def git_commit():
    try:
        out = subprocess.run(["git", "-C", ROOT, "rev-parse", "--short", "HEAD"], capture_output=True, text=True, check=True)
        dirty = subprocess.run(["git", "-C", ROOT, "status", "--porcelain", "--untracked-files=no"],
                               capture_output=True, text=True).stdout.strip()
        return out.stdout.strip() + ("+changes" if dirty else "")
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def model_server_ready():
    try:
        with urllib.request.urlopen(ab.BASE_URL.rstrip("/") + "/models", timeout=10) as response:
            return response.status == 200
    except OSError:
        return False


def run_task(task, folder, minutes):
    """Runs one task through the live test runner, copying its console output to the screen and to console.txt."""
    os.makedirs(folder, exist_ok=True)
    env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
    command = [sys.executable, "-u", os.path.join(ROOT, "tools", "live_test.py"), task["objective"],
               "--minutes", str(minutes), "--out", folder]
    with open(os.path.join(folder, "console.txt"), "w", encoding="utf-8") as console:
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env,
                                   text=True, encoding="utf-8", errors="replace")
        for line in process.stdout:
            print(line, end="", flush=True)
            console.write(line)
            console.flush()
        process.wait()


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--suite", choices=["core", "all"], default="core", help="core: one task of each kind (default)")
    parser.add_argument("--only", help="comma-separated task ids to run")
    parser.add_argument("--baseline", help="summary.json of an earlier run to compare with")
    parser.add_argument("--rescore", help="score the task folders of an earlier run again instead of running")
    parser.add_argument("--minutes", type=float, help="time limit for every task instead of each task's own")
    parser.add_argument("--list", action="store_true", help="list the tasks and exit")
    args = parser.parse_args()

    tasks = [t for t in TASKS if args.suite == "all" or t["core"]]
    if args.only:
        wanted = [w.strip() for w in args.only.split(",")]
        unknown = [w for w in wanted if w not in {t["id"] for t in TASKS}]
        if unknown:
            parser.error(f"unknown task id(s): {', '.join(unknown)}")
        tasks = [t for t in TASKS if t["id"] in wanted]
    if args.list:
        for t in TASKS:
            print(f"{t['id']:<16} {'core ' if t['core'] else 'extra'} {t['minutes']:>3} min  {t['kind']}: {t['objective'][:70]}...")
        return
    baseline = None
    if args.baseline:
        with open(args.baseline, encoding="utf-8") as f:
            baseline = json.load(f)

    if args.rescore:
        folder = args.rescore
        with open(os.path.join(folder, "summary.json"), encoding="utf-8") as f:
            meta = json.load(f)["meta"]
        scores = [score_run(t, *load_run(os.path.join(folder, t["id"]))[1:]) for t in TASKS
                  if os.path.isdir(os.path.join(folder, t["id"]))]
        write_summary(folder, meta, scores, baseline)
        print(summary_markdown(meta, scores, baseline))
        return

    if not model_server_ready():
        sys.exit(f"The model server at {ab.BASE_URL} is not answering; start LM Studio's server first.")
    version = project_version()
    started = datetime.datetime.now()
    name = f"{version}_{started:%Y%m%d-%H%M}"
    folder = os.path.join(ROOT, "regression_runs", name)
    meta = {"name": name, "version": version, "commit": git_commit(), "started": f"{started:%Y-%m-%d %H:%M}",
            "tasks": [t["id"] for t in tasks]}
    scores = []
    for number, task in enumerate(tasks, start=1):
        minutes = args.minutes or task["minutes"]
        print(f"\n===== REGRESSION TASK {number}/{len(tasks)}: {task['id']} ({task['kind']}, up to {minutes:g} min) =====\n",
              flush=True)
        run_task(task, os.path.join(folder, task["id"]), minutes)
        score = score_run(task, *load_run(os.path.join(folder, task["id"]))[1:])
        scores.append(score)
        write_summary(folder, meta, scores, baseline)
        print(f"\n===== RESULT {task['id']}: {'finished' if score['finished'] else 'not finished'}, {score['turns']} turns, "
              f"{score['model_minutes']} model min; problems: {'; '.join(score['problems']) or 'none'} =====", flush=True)
    print("\n" + summary_markdown(meta, scores, baseline))
    print(f"Summary: {os.path.join(folder, 'summary.md')}")


if __name__ == "__main__":
    main()
