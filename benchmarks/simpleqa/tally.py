"""Tallies a SimpleQA results folder and writes its answer sheet.

  python benchmarks/simpleqa/tally.py results/<run>        writes results/<run>/answers.md and prints the counts

A results folder holds run_info.json (or COMPLETE.txt), grades.json and either one folder per question (q01/ or
q001/, each with the agent's research_report.md) or answers.json (closed-book runs, no browsing). grades.json maps each
question number to {"grade": CORRECT | INCORRECT | NOT_ATTEMPTED, "note": ...}; "_grader" says who graded and how.
The F-score is SimpleQA's: the harmonic mean of correct over all questions and correct over those attempted.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import run as simpleqa  # noqa: E402

GRADES = ("CORRECT", "INCORRECT", "NOT_ATTEMPTED")


def answers(folder, questions):
    path = os.path.join(folder, "answers.json")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return {r["n"]: r["answer"] for r in json.load(f)["results"]}
    found = {}
    for q in questions:
        for name in (f"q{q['n']:02d}", f"q{q['n']:03d}"):
            if os.path.isdir(os.path.join(folder, name)):
                found[q["n"]] = simpleqa.final_result(os.path.join(folder, name))
    return found


def counts(grades):
    graded = [g["grade"] for k, g in grades.items() if not k.startswith("_") and isinstance(g, dict) and g.get("grade")]
    c, i, na = (graded.count(x) for x in GRADES)
    n, attempted = len(graded), c + i
    accuracy = c / n if n else 0
    given_attempted = c / attempted if attempted else 0
    f = 2 * accuracy * given_attempted / (accuracy + given_attempted) if accuracy + given_attempted else 0
    return {"graded": n, "correct": c, "incorrect": i, "not_attempted": na, "accuracy": round(accuracy, 3),
            "correct_given_attempted": round(given_attempted, 3), "f_score": round(f, 3)}


def main(folder):
    info_path = os.path.join(folder, "run_info.json")
    sample = "sample.json"
    if os.path.exists(info_path):
        with open(info_path, encoding="utf-8") as f:
            sample = json.load(f).get("sample", sample)
    with open(os.path.join(simpleqa.HERE, sample), encoding="utf-8") as f:
        questions = json.load(f)["questions"]
    grades = {}
    if os.path.exists(os.path.join(folder, "grades.json")):
        with open(os.path.join(folder, "grades.json"), encoding="utf-8") as f:
            grades = json.load(f)
    got = answers(folder, questions)
    total = counts(grades)
    grader = grades.get("_grader", {}).get("note", "not graded yet")
    lines = [f"# SimpleQA answers: {os.path.basename(os.path.normpath(folder))}", "",
             f"Questions: {sample}. Grader: {grader}", "",
             f"Correct {total['correct']}, incorrect {total['incorrect']}, not attempted {total['not_attempted']} "
             f"of {total['graded']} graded; accuracy {total['accuracy']:.1%}, correct given attempted "
             f"{total['correct_given_attempted']:.1%}, F-score {total['f_score']:.3f}.", ""]
    for q in questions:
        g = grades.get(str(q["n"]), {})
        answer = got.get(q["n"])
        lines += [f"## q{q['n']} ({q['topic']})", "", f"**Question:** {q['problem']}", "",
                  f"**Reference answer:** {q['answer']}", "", "**Answer:**", "",
                  answer if answer else "_(no final result)_", "",
                  f"**Grade:** {g.get('grade', '-')}" + (f" ({g['note']})" if g.get("note") else ""), ""]
    with open(os.path.join(folder, "answers.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(json.dumps(total))


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    main(sys.argv[1])
