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
  normal Chromium window, just as if you visited them yourself. By default nothing is sent to any cloud AI service (if you turn on cloud models, your prompts and the pages read go
  to OpenRouter and the model's provider); the
  model runs on your own LM Studio server.
- **Keep LM Studio's server local.** The agent talks to the address in `LMSTUDIO_BASE_URL` (by default
  `localhost`). If you expose LM Studio on a network, protect it as you would any other service.
- **Clean Room, a private edition (from 1.67)** has no cloud code at all: it is removed before the edition is built,
  and the build fails if any is left. It also refuses a model server anywhere but this PC or the local network,
  whatever `LMSTUDIO_BASE_URL` says. Both editions block the browser's own calls to Google (account checks, push
  messages, component updates and autofill lookups).
- **Your OpenRouter key (cloud models only).** Keep it in the `OPENROUTER_API_KEY` environment variable of your
  own account. Setup never asks for it and never stores it: the cloud settings file it writes holds no key, and the
  agent sends the key only to OpenRouter. A spending limit in your OpenRouter account caps what a run can cost.
- **The log and report are plain text.** `research_log.txt` and `research_report.md` contain your objective
  and everything the agent found. They stay on your machine and are excluded from git by `.gitignore`.
