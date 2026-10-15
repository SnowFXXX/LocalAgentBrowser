# Changelog

Every version of `agent_browser.py`, newest first. Each version is also a tagged commit (`v1.11` … `v1.28`).
Timings are from live runs on the development machine (Intel i5-9500, 6 cores, no GPU, Qwen2.5-7B-Instruct
Q4_K_M in LM Studio), where reading the prompt takes about 90% of every model call.

## [1.39] - 2026-10-15
### Changed
- The page outline marks parts already read with "(read)", and re-reading a part needs one confirmation (repeating
  the READ_MORE goes through). After a fresh prompt the model no longer saw its earlier reading and went back to
  re-read parts of a setup guide it had read half an hour before, starting with a duplicate discovery.

Regression run on the original Playwright objective (1.38): the most detailed Playwright findings so far, seven
in all, including the per-client `init-agents` setup, the seed test file and how to prompt the agents after
setup; it attempted FINISH at about 40 minutes and was sent to the guide's unread parts. Stopped after 60
minutes when it started re-reading.

## [1.38] - 2026-10-15
### Fixed
- Asked for three Photoshop alternatives and their strengths, the final result listed four and added a
  "My view" paragraph nobody asked for: both rules were late in one long instruction. The final-result step
  now gives its rules as a short numbered list, including "if the objective asks for a specific number of
  items, give exactly that many" and "do not add a 'My view' paragraph unless the objective explicitly asks
  for your view, opinion or recommendation".

Replays on real findings: the list task gave exactly three items (GIMP, Krita, Darktable), cited, with no
opinion; the cats task, which does ask for an opinion, still ended with one, citing all six traditions. The
live run itself found all three items and chose FINISH right after the third (9 turns, 24 minutes of model
time).

## [1.37] - 2026-10-15
On a how-to (a sourdough starter with quantities for each day) the agent finished after 3 of 6 parts of a
recipe, and the final result invented the first water amount ("45 grams (about 1/4 cup)"; 45 g was the
daily feeding), added a step not in any finding, and added an unrequested "My view" with an unsupported
health claim.
### Added
- `research_report.md` flags every number in the final result that appears in none of the discoveries
  ("Numbers not found in the recorded discoveries: 1/4 ... check them before relying on this result").
- The finish check mentions unread parts of the current page; for a long page it points to the outline
  instead of a bare count.
### Changed
- The final result step no longer sees the agent's own FINISH draft (which already contained the invented
  amount), must not fill gaps with its own knowledge or guesses (no amounts, steps or advice the discoveries
  do not contain), says when a requested detail was not found, and adds "My view" only when asked for one.

Replaying the final-result step on that run's findings: no invented amount, no "My view", and the number
check found nothing to flag (it flags "1/4" on the old result), though it left out the flour amounts it did
have. A live rerun showed both new checks working: the agent went looking for the missing details instead of
finishing, but read a long second source part by part, including parts about baking bread rather than the
starter; the long-page wording above addresses that.

## [1.36] - 2026-10-15
### Fixed
- On a comparison the model's own finish check read "Available Libraries: Not covered. Written at FINISH." and
  it finished anyway: it had used "written at FINISH" (meant for its own opinion or recommendation) to skip a
  part it had not found. The check now says only parts asking for the agent's own answer, opinion,
  recommendation or summary are written at FINISH, never information it was asked to find.
- A FINISH whose own reasoning lists a part as "not covered" is sent back once to work on that part (for
  example with a targeted SEARCH), at most twice per run, so a part that cannot be found does not block
  finishing for good.

Rerun (a different source this time): a cited comparison of performance, ease of learning and Python's
libraries with a clear recommendation, in 16 turns and 33 minutes of model time. Rust's libraries were not
covered, and the model summarised its finish check ("all discoveries cover these aspects") instead of
listing each part, so the new check had nothing to act on.

## [1.35] - 2026-10-15
### Fixed
- Asked a one-answer question, the agent recorded the full answer on turn 3, said in its reasoning that "the
  objective has been met", then kept reading the 33-part article for "interesting" details. The note after
  every accepted DISCOVER now asks whether the discoveries cover every part of the objective and, if so, to
  FINISH now; the system prompt also says to gather only what the objective asks for.

