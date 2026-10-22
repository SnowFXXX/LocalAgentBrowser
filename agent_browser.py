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
"""
import asyncio
import json
import os
import urllib.parse
import re
import unicodedata
from openai import AsyncOpenAI
from playwright.async_api import async_playwright

BASE_URL = os.environ.get("LMSTUDIO_BASE_URL", "http://localhost:1234/v1")
MODEL = os.environ.get("LMSTUDIO_MODEL", "qwen2.5-7b-instruct")
API_KEY = os.environ.get("LMSTUDIO_API_KEY", "lm-studio")
CONTEXT_LENGTH = int(os.environ.get("LMSTUDIO_CONTEXT_LENGTH", "8192"))

SEARCH_URL = "https://html.duckduckgo.com/html/?q="
REPORT_FILE = "research_report.md"
JOURNAL_BATCH = 6        # The prompt shows 6-11 recent journal entries; old ones are dropped 6 at a time
MAX_JOURNAL_DETAIL_CHARS = 120  # Longest query/URL/request text shown per journal line
MAX_JOURNAL_REASON_CHARS = 120  # Longest refusal reason shown per journal line (the full text is in research_log.txt)
MAX_THOUGHT_CHARS = 120 # Old reasoning is trimmed in the prompt; the full text stays in research_log.txt
MAX_SEARCH_RESULTS = 8
MAX_SNIPPET_CHARS = 200
PAGE_PART_CHARS = 2500   # Pages are shown in parts of about this size; READ_MORE shows the next part
MAX_LINKS = 8           # Links listed per page; most pages are reached from search results
CONTENT_LINK_WORDS = 3   # Links with at least this many words of text are listed before shorter (menu) links
OUTLINE_MIN_PARTS = 5    # Pages with at least this many parts get an outline of what each part covers
MAX_OUTLINE_LINES = 40
MAX_OUTLINE_LABEL = 90
LONG_PAGE_UNREAD = 3     # Above this many unread parts, the leave check points to the outline instead of the count
MAX_JSON_RETRIES = 3
MAX_VISITS_PER_URL = 3   # Returning to an earlier page is allowed; this caps back-and-forth loops
GAP_REFUSAL_LIMIT = 2    # FINISH attempts sent back per run because their own reasoning lists a part as not covered
MAX_CHEAP_RETRIES = 2  # Refused replies re-asked within the same turn before the refusal costs a whole turn
MAX_FOLLOWUPS = 12       # Turns that continue one conversation before a fresh full prompt (the token budget usually ends it first)
MAX_CONVERSATION_TOKENS = int(CONTEXT_LENGTH * 0.75)  # Start fresh with room left for the next page and the reply
MAX_DISCOVERY_PROMPT_CHARS = 250  # Discoveries are shortened to this in fresh prompts; the log keeps the full text
MAX_CHECK_DISCOVERY_CHARS = 150   # Shorter still in the numbered list that comes with the finish and progress checks
GROUNDED_PAIR_SHARE = 0.7  # A DISCOVER sharing fewer of its word pairs with the page text shown is confirmed first
MIN_GROUNDING_PAIRS = 20   # Shorter discoveries are not checked: a few connecting words sink their score
GROUNDED_SENTENCE_SHARE = 0.35  # Any one sentence matching the page text shown less than this is confirmed too
MIN_SENTENCE_PAIRS = 8     # Shorter sentences are not scored on their own
UNGROUNDED_PAIR_SHARE = 0.4       # Below this (whole) or the next (any sentence) a DISCOVER is refused outright
UNGROUNDED_SENTENCE_SHARE = 0.25
MAX_CLOSEST_CHARS = 200   # Longest page sentence quoted when a DISCOVER is refused as not on the page
TURN_PAUSE_SECONDS = 1
STALL_TURNS = 4          # Turns without a new discovery before the model is asked to check its progress
COMPOSE_MAX_TOKENS = 900  # Length limit for the separately written final result
VIEW_MAX_TOKENS = 250     # Length limit for a 'My view:' paragraph added when the result left it out
OPINION_REQUEST = re.compile(r"\b(opinion|your (own )?view|recommend\w*|what do you think)\b", re.I)
OBJECTIVE_CHECK_EVERY = 8 # Turns between scheduled objective checks, which happen even while discoveries continue
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
MAX_CHECKLIST_ITEMS = 8   # Most parts an objective is split into; a longer split is cut, a failed one is the whole objective
MAX_CHECKLIST_ITEM_CHARS = 200
SPLIT_MAX_TOKENS = 300
COVERAGE_MAX_TOKENS = 400  # Length limit for the check of one checklist item before finishing
MAX_CONDITION_WORDS = 2    # Longer "conditions" are descriptions of the item, not properties a thing must have
MAX_THING_NAME_WORDS = 6   # Longer "things" named by the coverage check are descriptions, not names
UNANSWERED_GAP = "nothing in DISCOVERIES SO FAR answers it"
MAX_MISSING_CLAIM_WORDS = 4  # Short "missing" claims are checked against the discoveries' words; longer ones are kept
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

BLOCK_START = "[[[BLOCK_START {}]]]"
BLOCK_END = "[[[BLOCK_END]]]"
BLOCK_PATTERN = re.compile(r'\[\[\[BLOCK_START (\w+)\]\]\](.*?)\[\[\[BLOCK_END\]\]\]', re.S)
HEADING_MARK = "[[[H]]]"

# Hides navigation menus, footers and cookie/consent banners, wraps code blocks and tables in markers
# so they are never split, marks headings for the page outline, reads the visible text, then restores
# the page exactly as it was.
PAGE_TEXT_JS = r"""([blockStart, blockEnd, headingMark]) => {
    const clutter = new Set();
    document.querySelectorAll('nav, footer, [role=navigation], [role=contentinfo]').forEach(el => clutter.add(el));
    document.querySelectorAll('#onetrust-banner-sdk, #onetrust-consent-sdk, #CybotCookiebotDialog, #usercentrics-root, .qc-cmp2-container, .fc-consent-root, #truste-consent-track').forEach(el => clutter.add(el));
    // Site notices and fundraising banners: Wikipedia's filled the whole first part of an article
    document.querySelectorAll('#siteNotice, #centralNotice, .cn-fundraising, .frb').forEach(el => clutter.add(el));
    const consentName = /cookie|consent|gdpr/i;
    document.querySelectorAll('body *').forEach(el => {
        const name = (el.id || '') + ' ' + (typeof el.className === 'string' ? el.className : '');
        if (!consentName.test(name)) return;
        const pos = getComputedStyle(el).position;
        if (pos === 'fixed' || pos === 'sticky' || el.getAttribute('role') === 'dialog' || el.getAttribute('aria-modal') === 'true') clutter.add(el);
    });

    const fullText = document.body ? document.body.innerText : '';
    const hidden = [];
    const markers = [];
    try {
        clutter.forEach(el => {
            hidden.push([el, el.style.getPropertyValue('display'), el.style.getPropertyPriority('display')]);
            el.style.setProperty('display', 'none', 'important');
        });
        document.querySelectorAll('pre, table').forEach(el => {
            if (el.parentElement && el.parentElement.closest('pre, table')) return;  // Only mark the outermost block
            const start = document.createTextNode(blockStart.replace('{}', el.tagName.toLowerCase()));
            const end = document.createTextNode(blockEnd);
            el.parentNode.insertBefore(start, el);
            el.parentNode.insertBefore(end, el.nextSibling);
            markers.push(start, end);
        });
        document.querySelectorAll('h1, h2, h3, h4').forEach(el => {
            if (el.closest('pre, table')) return;
            const mark = document.createTextNode(headingMark);
            el.insertBefore(mark, el.firstChild);
            markers.push(mark);
        });
        let text = document.body ? document.body.innerText : '';
        // Safety net: if hiding clutter removed most of the page, the "clutter" was the content
        if (text.length < fullText.length * 0.2) {
            hidden.forEach(([el, value, priority]) => el.style.setProperty('display', value, priority));
            hidden.length = 0;
            text = document.body.innerText;
        }
        return text;
    } finally {
        markers.forEach(node => node.remove());
        hidden.forEach(([el, value, priority]) => {
            if (value) el.style.setProperty('display', value, priority); else el.style.removeProperty('display');
        });
    }
}"""

def normalize_url(url):
    return url.split("#")[0].rstrip("/") if url else url

def normalize_query(query):
    return " ".join(str(query).lower().split())

def determine_url(action, parameter):
    if isinstance(parameter, list):
        clean_param = str(parameter[0]) if len(parameter) > 0 else ""
    else:
        clean_param = str(parameter)

    clean_param = clean_param.strip().strip('"').strip("'")

    if action == "NAVIGATE":
        if "#" in clean_param:
            clean_param = clean_param.split("#")[0]

        if clean_param.startswith(('http://', 'https://')):
            return clean_param
        return f"https://{clean_param}"
    elif action == "SEARCH":
        return f"{SEARCH_URL}{urllib.parse.quote_plus(search_query(clean_param))}"
    return None

def search_query(parameter):
    # If the model hands back a full search engine URL, pull the query back out of it
    clean_param = str(parameter).strip().strip('"').strip("'")
    if clean_param.startswith(('http://', 'https://')) and ("duckduckgo.com" in clean_param or "google.com/search" in clean_param):
        query_dict = urllib.parse.parse_qs(urllib.parse.urlparse(clean_param).query)
        if 'q' in query_dict:
            clean_param = query_dict['q'][0]
    return clean_param.replace('+', ' ')

def decode_ddg_link(href):
    # DuckDuckGo wraps result links as //duckduckgo.com/l/?uddg=<encoded-target>&rut=...
    if not href:
        return ""
    full = urllib.parse.urljoin("https://duckduckgo.com", href)
    parsed = urllib.parse.urlparse(full)
    if parsed.netloc.endswith("duckduckgo.com") and parsed.path.startswith("/l/"):
        target = urllib.parse.parse_qs(parsed.query).get("uddg")
        if target:
            return target[0]
    return full

async def count_search_results(page):
    try:
        return await page.locator(".result:not(.result--ad) a.result__a").count()
    except Exception:
        return 0

async def extract_search_results(page, visited_urls):
    results = await page.evaluate("""() => Array.from(document.querySelectorAll('.result:not(.result--ad)')).map(r => {
        const a = r.querySelector('a.result__a');
        const s = r.querySelector('.result__snippet');
        return a ? {title: a.innerText.trim(), href: a.getAttribute('href'), snippet: s ? s.innerText.trim() : ''} : null;
    }).filter(Boolean)""")

    lines = []
    for i, r in enumerate(results[:MAX_SEARCH_RESULTS], start=1):
        url = decode_ddg_link(r['href'])
        visited = " (ALREADY VISITED)" if normalize_url(url) in visited_urls else ""
        snippet = r['snippet'][:MAX_SNIPPET_CHARS]
        lines.append(f"[{i}] {r['title']}{visited}\n    URL: {url}\n    {snippet}")
    return "\n\n".join(lines)

def text_units(text):
    """Split page text into units that must stay whole: code blocks, tables, and paragraphs."""
    units = []
    def paragraphs(chunk):
        return [p.strip() for p in re.split(r'\n\s*\n', chunk) if p.strip()]

    pos = 0
    for m in BLOCK_PATTERN.finditer(text):
        units += paragraphs(text[pos:m.start()])
        body = m.group(2).strip("\n")
        if body.strip():
            units.append(f"```\n{body}\n```" if m.group(1) == "pre" else body)
        pos = m.end()
    units += paragraphs(text[pos:])
    return units

def split_oversized(unit, limit):
    """Last resort for a single unit bigger than a whole part: split at line, then sentence, boundaries."""
    if len(unit) <= limit:
        return [unit]
    fenced = unit.startswith("```\n") and unit.endswith("\n```")
    body = unit[4:-4] if fenced else unit
    budget = limit - 40 if fenced else limit

    pieces, current = [], ""
    lines = body.split("\n") if "\n" in body else re.split(r'(?<=[.!?])\s+', body)
    joiner = "\n" if "\n" in body else " "
    for line in lines:
        while len(line) > budget:  # A single line longer than a part: hard cut is unavoidable
            if current:
                pieces.append(current)
                current = ""
            pieces.append(line[:budget])
            line = line[budget:]
        if current and len(current) + len(joiner) + len(line) > budget:
            pieces.append(current)
            current = line
        else:
            current = f"{current}{joiner}{line}" if current else line
    if current:
        pieces.append(current)

    if fenced:
        return [f"```\n{p}\n```" + ("\n(code block continues in next part)" if i < len(pieces) - 1 else "") for i, p in enumerate(pieces)]
    return pieces

def split_into_parts(text, limit=PAGE_PART_CHARS):
    parts, current = [], ""
    for unit in text_units(text):
        for piece in split_oversized(unit, limit):
            if current and len(current) + 2 + len(piece) > limit:
                parts.append(current)
                current = piece
            else:
                current = f"{current}\n\n{piece}" if current else piece
    if current:
        parts.append(current)
    return parts

async def extract_page_parts(page):
    """Returns the page split into parts, and an outline label for each part."""
    text = await page.evaluate(PAGE_TEXT_JS, [BLOCK_START, BLOCK_END, HEADING_MARK])
    return build_outline(split_into_parts(text or ""))

def build_outline(parts):
    """Strips the heading markers and labels each part with its headings (or its first line if it has none)."""
    clean, outline = [], []
    for part in parts:
        headings = [" ".join(h.split()) for h in re.findall(re.escape(HEADING_MARK) + r"([^\n]*)", part) if h.strip()]
        text = part.replace(HEADING_MARK, "")
        if headings:
            label = " / ".join(headings)
        else:
            # First line with real text, skipping code-fence lines (```)
            label = next((line.strip() for line in text.splitlines() if line.strip().strip("`").strip()), "(no text)")
        clean.append(text)
        outline.append(label if len(label) <= MAX_OUTLINE_LABEL else label[:MAX_OUTLINE_LABEL] + "...")
    return clean, outline

async def extract_page_links(page, shown_links=None):
    # Links come from the whole page, menus included, since navigation menus are how sites are used
    # a.href resolves relative links (e.g. /docs/intro) to absolute URLs
    # shown_links holds links already listed earlier in the running conversation; they are skipped so a
    # new page on the same site lists its own links instead of repeating the site menu
    links = await page.evaluate("""() => Array.from(document.querySelectorAll('a[href]'))
        .map(a => ({text: a.innerText.trim().replace(/\\s+/g, ' '), href: a.href.split('#')[0]}))
        .filter(l => l.href.startsWith('http') && l.text.length >= 4)""")
    seen = set()
    link_lines = []
    omitted = 0
    # Links with a few words of text (article titles, "Next: ...") before short ones (menu items such as "GUIDES"),
    # each group in page order: the list is capped, and menus come first on most pages. In 4 regression suites the
    # agent opened 39 pages, all from search results and none from page links, while the links were more than half
    # of every new page it was shown
    links = sorted(links, key=lambda link: len(link["text"].split()) < CONTENT_LINK_WORDS)
    for link in links:
        if link["href"] in seen or normalize_url(link["href"]) == normalize_url(page.url):
            continue
        seen.add(link["href"])
        # Image links whose text is the image's HTML ('<img src="...') say nothing and took about a third of a
        # site's link list (shop links, mostly)
        if link["text"].startswith("<"):
            continue
        if shown_links is not None and link["href"] in shown_links:
            omitted += 1
            continue
        if len(link_lines) < MAX_LINKS:
            link_lines.append(f"- {link['text'][:60]} -> {link['href']}")
            if shown_links is not None:
                shown_links.add(link["href"])
    if omitted:
        link_lines.append(f"({omitted} links already listed earlier in this conversation are not repeated)")
    return "\n".join(link_lines)

async def build_environment(page, current_url, page_state, visited_urls=frozenset(), shown_links=None):
    if not current_url:
        return "[Empty or No Target Page Loaded Yet]"
    try:
        if "duckduckgo.com" in current_url:
            results = await extract_search_results(page, visited_urls)
            if results:
                return f"Search Results:\n{results}"
            parts, _ = await extract_page_parts(page)
            return f"[No search results parsed - possibly no matches or a search engine error]\n{parts[0][:1500] if parts else ''}"

        # Parts are cached per URL so part numbers stay stable while the model reads through a page
        if page_state["url"] != normalize_url(current_url):
            parts, outline = await extract_page_parts(page)
            page_state.update(url=normalize_url(current_url), parts=parts, outline=outline, index=0,
                              seen_parts={0}, visit_discoveries=0, leave_confirmed=False, reread_confirmed=set(),
                              read_on_confirmed=set(), grounding_asked=[],
                              # Kept per URL: on a return visit the read-on check asked about a part already recorded
                              recorded_parts=page_state.setdefault("recorded_by_url", {}).setdefault(
                                  normalize_url(current_url), set()))
        link_lines = await extract_page_links(page, shown_links)
        return f"{render_part(page_state, with_outline=True)}\n\nLinks On Page:\n{link_lines or '[None]'}"
    except Exception as e:
        return f"[Error reading page content: {e}]"

def render_part(page_state, with_outline=False):
    parts, index = page_state["parts"], page_state["index"]
    if not parts:
        return "Page Text:\n[No readable text]"
    if index < len(parts) - 1:
        # A part ending "Just follow these 6 easy steps" led the model to write the steps itself
        footer = (f"[Page continues: READ_MORE shows part {index + 2} of {len(parts)}. "
                  "What comes next on the page has not been shown to you yet.]")
    else:
        footer = "[End of page]"
    text = f"Page Text (part {index + 1} of {len(parts)}):\n{parts[index]}\n{footer}"
    if with_outline:
        text = f"{render_outline(page_state)}{text}"
    return text

def render_outline(page_state):
    # On a long page the model read part after part in order (part 4 of 33, nothing new) because it could
    # not see where anything was; the outline lets it jump to the part it needs, like skimming headings
    outline = page_state.get("outline") or []
    if len(outline) < OUTLINE_MIN_PARTS:
        return ""
    # Read parts are marked: after a fresh prompt the model no longer sees its earlier reading and went back
    # to re-read parts it had read half an hour before
    seen = page_state.get("seen_parts") or set()
    lines = [f"  {i}. {label}{' (read)' if i - 1 in seen else ''}"
             for i, label in enumerate(outline[:MAX_OUTLINE_LINES], start=1)]
    if len(outline) > MAX_OUTLINE_LINES:
        lines.append(f"  ... ({len(outline) - MAX_OUTLINE_LINES} more parts)")
    return (f"Page Outline ({len(outline)} parts; READ_MORE with a part number jumps straight to that part):\n"
            + "\n".join(lines) + "\n\n")

QUOTE_STYLES = str.maketrans({"‘": "'", "’": "'", "“": "'", "”": "'", '"': "'", "`": "'"})

KNOWN_LINE_THRESHOLD = 0.9

def discovery_lines(text):
    # Case, spacing and quote style (curly, straight, single, double) do not make a line new
    text = unicodedata.normalize("NFKC", str(text)).translate(QUOTE_STYLES)
    return {" ".join(line.lower().split()) for line in text.splitlines() if line.strip()}

def grounded_share(text, parts):
    """The share of a discovery's consecutive word pairs that also appear in the given page parts."""
    mine = word_pairs(str(text).lower())
    return len(mine & word_pairs(" ".join(parts).lower())) / len(mine) if mine else 1.0

