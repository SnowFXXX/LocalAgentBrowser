# Security Policy

## Reporting a vulnerability

Please **do not open a public issue** for security problems. Instead, report them privately through
GitHub: go to the repository's **Security** tab and choose **Report a vulnerability**. You will get a response
as soon as possible, and credit for the report if you would like it.

Only the latest version is supported with fixes.

## Things to know before running the agent

- **Web pages go into the model's prompt.** Any page the agent opens can contain text written to manipulate a
  language model (prompt injection). The agent can only search, open pages, read and record findings: it never
  clicks, fills in forms, signs in or runs downloaded files. Even so, treat its findings as
  unverified, especially from unfamiliar sites.
- **It browses the real web.** Search queries go to DuckDuckGo, and the pages the agent chooses are loaded in a
  normal Chromium window, just as if you visited them yourself. Nothing is sent to any cloud AI service; the
  model runs on your own LM Studio server.
- **Keep LM Studio's server local.** The agent talks to the address in `LMSTUDIO_BASE_URL` (by default
  `localhost`). If you expose LM Studio on a network, protect it as you would any other service.
- **The log and report are plain text.** `research_log.txt` and `research_report.md` contain your objective
  and everything the agent found. They stay on your machine and are excluded from git by `.gitignore`.
