# Contributing

Thanks for your interest in LocalAgentBrowser! Bug reports, ideas, test results with other models and pull
requests are all welcome.

## Ways to help

- **Report a bug** or **suggest a feature** using the [issue templates](https://github.com/SnowFXXX/LocalAgentBrowser/issues/new/choose).
- **Share a run.** Results with other models, hardware or kinds of objectives are especially useful: the
  agent has been tuned on seven kinds of objectives (see the README's results table), all with Qwen2.5-7B on
  a CPU.
- **Send a pull request.** For anything bigger than a small fix, please open an issue first so we can agree
  on the approach.

## Development setup

```bash
git clone https://github.com/SnowFXXX/LocalAgentBrowser.git
cd LocalAgentBrowser
python -m venv venv
# Windows: venv\Scripts\activate    macOS/Linux: source venv/bin/activate
pip install -r requirements-dev.txt
python -m playwright install chromium   # only needed for real runs, not for the tests
```

## Before opening a pull request

```bash
python -m pytest        # about a second; no LM Studio or browser needed
python -m ruff check .
```

If your change affects how the agent behaves, please also try it for real and mention the result in the
pull request:

```bash
python tools/live_test.py "Your objective here" --minutes 25
```

## Guidelines

- **Keep the agent general-purpose.** It is meant to handle any objective. Rules, filters and prompts should
  not assume what kind of task it is doing or what kind of content matters.
- **Measure speed changes.** On slow hardware, most of each turn is the model reading the prompt. If a change
  is meant to make the agent faster, include before-and-after timings (the live test runner prints them).
- **Add or update tests** for behaviour changes. `tests/fakes.py` has a scripted model and a fake website, so
  even the main loop can be tested without real services.
- **Explain the why.** Commit messages and changelog entries in this project say what changed and why,
  ideally with the observation or measurement behind it. Please add an entry under a new version at the top
  of [CHANGELOG.md](CHANGELOG.md).
- Keep the code style consistent with the surrounding code: named constants at the top of
  `agent_browser.py` with a comment explaining each, and comments that explain why rather than what.

By participating you agree to follow the [Code of Conduct](CODE_OF_CONDUCT.md).
