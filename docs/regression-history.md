# Regression history

Every change since 1.53 has been measured on the regression suite (`tools/regression.py`): the same seven
objectives, one of each kind of task, run one after the other on the development machine (Intel i5-9500, no GPU,
Qwen2.5-7B-Instruct Q4_K_M in LM Studio, 8,192-token context). Single runs vary with the pages a search turns up, so
each step was judged on all seven at once, and the run folders were read, not just their scores.

Versions 1.54 to 1.56 were **internal steps**: each was labelled with its own version number for a suite run, and
none was committed or released on its own. They are released together as 1.57. This page keeps their results so
they can be referred to. Run folders (`regression_runs/<version>_<date-time>/`) are local and not in the
repository; the numbers below are taken from them.

Times are model minutes, the time spent in model calls. Each task has a time limit (45 minutes, 60 for the
explanation; the documentation task had 30 until 1.56). The how-to task changed in 1.55, see below.

## Results by step

| Task | 1.52 | 1.53 | 1.54 | 1.54 fixed | 1.55 | 1.56 | 1.57 |
|---|---|---|---|---|---|---|---|
| Factual question | ✅ 11.9 | ✅ 13.6 | ✅ 17.0 | ✅ 15.7 | ✅ 18.5 | ✅ 16.3 | ✅ 13.2 |
| List of three office suites | ❌ 40.9 | ✅ 30.1 ¹ | ✅ 36.9 ² | ✅ 20.7 ³ | ❌ 43.5 | ✅ 37.0 ⁴ | ❌ 43.9 |
| Comparison and recommendation | ❌ 43.4 | ✅ 39.0 | ❌ 43.5 | ❌ 39.8 | ✅ 28.7 ⁵ | ✅ 36.5 | ❌ 38.8 |
| How-to (repotting; cold brew from 1.55) | ❌ 42.2 | ❌ 39.7 | ❌ 43.8 | ✅ 31.0 ⁶ | ✅ 42.1 | ❌ 43.4 | ✅ 35.8 ⁷ |
| Explanation (photosynthesis) | ❌ 57.9 | ✅ 12.9 ⁸ | ✅ 38.0 | ✅ 46.6 | ✅ 33.5 ⁹ | ❌ 58.1 | ✅ 59.0 |
| Opinion (owls) | ❌ 43.0 | ✅ 38.1 | ✅ 32.4 | ✅ 44.2 | ✅ 37.6 | ❌ 44.0 | ❌ 42.1 ¹² |
| Documentation (venv, official docs) | ✅ 15.1 ¹⁰ | ❌ 26.4 | ❌ 27.1 | ❌ 26.8 ¹⁰ | ❌ 28.6 | ✅ 36.8 ¹¹ | ✅ 36.7 ¹¹ |
| **Finished** | **2 of 7** | **5 of 7** | **4 of 7** | **5 of 7** | **5 of 7** | **4 of 7** | **4 of 7** |
| **Total model minutes** | 254 | 200 | 239 | 225 | 233 | 272 | 270 |

✅ finished, ❌ did not finish within its time limit. Notes on the results themselves:

1. WPS Office, which is not open source, as the third suite.
2. LibreOffice and OnlyOffice, and "the third alternative was not found" (honest, incomplete).
3. LibreOffice listed three times to reach three.
4. LibreOffice and OnlyOffice proven open source by targeted searches; WPS Office still listed, without a citation.
5. Its "features" section listed full-text search and JSON support without citations; in 1.56 the result said
   instead that no features were found.
6. First finish of the repotting task; the care for the first two weeks was invented in the final result (no
   discovery covered it).
7. Quantities and steps right; "how long it keeps" answered with the steeping time instead of the shelf life.
8. From a single overview paragraph: no oxygen or water.
9. No glucose; the stages only located, not described.
10. From tutorial sites, not the official documentation the objective names (1.52 and 1.54 fixed).
11. Every finding from python.org, but the Windows line repeats the create command instead of the activate one.
12. Its first page covered two of the three cultures asked for; it tried to finish as time ran out.

## What each step changed, and what the suite showed

### 1.53 (released): objective checklist, regression suite
The objective is split once into a fixed checklist and every check goes through it. 2 → 5 of 7 finished. Left open:
a finding claimed for every item (the explanation from one paragraph) and conditions (WPS Office as open source).

### 1.54: coverage check before finishing (run 2026-10-17 16:06)
Before FINISH, each checklist item is checked against the discoveries in a call of its own; conditions are verified
in code (the proof must be copied from a discovery that names the thing and states the condition). A FINISH with
gaps is sent back once. Wikipedia's fundraising banner, which filled the whole first part of the Kilimanjaro article
on 2026-10-17, is hidden with the other site notices. Finishing with nothing recorded is sent back too.

