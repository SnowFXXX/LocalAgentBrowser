# Regression history

Every change since 1.53 has been measured on the regression suite (`tools/regression.py`): the same seven
objectives, one of each kind of task, run one after the other on the development machine (Intel i5-9500, no GPU,
Qwen2.5-7B-Instruct Q4_K_M in LM Studio, 8,192-token context). Single runs vary with the pages a search turns up, so
each step was judged on all seven at once, and the run folders were read, not just their scores.

Versions 1.54 to 1.56 and 1.58 to 1.60 were **internal steps**: each was labelled with its own version number for a
suite run, and none was committed or released on its own. They were released with 1.57 and 1.61. This page keeps
their results so they can be referred to. Run folders (`regression_runs/<version>_<date-time>/`) are local and not in the
repository; the numbers below are taken from them.

Times are model minutes, the time spent in model calls. Each task has a time limit (45 minutes, 60 for the
explanation; the documentation task had 30 until 1.56). The how-to task changed in 1.55, see below.

## Results by step

| Task | 1.52 | 1.53 | 1.54 | 1.54 fixed | 1.55 | 1.56 | 1.57 | 1.58 | 1.59 | 1.60 | 1.61 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| Factual question | ✅ 11.9 | ✅ 13.6 | ✅ 17.0 | ✅ 15.7 | ✅ 18.5 | ✅ 16.3 | ✅ 13.2 | ✅ 9.5 | ✅ 9.3 | ✅ 9.5 | ✅ 16.6 |
| List of three office suites | ❌ 40.9 | ✅ 30.1 ¹ | ✅ 36.9 ² | ✅ 20.7 ³ | ❌ 43.5 | ✅ 37.0 ⁴ | ❌ 43.9 | ✅ 40.1 ¹³ | ✅ 36.6 ¹⁶ | ✅ 33.3 ¹⁹ | ✅ 20.9 ²³ |
| Comparison and recommendation | ❌ 43.4 | ✅ 39.0 | ❌ 43.5 | ❌ 39.8 | ✅ 28.7 ⁵ | ✅ 36.5 | ❌ 38.8 | ❌ 41.9 | ❌ 42.2 | ❌ 43.5 | ✅ 26.1 ²⁴ |
| How-to (repotting; cold brew from 1.55) | ❌ 42.2 | ❌ 39.7 | ❌ 43.8 | ✅ 31.0 ⁶ | ✅ 42.1 | ❌ 43.4 | ✅ 35.8 ⁷ | ✅ 18.1 ¹⁴ | ✅ 32.9 ¹⁷ | ✅ 20.5 ²⁰ | ✅ 33.0 ²⁵ |
| Explanation (photosynthesis) | ❌ 57.9 | ✅ 12.9 ⁸ | ✅ 38.0 | ✅ 46.6 | ✅ 33.5 ⁹ | ❌ 58.1 | ✅ 59.0 | ✅ 40.1 | ✅ 23.6 ²¹ | ✅ 15.1 ²¹ | ✅ 38.9 |
| Opinion (owls) | ❌ 43.0 | ✅ 38.1 | ✅ 32.4 | ✅ 44.2 | ✅ 37.6 | ❌ 44.0 | ❌ 42.1 ¹² | ❌ 42.3 | ✅ 21.6 ¹⁸ | ✅ 17.0 | ✅ 42.3 ²⁶ |
| Documentation (venv, official docs) | ✅ 15.1 ¹⁰ | ❌ 26.4 | ❌ 27.1 | ❌ 26.8 ¹⁰ | ❌ 28.6 | ✅ 36.8 ¹¹ | ✅ 36.7 ¹¹ | ✅ 22.4 ¹⁵ | ❌ 43.3 | ✅ 31.7 ²² | ❌ 44.3 ²⁷ |
| **Finished** | **2 of 7** | **5 of 7** | **4 of 7** | **5 of 7** | **5 of 7** | **4 of 7** | **4 of 7** | **5 of 7** | **5 of 7** | **6 of 7** | **6 of 7** |
| **Total model minutes** | 254 | 200 | 239 | 225 | 233 | 272 | 270 | 214 | 209.5 | 170.6 | 222.1 |

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
13. LibreOffice, and OnlyOffice marked "(not shown in the pages read to be free and open-source)"; "the third
    alternative was not found".