def grounding_check(parameter, page_state, current_url, objective=""):
    # Shown part 2 of a guide, which ended "Just follow these 6 easy steps", the model recorded six steps it
    # wrote itself (the real ones were in part 3), then a care routine the page never gave. Real discoveries
    # from earlier runs shared 64-100% of their word pairs with the page; these shared 11-44%, and a rerun's
    # mix of real pot-size advice with invented steps shared 57%. A DISCOVER that matches the parts shown less
    # than that gets one cheap retry; repeating it then goes through, since a heavy paraphrase can score low too.
    parts = page_state.get("parts") or []
    if not (current_url and page_state.get("url") == normalize_url(current_url) and parts):
        return None
    # The objective's wording counts as well: "Mount Kilimanjaro is the tallest mountain in Africa..." echoed the
    # question's "tallest" where the page says "highest", and was sent back although it was accurate
    shown = [parts[i] for i in sorted(page_state.get("seen_parts", {0})) if i < len(parts)] + [objective]
    long_enough = len(word_pairs(parameter)) >= MIN_GROUNDING_PAIRS
    whole = grounded_share(parameter, shown) if long_enough else 1.0
    # An invented sentence can hide behind copied ones: an opinion task's discovery was 75% copied, but its last
    # sentence ("...depicted alongside other wise figures like the prophet Samuel") was not on the page (31%).
    # Real sentences scored 40-100% against their page, invented ones 0-41%.
    sentences = [s for s in re.split(r"(?<=[.!?])\s+|\n+", parameter) if len(word_pairs(s)) >= MIN_SENTENCE_PAIRS]
    lowest = min((grounded_share(s, shown) for s in sentences), default=1.0)
    if whole >= GROUNDED_PAIR_SHARE and lowest >= GROUNDED_SENTENCE_SHARE:
        return None
    # Far below anything real (whole discoveries 50%+, sentences 40%+), repeating does not confirm it: asked once,
    # the model sent its invented repotting steps again (32% overall, one step 9%) and they went through
    if whole < UNGROUNDED_PAIR_SHARE or lowest < UNGROUNDED_SENTENCE_SHARE:
        return "BLOCKED_NOT_ON_PAGE", ("That DISCOVER is mostly not in the page text you have been shown, so it comes "
                                       "from your own knowledge. DISCOVER only what the parts shown actually say, in the "
                                       f"page's own wording.{page_hint(parameter, shown[:-1], objective)} If what you need "
                                       "is not there, READ_MORE (a later part may have it) or look elsewhere.")
    # Only repeating the same DISCOVER confirms it: asked again, the model once sent a different text that was
    # still invented ("water sparingly", which the page never said), and a per-part pass let it through
    asked = page_state.setdefault("grounding_asked", [])
    mine = word_pairs(parameter.lower())
    if any(len(mine & earlier) >= 0.8 * len(mine) for earlier in asked):
        return None
    asked.append(mine)
    # Pointed to READ_MORE as well, the model left a part holding the real materials list and steps unrecorded
    return "CONFIRM_GROUNDED", (f"Some of that DISCOVER does not match the page text you have been shown, so part of it "
                                "seems to come from your own knowledge. DISCOVER again with only what the page actually "
                                f"says in part {page_state['index'] + 1} (shown now) or the parts before it, using the "
                                "page's own wording, for example its lists and steps. Do not add steps, amounts or "
                                "advice that the page does not give. If your DISCOVER really is what the page says, "
                                "repeat it.")

def closest_sentence(text, parts):
    """The page sentence sharing the most content words (4+ letters) with a text, or None below two."""
    mine = {w for w in plain_words(text) if len(w) > 3}
    # In page order, so equal scores always pick the same sentence (max keeps the first)
    sentences = list(dict.fromkeys(s.strip() for part in parts for s in re.split(r"(?<=[.!?])\s+|\n+", part)
                                   if len(s.split()) >= 4))
    score, best = max(((len(mine & {w for w in plain_words(s) if len(w) > 3}), s) for s in sentences),
                      key=lambda pair: pair[0], default=(0, None))
    return best if score >= 2 else None

def unseen_names(text, parts):
    """Names in a text (capitalised words not starting a sentence) that no part shown mentions."""
    seen = set(plain_words(" ".join(parts)))
    names = re.findall(r"(?<![.!?:]\s)(?<!^)\b([A-Z][a-z]{2,})", text.strip())
    return list(dict.fromkeys(n for n in names if n.lower() not in seen))

def page_hint(text, parts, objective=""):
    # Refused for an aftercare routine it wrote itself, the model read on past the page's one real line about it;
    # told to record the part the page gives, it still read on, and with two close sentences quoted it recorded one
    # half the time. One sentence, picked for the objective as well, was recorded 6 times out of 6, while a refusal
    # for steps that a later part held still led to READ_MORE 6 times out of 6. But on an opinion task the model had
    # written about Japan before the page said anything about it, and a quoted fragment ("This symbolism is
    # particularly prevalent...") was recorded instead of reading on: names the page never mentions mean the page
    # has not got there, so they are named instead
    missing = unseen_names(text, parts)
    if missing:
        return f" The parts shown never mention {', '.join(missing[:4])}."
    best = closest_sentence(f"{text} {objective}", parts)
    if not best:
        return ""
    return (f' What the page does say about it: "{shorten(best, MAX_CLOSEST_CHARS)}". If that gives part of what the '
            "OBJECTIVE asks for, DISCOVER exactly that now; what the page does not say is reported as not found at "
            "FINISH, never filled in.")

