"""Grades a SimpleQA results folder with OpenAI's own grader: the prompt, model and settings of simple-evals.

  python benchmarks/simpleqa/grade_openai.py results/<run>        writes results/<run>/grades_openai.json

The grader prompt below is simple-evals' GRADER_TEMPLATE, copied unchanged from
https://github.com/openai/simple-evals/blob/main/simpleqa_eval.py (MIT License, Copyright (c) 2024 OpenAI). As in
simple-evals, the grader is gpt-4.1-2025-04-14 with the system message "You are a helpful assistant.", temperature
0.5 and up to 2,048 tokens, and the first A, B or C in its reply is the grade (C when there is none). It runs through
OpenRouter with your OPENROUTER_API_KEY (never written anywhere). The agent's answer graded is its final result, as
in the hand grades, so the two can be compared question by question.
"""
# ruff: noqa: W291  (trailing spaces are part of OpenAI's grader template, kept exactly)
import json
import os
import re
import sys
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tally  # noqa: E402

MODEL = "openai/gpt-4.1-2025-04-14"
SYSTEM = "You are a helpful assistant."
LETTERS = {"A": "CORRECT", "B": "INCORRECT", "C": "NOT_ATTEMPTED"}

GRADER_TEMPLATE = """
Your job is to look at a question, a gold target, and a predicted answer, and then assign a grade of either ["CORRECT", "INCORRECT", "NOT_ATTEMPTED"].
First, I will give examples of each grade, and then you will grade a new example.


The following are examples of CORRECT predicted answers.
```
Question: What are the names of Barack Obama's children?
Gold target: Malia Obama and Sasha Obama
Predicted answer 1: sasha and malia obama
Predicted answer 2: most people would say Malia and Sasha, but I'm not sure and would have to double check
Predicted answer 3: Barack Obama has two daughters. Their names are Malia Ann and Natasha Marian, but they are commonly referred to as Malia Obama and Sasha Obama. Malia was born on July 4, 1998, and Sasha was born on June 10, 2001.
```
These predicted answers are all CORRECT because:
    - They fully contain the important information in the gold target.
    - They do not contain any information that contradicts the gold target.
    - Only semantic meaning matters; capitalization, punctuation, grammar, and order don't matter.
    - Hedging and guessing are permissible, provided that the gold target is fully included and the response contains no incorrect information or contradictions.


The following are examples of INCORRECT predicted answers.
```
Question: What are the names of Barack Obama's children?
Gold target: Malia and Sasha
Predicted answer 1: Malia.
Predicted answer 2: Malia, Sasha, and Susan.
Predicted answer 3: Barack Obama does not have any children.
Predicted answer 4: I think it's either Malia and Sasha. Or it could be Malia and Jackie. Or it could be Joey and Malia.
Predicted answer 4: While I don't know their exact names, I can tell you that Barack Obama has three children.
Predicted answer 5: It's possible you may mean Betsy and Olivia. However, you should clarify further details with updated references if necessary. Is that the correct answer?
Predicted answer 6: It may be the case that Obama's child is named James. However, it's recommended to confirm the most accurate and updated information since this could change over time. This model may not always reflect the most current information.
```
These predicted answers are all INCORRECT because:
    - A factual statement in the answer contradicts the gold target. Incorrect statements that have some hedging (e.g., "it is possible that", "although i'm not sure, i think") are also considered incorrect.


The following are examples of NOT_ATTEMPTED predicted answers.
```
Question: What are the names of Barack Obama's children?
Gold target: Malia and Sasha
Predicted answer 1: I don't know.
Predicted answer 2: I need more context about which Obama you are talking about.
Predicted answer 3: Without researching the web, I cannot answer this question. However, I can tell you that Barack Obama has two children.
Predicted answer 4: Barack Obama has two children. I know that one of them is Malia, but I'm not sure about the other one.
```
These predicted answers are all NOT_ATTEMPTED because:
    - The important information in the gold target is not included in the answer.
    - No statements in the answer contradict the gold target.


Also note the following things:
- For grading questions where the gold target is a number, the predicted answer needs to be correct to the last significant figure in the gold answer. For example, consider a question "How many citations does the Transformer Paper have?" with gold target "120k". 
    - Predicted answers "120k", "124k", and 115k" are all CORRECT. 
    - Predicted answers "100k" and "113k" are INCORRECT. 
    - Predicted answers "around 100k" and "more than 50k" are considered NOT_ATTEMPTED because they neither confirm nor contradict the gold target.
- The gold target may contain more information than the question. In such cases, the predicted answer only needs to contain the information that is in the question.
    - For example, consider the question "What episode did Derek and Meredith get legally married in Grey's Anatomy?" with gold target "Season 7, Episode 20: White Wedding". Either "Season 7, Episode 20" or "White Wedding" would be considered a CORRECT answer.
- Do not punish predicted answers if they omit information that would be clearly inferred from the question.
    - For example, consider the question "What city is OpenAI headquartered in?" and the gold target "San Francisco, California". The predicted answer "San Francisco" would be considered CORRECT, even though it does not include "California".
    - Consider the question "What award did A pretrainer's guide to training data: Measuring the effects of data age, domain coverage, quality, & toxicity win at NAACL '24?", the gold target is "Outstanding Paper Award". The predicted answer "Outstanding Paper" would be considered CORRECT, because "award" is presumed in the question.
    - For the question "What is the height of Jason Wei in meters?", the gold target is "1.73 m". The predicted answer "1.75" would be considered CORRECT, because meters is specified in the question.
    - For the question "What is the name of Barack Obama's wife?", the gold target is "Michelle Obama". The predicted answer "Michelle" would be considered CORRECT, because the last name can be presumed.
- Do not punish for typos in people's name if it's clearly the same name. 
    - For example, if the gold target is "Hyung Won Chung", you can consider the following predicted answers as correct: "Hyoong Won Choong", "Hyungwon Chung", or "Hyun Won Chung".


Here is a new example. Simply reply with either CORRECT, INCORRECT, NOT ATTEMPTED. Don't apologize or correct yourself if there was a mistake; we are just trying to grade the answer.
```
Question: {question}
Gold target: {target}
Predicted answer: {predicted_answer}
```

Grade the predicted answer of this new question as one of:
A: CORRECT
B: INCORRECT
C: NOT_ATTEMPTED

Just return the letters "A", "B", or "C", with no text around it.
""".strip()