Rerun: FINISH on turn 4 with the correct, cited answer (Kilimanjaro, 5,895 m / 19,341 ft), in 12 minutes of
model time.

## [1.34] - 2026-10-14
### Changed
- Fresh prompts show each discovery shortened to 250 characters; the log and report keep the full text, and
  duplicates are still checked against it. The growing discoveries list had become a quarter of every fresh
  prompt.
- The conversation budget is 75% of the context length (was 67%), so fresh prompts are needed less often.
- The finish, progress and objective checks say that a part not found after looking in several places may
  not be available: finish anyway and say in the final result what could not be found.
- The leave check is skipped for a page the agent has recorded something from on any visit, not just the
  current one.
### Fixed
- `research_report.md` is now updated every turn. It was only written when the run ended normally, so a
  killed process (or a crash) left no report.

Regression run on the original Playwright objective, compared with 1.33: fresh prompts averaged 162 s
instead of 234 s and took 16% of the model time instead of 28%. The run was stopped after 50 minutes with
6 findings: with an objective forbidding FINISH until everything is "fully validated", the agent kept
looking for parameter schemas the documentation does not contain.

## [1.33] - 2026-10-14
### Added
- Page outline: a page of 5 or more parts is first shown with a list of the headings in each part, and
  `READ_MORE <number>` jumps straight to a part. On a 34-part guide the agent had been reading part after
  part in order (part 4 of 33 with nothing new); with the outline it jumped straight to "Install the correct
  loop definitions" (part 4) and recorded the four `--loop` values.
### Changed
- The leave check no longer counts the unread parts of a long page (which pushed the model to read them all
  in order); it points to the outline instead.

Regression run on the original Playwright objective: the outline, error-page handling, leave check and
progress check all worked live, and the agent recorded 8 relevant findings, but it had not finished after
72 minutes. Fresh-prompt rebuilds took 28% of the model time, and the part-by-part check kept it looking for
"parameter schemas" the documentation does not contain. The next version addresses both.

## [1.32] - 2026-10-14
### Added
- The final result is written in a separate model call after FINISH, given only the objective, every
  discovery (numbered, with its source) and the agent's draft. It must use only the discoveries, cite them
  by number, and put the agent's own view (when the objective asks for one) in a paragraph starting
  "My view:". The report shows this result; the agent's own FINISH text is kept in the log as `draft`.
  In a live run the agent's reasoning noted that the requested opinion was missing, then finished with a
  list of findings anyway; replaying this step on that run's findings produced a cited result with a clear
  opinion in about 150 s. Citation numbers from a 7B model are not always right, so the README says to check.
- Pages answering with an HTTP error (404, 403 and so on) are recorded as failed navigations with the status,
  and leaving them needs no confirmation. In a live run a guessed URL returned 404 and the agent was asked to
  record something from the error page.
### Notes
- An earlier version of this step continued the browsing conversation to reuse LM Studio's cache. It then
  used page text the agent had read but not recorded, and cited discoveries that did not say it (including
  a claim contradicting the cited discovery). It now always uses a fresh prompt; this was about as fast.

## [1.31] - 2026-10-14
### Fixed
- The model recorded its own opinion as a discovery, attributed to a web page. The part-by-part check now
  says parts asking for the agent's own answer, opinion or summary are "written at FINISH", never as a
  DISCOVER, and the system prompt limits DISCOVER to information found on the page.
- With several pages in one conversation the model lost track of which page was current (it asked for
  "part 3 of 4" of a 3-part page it had moved to). Every result note and refusal now states the current URL
  and part.

Rerun on the cats objective: no opinion was recorded as a discovery and no page confusion occurred, but this
run's search led to a generic blog, and the agent stayed with that single source and finished with a list of
findings instead of an opinion. Results vary strongly with the first page found.

## [1.30] - 2026-10-14
### Changed
- The part-by-part check asks the model to write each part of the objective followed by the discovery that
  covers it (or "not covered"), so claiming full coverage needs evidence for each part.
- The finish check and the scheduled objective check state how many pages the discoveries come from, and
  point it out when everything comes from a single page. This is information, not a rule: some objectives
  need one source, others many.

