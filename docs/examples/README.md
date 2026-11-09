# Example reports

Three reports exactly as the agent wrote them, unedited, from the test run of the released 1.64 code on
2026-11-07. All three used Qwen2.5-7B-Instruct (Q4_K_M), the default model, in LM Studio on a 6-core CPU with no GPU.
Each report has the final result, the objective's checklist with the findings recorded for each part, and every
finding with the page it came from.

| Report | Objective | Turns | Model time |
|---|---|---|---|
| [explanation-photosynthesis.md](explanation-photosynthesis.md) | How photosynthesis works: what goes in and what comes out, the light-dependent reactions and the Calvin cycle, and where each takes place in the plant cell | 13 | 72.7 min |
| [howto-cold-brew.md](howto-cold-brew.md) | How to make cold brew coffee at home: what you need with quantities, the steps in order, how long it keeps in the fridge | 10 | 50.4 min |
| [comparison-sqlite-postgresql.md](comparison-sqlite-postgresql.md) | Compare SQLite and PostgreSQL for a small web application, then recommend one for a hobby project with a few hundred users | 9 | 49.2 min |

## What to notice, good and bad

**The explanation** is the clean case. Every part of the question is answered, inputs and outputs (oxygen included),
both stages and where each takes place, and every sentence cites the finding it came from. Two small things: finding
[3] largely repeats [2] from the same page, and finding [4] (common misconceptions) isn't used.

**The how-to** copies the page's amounts and steps faithfully: 1 cup of coarse coffee to 4 cups of water, 16 hours in
the fridge, 7 to 14 days to keep. But steps 1 to 4 and 6 carry no citation, although they come from finding [2], so
a reader has to look them up. The test suite flags them as uncited lines.

**The comparison** shows the limit of checking against the page.
- Every claim cites a finding, and the findings are copied from the page. But one of them repeats the page's own
  mistake: it says SQLite "lacks some advanced SQL features like partial indexes, check constraints, and triggers",
  and SQLite has all three. The checks prove a claim is on the page, not that the page is right.
- All three findings come from one page, so there is no second source to catch that.
- The recommendation, PostgreSQL for a hobby project with a few hundred users, leans on the page's "hundreds to
  thousands of concurrent users"; many would pick SQLite at that size. It is labelled as the model's view.

The documentation task (Python virtual environments) ran out of time for the 7B in this run; the
[regression history](https://github.com/SnowFXXX/LocalAgentBrowser/blob/main/docs/regression-history.md) has every
task and model.

Check any result against its listed findings before relying on it. That's what the report is laid out for.
