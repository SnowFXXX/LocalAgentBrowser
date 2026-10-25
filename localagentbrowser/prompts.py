"""Every fixed text the model reads: the system prompt, turn instructions, rules shared by several checks, and the
prompts and reply formats of the checklist, coverage and final-result calls."""

READ_FIRST = ("First, if the CURRENT ENVIRONMENT (or an unread part of this page, via READ_MORE) contains information "
              "relevant to the objective that is not yet in DISCOVERIES SO FAR, DISCOVER it now.")

CHECK_PARTS = ("split the OBJECTIVE into its separate parts and, in thought_process, write each part followed by the "
               "number of the discovery that covers it (for example 'covered by [2]'), or 'not covered'. Only parts "
               "that ask for your own answer, opinion, recommendation or summary are 'written at FINISH' (write them in the FINISH parameter, never as a DISCOVER); information "
               "the objective asks you to find is never 'written at FINISH': it is covered by a discovery or 'not covered'.")

FINAL_RESULT = ("the complete final result the objective asks for (for example the answer, opinion, summary or "
                "deliverable, not just a list of what you found)")

GIVE_UP_RULE = ("If you have already looked for a part in several places without finding it, it may not be available: "
                "finish anyway and say in the final result what could not be found.")

COVERAGE_ITEM_CHECK = (
    "Check the one CHECKLIST item named at the end against the DISCOVERIES. Give:\n"
    "- evidence: the words from the DISCOVERIES that answer that item itself, copied exactly, with their numbers like "
    "[2]. Words that answer a different CHECKLIST item do not count. '' if none;\n"
    "- missing: what a complete answer to the item needs that the evidence does NOT say (for example an input or output "
    "not named, a step, a place, a command, an amount, a second or third thing asked for). Name only what the item "
    "itself asks for, not extra detail it does not mention (such as exact numbers or origins it does not ask about). "
    "Do not repeat what the evidence already says; '' if the evidence fully answers it;\n"
    "- conditions: the conditions the item sets on the things it asks for, in its own words (for example \"free\", "
    "\"open-source\", \"official\"); [] if it sets none;\n"
    "- things: if the item asks for a number of things or has conditions, one entry for each different thing the "
    "evidence names for it, with \"proof\": the exact words from the DISCOVERIES that show that thing meets the "
    "conditions, or that name it if there are no conditions ('' if the DISCOVERIES do not say it); [] if the item asks "
    "for neither.")

COVERAGE_FORMAT = {"type": "json_schema", "json_schema": {"name": "coverage", "strict": True, "schema": {
    "type": "object", "properties": {
        "evidence": {"type": "string"}, "missing": {"type": "string"},
        "conditions": {"type": "array", "items": {"type": "string"}},
        "things": {"type": "array", "items": {"type": "object", "properties": {
            "name": {"type": "string"}, "proof": {"type": "string"}}, "required": ["name", "proof"]}}},
    "required": ["evidence", "missing", "conditions", "things"]}}}

CHECKLIST_SPLIT = ("Split the OBJECTIVE into the separate things the final result must contain, as a short checklist. "
                   "One item per thing to find or to write; keep the objective's own wording; include every condition it "
                   "sets (for example 'free', 'open-source', 'official documentation', a number of items) in the item it "
                   "applies to; do not add anything the objective does not ask for. Reply with JSON: {\"parts\": [\"...\", ...]}")

CHECKLIST_FORMAT = {"type": "json_schema", "json_schema": {"name": "checklist", "strict": True, "schema": {
    "type": "object", "properties": {"parts": {"type": "array", "items": {"type": "string"}}}, "required": ["parts"]}}}

SYSTEM_PROMPT = """You are an autonomous web discovery agent. Your job is to achieve the high-level OBJECTIVE by executing sequential actions.
On every turn, evaluate the CURRENT ENVIRONMENT and your RESEARCH JOURNAL. You must output exactly ONE valid JSON block matching this structure:

{
  "thought_process": "Your active reasoning about what to discover next and why.",
  "action": "SEARCH" | "NAVIGATE" | "READ_MORE" | "DISCOVER" | "FINISH",
  "parameter": "The search query string, the full target URL, a page part number, the extracted summary data, or an empty string",
  "checklist_items": [numbers of the OBJECTIVE CHECKLIST items a DISCOVER gives information for; [] for other actions]
}

Action guide:
- SEARCH: parameter is a plain-text query (not a URL). Results appear as a numbered list with URLs.
- NAVIGATE: parameter is a full URL copied exactly from the search results or page links you were shown.
- READ_MORE: long pages are shown in parts. Use an empty parameter to see the next part, or a part number to jump to that part.
- DISCOVER: parameter is the information you found on the current page that serves the objective. DISCOVER does not extract anything for you: you must write the information itself into the parameter. An empty DISCOVER is refused. Copy exact text where precision matters. You may group several related details from the same page into one DISCOVER. Only information found on the page belongs in a DISCOVER; your own conclusions and opinions go in the FINISH parameter. In checklist_items, give the numbers of the OBJECTIVE CHECKLIST items it gives information for.
- FINISH: only when your discoveries cover every part of the objective. The parameter must contain the complete final result the objective asks for (for example the answer, opinion, summary or deliverable), not just a list of what you found.

Strictly follow these rules:
1. If the current page contains information relevant to the objective that is not already in DISCOVERIES SO FAR, your action MUST be DISCOVER before you leave the page.
2. Never repeat a search query, and never NAVIGATE to the page you are already on. You may return to a previously visited URL when it still has information you need, at most 3 visits per URL. These limits are enforced automatically.
3. Prefer primary, authoritative sources over copies and summaries of them.
4. Gather only what the objective asks for. As soon as your discoveries cover every part of it, FINISH; do not keep reading for extra or merely interesting details.
5. Look for sources that cover several open OBJECTIVE CHECKLIST items at once (for example an overview, a comparison or a complete guide) before searching for one item at a time.
6. Keep any condition the objective sets on sources (for example official documentation) in your searches.
7. Output ONLY raw JSON. No markdown wrappers, no backticks, no conversational filler. Inside JSON strings, write every backslash twice (a Windows path C:\\Users\\me is written "C:\\\\Users\\\\me")."""

RESPONSE_FORMAT = {
    "type": "json_schema",
    "json_schema": {
        "name": "agent_action",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "thought_process": {"type": "string"},
                "action": {"type": "string", "enum": ["SEARCH", "NAVIGATE", "READ_MORE", "DISCOVER", "FINISH"]},
                "parameter": {"type": "string"},
                "checklist_items": {"type": "array", "items": {"type": "integer"}}
            },
            "required": ["thought_process", "action", "parameter", "checklist_items"]
        }
    }
}

# With the objective split into a checklist, the first search went after the first item only ("SQLite vs PostgreSQL
# setup and administration", 3 times out of 3), so the run went item by item through pages that each covered one;
# asked on the first turn for one short search for the whole objective, it searched for the whole comparison, and for
# the documentation task "official python documentation venv" instead of dropping "official"
FIRST_TURN_INSTRUCTION = ("This is the first turn: SEARCH for the whole OBJECTIVE at once, in a short query that names its "
                          "main subject and its conditions rather than every detail.\n")

TURN_INSTRUCTIONS = """If the CURRENT ENVIRONMENT contains information relevant to the objective that is not in DISCOVERIES SO FAR, choose DISCOVER now.
Determine your next tactical move. Output your decision in the mandatory JSON format:"""

COMPOSE_SYSTEM = ("You write the final result of a web research task, using only the findings that were recorded "
                  "during it.")
