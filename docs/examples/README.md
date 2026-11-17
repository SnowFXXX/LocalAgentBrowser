# Example reports

Three reports exactly as the agent wrote them, unedited, from the test-suite run of the released 1.66 code on
2026-11-15. All three used Phi-4, the cloud default, through OpenRouter. Each report has the final result, the
objective's checklist with the findings recorded for each part, and every finding with the page it came from.

| Report | Objective | Turns | Model time |
|---|---|---|---|
| [explanation-photosynthesis.md](explanation-photosynthesis.md) | How photosynthesis works: what goes in and what comes out, the light-dependent reactions and the Calvin cycle, and where each takes place in the plant cell | 19 | 1.5 min |
| [howto-cold-brew.md](howto-cold-brew.md) | How to make cold brew coffee at home: what you need with quantities, the steps in order, how long it keeps in the fridge | 6 | 0.3 min |
| [comparison-sqlite-postgresql.md](comparison-sqlite-postgresql.md) | Compare SQLite and PostgreSQL for a small web application, then recommend one for a hobby project with a few hundred users | 18 | 1.3 min |

The same three tasks with the default local model, Qwen2.5-7B on a CPU with no GPU, are in the
[1.64 examples](https://github.com/SnowFXXX/LocalAgentBrowser/tree/v1.65.1/docs/examples): the same checks, at 49 to
73 minutes of model time a task instead of one or two.

## What to notice, good and bad

**The explanation** is detailed and cited throughout: inputs and outputs, what happens in each photosystem, how ATP
is made, and where each stage takes place, from seven findings on two textbook pages. Three flaws:
- It says the Calvin cycle "involves three stages" and lists two. The model wrote the third, "Ribulose-1,5-bisphosphate
  (RuBP) is regenerated using ATP [2]", and the number check cut it: "1,5" is part of a chemical name, and no finding
  contains it. Finding [2] does say RuBP is regenerated using ATP. The warning after the result ("Numbers not found
  ... 1,5") is that check, firing on a name rather than a number.
- The sentence naming the outputs (carbohydrates and oxygen) carries no citation, though findings [2] and [4] cover it.
- "Creating a proton gradient" is the model's wording; the page describes the same thing without that phrase.

**The how-to** is short and faithful to its one page: 1 cup of beans to 4 cups of water, a coarse grind, about 12 hours
of steeping, then straining. But it is thin. The strainer the page names isn't in the list of what you need, the water
line has no citation, and "all week long" is the page's only word on how long it keeps; the 1.64 local run found a
page saying 7 to 14 days.

**The comparison** shows the limit of checking against the page.
- Every claim is copied from a finding, and the findings are copied from the page. But one repeats the page's own
  mistake: SQLite "doesn't include some of the more advanced SQL features such as partial indexes, check constraints,
  and triggers", and SQLite has all three. The checks prove a claim is on the page, not that the page is right.
- The second-source rule (new in 1.66) sent it to a second site, and it recorded finding [5] from dev.to; the result
  still cites only the first site.
- The recommendation, SQLite for a hobby project with a few hundred users, is a fair reading of the findings, but it is
  written under a heading rather than as a "My view:" paragraph, and it cites [3] as if the page said it. The checklist
  marks it as written by the model, not found on a page.

Check any result against its listed findings before relying on it. That's what the report is laid out for.