def word_pairs(text):
    words = re.findall(r"\w+", text)
    return {tuple(words[i:i + 2]) for i in range(len(words) - 1)} or ({tuple(words)} if words else set())

def is_known_discovery(parameter, journal):
    # Compared by consecutive word pairs, so line breaks, commas, punctuation and the order of lines do not
    # make old information look new. A line is known when 90% of its word pairs were already recorded;
    # the discovery is a repeat only when every line is known, so one genuinely new line keeps it.
    # A long line (8+ words) made entirely of recorded words is also known: it catches a block rejoined
    # after its recorded version had other lines (such as comments) between its parts. Short lines like
    # commands are judged by word pairs only, so a new command built from familiar words stays new.
    known_pairs, known_words = set(), set()
    for e in journal:
        if e.get("action") == "DISCOVER":
            lines = discovery_lines(e["extracted_data"])
            known_pairs |= word_pairs(" ".join(lines))  # Pairs across line breaks, for text later rejoined
            for line in lines:
                known_pairs |= word_pairs(line)
                known_words |= set(re.findall(r"\w+", line))

    def line_is_known(line):
        pairs, words = word_pairs(line), set(re.findall(r"\w+", line))
        if len(re.findall(r"\w+", line)) >= 8 and words <= known_words:
            return True
        return len(pairs & known_pairs) >= KNOWN_LINE_THRESHOLD * len(pairs)

    new_lines = [line for line in discovery_lines(parameter) if re.search(r"\w", line)]
    return bool(new_lines) and all(line_is_known(line) for line in new_lines)

def read_more_target(parameter, page_state):
    # The model sometimes writes "2 of 2" or "part 3"; the first number is the part it means
    number = re.search(r'\d+', parameter)
    target = int(number.group()) - 1 if number else page_state["index"] + 1
    # Told by the read-on check to read the outline part that names an open item, the model repeated a plain
    # READ_MORE for the next part in 2 to 3 of 6 replays; a plain READ_MORE after that advice reads the named part
    pointed = page_state.get("pointed")
    if not number and pointed and pointed[0] == (page_state.get("url"), page_state["index"]):
        target = pointed[1]
    # Asking for the part already shown nearly always meant the next one ("READ_MORE 9" while on part 9 came up
    # in most long-page runs, and refusing it cost a retry each time), so it is read as that, except on the last part
    if target == page_state["index"] and target < len(page_state["parts"]) - 1:
        target += 1
    return target

def leave_check(action, page_state, current_url, journal=()):
    # The model often left content pages without recording what it needed from them. The first attempt to
    # leave a page it has recorded nothing from gets a cheap retry asking it to confirm; repeating the same
    # action then goes through, so tasks where leaving without a discovery is normal lose only one retry.
    # Search result pages are exempt (page_state only tracks content pages).
    parts = page_state["parts"]
    on_page = current_url and page_state["url"] == normalize_url(current_url) and parts
    # Anything recorded from this page counts, on this visit or an earlier one
    recorded_here = any(e.get("action") == "DISCOVER" and normalize_url(e.get("source") or "") == normalize_url(current_url)
                        for e in journal)
    if not on_page or recorded_here or page_state.get("visit_discoveries") or page_state.get("leave_confirmed"):
        return None
    page_state["leave_confirmed"] = True  # Asked once per visit
    unread = len(parts) - len(page_state.get("seen_parts", {0}))
    if unread > LONG_PAGE_UNREAD:
        # Counting unread parts of a long page pushed the model to read all of them in order
        unread_note = (f" It is a long page ({len(parts)} parts, {unread} unread): use its outline to judge "
                       "whether a part is worth reading.")
        read_more = ", or READ_MORE with the number of a part from the outline that is likely to have it"
    elif unread:
        unread_note = f" {unread} of its {len(parts)} parts are still unread."
        read_more = ", or READ_MORE if the unread parts may have it"
    else:
        unread_note = read_more = ""
    return ("CONFIRM_LEAVE", f"You are about to leave {current_url} without recording anything from it.{unread_note} "
            f"If it has information relevant to the objective, DISCOVER it now{read_more}. "
            f"If it truly has nothing relevant, repeat your {action} to leave.")

def read_on_check(page_state, current_url, target, checklist=None):
    # The model read long pages part after part without recording anything, even though every prompt ends by
    # asking it to DISCOVER relevant information first: on an explanation task it read four parts of a page that
    # had the inputs, outputs and cell locations it needed and recorded them only when a progress check fired.
    # Moving on from a part nothing was recorded from gets one cheap retry that makes the model name what the
    # part says about the objective; repeating the READ_MORE then goes through. It also offers FINISH: with three
    # cultures recorded where three were asked for, the check pushed an opinion task to keep collecting more, and
    # it offers leaving: shown an unrelated "agent" project page, the model read on part after part.
    index = page_state["index"]
    # Only moving forward counts: going back to an earlier part is covered by the re-read check
    if target <= index or index in page_state.get("recorded_parts", set()):
        return None
    confirmed = page_state.setdefault("read_on_confirmed", set())
    if index in confirmed:
        return None
    confirmed.add(index)
    # Naming the items still open: on an explanation task the model answered 'none' for every part of an overview
    # that gave the inputs and outputs it still needed, while it was thinking about the Calvin cycle
    # With every item holding what it asks for, a documentation task got "choose FINISH now" after a discovery and
    # read four more parts anyway, answering each of these checks 'none' and repeating READ_MORE: then this check
    # asks for FINISH first and no longer offers reading on
    if every_item_has_discoveries(checklist):
        return "CONFIRM_READ_ON", (f"Before reading on: every item of the OBJECTIVE CHECKLIST already has the discoveries "
                                   f"it asks for. Choose FINISH now with the complete final result. Only if part "
                                   f"{index + 1}, which is shown now, gives something an item still clearly lacks, "
                                   "DISCOVER that instead.")
    still_open = open_items(checklist)
    serves = f"the OBJECTIVE (above all the checklist items with nothing recorded yet: {still_open})" if still_open else "the OBJECTIVE"
    pointer = outline_pointer(page_state, checklist)
    if pointer and pointer[0] != index + 2:
        page_state["pointed"] = ((page_state.get("url"), index), pointer[0] - 1)
    read_on = (f"repeat your READ_MORE: it will show part {pointer[0]}, not the next part, because the Page Outline "
               f"names '{pointer[1]}' there, from an item with nothing recorded yet" if pointer and pointer[0] != index + 2
               else "repeat your READ_MORE")
    return "CONFIRM_READ_ON", (f"Before reading on: you have recorded nothing from part {index + 1}, which is shown now. "
                               f"In thought_process, list each fact in it that serves {serves}, or write 'none'. If any "
                               f"of them are not in DISCOVERIES SO FAR, DISCOVER them now. If there are none, {read_on}. "
                               "But if DISCOVERIES SO FAR already cover every item of the OBJECTIVE CHECKLIST, choose "
                               "FINISH instead of reading on, and if this page is not about the OBJECTIVE at all, leave "
                               "it with SEARCH or NAVIGATE.")

# Words that say what to do with an item, not what it is about ("Compare ... features" matched "How do they compare")
ITEM_VERBS = {"compare", "find", "explain", "describe", "detail", "list", "give", "name", "say", "what", "which", "each",
              "about", "into", "does", "make"}

def outline_words(text):
    # Hyphenated words stay whole: "open-source" split into "open" matched "Can I open and edit ..." in an FAQ
    return re.findall(r"[a-z0-9]+(?:-[a-z0-9]+)*", str(text).lower())

def outline_word_parts(outline):
    """For each word in the outline labels, the part numbers (1-based) whose label has it."""
    parts = {}
    for number, label in enumerate(outline, 1):
        for word in set(outline_words(label)):
            parts.setdefault(word.rstrip("s"), set()).add(number)
    return parts

def outline_pointer(page_state, checklist):
    """The first unread part whose outline label names a word of an open checklist item, as (part, word), or None.

    Told to use the outline to reach an open item, the model still read the next part 6 of 6 times on a 10-part
    comparison page whose outline named "Administration" and "Features" in parts 5 and 6. Words in many labels
    ("SQLite", "PostgreSQL" in every heading of that page) say nothing about where an item is, so only words in
    at most a third of the labels count.
    """
    outline = page_state.get("outline") or []
    if len(outline) < OUTLINE_MIN_PARTS:
        return None
    seen = page_state.get("seen_parts") or set()
    parts = page_state.get("parts") or []
    where = outline_word_parts(outline)
    # Topic words point anywhere: the page's own title (its first label: "photosynthesis" on the Photosynthesis
    # article, "free" on a page of free office suites) and words every item shares ("SQLite" in each item of a
    # comparison) say what the whole page is about, not where one item is
    to_find = [item for item in checklist or [] if not OPINION_REQUEST.search(item["text"])]
    topic = {w.rstrip("s") for w in outline_words(outline[0])}
    if len(to_find) > 1:
        topic |= set.intersection(*({w.rstrip("s") for w in outline_words(item["text"])} for item in to_find))
    for item in to_find:
        if item["discoveries"]:
            continue
        for word in outline_words(item["text"]):
            key = word.rstrip("s")
            if len(key) < 4 or word in MISSING_STOPWORDS or word in ITEM_VERBS or key in topic:
                continue
            # A part that starts inside code is labelled by its first code line, not a heading: on the venv docs,
            # "Install pip in the virtual environment." was a docstring in the module's source
            numbers = sorted(n for n in where.get(key, ()) if n - 1 not in seen
                             and not str(parts[n - 1] if n <= len(parts) else "").lstrip().startswith("```"))
            if numbers and len(where[key]) <= len(outline) / 3:
                return numbers[0], word
    return None

def gap_check(thought, tried):
    # The model is asked to write 'not covered' for missing parts; on a comparison it wrote "Available Libraries:
    # Not covered" and finished anyway. A FINISH whose own reasoning says that is sent back to work on the gap,
    # at most GAP_REFUSAL_LIMIT times per run so a part that cannot be found does not block finishing for good
    if ("not covered" not in str(thought).lower() or tried.get("gap_refusals", 0) >= GAP_REFUSAL_LIMIT
            or tried.get("search_exhausted")):
        return None
    tried["gap_refusals"] = tried.get("gap_refusals", 0) + 1
    return ("BLOCKED_FINISH", "Your own check marks a part of the objective as 'not covered'. Do not finish yet: work on "
            "that part, for example with a SEARCH aimed at it. Only if you have already looked for it without success, "
            "FINISH and say in the final result what could not be found.")