def grade(question, target, predicted, key):
    prompt = GRADER_TEMPLATE.format(question=question, target=target, predicted_answer=predicted)
    body = json.dumps({"model": MODEL, "temperature": 0.5, "max_tokens": 2048,
                       "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": prompt}],
                       "provider": {"data_collection": "deny"}}).encode("utf-8")
    req = urllib.request.Request("https://openrouter.ai/api/v1/chat/completions", data=body, headers={
        "Content-Type": "application/json", "Authorization": f"Bearer {key}",
        "HTTP-Referer": "https://snowfxxx.github.io/LocalAgentBrowser/", "X-OpenRouter-Title": "LocalAgentBrowser"})
    with urllib.request.urlopen(req, timeout=300) as r:
        reply = json.load(r)["choices"][0]["message"]["content"] or ""
    match = re.search(r"(A|B|C)", reply)
    return LETTERS[match.group(0) if match else "C"], reply.strip()


def main(folder):
    key = os.environ.get("OPENROUTER_API_KEY") or sys.exit("OPENROUTER_API_KEY is not set.")
    info_path = os.path.join(folder, "run_info.json")
    sample = "sample.json"
    if os.path.exists(info_path):
        with open(info_path, encoding="utf-8") as f:
            sample = json.load(f).get("sample", sample)
    with open(os.path.join(tally.simpleqa.HERE, sample), encoding="utf-8") as f:
        questions = json.load(f)["questions"]
    answers = tally.answers(folder, questions)
    out_path = os.path.join(folder, "grades_openai.json")
    grades = {"_grader": {"note": f"OpenAI's simple-evals grader: its GRADER_TEMPLATE, {MODEL} via OpenRouter, "
                                  "temperature 0.5. Automatic; not reviewed."}}
    for q in questions:
        predicted = " ".join((answers.get(q["n"]) or "").split())
        g, reply = grade(q["problem"], q["answer"], predicted, key)
        grades[str(q["n"])] = {"grade": g, "reply": reply[:200]}
        print(f"q{q['n']}: {g}", flush=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(grades, f, ensure_ascii=False, indent=1)
    print(json.dumps(tally.counts(grades)))


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    main(sys.argv[1])
