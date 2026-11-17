# SimpleQA with LocalAgentBrowser

[SimpleQA](https://openai.com/index/introducing-simpleqa) (OpenAI, MIT license) is 4,326 short, hard factual questions,
each with one reference answer. Every question here is run through LocalAgentBrowser as an ordinary objective, the
same code as any other run, and the answer is graded with SimpleQA's three grades:

- **CORRECT**: contains the reference answer and contradicts nothing.
- **INCORRECT**: a different answer, a contradiction, or a hedge between candidates.
- **NOT_ATTEMPTED**: says it couldn't find out, with no wrong answer.

For an agent whose goal is "a verifiable answer, or say honestly that it can't", NOT_ATTEMPTED isn't a failure in the
way INCORRECT is, so all three are reported, with SimpleQA's F-score (the harmonic mean of correct over all
questions and correct over those attempted).

## Results so far

| Run | Model | Questions | Correct | Wrong | Not attempted | F-score |
|---|---|---|---|---|---|---|
| [1.66](results/1.66_microsoft_phi-4_20261115/answers.md) | Phi-4 (cloud) | 50 | **45** | 4 | 1 | 0.909 |
| [1.65.1, Nov 15](results/1.65.1_microsoft_phi-4_20261115/answers.md) | Phi-4 (cloud) | 50 | 39 | 5 | 6 | 0.830 |
| [1.65.1, Nov 11](results/1.65.1_microsoft_phi-4_20261111/answers.md) | Phi-4 (cloud) | 50 | 38 | 6 | 6 | 0.809 |
| [Phi-4 alone, no browsing](results/closed-book_microsoft_phi-4/answers.md) | Phi-4 (cloud) | 50 | 1 | 34 | 15 | 0.024 |
| [1.64, local](results/1.64_qwen2.5-7b-instruct_local/answers.md) | Qwen2.5-7B, CPU only | 50 | 35 | 6 | 9 | 0.769 |
| [Qwen2.5-7B alone, no browsing](results/closed-book_qwen2.5-7b-instruct_local/answers.md) | Qwen2.5-7B, CPU only | 50 | 0 | 13 | 37 | 0 |

**Grading: graded by Claude, judgement calls reviewed by the author.** Each answer was graded by Claude against
SimpleQA's reference answer, one question at a time, with a note for every wrong answer and every judgement call (an
answer that isn't a clean match or a clean miss). The author reviewed all 18 judgement calls and borderline grades
in these runs and agreed with every one. Every grade, note and answer is in the
run's folder, so any of them can be checked.

**Sample size.** 50 questions can't separate close scores: the likely range for 45 of 50 is about 79% to 96%. A
200-question run follows (below).

## The questions

- **[sample.json](sample.json):** the first 50, drawn with `random.Random(20261106)` from the dataset and sorted.
- **[sample-200.json](sample-200.json):** the 200 questions OpenAI's [simple-evals](https://github.com/openai/simple-evals)
  runs with `--examples 200` (`random.Random(0).sample` over the dataset rows), so a run on them can be compared
  with anyone else's on the same subset. One question is in both samples.

`run.py sample` draws either one again from the dataset file and checks its SHA-256 first.

## How a run works

- The sites that republish SimpleQA with its answers are blocked (`LAB_BLOCKED_SITES`, listed in `run.py`), so an
  answer has to be found on the open web the way any other answer is.
- Each question has 60 minutes and starts a fresh agent with nothing carried over.
- Searches go to DuckDuckGo; pages are read in a real Chromium browser.
- The answer graded is the agent's final result, with its citations; each question's `research_report.md` has every
  finding and the page it came from.

## Running it

The dataset isn't in this repository. Download it to `data/` (2.0 MB):

    curl -o benchmarks/simpleqa/data/simple_qa_test_set.csv https://openaipublic.blob.core.windows.net/simple-evals/simple_qa_test_set.csv

Then, from the repository:

    python benchmarks/simpleqa/run.py run --model qwen2.5-7b-instruct            # local model in LM Studio
    python benchmarks/simpleqa/run_cloud.py microsoft/phi-4 --sample sample-200.json --label 1.66   # OpenRouter
    python benchmarks/simpleqa/closed_book.py                                    # the model alone, no browsing
    python benchmarks/simpleqa/tally.py results/<run>                            # answer sheet and counts

`run_cloud.py` reads your key from `OPENROUTER_API_KEY` and never writes it anywhere; each run gets its own folder
under `results/` with `run_info.json` (model, commit, sample, times).

## Files in a results folder

- `run_info.json`: model, version or commit, sample file, when it ran.
- `grades.json`: one grade and note per question; `_grader` says who graded and how.
- `answers.md`: question, reference answer, the agent's answer and the grade, for reading (written by `tally.py`).
- `qNN/research_report.md`: the agent's report for each question: final result, findings and sources.
- `answers.json` (closed-book runs): the model's answer to each question, with no browsing.