def check_decision(action, parameter, journal, tried, page_state, current_url, thought=""):
    """Refusals that can be decided before acting. Returns (journal action, reason), or None if the decision may proceed."""
    if action == "SEARCH":
        query = search_query(parameter)
        if not query.strip():
            return "BLOCKED_SEARCH", "The SEARCH query was empty."
        # Repeats are blocked outright; a warning alone does not stop small models from looping
        if normalize_query(query) in [normalize_query(q) for q in tried["queries"]]:
            # A model looking for something the web does not have kept resubmitting the same search (three times
            # in one turn); from the second repeat it is told plainly that finishing with the gap is an option
            repeats = tried.setdefault("repeated_searches", {})
            repeats[normalize_query(query)] = repeats.get(normalize_query(query), 0) + 1
            if repeats[normalize_query(query)] >= 2:
                # Told to finish with the gap, the model tried FINISH, was asked by the finish check to work on the
                # uncovered part, and repeated the search again (five times on a strict objective); from here a
                # FINISH goes straight through
                tried["search_exhausted"] = True
                return "BLOCKED_SEARCH", (f"You have now tried to repeat the search '{query}' "
                                          f"{repeats[normalize_query(query)]} times. It was already done and did not find "
                                          "what you need. Either SEARCH with clearly different words, or accept that it "
                                          "may not be available: FINISH and say in the final result that it could not be "
                                          "found.")
            return "BLOCKED_SEARCH", "This exact query was already searched. Use a new query, or DISCOVER from the current page."
    elif action == "NAVIGATE":
        if not parameter.strip():
            return "BLOCKED_NAVIGATE", "NAVIGATE needs a full URL in the parameter."
        target = normalize_url(determine_url(action, parameter))
        # Shown a long page's outline, the model kept trying to jump to a section with a made-up anchor
        # ("NAVIGATE ...#sqlite-vs-postgresql", then "NAVIGATE #sqlite-vs-postgresql")
        section = "#" in parameter and (parameter.strip().startswith("#") or target == normalize_url(current_url or ""))
        if current_url and (target == normalize_url(current_url) or section):
            jump = (" To go to a section of this page, use READ_MORE with that section's part number from the outline."
                    if section else "")
            return "BLOCKED_NAVIGATE", (f"{where_you_are(page_state, current_url)} You are already on this page.{jump} "
                                        "Use READ_MORE to see its other parts, DISCOVER from it, or go somewhere else.")
        # Going back to an earlier page is allowed; the cap stops back-and-forth loops between pages
        if [normalize_url(u) for u in tried["urls"]].count(target) >= MAX_VISITS_PER_URL:
            return "BLOCKED_NAVIGATE", f"This URL was already visited {MAX_VISITS_PER_URL} times. Pick a different page."
    if action in ("SEARCH", "NAVIGATE"):
        return leave_check(action, page_state, current_url, journal)
    elif action == "READ_MORE":
        total = len(page_state["parts"])
        target = read_more_target(parameter, page_state)
        if not (current_url and page_state["url"] == normalize_url(current_url)) or total == 0:
            return "BLOCKED_READ_MORE", f"{where_you_are(page_state, current_url)} This page has no parts to read."
        if not 0 <= target < total:
            return "BLOCKED_READ_MORE", (f"There is no part {target + 1}. {where_you_are(page_state, current_url)} "
                                         f"This page has {total} part(s).")
        # On part 3 the model asked for "READ_MORE 3", which only showed the same part again and cost a turn
        if target == page_state["index"]:
            following = (f"For part {target + 2}, use READ_MORE with an empty parameter. "
                         if target < total - 1 else "It is the last part of this page. ")
            return "BLOCKED_READ_MORE", (f"Part {target + 1} is the part already shown. {following}"
                                         "Otherwise choose DISCOVER or another action.")
        # Re-reading a part is allowed, but confirmed once per part (the same pattern as the leave check)
        confirmed = page_state.setdefault("reread_confirmed", set())
        if target in page_state.get("seen_parts", set()) and target != page_state["index"] and target not in confirmed:
            confirmed.add(target)
            return "CONFIRM_REREAD", (f"You already read part {target + 1} of this page; anything useful from it should "
                                      "already be in DISCOVERIES SO FAR. Choose an unread part or another action, or "
                                      "repeat this READ_MORE if you really need to read it again.")
        return read_on_check(page_state, current_url, target, tried.get("checklist"))
    elif action == "DISCOVER":
        if not discovery_lines(parameter):
            return "BLOCKED_DISCOVER", "The DISCOVER parameter was empty. Put the actual information in the parameter."
        if is_known_discovery(parameter, journal):
            # The model has weighed this part: without this, the read-on check asked it to DISCOVER again
            # right after this refusal, and it repeated the duplicate until the turn was lost
            page_state.setdefault("read_on_confirmed", set()).add(page_state.get("index", 0))
            return "DUPLICATE_DISCOVER", "Everything in that DISCOVER is already recorded in DISCOVERIES SO FAR."
        return grounding_check(parameter, page_state, current_url, tried.get("objective", ""))
    elif action == "FINISH":
        return finish_check(tried, journal, page_state, current_url) or gap_check(thought, tried)
    else:
        return f"UNKNOWN_{action}", f"'{action}' is not a valid action."
    return None

def where_you_are(page_state, current_url):
    # With several pages in one conversation the model lost track of which one was current (it paged through
    # a 3-part article asking for "part 3 of 4" of a page it had left), so notes and refusals always say so
    if not current_url:
        return "No page is open yet."
    if page_state["url"] == normalize_url(current_url) and page_state["parts"]:
        return f"You are on {current_url} (part {page_state['index'] + 1} of {len(page_state['parts'])})."
    return f"You are on {current_url}."

def finish_check(tried, journal, page_state=None, current_url=None):
    # Asked to judge whether its discoveries "satisfy" the objective, a small model says yes too easily: on an
    # opinion task it finished after one source and gave a summary instead of the opinion asked for. The first
    # FINISH of each turn gets a cheap retry asking for a part-by-part check; repeating FINISH then goes through.
    if tried.get("finish_confirmed") or tried.get("search_exhausted"):
        return None
    tried["finish_confirmed"] = True  # Reset at the start of every turn
    return "CONFIRM_FINISH", (f"{source_note(journal)}{unread_note(page_state or {}, current_url)}"
                              f"{numbered_discoveries(journal)} {finish_check_text(tried)}")

def numbered_discoveries(journal, limit=MAX_CHECK_DISCOVERY_CHARS):
    # Asked to name the discovery covering each part of the objective, the model marked the light reactions
    # "not covered" although its first discovery described them: discoveries were an unnumbered list, far back
    # in the prompt. Checks now carry the list, numbered, so each part can be matched to a number.
    lines = numbered_lines(journal, limit)
    if not lines:
        return ""
    return "\nYour discoveries, numbered:\n" + "\n".join(lines) + "\n"

def numbered_lines(journal, limit):
    found = [e for e in journal if e.get("action") == "DISCOVER"]
    return [f"[{i}] {shorten(e['extracted_data'], limit)}" for i, e in enumerate(found, 1)]

async def split_objective(client, objective):
    """The objective as a fixed checklist of the things the final result must contain, made once per run.

    Every check used to ask the model to split the objective again, from memory, and a 7B model's split drifted:
    on a list task it added "migration strategies" from a page it had read and blocked its own FINISH over it, and
    on a comparison it added "data types" while never covering "many simultaneous users". Asked once, on its own,
    it split all seven test objectives cleanly, keeping their conditions ("free, open-source", "official
    documentation", "three"). If the split fails, the whole objective is the only item.
    """
    try:
        response = await client.chat.completions.create(
            model=MODEL, temperature=0.2, max_tokens=SPLIT_MAX_TOKENS, response_format=CHECKLIST_FORMAT,
            # Same system prompt as turn 1, so LM Studio's cache of it is still valid there: without it turn 1
            # took 128 s instead of about 40
            messages=[{"role": "system", "content": SYSTEM_PROMPT},
                      {"role": "user", "content": f"### OBJECTIVE\n{objective}\n\n{CHECKLIST_SPLIT}"}])
        parts = json.loads(extract_message_content(response)).get("parts")
        items = [shorten(p, MAX_CHECKLIST_ITEM_CHARS) for p in parts if isinstance(p, str) and p.strip()] if isinstance(parts, list) else []
    except Exception as err:  # A malformed reply or a server problem: the run still works with one item
        print(f"⚠️ Could not split the objective into a checklist ({err}); using the whole objective.")
        items = []
    return [{"text": text, "discoveries": []} for text in items[:MAX_CHECKLIST_ITEMS]] or [{"text": objective, "discoveries": []}]

def checklist_lines(checklist):
    lines = []
    for number, item in enumerate(checklist or [], 1):
        # An opinion is written at FINISH, not found on a page: shown as "discoveries for it: none yet", it kept an
        # owl task collecting cultures after it had the three asked for, and the model tried to DISCOVER its own view
        if OPINION_REQUEST.search(item["text"]):
            lines.append(f"{number}. {item['text']} (written by you at FINISH, not found on a page)")
            continue
        found = ", ".join(f"[{n}]" for n in item["discoveries"])
        lines.append(f"{number}. {item['text']} (discoveries for it: {found or 'none yet'})")
    return "\n".join(lines)

def every_item_has_discoveries(checklist):
    """Whether each item to find has at least the discoveries it asks for: one, or the number it names."""
    items = [item for item in checklist or [] if not OPINION_REQUEST.search(item["text"])]
    needed = [asked_count(item["text"]) or 1 for item in items]
    # As many different discoveries as the items ask for in all: on 1.53 one overview paragraph was linked to all four
    # items of an explanation, and finishing from it gave a thin result
    distinct = {n for item in items for n in item["discoveries"]}
    return (bool(items) and all(len(item["discoveries"]) >= k for item, k in zip(items, needed, strict=True))
            and len(distinct) >= sum(needed))

def after_discovery_advice(checklist):
    # With Western, European and Native American views recorded for "at least three cultures", an owl task kept
    # searching for more until its time ran out (twice); told only "if DISCOVERIES SO FAR now cover every item", the
    # model kept judging that they did not. When the code can see each item has what it asks for, it says so; the
    # coverage check before FINISH still looks at what the discoveries say
    if every_item_has_discoveries(checklist):
        return ("Every item of the OBJECTIVE CHECKLIST now has the discoveries it asks for (an item asking for three "
                "things has three). Choose FINISH now with the complete final result, unless one of these discoveries "
                "clearly does not answer its item; do not read further for extra or merely interesting details.")
    return ("If DISCOVERIES SO FAR now cover every item of the OBJECTIVE CHECKLIST, choose FINISH now; do not read further "
            "for extra or merely interesting details. If some items are still not covered, work on those next instead of "
            "adding more detail to items already covered.")

def open_items(checklist):
    """The checklist items nothing has been recorded for yet (opinions are written at FINISH), as one line for prompts."""
    return "; ".join(f"{n}. {item['text']}" for n, item in enumerate(checklist or [], 1)
                     if not item["discoveries"] and not OPINION_REQUEST.search(item["text"]))

def link_discovery(checklist, items, number):
    """Records which checklist items a new discovery serves, as the model named them; returns the valid ones."""
    linked = []
    for item in items if isinstance(items, list) else []:
        if isinstance(item, int) and not isinstance(item, bool) and 1 <= item <= len(checklist or []) and item not in linked:
            linked.append(item)
            checklist[item - 1]["discoveries"].append(number)
    return linked

def check_parts(tried):
    # The part-by-part check goes through the fixed checklist instead of a fresh split of the objective; "do not
    # add other parts" because the model's own additions blocked finishing. Recorded discoveries per item are
    # facts, not a verdict: LibreOffice recorded three times is three discoveries for "three alternatives"
    checklist = tried.get("checklist")
    if not checklist:
        return CHECK_PARTS
    return ("go through the OBJECTIVE CHECKLIST below, which lists every part of the OBJECTIVE (do not add other parts), "
            "and in thought_process write each item's number followed by the number of the discovery that covers it (for "
            "example '2: covered by [3]'), or 'not covered'. An item asking for a number of things is covered only when "
            "that many different ones are recorded. Only items that ask for your own answer, opinion, recommendation or "
            "summary are 'written at FINISH' (write them in the FINISH parameter, never as a DISCOVER); information an "
            "item asks you to find is never 'written at FINISH': it is covered by a discovery or 'not covered'.\n"
            f"OBJECTIVE CHECKLIST:\n{checklist_lines(checklist)}\n")

