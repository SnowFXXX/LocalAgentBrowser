# Results by kind of task

Live runs on the development machine (CPU only, Qwen2.5-7B), from the README before 1.65. Every step since 1.53, on all models, is in [regression-history.md](regression-history.md).

## Single runs, by kind of task

Live runs on the development machine (CPU only, Qwen2.5-7B). Each kind of task is used to find and fix what
does not generalise; the [changelog](https://github.com/SnowFXXX/LocalAgentBrowser/blob/main/CHANGELOG.md) has the details.

| Kind of task | Objective (abridged) | Version | Outcome |
|---|---|---|---|
| Documentation research | Setup commands and file layout for Playwright's test agents | 1.27 | Finished on its own in 13 turns (~36 min) with commands, roles and file layout |
| Documentation research, strict | The same, but "do not FINISH until fully validated", including "parameter schemas" | 1.38–1.40, 1.50 | 1.38–1.40: its most detailed findings (per-client setup, seed test, agent definition files), but it kept searching for schemas the docs do not contain and did not finish within an hour. 1.50: finished in 10 turns (25 min) with the official init command and file layout, but with thin findings and without saying the schemas were not found |
| Opinion from several sources | How spiritual traditions view cats, then form an opinion | 1.30 | Six traditions from two sources and a stated opinion, in 18 turns (37 min of model time) |
| Opinion from several sources | How at least three cultures or religions have viewed owls, then an opinion on why | 1.47–1.48 | Greek, English and many Native American views from one detailed source in 11 turns (38 min); 1.48 adds the opinion the result had left out and catches an invented sentence in a discovery |
| Factual question | Tallest mountain in Africa, height in meters and feet | 1.35, 1.40, 1.46 | Correct, cited answer in 4 turns (about 12 min of model time) in all three versions; before 1.35 it kept reading after finding it |
| Comparison and recommendation | Python vs Rust for command-line tools; recommend one for a beginner. Later: SQLite vs PostgreSQL for a small web app, then a recommendation for a hobby project | 1.36, 1.42, 1.50 | Python vs Rust: cited comparison recommending Python in 16 turns (33 min), Rust's libraries not covered. SQLite vs PostgreSQL: all three parts covered and cited, with a recommendation, in 13 turns (38 min); on 1.50 in 8 turns (25 min) |
| List of items | Three free, open-source Photoshop alternatives and the strengths of each | 1.37–1.38 | GIMP, Krita and Darktable with their strengths, all cited, in 9 turns (24 min); 1.37 listed a fourth and added an unrequested opinion, which 1.38 fixes |
| List of items | Three free, open-source alternatives to Microsoft Office and what each is best at | 1.46, 1.51 | LibreOffice and OnlyOffice with their strengths, cited, in 18 turns (49 min); the third, FreeOffice, is free but not open source, and LibreOffice was recorded four times in different words; on 1.51: LibreOffice, OnlyOffice and Apache OpenOffice, all open source, in 7 turns (16 min) |
| Step-by-step how-to | Make a sourdough starter, with quantities and what to do each day | 1.36–1.37 | A clear plan in 7 turns (19 min), but it invented the first water amount; 1.37 stops guessing and flags unsupported numbers, and the agent then went looking for the missing details |
| Step-by-step how-to | Repot a houseplant: what you need, the steps, and care for the first two weeks | 1.43–1.44, 1.51 | 1.43 recorded six steps it wrote itself before reading the part that had them; 1.44 recorded the guide's real steps but took 68 min and its result left one out. 1.51: every step from the page, in 7 turns (29 min), and it says that no care instructions beyond the first watering were found |
| Explanation of a topic | How photosynthesis works: inputs and outputs, both stages and where each takes place | 1.41–1.42, 1.52 | A complete, cited explanation in 18 turns (45 min); 1.41 read four parts of a relevant page without recording them and had not finished after 49 min. 1.52 recorded both stages and where each takes place, but did not finish within 65 min: it read the history sections of a long encyclopedia article twice and never recorded the inputs and outputs |

## The regression suite, by version

Since 1.53, every change is measured on the same seven objectives, one of each kind above, run one after the other
with a time limit each (`tools/regression.py`, see [Testing](../README.md#testing)). Single runs vary with the pages a search
turns up, so the suite compares versions on all seven at once. 1.54 to 1.56 and 1.58 to 1.60 were internal steps
measured this way and released with 1.57 and 1.61; [docs/regression-history.md](https://github.com/SnowFXXX/LocalAgentBrowser/blob/main/docs/regression-history.md)
has every step's results and what each one showed.

| Task | 1.52 | 1.53 (objective checklist) | 1.57 (coverage check) | 1.61 (verified send-backs, outline pointer) | 1.64 (quote-first coverage, send-backs for missing amounts and terms) |
|---|---|---|---|---|---|
| Factual question | Finished, 12 min | Finished, 14 min | Finished, 13 min | Finished, 17 min | Finished, 17 min |
| List (three office suites; three Photoshop alternatives from 1.64) | Time limit | Finished, 30 min; but WPS Office is not open source | Time limit: three correct open-source suites by turn 7, sent back because no words showed them to be "free" | Finished, 21 min: LibreOffice, WPS Office and OnlyOffice, each marked "not shown in the pages read to be free and open-source" | Finished, 34 min: GIMP, Krita and Darktable (the list task asks for Photoshop alternatives from 1.64) |
| Comparison and recommendation | Time limit | Finished, 39 min | Time limit, after a send-back for "official documentation", which the objective does not ask for | Finished, 26 min, setup and administration from the part of a long page the outline pointer chose | Finished, 49 min |
| How-to (cold brew from 1.55; repotting before) | Time limit | Time limit | Finished, 36 min; but "how long it keeps" answered with the steeping time | Finished, 33 min: every step, the 16-hour steep and the shelf life, cited | Finished, 50 min |
| Explanation (photosynthesis) | Time limit | Finished, 13 min, but thin: no oxygen or water | Finished, 59 min: inputs, outputs and both stages, cited | Finished, 39 min | Finished, 73 min, oxygen included |
| Opinion (owls) | Time limit | Finished, 38 min | Time limit: its first page covered two of the three cultures asked for | Finished, 42 min: four cultures, cited, with a view | Finished, 49 min |
| Documentation (venv, official docs) | Finished, 15 min, from a tutorial site | Time limit (30 min) | Finished, 37 min, all from python.org; but the Windows line repeats the create command instead of the activate one | Time limit: all four items found from python.org, then it read on | Time limit: read the reference page part after part without recording (finished in 34 and 42 min in the two v11 runs) |