14. "How long it keeps in the fridge was not found" instead of the steeping time.
15. First fully correct run: `tutorial-env\Scripts\activate` on Windows, all from docs.python.org.
16. LibreOffice and Apache OpenOffice, cited; FreeOffice marked "(not shown in the pages read to be free and
    open-source)", and what it is best at not found.
17. Fully correct: what you need, the steps, and "stored in the refrigerator for up to 2 weeks", all cited.
18. Two American Indian tribes for "at least three cultures", from a single discovery ("no additional cultures were
    found"). The count check ran only on items with a condition such as "open-source" (fixed in 1.60).
19. OnlyOffice, WPS Office and Collabora Online, none shown to be open source; only OnlyOffice was marked. A bug,
    fixed after the run: the identical note already added to OnlyOffice counted as the note for the other two.
20. The steps stop after step 2 (no steeping time, no straining), and "1 pound" of coffee became "1/2 pound".
21. Inputs, outputs and where each stage happens, from one overview paragraph; what happens in each stage is missing.
22. From docs.python.org; the Windows activation command reported as not found (the Windows line is the create
    command again).
23. LibreOffice, WPS Office and OnlyOffice, each marked "(not shown in the pages read to be free and open-source)":
    honest about the page, which never said so, though LibreOffice and OnlyOffice are open source and WPS Office is
    not.
24. First finish since 1.56: setup and administration from datacamp.com's Administration part, reached by the
    outline pointer from part 1; "many simultaneous users" thin (performance, file locking).
25. Every step, the 16-hour steep and "up to 2 weeks"; the result added "within 7 days", which no discovery says
    (flagged by the number check).
26. Four cultures (Native American, Chinese, Celtic, Noongar), cited, with the view written into the same paragraph.
27. All four items found by turn 12; the next turn was a fresh prompt (the conversation had reached its token
    budget), which does not carry the "every item has the discoveries it asks for" advice, and it read on and
    searched again.

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

### 1.58 (internal; run 2026-10-20 00:41)
The agent is sent back only for gaps the code verifies (an item nothing was copied for, fewer things than asked
for, nothing recorded); the model's other verdicts are logged "(not sent back)" and only shape the final result. A
thing not shown to meet a condition stays in the result, marked where it is first named ("**OnlyOffice** (not shown
in the pages read to be free and open-source)"), instead of being left out. Refusal reasons in the journal are cut to
120 characters (the journal section of fresh prompts 54% smaller over the 1.57 runs) and links whose text is HTML are
skipped. Quick rounds before the run: asked inside the conversation, the coverage check missed a gap that separate
prompts found, so it stays in separate prompts.

Result: 5 of 7 in 214 minutes, with no send-backs at all; the fastest full run since 1.53. Factual 9.5 minutes; the
list finished honestly (LibreOffice, OnlyOffice with its caveat, "the third alternative was not found"); cold brew in
18.1 minutes with "how long it keeps in the fridge was not found"; the explanation in 40.1 minutes; the documentation
task fully correct for the first time (`tutorial-env\Scripts\activate`, all from docs.python.org). The comparison
researched item by item and the opinion task kept collecting after three cultures; neither finished.

### 1.59 (internal; run 2026-10-20)
A description named as one of a list's "things" is ignored (it had made two office suites count as three); when every
item has the discoveries it asks for, and there are at least as many different discoveries as the items ask for in
all, the note after a discovery says to finish now (replay of the owl task: it searched again 2 of 2 times before, and
finished or recorded the next culture on the page after); 8 links per page instead of 15, those with a few words of
text first (in 4 suites the agent opened 39 pages, all from search results, none from page links, while links were
more than half of every new page shown).

Result: 5 of 7 in 209.5 minutes, the lowest total until then. Cold brew fully correct for the first time; the
explanation in 23.6 minutes; the opinion task finished, but with two tribes for three cultures. The documentation task
had all four items by turn 12 and read four more parts, answering each read-on check "none" and reading on. The
comparison ran out of time for the third run in a row, the second on datacamp.com (flagged by the suite).

### 1.60 (internal; run 2026-10-21 09:53)
Every item that asks for a number is counted, not only items with a condition: the coverage check names the
different things the evidence gives for it, and fewer than asked for is a gap the agent is sent back for (replay: the
1.59 owl finish sent back 2 of 2 times; the 1.55-1.57 owl moments with three or four cultures 0 of 6). "The first two
weeks" and other amounts of time or measure are no longer read as a count. Once every item has the discoveries it asks
for, the read-on check asks for FINISH instead of offering to read on (replay of the 1.59 documentation run's parts
8-10: read on 6 of 6 before, FINISH 9 of 9 after).

Result: 6 of 7 in 170.6 minutes, the most finished and the lowest total so far. The documentation task finished
(31.7 minutes); the opinion task found four cultures on one page in 17.0 minutes; the explanation took 15.1. But the
new read-on wording never fired in this run, and the count found a real shortfall only on the list (sent back once:
"only 2 of 3 found"), so most of the gain is the paths the runs took, not the changes. Two results were worse: the
cold brew steps stopped after step 2, and the list named three suites none of which the pages showed to be open
source, with the caveat on only the first (bug fixed after the run). The comparison ran out of time again, reading
datacamp.com part by part from the top; that page's outline does cover administration, features and scalability, so
the task stays as it is.

### 1.61 (released; run 2026-10-21 18:14)
An outline pointer for long pages. When the read-on check fires on a page with an outline, the code looks for an
unread part whose label names a word of an item with nothing recorded yet ("Administration" for "setup and
administration"), and a plain READ_MORE then reads that part instead of the next one. Words that say what the whole
page is about do not count (the page title's words, words every item shares such as "SQLite"), nor do item verbs
("compare"), and a part that starts inside code is skipped (on the venv docs, "Install pip in the virtual
environment." was a docstring). Hyphenated words stay whole ("open-source" matched "Can I open and edit" when split).
Seven quick rounds: told to use the outline, the model jumped 0 of 6 times on datacamp; told which part, it went
there 3 to 4 of 6 times; with the code making the jump on a plain READ_MORE, every time (9 of 9 plain repeats). An
offline audit of 63 long-page moments in the saved runs left only sensible pointers ("Light-dependent reactions" on
the Photosynthesis article, "You will need / Method" on a cold brew page). Also: when several things share a caveat,
each is marked (the identical note next to the first had counted for all of them).

Two ideas were measured and not used. Checking a discovery's link to its item by shared words: 75 of 247 links in
the saved journals share no key word with their item, and most are right ("5,895 m" for "how tall in meters",
"concurrent connections" for "simultaneous users"). A larger context (16,384 tokens instead of 8,192) would about
halve the fresh prompts that cost 4-5 minutes each when a conversation reaches its budget (about 9 of the 1.60
comparison's 43.5 minutes), but it needs more memory; the default stays where a modest PC can run it.

Result: 6 of 7 in 222.1 minutes. The comparison finished for the first time since 1.56 (26.1 minutes, 7 turns),
reaching datacamp.com's Administration part by the pointer; the list, now with every suite marked, in 20.9. The
documentation task found all four items and ran out of time after a fresh prompt dropped the advice to finish. The
total is higher than 1.60's 170.6 because the explanation, opinion and cold brew runs went to longer pages this time
(38.9, 42.3 and 33.0 minutes), each finishing with a fuller result.

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

## Next step (after 1.61)

- **Fresh prompts lose the finish advice.** A fresh prompt does not carry "every item has the discoveries it asks
  for: choose FINISH now"; the documentation task read on after one on 1.61.
- **Incomplete steps.** A how-to recorded two of a recipe's steps and finished; nothing checks that a list of steps
  reaches its end.