NUMBER_WORDS = {"two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10}
MEASURE_WORDS = {"second", "seconds", "minute", "minutes", "hour", "hours", "day", "days", "week", "weeks", "month",
                 "months", "year", "years", "cup", "cups", "times", "percent"}

def asked_count(text):
    """The number of things an item asks for ("three ...", "5 ..."), or None. "At least three" counts as three.

    A number of time units or amounts ("the first two weeks", "2 cups") is not a number of things to find."""
    words = plain_words(text)
    for word, after in zip(words, words[1:] + [""], strict=True):
        if after in MEASURE_WORDS:
            continue
        if word in NUMBER_WORDS:
            return NUMBER_WORDS[word]
        if word.isdigit() and 2 <= int(word) <= 20:
            return int(word)
    return None

def plain_words(text):
    return re.findall(r"[a-z0-9]+", str(text).lower())

def quoted_from(quote, text):
    """Whether a quote (in other spacing or punctuation) is copied from the text: 80% of its word pairs."""
    pairs = word_pairs(" ".join(plain_words(quote)))
    return bool(pairs) and len(pairs & word_pairs(" ".join(plain_words(text)))) >= 0.8 * len(pairs)

MISSING_STOPWORDS = {"the", "and", "or", "of", "from", "in", "on", "at", "to", "a", "an", "its", "their", "for", "with",
                     "is", "are", "not", "mentioned", "specific", "exact", "details", "detail", "information"}

def really_missing(missing, found):
    """The parts of a "missing" verdict that the discoveries do not already contain.

    On an explanation task the check said "missing H2O from the soil" and "missing O2" three times out of three,
    while a discovery held "6CO2 + 6H2O + Light Energy -> C6H12O6 + 6O2 ... water from the soil", and the finish it
    sent back ran out of time. A short claim (up to MAX_MISSING_CLAIM_WORDS key words) whose every key word is in
    the discoveries is dropped; longer ones ("what happens during the light-dependent reactions") cannot be checked
    by words and are kept.
    """
    text = " ".join(e["extracted_data"] for e in found).lower()
    kept = []
    for part in re.split(r"\s*(?:[,;]|\band\b)\s*", missing):
        words = [w for w in plain_words(part) if w not in MISSING_STOPWORDS]
        present = words and all(re.search(rf"(?<![a-z]){re.escape(w)}(?![a-z])", text) for w in words)
        if part.strip() and not (len(words) <= MAX_MISSING_CLAIM_WORDS and present):
            kept.append(part.strip())
    return ", ".join(kept)

def states(text, condition):
    """Whether the text contains every word of a condition ("open-source" matches "open source")."""
    words = set(plain_words(text))
    return all(w in words for w in plain_words(condition) if len(w) > 2)

async def coverage_gaps(client, objective, checklist, journal, only=None):
    """What the discoveries still lack for each checklist item, judged item by item just before finishing.

    The finish check takes the model's word that an item is covered: on an explanation task it finished from one
    overview paragraph it said covered all four items, without oxygen or water, and on a list it counted WPS
    Office as "free, open-source". Asked about all items at once, the model skipped items and judged both ways
    wrong; asked about one item at a time it named such gaps. Conditions are not taken on its word: for each
    thing it names, it must copy words from the discoveries, and the code checks they are there and state the
    condition. A condition counts only if the item itself states it (the model copied one item's conditions to
    the next). Items asking for an opinion or recommendation are not checked; a failed call reports no gaps.

    Each item is asked in a prompt of its own, with the item last so the long instructions before it are read once.
    Asked as a continuation of the browsing conversation instead (which kept that conversation in LM Studio's
    cache), the check was more lenient: on an explanation task's finish it found the gaps twice out of twice in
    separate prompts and never inside the conversation, which also overflowed the context near its limit.
    """
    found = [e for e in journal if e.get("action") == "DISCOVER"]
    listing = "\n\n".join(f"[{n}] {e['extracted_data']}" for n, e in enumerate(found, 1))
    items = "\n".join(f"{n}. {item['text']}" for n, item in enumerate(checklist, 1))
    gaps, unanswered, unproven = [], [], []
    for n, item in enumerate(checklist, 1):
        if OPINION_REQUEST.search(item["text"]) or (only is not None and n not in only):
            continue
        # The discoveries come before the item, so after the first item LM Studio only reads the question
        request = (f"### OBJECTIVE\n{objective}\n\n### DISCOVERIES\n{listing}\n\n### CHECKLIST\n{items}\n\n"
                   f"{COVERAGE_ITEM_CHECK}\n\nThe item to check: ITEM {n}. {item['text']}")
        messages = [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": request}]
        try:
            response = await client.chat.completions.create(
                model=MODEL, temperature=0.2, max_tokens=COVERAGE_MAX_TOKENS, response_format=COVERAGE_FORMAT,
                messages=messages)
            verdict = json.loads(extract_message_content(response))
            missing = really_missing(str(verdict.get("missing", "")).strip(" '\"."), found)
            # Real conditions are short properties ("free", "open-source", "official", "on Windows"); the model also
            # gave "in different cultures or religions", the item's own topic, and sent back a good opinion task
            # Conditions are checked only on items that ask for a number of things ("three free, open-source
            # alternatives"), where each thing named must meet them; elsewhere the model took the question's own
            # wording for one ("in the fridge" in "how long it keeps in the fridge"), and a correct finish was sent
            # back to search for proof of it
            conditions = [c for c in verdict.get("conditions") or [] if isinstance(c, str) and states(item["text"], c)
                          and len([w for w in plain_words(c) if len(w) > 2]) <= MAX_CONDITION_WORDS
                          and asked_count(item["text"])]
            # A thing is a name ("OnlyOffice"), not a sentence: the model also listed ".docx/.xlsx/.pptx compatibility
            # is genuinely excellent ..." as one, which got a caveat of its own and made two suites count as three
            things = [t for t in verdict.get("things") or [] if isinstance(t, dict) and str(t.get("name", "")).strip()
                      and "\n" not in str(t["name"]).strip() and len(str(t["name"]).split()) <= MAX_THING_NAME_WORDS]
        except Exception as err:
            print(f"⚠️ Coverage check of item {n} failed ({err}); treating it as covered.")
            continue
        # Nothing copied for the item, not even a discovery number, means nothing recorded answers it: on a how-to
        # the check gave empty evidence and nothing missing for "care in the first two weeks", the agent had linked
        # that item to "the best time to repot is spring and summer", and the final result invented a care routine
        if not re.search(r"\w", str(verdict.get("evidence", ""))):
            unanswered.append(n)
            gaps.append(f"Item {n} ({item['text']}): {UNANSWERED_GAP}")
        elif missing:
            gaps.append(f"Item {n} ({item['text']}): missing {missing}")
        # A number the item asks for is counted, not judged: a list run recorded only LibreOffice, twice, and the
        # check found nothing missing from "three free, open-source alternatives"
        wanted = asked_count(item["text"])
        names = list(dict.fromkeys(str(t["name"]).strip() for t in things))
        # Also without a condition: an owl task finished from one discovery naming two tribes for "at least three
        # cultures", with the two named as things and nothing missing, because the count ran only with conditions
        if wanted and names and len(names) < wanted:
            gaps.append(f"Item {n} ({item['text']}): only {len(names)} of {wanted} found ({', '.join(names)})")
        for thing in things:
            proof = str(thing.get("proof", ""))
            # Copied from a discovery that names the thing: another thing's sentence with this name put in front
            # ("OnlyOffice is free and open source" from LibreOffice's) does not prove anything about it
            name = {w for w in plain_words(thing["name"]) if len(w) > 2}
            copied = any(quoted_from(proof, e["extracted_data"]) and name & set(plain_words(e["extracted_data"]))
                         for e in found)
            unmet = [c for c in conditions if not (copied and states(proof, c))]
            if unmet:
                condition = " and ".join(unmet)
                unproven.append((str(thing["name"]).strip(), condition))
                gaps.append(f"Item {n}: {thing['name']}: no words in DISCOVERIES SO FAR show it is {condition}. SEARCH "
                            f"for '{thing['name']} {condition}' to check it, or replace it with one that is")
    return gaps, unanswered, unproven

async def coverage_refusal(client, objective, tried, journal):
    # Once per run, so a noisy verdict costs one turn at most and a gap that cannot be filled never blocks finishing
    checklist = tried.get("checklist")
    if tried.get("coverage_checked") or tried.get("search_exhausted") or not checklist:
        return None
    tried["coverage_checked"] = True
    if any(e.get("action") == "DISCOVER" for e in journal):
        print("🔎 Checking each checklist item against the discoveries before finishing...")
        gaps, tried["unanswered"], unproven = await coverage_gaps(client, objective, checklist, journal)
        # Items whose named things lacked proof of a condition are checked again before the final result
        tried["unproven_items"] = sorted({n for n, item in enumerate(checklist, 1) if any(
            g.startswith(f"Item {n}: ") and "no words in DISCOVERIES SO FAR show it is" in g for g in gaps)})
    else:
        # A factual run finished with nothing recorded (its answer was refused as not on the page shown) and its
        # final result said nothing was found
        gaps = ["Nothing has been recorded for any checklist item yet"]
        tried["unanswered"] = [n for n, item in enumerate(checklist, 1) if not OPINION_REQUEST.search(item["text"])]
    # Only gaps the code can confirm send the agent back: an item nothing was copied for, fewer things than the item
    # asks for, or nothing recorded at all. About half of the send-backs for the model's other verdicts were wrong in
    # four suite runs ("missing official documentation" on a comparison, "H2O, O2" already recorded, "free" on a
    # correct list of open-source suites) and cost a finish each; those verdicts now only shape the final result
    hard = [g for g in gaps if g.endswith(UNANSWERED_GAP) or re.search(r"\): only \d+ of \d+ found", g)
            or g.startswith("Nothing has been recorded")]
    if gaps:
        print("⚠️ Gaps found:\n" + "\n".join(f"   - {g}{'' if g in hard else ' (not sent back)'}" for g in gaps))
    gaps = hard
    if not gaps:
        return None
    return "BLOCKED_COVERAGE", ("Before finishing, each checklist item was checked against DISCOVERIES SO FAR, and these "
                                "gaps were found:\n" + "\n".join(f"- {g}" for g in gaps) + "\nWork on them first: DISCOVER "
                                "the missing information, or words that show a condition is met, from the current page if "
                                "it has them, or look for them elsewhere; replace a thing that does not meet a condition "
                                "with one that does. If something cannot be found, FINISH again and say so in the final "
                                "result.")

async def not_found_items(client, objective, tried, journal):
    """The checklist items no discovery answers, and the things not shown to meet an item's conditions, decided in
    code just before the final result is written.

    The final result is told to say "not found" for such items, but filled one from the model's own knowledge
    instead (a care routine for repotted plants that no discovery gave). Items nothing is linked to, or that the
    coverage check found nothing for, are checked once more (the agent may have recorded more since); those still
    unanswered are left out of what the final result is asked to write, and the code reports them itself. Things
    named for an item with conditions are rechecked the same way: sent back once, a list run proved two of its three
    office suites open source and finished with the third, WPS Office, which is not, still in the result.
    """
    checklist = tried.get("checklist") or []
    if len(checklist) < 2:
        return [], []
    candidates = sorted({n for n, item in enumerate(checklist, 1) if not item["discoveries"]
                         and not OPINION_REQUEST.search(item["text"])} | set(tried.get("unanswered") or []))
    rechecked = sorted(set(candidates) | set(tried.get("unproven_items") or []))
    if not rechecked or not any(e.get("action") == "DISCOVER" for e in journal):
        return candidates, []
    print(f"🔎 Checking checklist item(s) {', '.join(map(str, rechecked))} once more before writing the final result...")
    _, unanswered, unproven = await coverage_gaps(client, objective, checklist, journal, only=set(rechecked))
    return [n for n in unanswered if n in candidates or n in rechecked], unproven

def finish_check_text(tried):
    return (f"Before finishing, {check_parts(tried)} If any part is 'not covered', do not finish: choose the action that "
            f"works on that part ({GIVE_UP_RULE}) If every other part is covered, repeat FINISH with {FINAL_RESULT} "
            "in the parameter.")

def unread_note(page_state, current_url):
    # On a how-to the agent finished after 3 of 6 parts of the recipe, missing the amounts on the unread parts
    parts = page_state.get("parts") or []
    if not (current_url and page_state.get("url") == normalize_url(current_url) and parts):
        return ""
    unread = len(parts) - len(page_state.get("seen_parts", {0}))
    if unread > LONG_PAGE_UNREAD:
        # A bare count pushed the model to read every remaining part in order, including ones about other topics
        return (f" The current page still has {unread} unread parts: use its outline to judge whether any of them "
                "is relevant before reading them.")
    return f" The current page still has {unread} unread part(s) (READ_MORE)." if unread > 0 else ""

def source_note(journal):
    # Facts, not a rule: some objectives need one source and some need many, so the model is only shown how
    # many pages its discoveries came from (it judged one blog as covering "traditions around the world")
    sources = []
    for e in journal:
        if e.get("action") == "DISCOVER":
            source = normalize_url(e.get("source") or "") or "an unknown page"
            if source not in sources:
                sources.append(source)
    found = sum(1 for e in journal if e.get("action") == "DISCOVER")
    if not found:
        return "You have no discoveries yet."
    if found == 1:
        return f"Your only discovery comes from {sources[0]}. Consider whether the objective needs more."
    if len(sources) == 1:
        return (f"All {found} of your discoveries come from a single page ({sources[0]}). "
                "Consider whether the objective needs other sources.")
    return f"Your {found} discoveries come from {len(sources)} different pages."

def action_options(page_state, current_url):
    on_page = current_url and page_state["url"] == normalize_url(current_url) and page_state["parts"]
    if on_page and page_state["index"] < len(page_state["parts"]) - 1:
        read_more = f"READ_MORE to see part {page_state['index'] + 2} of {len(page_state['parts'])} of this page"
    elif on_page:
        read_more = "READ_MORE with a part number to revisit an earlier part (you are on the last part of this page)"
    else:
        read_more = "READ_MORE is not available here"
    return (f"- {read_more}\n"
            f"- NAVIGATE to another page: a new link, or an earlier page that still has information you need\n"
            f"- SEARCH with a new query\n"
            f"- FINISH if DISCOVERIES SO FAR already satisfies the objective")

def refusal_correction(action, reason, page_state, current_url):
    # Listing the alternatives on the first refusal stops the model resubmitting the same action
    return (f"REFUSED: {reason} Do NOT choose {action} with the same or similar parameter again. Choose a different action:\n"
            f"{action_options(page_state, current_url)}\nReply with only the JSON object.")

def escalation_message(refusals, last_action, last_reason, page_state, current_url):
    return (f"Your last {refusals} actions were all refused. Do NOT choose {last_action} again with the same or similar parameter. "
            f"Last refusal reason: {last_reason} Choose a different action:\n"
            f"{action_options(page_state, current_url)}")

def compact_journal(journal):
    # Dropping old entries in batches (not one per turn) keeps the start of the journal unchanged
    # between turns, so LM Studio can reuse its cached processing of it
    # One line per entry instead of indented JSON: same information at about a third of the tokens
    start = max(0, (len(journal) // JOURNAL_BATCH - 1) * JOURNAL_BATCH)
    return "\n".join(journal_line(e) for e in journal[start:]) or "[None yet]"

def journal_line(e):
    action = e.get("action", "")
    if action == "DISCOVER":
        detail = "(recorded in DISCOVERIES SO FAR)"
    elif action == "FINISH":
        detail = str(e.get("finding", ""))[:200]
    else:
        detail = str(e.get("requested") or e.get("part") or "")
    detail = " ".join(detail.split())  # Refused discoveries carry multi-line text; keep each entry on one line
    if len(detail) > MAX_JOURNAL_DETAIL_CHARS:
        detail = detail[:MAX_JOURNAL_DETAIL_CHARS] + "..."
    destination = e.get("destination", "")
    if action == "NAVIGATE" and destination and normalize_url(destination) != normalize_url(detail):
        detail += f" (landed on {destination})"
    line = f"T{e.get('turn')} {action} {detail}".rstrip()
    if e.get("reason") or e.get("error"):
        # Shortened like the details: the model saw the full refusal when it happened, and repeating it in every
        # fresh prompt made the journal the largest part of one (up to 7,000 characters, mostly read-on checks
        # and the finish and coverage checks with their lists), at about 8 tokens a second
        line += f" -> {shorten(e.get('reason') or e.get('error'), MAX_JOURNAL_REASON_CHARS)}"
    thought = e.get("thought", "")
    if thought:
        line += f" | thought: {thought[:MAX_THOUGHT_CHARS] + '...' if len(thought) > MAX_THOUGHT_CHARS else thought}"
    return line

def extract_message_content(response):
    choices = getattr(response, "choices", None)
    if choices is None and isinstance(response, dict):
        choices = response.get("choices", [])

    if isinstance(choices, list) and len(choices) > 0:
        first_choice = choices[0]
    else:
        first_choice = choices

    if hasattr(first_choice, "message"):
        return (first_choice.message.content or "").strip()
    elif isinstance(first_choice, dict) and "message" in first_choice:
        msg = first_choice["message"]
        return msg.get("content", "").strip() if isinstance(msg, dict) else msg.content.strip()
    return first_choice.get("message", {}).get("content", "").strip()

def visited_summary(urls):
    counts = {}
    for u in urls:
        counts[normalize_url(u)] = counts.get(normalize_url(u), 0) + 1
    if not counts:
        return "[None]"
    return json.dumps([u if n == 1 else f"{u} (visited {n} times)" for u, n in counts.items()])

def shorten(text, limit):
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[:limit].rstrip() + "..."

def new_conversation():
    return {"messages": None, "followups": 0, "tokens": 0, "note": None, "note_url": None, "shown_links": set()}

def set_note(conversation, note, current_url):
    # A note is only used if the agent is still on the page it describes when the next turn starts
    conversation.update(note=note, note_url=normalize_url(current_url) if current_url else None)

async def run_agent_turn(client, page, objective, journal, current_url, tried, page_state, conversation, loop_warning=None):
    warning_prompt = f"\n\n⚠️ LOOP DETECTION WARNING: {loop_warning}" if loop_warning else ""

    # Turns continue the previous conversation: LM Studio has already processed all of it, so only the
    # note about what changed (a result, a new page part, or a whole new page) is new. Too many
    # follow-ups, or a conversation nearing the context limit, starts over with a full prompt.
    note = conversation["note"]
    estimated_tokens = conversation["tokens"] + len(note or "") // 3
    if (conversation["messages"] and note and current_url
            and conversation["note_url"] == normalize_url(current_url)
            and conversation["followups"] < MAX_FOLLOWUPS
            and estimated_tokens < MAX_CONVERSATION_TOKENS):
        conversation["followups"] += 1
        print(f"⚡ Continuing the conversation (follow-up {conversation['followups']}/{MAX_FOLLOWUPS}, ~{estimated_tokens} tokens)")
        messages = conversation["messages"] + [{"role": "user", "content": f"{note}{warning_prompt}\n\n{TURN_INSTRUCTIONS}"}]
    else:
        conversation.update(followups=0, shown_links=set())
        messages = await build_full_prompt(page, objective, journal, current_url, tried, page_state, warning_prompt, conversation["shown_links"])
    conversation["note"] = None

    decision, refused, messages, tokens = await request_decision(client, messages, journal, tried, page_state, current_url)
    conversation.update(messages=messages, tokens=tokens)
    return decision, refused

# With the objective split into a checklist, the first search went after the first item only ("SQLite vs PostgreSQL
# setup and administration", 3 times out of 3), so the run went item by item through pages that each covered one;
# asked on the first turn for one short search for the whole objective, it searched for the whole comparison, and for
# the documentation task "official python documentation venv" instead of dropping "official"
FIRST_TURN_INSTRUCTION = ("This is the first turn: SEARCH for the whole OBJECTIVE at once, in a short query that names its "
                          "main subject and its conditions rather than every detail.\n")

TURN_INSTRUCTIONS = """If the CURRENT ENVIRONMENT contains information relevant to the objective that is not in DISCOVERIES SO FAR, choose DISCOVER now.
Determine your next tactical move. Output your decision in the mandatory JSON format:"""

async def build_full_prompt(page, objective, journal, current_url, tried, page_state, warning_prompt, shown_links=None):
    print("🔍 Scraping and preparing current environment text layout...")
    environment = await build_environment(page, current_url, page_state, {normalize_url(u) for u in tried["urls"]}, shown_links)

    # Shortened in the prompt (the full text is in the log and the report, and duplicates are checked against
    # it in code): the growing discoveries list had become a quarter of every fresh prompt
    discoveries = "\n".join(numbered_lines(journal, MAX_DISCOVERY_PROMPT_CHARS)) or "[None yet]"
    recent_journal = compact_journal(journal)

    # LM Studio only reuses the part of the prompt that is identical to the previous turn's, up to the
    # first change. Sections are ordered from least to most often changed, carry no counters, and the
    # journal only grows at its end, so each turn mostly adds text instead of changing earlier text.
    # The page content goes last, where small models pay the most attention.
    user_payload = f"""### HIGH-LEVEL OBJECTIVE
{objective}

### DISCOVERIES SO FAR
{discoveries}

### OBJECTIVE CHECKLIST (every part of the objective, with the discoveries recorded for each)
{checklist_lines(tried.get("checklist")) or "[None]"}

### RECENT RESEARCH JOURNAL
{recent_journal}

### ALREADY TRIED
Queries (do not repeat): {json.dumps(tried["queries"]) if tried["queries"] else "[None]"}
URLs visited (you may return to one that still has information you need, at most {MAX_VISITS_PER_URL} visits each): {visited_summary(tried["urls"])}

### CURRENT ENVIRONMENT
Active URL: {current_url if current_url else "None (Loop Initializing)"}
{environment}{warning_prompt}

{"" if journal else FIRST_TURN_INSTRUCTION}{TURN_INSTRUCTIONS}"""
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_payload}
    ]

PATH_ESCAPES = {"\t": "\\t", "\r": "\\r", "\b": "\\b", "\f": "\\f"}

def restore_path_escapes(text):
    """Undoes JSON escapes the model did not mean: recording the Windows command "python -m venv
    C:\\path\\to\\new\\virtual\\environment", it wrote single backslashes, and its \\t, \\n and \\r became a tab, a
    newline and a carriage return. A tab, carriage return, backspace or form feed right before a letter is
    treated as such a path; a newline is only restored where it sits between word characters (as in "to\\new").
    Only text that also has a backslash or a drive letter is touched: page tables are read with real tabs."""
    if not re.search(r"[\t\r\b\f][A-Za-z]", text) or not re.search(r"\\|\b[A-Za-z]:[\\\"\t\r\b\f\n]", text):
        return text
    text = re.sub(r"(?<=\w)\n(?=[a-z])", r"\\n", text)
    return re.sub(r"[\t\r\b\f](?=[A-Za-z])", lambda m: PATH_ESCAPES[m.group()], text)

async def request_decision(client, messages, journal, tried, page_state, current_url):
    # Cheap retries: a reply that is invalid or would be refused is answered with a short correction
    # appended to the same conversation. Everything before the correction is identical to what LM Studio
    # just processed, so it only reads the correction instead of the whole prompt again. Resending the
    # prompt unchanged would not help: the same prompt returns the same answer.
    messages = list(messages)
    refused = []
    json_failures = 0
    while True:
        response = await client.chat.completions.create(
            model=MODEL,
            messages=messages,
            temperature=0.2,
            response_format=RESPONSE_FORMAT
        )
        original_content = extract_message_content(response)
        raw_content = original_content

        if raw_content.startswith("```"):
            raw_content = re.sub(r'^```json\s*|^```\s*|```$', '', raw_content, flags=re.MULTILINE).strip()

        try:
            action_decision = json.loads(raw_content)
            if not isinstance(action_decision, dict):
                raise ValueError("the reply was JSON but not an object")
            for key in ("parameter", "thought_process"):
                if isinstance(action_decision.get(key), str):
                    action_decision[key] = restore_path_escapes(action_decision[key])
        except (json.JSONDecodeError, ValueError) as parse_err:
            json_failures += 1
            print(f"⚠️ Model returned invalid JSON (attempt {json_failures}/{MAX_JSON_RETRIES}): {raw_content[:200]}")
            if json_failures >= MAX_JSON_RETRIES:
                raise ValueError(f"No valid JSON decision after {MAX_JSON_RETRIES} attempts.") from parse_err
            messages += [{"role": "assistant", "content": original_content},
                         {"role": "user", "content": f"That reply was not a valid JSON object ({parse_err}). Reply with only the JSON object in the mandatory format."}]
            continue

        action = str(action_decision.get("action", "FINISH")).upper()
        parameter = str(action_decision.get("parameter", "")).strip()
        refusal = check_decision(action, parameter, journal, tried, page_state, current_url,
                                 action_decision.get("thought_process", ""))
        if not refusal or len(refused) >= MAX_CHEAP_RETRIES:
            usage = getattr(response, "usage", None)
            tokens = (getattr(usage, "prompt_tokens", 0) or 0) + (getattr(usage, "completion_tokens", 0) or 0)
            return action_decision, refused, messages + [{"role": "assistant", "content": original_content}], tokens

        refused.append({"action": action, "parameter": parameter, "journal_action": refusal[0], "reason": refusal[1]})
        print(f"♻️ Cheap retry {len(refused)}/{MAX_CHEAP_RETRIES}: [{action}] refused. {refusal[1]}")
        correction = refusal_correction(action, refusal[1], page_state, current_url)
        if refusal[0].startswith("CONFIRM_"):
            # Not a true refusal: repeating the action is an allowed answer, so no "do not repeat" wording
            correction = f"CHECK: {refusal[1]} Reply with only the JSON object."
        elif refusal[0] == "BLOCKED_NOT_ON_PAGE" and len(refused) < 2:
            # The refusal says what to do instead (DISCOVER the quoted sentence or read on); with the usual "Do NOT
            # choose DISCOVER ... choose a different action" around it, the model read on 3 times out of 3 past the
            # sentence it was shown
            correction = f"REFUSED: {refusal[1]} Reply with only the JSON object."
        elif len(refused) >= 2:
            correction = escalation_message(len(refused), action, refusal[1], page_state, current_url) + "\nReply with only the JSON object."
        messages += [{"role": "assistant", "content": original_content}, {"role": "user", "content": correction}]

def write_research_log(objective, journal, checklist=None):
    with open("research_log.txt", "w", encoding="utf-8") as log:
        log.write(f"OBJECTIVE: {objective}\n")
        if checklist:
            log.write(f"CHECKLIST: {json.dumps(checklist, ensure_ascii=False)}\n")
        log.write(json.dumps(journal, indent=2))

def read_research_log(path="research_log.txt"):
    """The objective, checklist (None in logs from before 1.53) and journal from a research log."""
    with open(path, encoding="utf-8") as log:
        objective, _, rest = log.read().partition("\n")
    checklist = None
    if rest.startswith("CHECKLIST: "):
        line, _, rest = rest.partition("\n")
        checklist = json.loads(line.removeprefix("CHECKLIST: "))
    return objective.removeprefix("OBJECTIVE: "), checklist, json.loads(rest) if rest.strip() else []

NUMBER_PATTERN = re.compile(r"\d+(?:[.,/]\d+)*")

def unverified_numbers(text, discoveries):
    """Numbers in the final result that appear in none of the discoveries.

    A small model writing a how-to filled a gap with an invented quantity ("45 grams of lukewarm water (about
    1/4 cup)"); numbers are where such errors do the most harm and the easiest place to check them.
    """
    text = re.sub(r"\[\d+(?:\s*[-–,;]\s*\d+)*\]", " ", str(text))  # Citation markers: [2], [4, 5], [1-4]
    text = re.sub(r"(?m)^\s*(?:#+\s*)?\d+[.)]\s", " ", text)  # Numbered list items and headings ("#### 1. Setup")
    known = {n.replace(",", "") for e in discoveries for n in NUMBER_PATTERN.findall(e["extracted_data"])}
    unverified = []
    for number in NUMBER_PATTERN.findall(text):
        if number.replace(",", "") not in known and number not in unverified:
            unverified.append(number)
    return unverified

def write_report(objective, journal, checklist=None):
    """A readable report with the final result and every discovery, so nothing depends on the model's
    summary being complete. Written however the run ends."""
    finish = next((e for e in reversed(journal) if e.get("action") == "FINISH"), None)
    found = [e for e in journal if e.get("action") == "DISCOVER"]
    last_turn = journal[-1].get("turn", 0) if journal else 0
    lines = ["# Research report", "", f"**Objective:** {objective}", ""]
    if finish:
        final = finish.get("finding") or "_(The agent finished without writing a final result.)_"
        lines += [f"**Status:** finished by the agent on turn {finish.get('turn')}", "", "## Final result", "", final]
        unchecked = unverified_numbers(final, found)
        if unchecked:
            lines += ["", f"> ⚠️ Numbers not found in the recorded discoveries: {', '.join(unchecked)}. "
                          "Check them before relying on this result."]
    else:
        lines += [f"**Status:** stopped before the agent finished (after turn {last_turn})", "", "## Final result", "",
                  "_(The agent did not finish; see the discoveries below.)_"]
    if checklist and len(checklist) > 1:
        lines += ["", "## Checklist", "", "The parts of the objective, with the discoveries recorded for each:", "",
                  checklist_lines(checklist)]
    lines += ["", f"## Discoveries ({len(found)})"]
    for i, e in enumerate(found, start=1):
        lines += ["", f"### {i}. Turn {e.get('turn')}, from {e.get('source') or 'unknown page'}", "", e["extracted_data"]]
    with open(REPORT_FILE, "w", encoding="utf-8") as report:
        report.write("\n".join(lines) + "\n")

COMPOSE_SYSTEM = ("You write the final result of a web research task, using only the findings that were recorded "
                  "during it.")

async def compose_final_result(client, objective, journal, checklist=None, not_found=(), unproven=()):
    """Writes the final result in a model call whose only job is writing.

    Deciding what to browse and writing the deliverable compete for a small model's attention: in a live run
    its reasoning noted the objective's request for an opinion was not covered, then it finished with a list
    of findings anyway. Given only the objective and every discovery, it can focus on what was asked for.
    """
    found = [e for e in journal if e.get("action") == "DISCOVER"]
    listing = "\n\n".join(f"[{i}] (from {e.get('source') or 'an unknown page'})\n{e['extracted_data']}"
                          for i, e in enumerate(found, start=1)) or "[None]"
    # The agent's own FINISH draft is not shown: on a how-to it already contained an invented quantity, which
    # this step then copied; everything here must come from the recorded discoveries
    # Rules a small model skipped when they came late in one long instruction (it listed four items when asked
    # for three, and added "My view" to a request for strengths) are given as a short numbered list
    # The checklist gives the parts in order: the strict documentation result did not say that the parameter
    # schemas it was asked for were never found
    # Items no discovery answers are not asked for at all, and are reported by the code after the result
    unfound = [checklist[n - 1]["text"] for n in not_found if checklist and 0 < n <= len(checklist)]
    asked = [item["text"] for item in checklist or [] if item["text"] not in unfound]
    parts = ("### CHECKLIST (the parts of the OBJECTIVE)\n" + "\n".join(f"{n}. {text}" for n, text in enumerate(asked, 1))
             + "\n\n") if checklist and len(checklist) > 1 else ""
    if unfound:
        parts += ("### NOT FOUND (no discovery answers these; they are reported after your result)\n"
                  + "\n".join(f"- {text}" for text in unfound) + "\n\n")
    if unproven:
        parts += ("### NOT SHOWN TO MEET THE CONDITIONS (the discoveries do not show these meet them; if you list one, "
                  "say so right after it)\n" + "\n".join(f"- {name}: not shown to be {cond}" for name, cond in unproven)
                  + "\n\n")
    request = (f"### OBJECTIVE\n{objective}\n\n{parts}### DISCOVERIES\n{listing}\n\n"
               "Write the complete final result the OBJECTIVE asks for: do exactly what it asks (for example answer "
               "the question, write the summary or the plan, or give the opinion). Rules:\n"
               "1. Use only the information in the DISCOVERIES. Do not fill gaps from your own knowledge or with "
               "guesses: no amounts, numbers, steps or advice that the discoveries do not contain. Where a detail the "
               "objective asks for is missing, say that it was not found. Copy commands, code, file paths and "
               "names exactly as the discoveries give them.\n"
               "2. A discovery about one source's own situation (for example a company's workload in a case study) "
               "describes that situation, not the one in the OBJECTIVE: say whose situation it is, and do not assume "
               "details of the OBJECTIVE's situation that it does not state.\n"
               "3. If the objective asks for a specific number of items, give that many (the best supported); if the "
               "discoveries have fewer different ones, give those and say how many could not be found. Never list the "
               "same item twice.\n"
               "4. Refer to the discoveries that support each point by their numbers, like [2], and only cite a number "
               "for something that discovery actually says.\n"
               "5. Do not add a 'My view:' paragraph unless the objective explicitly asks for your view, opinion or "
               "recommendation; if it does, give it in a paragraph that starts with 'My view:'.\n"
               + ("6. Cover every CHECKLIST item; for an item no discovery supports, say plainly that it was not found. "
                  "Write nothing about the NOT FOUND items.\n"
                  "7. Write plain text, not JSON." if parts else "6. Write plain text, not JSON."))
    # A fresh prompt with only the discoveries, not the browsing conversation: continuing that conversation let
    # the model use page text it had read but not recorded, and cite discovery numbers that did not say it
    messages = [{"role": "system", "content": COMPOSE_SYSTEM}, {"role": "user", "content": request}]
    response = await client.chat.completions.create(model=MODEL, messages=messages, temperature=0.3,
                                                    max_tokens=COMPOSE_MAX_TOKENS)
    text = extract_message_content(response)
    if text.startswith("{"):  # Habit from every earlier turn: unwrap a JSON reply instead of reporting it raw
        try:
            reply = json.loads(text)
            text = str(reply.get("parameter") or reply.get("thought_process") or text)
        except (json.JSONDecodeError, AttributeError):
            pass
    text = text.strip()
    # Asked to "give your own opinion" about owls, the result listed the traditions and ended without one,
    # although rule 6 asks for a 'My view:' paragraph whenever the objective wants a view. A short second call
    # writes only that paragraph, from the result just written
    if OPINION_REQUEST.search(objective) and "my view:" not in text.lower():
        view = await client.chat.completions.create(model=MODEL, temperature=0.3, max_tokens=VIEW_MAX_TOKENS, messages=[
            {"role": "system", "content": COMPOSE_SYSTEM},
            {"role": "user", "content": f"### OBJECTIVE\n{objective}\n\n### RESULT\n{text}\n\nThe OBJECTIVE asks for your "
                                        "own view, opinion or recommendation, and the RESULT does not give it yet. Write "
                                        "only that, as one short paragraph that starts with 'My view:', based on the "
                                        "RESULT. Plain text."}])
        paragraph = extract_message_content(view).strip()
        if paragraph:
            text += "\n\n" + (paragraph if paragraph.lower().startswith("my view:") else f"My view: {paragraph}")
    if unfound:
        text += "\n\nNot found in the pages read:\n" + "\n".join(f"- {t}" for t in unfound)
    return with_caveats(text, unproven)

def with_caveats(text, unproven):
    """The final result with each thing not shown to meet a condition marked where it is first named.

    Leaving such things out dropped correct answers along with wrong ones: a page said "open-source" of
    LibreOffice, ONLYOFFICE and Apache OpenOffice, but not "free". So they stay, marked by the code, which does not
    rely on the final result to say it; things the result does not name are listed after it.
    """
    unnamed = []
    for name, condition in unproven:
        note = f"(not shown in the pages read to be {condition})"
        match = re.search(re.escape(name) + r"(\*\*)?", text, re.I)
        if not match:
            unnamed.append(f"- {name}: {note[1:-1]}")
        # Only a note right after this name counts as already there: checked against the whole text, the note added
        # to OnlyOffice left WPS Office, with the same condition, unmarked in a list run's result
        elif not text[match.end():].lstrip().lower().startswith(note.lower()):
            text = text[:match.end()] + f" {note}" + text[match.end():]
    if unnamed:
        text += "\n\nNot shown in the pages read to meet the conditions:\n" + "\n".join(unnamed)
    return text

async def load_page(page, url=None):
    """Loads a URL (or reloads the page) and returns Playwright's response, which may be None."""
    if url:
        response = await page.goto(url, wait_until="domcontentloaded", timeout=30000)
    else:
        response = await page.reload(wait_until="domcontentloaded", timeout=30000)
    try:
        await page.wait_for_load_state("networkidle", timeout=5000)
    except Exception:
        pass  # Busy pages rarely go fully idle; the DOM is already usable
    await asyncio.sleep(1)
    return response

async def main():
    client = AsyncOpenAI(
        base_url=BASE_URL,
        api_key=API_KEY
    )

    print("🤖 ==================================================== 🤖")
    print("🚀 Initializing Autonomous Discovery Agent Setup...")
    objective = input("🎯 Define the Agent's High-Level Objective: ").strip()
    if not objective:
        print("⚠️ Objective cannot be empty.")
        return

    journal = []
    current_url = None
    turn_counter = 1
    max_turns = 100
    tried = {"queries": [], "urls": [], "objective": objective}
    print("📋 Splitting the objective into a checklist...")
    tried["checklist"] = await split_objective(client, objective)
    print("📋 Objective checklist:\n" + checklist_lines(tried["checklist"]))
    page_state = {"url": None, "parts": [], "index": 0}
    conversation = new_conversation()
    loop_msg = None
    consecutive_refusals = 0
    turns_without_discovery = 0
    turns_since_objective_check = 0

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        # No user_agent override: headed Chromium already reports a real, version-matched Chrome UA
        context = await browser.new_context(
            java_script_enabled=True,
            viewport={"width": 1280, "height": 720}
        )
        page = await context.new_page()

        try:
            while turn_counter <= max_turns:
                print(f"\n🧠 [TURN {turn_counter}/{max_turns}] Active reasoning cycle executing...")
                tried["finish_confirmed"] = False  # The finish check runs once per turn

                try:
                    decision, refused = await run_agent_turn(client, page, objective, journal, current_url, tried, page_state, conversation, loop_warning=loop_msg)
                    thought = decision.get("thought_process", "No reasoning recorded.")
                    action = str(decision.get("action", "FINISH")).upper()
                    parameter = str(decision.get("parameter", "")).strip()
                    loop_msg = None
                except ValueError as parse_err:
                    print(f"⚠️ {parse_err} Skipping turn.")
                    journal.append({"turn": turn_counter, "action": "INVALID_OUTPUT", "error": str(parse_err)})
                    turn_counter += 1
                    continue
                except Exception as err:
                    print(f"❌ Local AI API transaction failed: {err}")
                    print(f"💡 Check that LM Studio is running at {BASE_URL} and model '{MODEL}' is loaded.")
                    break

                # Replies refused and retried within this turn stay in the journal so the model sees what failed;
                # "check" names the check that refused them, for counting in the regression runs (not shown to the model)
                for r in refused:
                    journal.append({"turn": turn_counter, "action": "REFUSED_IN_TURN", "requested": f"{r['action']} {r['parameter'][:120]}", "reason": r["reason"],
                                    "check": r["journal_action"]})

                print(f"💡 Agent Thoughts: {thought}")
                print(f"🎬 Chosen Action: [{action}] -> Parameter: {parameter}")

                refusal = check_decision(action, parameter, journal, tried, page_state, current_url, thought)
                if not refusal and action == "FINISH":
                    refusal = await coverage_refusal(client, objective, tried, journal)
                if refusal:
                    # Only reached once this turn's cheap retries are used up
                    print(f"⚠️ Action refused: {refusal[1]}")
                    journal.append({"turn": turn_counter, "thought": thought, "action": refusal[0], "requested": parameter, "reason": refusal[1]})
                    loop_msg = f"Your {action} was refused. {refusal[1]}"
                    set_note(conversation, f"RESULT: Nothing changed. {where_you_are(page_state, current_url)}", current_url)
                    if refusal[0] == "BLOCKED_COVERAGE":
                        # The coverage prompts pushed the conversation out of LM Studio's cache, and re-reading it took
                        # 626-651 s; a fresh prompt is shorter and starts with the system prompt they left cached
                        conversation["messages"] = None

                elif action == "FINISH":
                    print("\n🎯 Agent has signaled mission complete. Writing the final result...")
                    finish_entry = {"turn": turn_counter, "thought": thought, "action": "FINISH", "finding": parameter}
                    journal.append(finish_entry)
                    try:
                        missing, unproven = await not_found_items(client, objective, tried, journal)
                        final = await compose_final_result(client, objective, journal, tried.get("checklist"), missing,
                                                           unproven)
                        if final:
                            finish_entry.update(draft=parameter, finding=final)
                    except Exception as err:
                        print(f"⚠️ Could not write the final result separately ({err}); keeping the agent's FINISH text.")
                    break

                elif action in ["SEARCH", "NAVIGATE"]:
                    target_url = determine_url(action, parameter)
                    print(f"🌐 Driving Playwright to cross-domain target: {target_url}")
                    try:
                        response = await load_page(page, target_url)
                        current_url = page.url
                        status = getattr(response, "status", None) or 0

                        if action == "SEARCH":
                            # The search engine sometimes returns an error page; reload once before giving up
                            if await count_search_results(page) == 0:
                                print("⚠️ Search returned no results. Retrying once...")
                                await asyncio.sleep(3)
                                await load_page(page)
                                current_url = page.url
                            if await count_search_results(page) == 0:
                                print("❌ Search still returned no results.")
                                journal.append({"turn": turn_counter, "thought": thought, "action": "FAILED_SEARCH", "requested": parameter,
                                                "error": "No results (search engine error or no matches). This query may be tried again."})
                            else:
                                tried["queries"].append(search_query(parameter))
                                journal.append({"turn": turn_counter, "thought": thought, "action": action, "requested": parameter, "destination": current_url})
                        else:
                            tried["urls"].append(target_url)
                            if normalize_url(current_url) != normalize_url(target_url):
                                tried["urls"].append(current_url)  # Also remember where redirects landed
                            if status >= 400:
                                # Often a guessed URL; reported as a failure so the model does not treat the
                                # error page as content (and is not asked to record something from it)
                                print(f"❌ The page returned HTTP {status}.")
                                journal.append({"turn": turn_counter, "thought": thought, "action": "FAILED_NAVIGATE", "requested": parameter,
                                                "error": f"The page returned HTTP {status} (for example 404: not found). "
                                                         "Use a link shown on a page or in search results instead of guessing URLs."})
                            else:
                                journal.append({"turn": turn_counter, "thought": thought, "action": action, "requested": parameter, "destination": current_url})
                    except Exception as b_err:
                        print(f"❌ Failed to reach target destination: {b_err}")
                        if action == "NAVIGATE":
                            tried["urls"].append(target_url)
                        journal.append({"turn": turn_counter, "thought": thought, "action": f"FAILED_{action}", "requested": parameter, "error": str(b_err)})

                    # The new page joins the running conversation instead of forcing a full prompt rebuild
                    outcome = journal[-1]
                    if "error" in outcome:
                        result = f"RESULT: {outcome['action']} '{parameter}' failed: {outcome['error'][:200]}"
                    else:
                        added = search_query(parameter) if action == "SEARCH" else target_url
                        result = f"RESULT: {action} '{parameter}' done. ALREADY TRIED now also includes: {added}"
                        if action == "NAVIGATE":
                            visits = [normalize_url(u) for u in tried["urls"]].count(normalize_url(target_url))
                            result = f"RESULT: NAVIGATE '{parameter}' done (visit {visits} of {MAX_VISITS_PER_URL} allowed for this URL)."
                    environment = await build_environment(page, current_url, page_state, {normalize_url(u) for u in tried["urls"]}, conversation["shown_links"])
                    if "error" in outcome and page_state["url"] == normalize_url(current_url):
                        page_state["leave_confirmed"] = True  # Nothing to record on an error page
                    set_note(conversation, f"{result}\n\n### CURRENT ENVIRONMENT (replaces the earlier one)\n"
                                           f"Active URL: {current_url}\n{environment}", current_url)

                elif action == "READ_MORE":
                    target = read_more_target(parameter, page_state)
                    page_state["index"] = target
                    page_state.setdefault("seen_parts", {0}).add(target)
                    print(f"📖 Showing part {target + 1} of {len(page_state['parts'])} of the current page.")
                    journal.append({"turn": turn_counter, "thought": thought, "action": "READ_MORE", "part": f"{target + 1} of {len(page_state['parts'])}"})
                    set_note(conversation, f"RESULT: READ_MORE done. {where_you_are(page_state, current_url)} "
                                           f"The CURRENT ENVIRONMENT now shows:\n{render_part(page_state)}\n"
                                           f"The links listed earlier for this page still apply.", current_url)

                elif action == "DISCOVER":
                    print(f"📝 Logging critical discovery element: {parameter}")
                    number = sum(1 for e in journal if e.get("action") == "DISCOVER") + 1
                    linked = link_discovery(tried["checklist"], decision.get("checklist_items"), number)
                    journal.append({"turn": turn_counter, "thought": thought, "action": "DISCOVER", "source": current_url,
                                    "extracted_data": parameter, "checklist_items": linked})
                    page_state["visit_discoveries"] = page_state.get("visit_discoveries", 0) + 1
                    page_state.setdefault("recorded_parts", set()).add(page_state["index"])
                    # A discovery is when the objective can become complete; on a one-answer question the model
                    # recorded the full answer, said "the objective has been met", then kept reading for
                    # "interesting" details, so completion is checked right here. On an explanation task it recorded
                    # the Calvin cycle step by step while the light reactions were still missing, so the note also
                    # points to the parts not yet covered, and the checklist with its discoveries shows which ones are open
                    linked_note = (f" for checklist item {', '.join(map(str, linked))}" if linked else
                                   " (linked to no checklist item)")
                    set_note(conversation, f"RESULT: Your DISCOVER was recorded as discovery [{number}]{linked_note}. "
                                           f"{where_you_are(page_state, current_url)} The page is unchanged.\n"
                                           f"OBJECTIVE CHECKLIST now:\n{checklist_lines(tried['checklist'])}\n"
                                           f"Do not record the same information again. {after_discovery_advice(tried['checklist'])}",
                             current_url)

                # A single refusal gets a short warning; repeated refusals get an explicit list of other options
                last_entry = journal[-1]["action"] if journal else ""
                if last_entry.startswith(("BLOCKED_", "UNKNOWN_")) or last_entry == "DUPLICATE_DISCOVER":
                    consecutive_refusals += 1
                    if consecutive_refusals >= 2:
                        print(f"⚠️ {consecutive_refusals} refused actions in a row. Escalating loop warning.")
                        loop_msg = escalation_message(consecutive_refusals, action, journal[-1].get("reason", ""), page_state, current_url)
                else:
                    consecutive_refusals = 0

                # Without a nudge the model keeps browsing after it already has enough; after several turns
                # with no new discovery it is asked to weigh its discoveries against the objective
                if action == "DISCOVER" and last_entry == "DISCOVER":
                    turns_without_discovery = 0
                else:
                    turns_without_discovery += 1
                # Fires every STALL_TURNS stalled turns rather than every turn: repeated each turn it crowded out
                # the current page and pushed the model to navigate away without reading what was in front of it
                if turns_without_discovery and turns_without_discovery % STALL_TURNS == 0:
                    print(f"⚠️ No new discoveries in {turns_without_discovery} turns. Adding progress check.")
                    progress = (f"PROGRESS CHECK: no new discoveries in the last {turns_without_discovery} turns."
                                f"{numbered_discoveries(journal)} {READ_FIRST} "
                                f"Then {check_parts(tried)} If every part is covered, choose FINISH with {FINAL_RESULT} in the "
                                "parameter. If not, state in thought_process exactly what is still missing, then go and find "
                                f"that specific thing. {GIVE_UP_RULE}")
                    loop_msg = f"{loop_msg}\n{progress}" if loop_msg else progress
                    turns_since_objective_check = 0
                else:
                    # Discoveries on loosely related topics keep resetting the stall count, so the objective is
                    # also re-checked on a fixed schedule to stop the model drifting away from it
                    turns_since_objective_check += 1
                    if turns_since_objective_check >= OBJECTIVE_CHECK_EVERY:
                        turns_since_objective_check = 0
                        count = sum(1 for e in journal if e.get("action") == "DISCOVER")
                        print(f"🧭 Objective check after {turn_counter} turns ({count} discoveries).")
                        check = (f"OBJECTIVE CHECK: you have recorded {count} discoveries in {turn_counter} turns."
                                 f"{numbered_discoveries(journal)} "
                             f"{source_note(journal)} {READ_FIRST} "
                                 f"Then {check_parts(tried)} If every part is covered, choose FINISH with {FINAL_RESULT} in the "
                                 "parameter. If not, state in thought_process exactly what is still missing and go directly for it; "
                                 f"do not spend turns on topics outside the objective. {GIVE_UP_RULE}")
                        loop_msg = f"{loop_msg}\n{check}" if loop_msg else check

                # Saved every turn as well, so a crash or a killed process still leaves a current report
                write_research_log(objective, journal, tried.get("checklist"))
                write_report(objective, journal, tried.get("checklist"))

                turn_counter += 1
                print("\n⏱️ Cycle pacing check complete. Refreshing navigation loop pipeline...")
                await asyncio.sleep(TURN_PAUSE_SECONDS)
        finally:
            # Always saved, however the run ends: FINISH, the turn limit, Ctrl+C or an outside time limit
            write_research_log(objective, journal, tried.get("checklist"))
            write_report(objective, journal, tried.get("checklist"))

        print(f"\n🔒 Shutting down browser instance. Report: {REPORT_FILE}; full journal: research_log.txt.")
        await browser.close()

def run():
    """Command-line entry point (installed as `localagentbrowser`)."""
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n⏹️ Stopped. Findings so far are in research_log.txt.")


if __name__ == "__main__":
    run()
