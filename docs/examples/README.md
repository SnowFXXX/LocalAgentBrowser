# Example reports

Two reports exactly as the agent wrote them, unedited, from the 1.62 regression run of 2026-10-25. Both runs used
Qwen2.5-7B-Instruct (Q4_K_M) in LM Studio on a 6-core CPU with no GPU. Each report has the final result, the
objective's checklist with the findings recorded for each part, and every finding with the page it came from.

| Report | Objective | Turns | Model time |
|---|---|---|---|
| [documentation-venv.md](documentation-venv.md) | Using the official Python documentation: create a virtual environment with venv, activate it on Windows and on macOS or Linux, install packages into it | 5 | 16.4 min |
| [comparison-sqlite-postgresql.md](comparison-sqlite-postgresql.md) | Compare SQLite and PostgreSQL for a small web application, then recommend one for a hobby project | 8 | 35.3 min |

## What to notice, good and bad

**The documentation report** is the clean case. Every finding comes from docs.python.org, as the objective asked,
and every step cites the finding it came from. One slip: the Windows command is given as `\venv\Scripts\activate`,
with a leading backslash; from the folder that holds the environment it is `venv\Scripts\activate`.

**The comparison report** shows how the agent handles a gap, and where a 7B model still falls short.
- The part it could not find, how each database handles many simultaneous users, is said plainly ("No specific
  information was found ...") instead of being filled in from the model's own knowledge.
- Every other claim cites a finding, and the findings are quoted from the page.
- The "My view" paragraph still leans on PostgreSQL's "better scalability for handling many simultaneous users",
  the very thing the findings didn't cover, though it says so in the same sentence.
- The first finding is the single word "administration", which adds nothing.

Check any result against its listed findings before relying on it. That's what the report is laid out for.
