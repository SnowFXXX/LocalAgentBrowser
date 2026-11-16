"""SimpleQA, the same 7B WITHOUT LocalAgentBrowser: each of the 50 sample questions asked straight to the model,
no browsing, no checks. The comparison for the agent run in runs/.

Same model and server as the agent run (qwen2.5-7b-instruct in LM Studio), temperature 0, one call per question.
The prompt allows "I don't know", so the model can decline (SimpleQA's NOT_ATTEMPTED), as the agent can.
Writes closed-book/answers.json and closed-book/grading.md (question, reference, answer, time) for hand grading.
Standard library only.
"""
import json
import os
import sys
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
# No arguments: the original local run (qwen2.5-7b-instruct in LM Studio, results in closed-book\).
# "MODEL --cloud": that OpenRouter model with your OPENROUTER_API_KEY, results in closed-book-cloud_<model>\.
CLOUD = "--cloud" in sys.argv[2:]
MODEL = sys.argv[1] if len(sys.argv) > 1 else "qwen2.5-7b-instruct"
if CLOUD:
    URL = "https://openrouter.ai/api/v1/chat/completions"
    HEADERS = {"Content-Type": "application/json", "Authorization": f"Bearer {os.environ['OPENROUTER_API_KEY']}",
               "HTTP-Referer": "https://snowfxxx.github.io/LocalAgentBrowser/", "X-OpenRouter-Title": "LocalAgentBrowser"}
    EXTRA = {"provider": {"require_parameters": True, "data_collection": "deny"}}
    OUT = os.path.join(HERE, "closed-book-cloud_" + MODEL.replace("/", "_"))
else:
    URL = "http://127.0.0.1:1234/v1/chat/completions"
    HEADERS = {"Content-Type": "application/json"}
    EXTRA = {}
    OUT = os.path.join(HERE, "closed-book")
SYSTEM = ("Answer the question with a short, direct answer. "
          "If you are not sure of the answer, say \"I don't know\" instead of guessing.")


def ask(question):
    body = json.dumps({"model": MODEL, "temperature": 0, "max_tokens": 200,
                       "messages": [{"role": "system", "content": SYSTEM},
                                    {"role": "user", "content": question}], **EXTRA}).encode("utf-8")
    req = urllib.request.Request(URL, data=body, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=1800) as r:
        return json.load(r)["choices"][0]["message"]["content"].strip()


def main():
    with open(os.path.join(HERE, "sample.json"), encoding="utf-8") as f:
        questions = json.load(f)["questions"]
    os.makedirs(OUT, exist_ok=True)
    results, start = [], time.time()
    print(f"SimpleQA closed-book: {len(questions)} questions to {MODEL}, no browsing, no checks.\n")
    for q in questions:
        t = time.time()
        try:
            answer = ask(q["problem"])
        except Exception as e:  # keep going; the error is recorded as the answer
            answer = f"(error: {e})"
        secs = round(time.time() - t, 1)
        results.append({"n": q["n"], "problem": q["problem"], "reference": q["answer"], "answer": answer,
                        "seconds": secs})
        print(f"q{q['n']:02d} ({secs:.0f} s)  {q['problem']}\n     model:     {answer}\n     reference: {q['answer']}\n",
              flush=True)
        with open(os.path.join(OUT, "answers.json"), "w", encoding="utf-8") as f:
            json.dump({"model": MODEL, "system_prompt": SYSTEM, "temperature": 0, "results": results}, f,
                      ensure_ascii=False, indent=1)
    total = round((time.time() - start) / 60, 1)
    lines = ["# SimpleQA closed-book (no LocalAgentBrowser): answers to grade", "",
             f"Model {MODEL}, temperature 0, no browsing. Total {total} min.", ""]
    for r in results:
        lines += [f"## q{r['n']:02d}", "", f"**Question:** {r['problem']}", "", f"**Reference answer:** {r['reference']}",
                  "", f"**Model (no browsing):** {r['answer']}", "", "**Grade:** —", ""]
    with open(os.path.join(OUT, "grading.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    with open(os.path.join(OUT, "COMPLETE.txt"), "w", encoding="utf-8") as f:
        f.write(f"Completed {time.strftime('%Y-%m-%dT%H:%M:%S')}, {len(results)} answers, {total} min.\n")
    print(f"Done: {len(results)} answers in {total} min. Saved in {OUT}")


if __name__ == "__main__":
    main()