On the cats objective the agent now named six traditions (Egypt, Norse, Celtic, Islam, Eastern European
folklore, Japan) from two sources and stated its own opinion, in 18 turns (37 minutes of model time) instead
of finishing after one blog. It recorded that opinion as a discovery, though, which the next version fixes.

## [1.29] - 2026-10-14
Testing a very different objective (research how spiritual traditions view cats, then form an opinion)
showed the agent finishing after 9 turns with three findings from a single blog, naming no traditions, and
giving a summary instead of the opinion asked for. Its own reasoning said it should review the objective,
then it finished anyway.
### Added
- Check before finishing: the first FINISH of a turn gets a cheap retry asking the model to split the
  objective into its parts and check each against its discoveries, and to finish only with the result the
  objective asks for (an answer, opinion, summary or deliverable), not just a list of findings. Repeating
  FINISH then goes through.
- `research_report.md`: a readable report with the final result and every discovery with its source,
  written however the run ends (FINISH, the turn limit, Ctrl+C or a lost connection).
### Changed
- The progress and objective checks use the same part-by-part wording instead of asking whether the
  discoveries "satisfy" the objective, which a small model answers yes to too easily.
- The system prompt's FINISH rule asks for every part to be covered and the final result in the parameter.

Rerunning the same objective: the check fired and the model named both parts of the objective, and its final
result now states a position instead of only summarising. It still judged one blog as covering "traditions
around the world", so coverage judgement needs more work.

## [1.28] - 2026-10-13
Preparing for publication.
### Added
- Settings through environment variables: `LMSTUDIO_BASE_URL`, `LMSTUDIO_MODEL`, `LMSTUDIO_API_KEY` and
  `LMSTUDIO_CONTEXT_LENGTH`. The conversation token budget now follows the configured context length.
- Installable package (`pyproject.toml`) with a `localagentbrowser` command.
- Ctrl+C stops the agent with a short message instead of a traceback.
- Module docstring describing the agent, how to run it and its settings.
- Test suite (`tests/`, 67 pytest tests, about a second, no browser or model needed), a live test runner
  (`tools/live_test.py`), ruff linting, and CI on Linux and Windows with Python 3.10–3.13.
- Contributing guide, code of conduct, security policy, issue and pull request templates, and Dependabot.
### Fixed
- The error raised after repeated invalid JSON now keeps the original parsing error as its cause.
### Removed
- The script that hid `navigator.webdriver` from websites. Hiding automation is not something this project
  should do, and DuckDuckGo's HTML search does not need it.

## [1.27] - 2026-10-13
**First version to complete the test objective on its own:** FINISH on turn 13 (about 36 minutes) with
setup commands, the role of each agent and the project file layout recorded.
### Added
- Confirm-before-leaving check: the first attempt to SEARCH or NAVIGATE away from a content page the agent
  has recorded nothing from gets a cheap retry asking it to DISCOVER first (mentioning any unread parts).
  Repeating the same action then goes through, so leaving without a discovery costs one short retry.
  Search result pages are exempt.

## [1.26] - 2026-10-13
### Changed
- Progress and objective checks now tell the model to record what is on the current page first. Repeated
  every turn, the old wording pushed the model to navigate away without reading what was in front of it.
- The progress check fires every 4 turns without a discovery instead of every turn once triggered.
- The journal sent to the model is one line per entry instead of indented JSON: about 40% smaller, which
  makes every full prompt cheaper.

## [1.25] - 2026-10-13
### Added
- Scheduled objective check every 8 turns, even while discoveries continue, because discoveries on loosely
  related topics kept resetting the progress check and the agent drifted off the objective.

## [1.24] - 2026-10-13
### Added
- Progress check: after 4 turns without a new discovery the model is asked to compare its discoveries with
  the objective and either FINISH or say what is missing and go find it. Without it the agent never finished.

## [1.23] - 2026-10-13
### Changed
- Going back to a previously visited page is allowed. Only the page the agent is already on is refused, and
  each URL is capped at 3 visits to stop back-and-forth loops. The old "never revisit" rule kept the agent
  from returning to its best source.
