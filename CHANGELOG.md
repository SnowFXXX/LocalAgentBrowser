# Changelog

Every version of `agent_browser.py`, newest first. Each version is also a tagged commit (`v1.11` … `v1.28`).
Timings are from live runs on the development machine (Intel i5-9500, 6 cores, no GPU, Qwen2.5-7B-Instruct
Q4_K_M in LM Studio), where reading the prompt takes about 90% of every model call.

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
