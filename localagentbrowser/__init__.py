"""LocalAgentBrowser: an autonomous web agent driven by a local language model.

A model served by LM Studio decides, one JSON action at a time, whether to SEARCH, NAVIGATE, READ_MORE,
DISCOVER or FINISH; Playwright carries the actions out in a visible Chromium window. Everything the
agent does and finds is written to research_log.txt.

Run:  python agent_browser.py   (or `localagentbrowser` once installed; it asks for the objective)

Settings (environment variables):
    LMSTUDIO_BASE_URL        OpenAI-compatible server address   (default http://localhost:1234/v1)
    LMSTUDIO_MODEL           model identifier                   (default qwen2.5-7b-instruct)
    LMSTUDIO_API_KEY         API key, if the server needs one   (default lm-studio)
    LMSTUDIO_CONTEXT_LENGTH  context length the model is loaded with (default 8192)

The package, one file per job; each imports only from the ones listed before it:
    config      settings and every limit and threshold
    prompts     every fixed text the model reads
    text        small word helpers the checks share
    pages       search results, page parts, the outline and its pointer, links
    grounding   whether a finding is really on the page shown, or already recorded
    checklist   the objective split into a fixed checklist; findings linked to its items
    coverage    the coverage check before finishing, verified in code
    checks      decisions refused or confirmed before they are carried out
    report      the journal, research log, final result and report file
    agent       the loop: each turn's conversation with the model, and main()
"""