Result: 4 of 7. The explanation became complete (water, CO2 and light in; oxygen and glucose out) and the list
honest; the comparison lost, searching item by item through pages that each covered one item. A false send-back on
the opinion task treated "in different cultures or religions" as a condition.

### 1.54 fixed (run 2026-10-18 07:58)
Only conditions of up to two words count; a rule to look for sources covering several open items at once; a refusal
of a DISCOVER not on the page quotes the single closest page sentence (chosen by the refused text and the objective)
or names what the page never mentions, and is no longer wrapped in "Do NOT choose DISCOVER again ... choose a
different action" (replays: the real aftercare line recorded 6 of 6 times where it had been 0 of 3; a read-on case
6 of 6; an owl case 5 of 6 reading on).

Result: 5 of 7. The repotting task finished for the first time. Two calls of 626 and 651 seconds came right after a
send-back: the separate coverage prompts had pushed the conversation out of LM Studio's cache (which keeps only the
last prompt), and the next turn re-read the whole conversation.

### 1.55 (run 2026-10-18 14:26)
Coverage questions asked as a continuation of the browsing conversation (the cache kept; the turn after a
send-back took 21-29 s in replay, 128 s live); a count check ("only 1 of 3 found"); the final result never repeats
an item to reach a number; a not-found safeguard (items no discovery answers are left out of what the final result
is asked to write, and the code reports them); one short search for the whole objective on the first turn; a rule to
keep source conditions such as "official documentation" in searches. The core how-to was replaced by the cold brew
objective: the repotting task went to the same guide in three runs, and that guide has no section on care in the
first two weeks (kept as an extra task, id `howto`).

Result: 5 of 7. The comparison finished in 28.7 minutes from one search for the whole comparison; the documentation
task used only python.org. But inside the conversation the coverage check was lenient: it passed the explanation
without glucose, and the not-found safeguard never fired. The list ran out of time re-reading one page for
open-source proof.

### 1.56 (run 2026-10-18 21:00)
Coverage questions back in separate prompts (replay: the explanation's gaps found 2 of 2 times, against 0 of 2 inside
the conversation), with the item last so the instructions are read once (the check took about 1.5 minutes instead of
3 to 4.5); a fresh prompt after a send-back instead of re-reading the conversation (471 and 541 s, against 626-651);
condition gaps name the next step ("SEARCH for 'OnlyOffice free and open-source' to check it, or replace it"; replay:
4 of 4 searched); the documentation task's limit raised to 45 minutes.

Result: 4 of 7. The list followed the searches and proved two suites open source; the documentation task finished
from the official docs. Three finishes were lost: a false condition on cold brew ("in the fridge"), an explanation
sent back for "H2O, O2" that a discovery already held, and an opinion task that never finished because the checklist
showed its opinion item as "discoveries: none yet".

### 1.57 (released; run 2026-10-19 17:15)
Conditions are checked only on items that ask for a number of things; things still not shown to meet a condition
before the final result are left out of it and reported by the code; opinion items are shown as "written by you at
FINISH"; a short "missing" claim whose every key word is already in the discoveries is dropped.

Result: 4 of 7 (270 minutes). Cold brew finished without a send-back; the explanation got one send-back, for
an item nothing answered, and finished with a complete answer. Two finishes were lost to send-backs for the model's
own verdicts: on the list, three correct open-source suites were sent back because no words showed them to be
"free"; on the comparison, for "official documentation", which the objective does not ask for. The opinion
task ran out of time on a page covering two of the three cultures asked for, and the documentation task finished
from python.org for the second run in a row.

## What the steps showed overall

- **The fixed checklist was the big step** (1.53). The model no longer invents parts of the objective or loses
  parts of it.
- **The model's judgement of "is this item covered?" is the weak link.** Over the four runs with a coverage check,
  about half of its send-backs were wrong, and the wrong ones cost finishes. What held up were the parts the code
  verifies: an item nothing was copied for, fewer things than asked for, nothing recorded at all, and the
  word-for-word proof of conditions.
- **A send-back costs 8-10 minutes** on this hardware: a fresh prompt reads 3,000-4,000 tokens at about 8 per
  second. Fresh prompts are now the most expensive kind of turn.
- **A site can decide a task.** The repotting guide could not answer one part of the objective in any run; the suite
  now flags a task that fails again on the same site.

## Next step (not in 1.57)

Send the agent back only for gaps the code verifies (an item nothing answers, fewer things than asked for, nothing
recorded), and let the model's other verdicts only shape the final result through the not-found and left-out
reports. In the 1.57 run this would have kept the list's and the comparison's correct finishes.