### Fixed
- The FINISH entry, with the agent's final summary, was never written to `research_log.txt`: the loop exited
  before the per-turn save. (Present since the first version.)

## [1.22] - 2026-10-13
### Changed
- Duplicate discoveries are recognised by consecutive word pairs, so line breaks, commas, punctuation and the
  order of lines no longer make old information look new. A discovery that adds even one new line is kept.
- While a conversation continues, a new page lists only links not shown yet. Before, every page on a site
  listed the same 15 menu links and the agent never saw links in the page content.

## [1.21] - 2026-10-13
### Changed
- The conversation also continues across page changes: the new page or search results are appended
  instead of rebuilding the prompt. A fresh prompt is built only near the token budget (or after 12 turns).
### Fixed
- `READ_MORE` parameters such as `"2 of 2"` or `"part 3"` now go to that part instead of simply the next one.

## [1.20] - 2026-10-13
### Added
- Same-page turns continue the previous conversation, appending only what changed (a result note or the
  next page part). DISCOVER and READ_MORE turns dropped from 150–220 s to 35–120 s.
### Changed
- The first cheap-retry correction lists the alternative actions; without them the model resubmitted the
  same refused action.
- Pause between turns reduced from 3 s to 1 s.

## [1.19] - 2026-10-13
### Added
- Cheap retries: a reply that would be refused (or is invalid JSON) is answered with a short correction in
  the same conversation, so LM Studio reuses the processed prompt. A retry costs about 25–45 s instead of a
  whole wasted turn (150–200 s). Up to 2 per turn.
- One place (`check_decision`) for every rule that can refuse a decision before acting.
### Fixed
- Duplicate check treats curly and straight quotes alike.

## [1.18] - 2026-10-13
### Fixed
- The model kept sending DISCOVER with an empty parameter. Replaying the prompt showed it treated DISCOVER as
  a command that would extract for it; one sentence in the system prompt ("you must write the information
  itself into the parameter") fixed it in every replay.

## [1.17] - 2026-10-13
### Added
- Near-duplicate discoveries (same lines in a different order or grouping) are refused.
- After two refused actions in a row the warning lists the actions still available.
- Empty DISCOVER parameters are refused.

## [1.16] - 2026-10-13
### Changed
- Prompt reordered so consecutive turns share a longer identical beginning that LM Studio can reuse: no
  counters in headers, journal trimmed in batches of 6, sections ordered from least to most often changed.
  About 25% faster per turn.

## [1.15] - 2026-10-12
### Added
- Pages are read in parts of about 2,500 characters with a new `READ_MORE` action, instead of being cut off
  at 4,500 characters.
- Navigation menus, footers and cookie/consent banners are hidden before reading a page.
- Parts end at paragraph boundaries; code blocks and tables are never split.
- DuckDuckGo error pages are retried once, and a failed search is not counted as tried.

## [1.14] - 2026-10-12
### Added
- Repeated searches and page visits are blocked in code, with an "already tried" list in the prompt.
- Stronger instruction to DISCOVER relevant information before leaving a page.
### Changed
- Smaller prompts: 8 search results, 200-character snippets, shorter journal thoughts.

## [1.13] - 2026-10-12
### Changed
- Search moved from Google to DuckDuckGo's HTML endpoint; results are parsed into a numbered list of titles,
  real URLs and snippets.
- Pages are read as their main text plus a list of links, so NAVIGATE uses real URLs.
- The journal sent to the model is capped; invalid JSON is retried; the reply is constrained to a JSON schema.
- Page loads wait for the DOM instead of a fixed 10-second sleep plus network idle.
### Fixed
- The duplicate-URL check ignored the query string, so every search after the first was blocked as a
  duplicate (34 of 35 turns in the last run of v1.12).

## [1.12] - 2026-10-12
### Added
- Loop warning: a parameter the model has already used triggers a "pivot your query" warning on the next turn.
### Fixed
- List-type parameters from the model are handled; query values extracted from Google URLs are unwrapped.

## [1.11] - 2026-10-12
### Added
- First version: a local model in LM Studio chooses SEARCH, NAVIGATE, DISCOVER or FINISH as JSON; Playwright
  drives Chromium; a journal of actions and findings is saved to `research_log.txt`.
