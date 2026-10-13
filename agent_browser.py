import asyncio
import json
import urllib.parse
import re
import unicodedata
from openai import AsyncOpenAI
from playwright.async_api import async_playwright

SEARCH_URL = "https://html.duckduckgo.com/html/?q="
JOURNAL_BATCH = 6        # The prompt shows 6-11 recent journal entries; old ones are dropped 6 at a time
MAX_THOUGHT_CHARS = 120  # Old reasoning is trimmed in the prompt; the full text stays in research_log.txt
MAX_SEARCH_RESULTS = 8
MAX_SNIPPET_CHARS = 200
PAGE_PART_CHARS = 2500   # Pages are shown in parts of about this size; READ_MORE shows the next part
MAX_LINKS = 15
MAX_JSON_RETRIES = 3
MAX_VISITS_PER_URL = 3   # Returning to an earlier page is allowed; this caps back-and-forth loops
MAX_CHEAP_RETRIES = 2   # Refused replies re-asked within the same turn before the refusal costs a whole turn
MAX_FOLLOWUPS = 12       # Turns that continue one conversation before a fresh full prompt (the token budget usually ends it first)
MAX_CONVERSATION_TOKENS = 5500  # Start fresh before the conversation nears LM Studio's 8192-token context
TURN_PAUSE_SECONDS = 1

SYSTEM_PROMPT = """You are an autonomous web discovery agent. Your job is to achieve the high-level OBJECTIVE by executing sequential actions.
On every turn, evaluate the CURRENT ENVIRONMENT and your RESEARCH JOURNAL. You must output exactly ONE valid JSON block matching this structure:

{
  "thought_process": "Your active reasoning about what to discover next and why.",
  "action": "SEARCH" | "NAVIGATE" | "READ_MORE" | "DISCOVER" | "FINISH",
  "parameter": "The search query string, the full target URL, a page part number, the extracted summary data, or an empty string"
}

Action guide:
- SEARCH: parameter is a plain-text query (not a URL). Results appear as a numbered list with URLs.
- NAVIGATE: parameter is a full URL copied exactly from the search results or page links you were shown.
- READ_MORE: long pages are shown in parts. Use an empty parameter to see the next part, or a part number to jump to that part.
- DISCOVER: parameter is the information you found on the current page that serves the objective. DISCOVER does not extract anything for you: you must write the information itself into the parameter. An empty DISCOVER is refused. Copy exact text where precision matters. You may group several related details from the same page into one DISCOVER.
- FINISH: only when your discoveries satisfy the objective.

Strictly follow these rules:
1. If the current page contains information relevant to the objective that is not already in DISCOVERIES SO FAR, your action MUST be DISCOVER before you leave the page.
2. Never repeat a search query, and never NAVIGATE to the page you are already on. You may return to a previously visited URL when it still has information you need, at most 3 visits per URL. These limits are enforced automatically.
3. Prefer primary, authoritative sources over copies and summaries of them.
4. Output ONLY raw JSON. No markdown wrappers, no backticks, no conversational filler."""

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
                "parameter": {"type": "string"}
            },
            "required": ["thought_process", "action", "parameter"]
        }
    }
}

BLOCK_START = "[[[BLOCK_START {}]]]"
BLOCK_END = "[[[BLOCK_END]]]"
BLOCK_PATTERN = re.compile(r'\[\[\[BLOCK_START (\w+)\]\]\](.*?)\[\[\[BLOCK_END\]\]\]', re.S)

# Hides navigation menus, footers and cookie/consent banners, wraps code blocks and tables in markers
# so they are never split, reads the visible text, then restores the page exactly as it was.
PAGE_TEXT_JS = r"""([blockStart, blockEnd]) => {
    const clutter = new Set();
    document.querySelectorAll('nav, footer, [role=navigation], [role=contentinfo]').forEach(el => clutter.add(el));
    document.querySelectorAll('#onetrust-banner-sdk, #onetrust-consent-sdk, #CybotCookiebotDialog, #usercentrics-root, .qc-cmp2-container, .fc-consent-root, #truste-consent-track').forEach(el => clutter.add(el));
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
    text = await page.evaluate(PAGE_TEXT_JS, [BLOCK_START, BLOCK_END])
    return split_into_parts(text or "")

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
    for l in links:
        if l["href"] in seen or normalize_url(l["href"]) == normalize_url(page.url):
            continue
        seen.add(l["href"])
        if shown_links is not None and l["href"] in shown_links:
            omitted += 1
            continue
        if len(link_lines) < MAX_LINKS:
            link_lines.append(f"- {l['text'][:60]} -> {l['href']}")
            if shown_links is not None:
                shown_links.add(l["href"])
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
            parts = await extract_page_parts(page)
            return f"[No search results parsed - possibly no matches or a search engine error]\n{parts[0][:1500] if parts else ''}"

        # Parts are cached per URL so part numbers stay stable while the model reads through a page
        if page_state["url"] != normalize_url(current_url):
            page_state.update(url=normalize_url(current_url), parts=await extract_page_parts(page), index=0)
        link_lines = await extract_page_links(page, shown_links)
        return f"{render_part(page_state)}\n\nLinks On Page:\n{link_lines or '[None]'}"
    except Exception as e:
        return f"[Error reading page content: {e}]"

def render_part(page_state):
    parts, index = page_state["parts"], page_state["index"]
    if not parts:
        return "Page Text:\n[No readable text]"
    if index < len(parts) - 1:
        footer = f"[Page continues: READ_MORE shows part {index + 2} of {len(parts)}]"
    else:
        footer = "[End of page]"
    return f"Page Text (part {index + 1} of {len(parts)}):\n{parts[index]}\n{footer}"

QUOTE_STYLES = str.maketrans({"‘": "'", "’": "'", "“": "'", "”": "'", '"': "'", "`": "'"})

KNOWN_LINE_THRESHOLD = 0.9

def discovery_lines(text):
    # Case, spacing and quote style (curly, straight, single, double) do not make a line new
    text = unicodedata.normalize("NFKC", str(text)).translate(QUOTE_STYLES)
    return {" ".join(line.lower().split()) for line in text.splitlines() if line.strip()}

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
    return int(number.group()) - 1 if number else page_state["index"] + 1

def check_decision(action, parameter, journal, tried, page_state, current_url):
    """Refusals that can be decided before acting. Returns (journal action, reason), or None if the decision may proceed."""
    if action == "SEARCH":
        query = search_query(parameter)
        if not query.strip():
            return "BLOCKED_SEARCH", "The SEARCH query was empty."
        # Repeats are blocked outright; a warning alone does not stop small models from looping
        if normalize_query(query) in [normalize_query(q) for q in tried["queries"]]:
            return "BLOCKED_SEARCH", "This exact query was already searched. Use a new query, or DISCOVER from the current page."
    elif action == "NAVIGATE":
        if not parameter.strip():
            return "BLOCKED_NAVIGATE", "NAVIGATE needs a full URL in the parameter."
        target = normalize_url(determine_url(action, parameter))
        if current_url and target == normalize_url(current_url):
            return "BLOCKED_NAVIGATE", "You are already on this page. Use READ_MORE to see its other parts, DISCOVER from it, or go somewhere else."
        # Going back to an earlier page is allowed; the cap stops back-and-forth loops between pages
        if [normalize_url(u) for u in tried["urls"]].count(target) >= MAX_VISITS_PER_URL:
            return "BLOCKED_NAVIGATE", f"This URL was already visited {MAX_VISITS_PER_URL} times. Pick a different page."
    elif action == "READ_MORE":
        total = len(page_state["parts"])
        target = read_more_target(parameter, page_state)
        if not (current_url and page_state["url"] == normalize_url(current_url)) or total == 0:
            return "BLOCKED_READ_MORE", "The current page has no parts to read. Use SEARCH or NAVIGATE first."
        if not 0 <= target < total:
            return "BLOCKED_READ_MORE", f"There is no part {target + 1}; this page has {total} part(s) and you are on part {page_state['index'] + 1}."
    elif action == "DISCOVER":
        if not discovery_lines(parameter):
            return "BLOCKED_DISCOVER", "The DISCOVER parameter was empty. Put the actual information in the parameter."
        if is_known_discovery(parameter, journal):
            return "DUPLICATE_DISCOVER", "Everything in that DISCOVER is already recorded in DISCOVERIES SO FAR."
    elif action != "FINISH":
        return f"UNKNOWN_{action}", f"'{action}' is not a valid action."
    return None

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
    start = max(0, (len(journal) // JOURNAL_BATCH - 1) * JOURNAL_BATCH)
    compact = []
    for entry in journal[start:]:
        e = {k: v for k, v in entry.items() if k != "extracted_data"}
        if len(e.get("thought", "")) > MAX_THOUGHT_CHARS:
            e["thought"] = e["thought"][:MAX_THOUGHT_CHARS] + "..."
        compact.append(e)
    return compact

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

TURN_INSTRUCTIONS = """If the CURRENT ENVIRONMENT contains information relevant to the objective that is not in DISCOVERIES SO FAR, choose DISCOVER now.
Determine your next tactical move. Output your decision in the mandatory JSON format:"""

async def build_full_prompt(page, objective, journal, current_url, tried, page_state, warning_prompt, shown_links=None):
    print("🔍 Scraping and preparing current environment text layout...")
    environment = await build_environment(page, current_url, page_state, {normalize_url(u) for u in tried["urls"]}, shown_links)

    discoveries = [e["extracted_data"] for e in journal if e.get("action") == "DISCOVER"]
    recent_journal = compact_journal(journal)

    # LM Studio only reuses the part of the prompt that is identical to the previous turn's, up to the
    # first change. Sections are ordered from least to most often changed, carry no counters, and the
    # journal only grows at its end, so each turn mostly adds text instead of changing earlier text.
    # The page content goes last, where small models pay the most attention.
    user_payload = f"""### HIGH-LEVEL OBJECTIVE
{objective}

### DISCOVERIES SO FAR
{json.dumps(discoveries, indent=2) if discoveries else "[None yet]"}

### RECENT RESEARCH JOURNAL
{json.dumps(recent_journal, indent=2)}

### ALREADY TRIED
Queries (do not repeat): {json.dumps(tried["queries"]) if tried["queries"] else "[None]"}
URLs visited (you may return to one that still has information you need, at most {MAX_VISITS_PER_URL} visits each): {visited_summary(tried["urls"])}

### CURRENT ENVIRONMENT
Active URL: {current_url if current_url else "None (Loop Initializing)"}
{environment}{warning_prompt}

{TURN_INSTRUCTIONS}"""
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_payload}
    ]

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
            model="qwen2.5-7b-instruct",
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
        except (json.JSONDecodeError, ValueError) as parse_err:
            json_failures += 1
            print(f"⚠️ Model returned invalid JSON (attempt {json_failures}/{MAX_JSON_RETRIES}): {raw_content[:200]}")
            if json_failures >= MAX_JSON_RETRIES:
                raise ValueError(f"No valid JSON decision after {MAX_JSON_RETRIES} attempts.")
            messages += [{"role": "assistant", "content": original_content},
                         {"role": "user", "content": f"That reply was not a valid JSON object ({parse_err}). Reply with only the JSON object in the mandatory format."}]
            continue

        action = str(action_decision.get("action", "FINISH")).upper()
        parameter = str(action_decision.get("parameter", "")).strip()
        refusal = check_decision(action, parameter, journal, tried, page_state, current_url)
        if not refusal or len(refused) >= MAX_CHEAP_RETRIES:
            usage = getattr(response, "usage", None)
            tokens = (getattr(usage, "prompt_tokens", 0) or 0) + (getattr(usage, "completion_tokens", 0) or 0)
            return action_decision, refused, messages + [{"role": "assistant", "content": original_content}], tokens

        refused.append({"action": action, "parameter": parameter, "journal_action": refusal[0], "reason": refusal[1]})
        print(f"♻️ Cheap retry {len(refused)}/{MAX_CHEAP_RETRIES}: [{action}] refused. {refusal[1]}")
        correction = refusal_correction(action, refusal[1], page_state, current_url)
        if len(refused) >= 2:
            correction = escalation_message(len(refused), action, refusal[1], page_state, current_url) + "\nReply with only the JSON object."
        messages += [{"role": "assistant", "content": original_content}, {"role": "user", "content": correction}]

def write_research_log(objective, journal):
    with open("research_log.txt", "w", encoding="utf-8") as log:
        log.write(f"OBJECTIVE: {objective}\n")
        log.write(json.dumps(journal, indent=2))

async def load_page(page, url=None):
    if url:
        await page.goto(url, wait_until="domcontentloaded", timeout=30000)
    else:
        await page.reload(wait_until="domcontentloaded", timeout=30000)
    try:
        await page.wait_for_load_state("networkidle", timeout=5000)
    except Exception:
        pass  # Busy pages rarely go fully idle; the DOM is already usable
    await asyncio.sleep(1)

async def main():
    client = AsyncOpenAI(
        base_url="http://localhost:1234/v1",
        api_key="lm-studio"
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
    tried = {"queries": [], "urls": []}
    page_state = {"url": None, "parts": [], "index": 0}
    conversation = new_conversation()
    loop_msg = None
    consecutive_refusals = 0

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        # No user_agent override: headed Chromium already reports a real, version-matched Chrome UA
        context = await browser.new_context(
            java_script_enabled=True,
            viewport={"width": 1280, "height": 720}
        )
        await context.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined});")
        page = await context.new_page()

        while turn_counter <= max_turns:
            print(f"\n🧠 [TURN {turn_counter}/{max_turns}] Active reasoning cycle executing...")

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
                print("💡 Check that LM Studio is actively running and model 'qwen2.5-7b-instruct' is loaded.")
                break

            # Replies refused and retried within this turn stay in the journal so the model sees what failed
            for r in refused:
                journal.append({"turn": turn_counter, "action": "REFUSED_IN_TURN", "requested": f"{r['action']} {r['parameter'][:120]}", "reason": r["reason"]})

            print(f"💡 Agent Thoughts: {thought}")
            print(f"🎬 Chosen Action: [{action}] -> Parameter: {parameter}")

            refusal = check_decision(action, parameter, journal, tried, page_state, current_url)
            if refusal:
                # Only reached once this turn's cheap retries are used up
                print(f"⚠️ Action refused: {refusal[1]}")
                journal.append({"turn": turn_counter, "thought": thought, "action": refusal[0], "requested": parameter, "reason": refusal[1]})
                loop_msg = f"Your {action} was refused. {refusal[1]}"
                set_note(conversation, "RESULT: Nothing changed; the CURRENT ENVIRONMENT above is unchanged.", current_url)

            elif action == "FINISH":
                print("\n🎯 Agent has signaled mission complete. Writing final summary report...")
                journal.append({"turn": turn_counter, "thought": thought, "action": "FINISH", "finding": parameter})
                break

            elif action in ["SEARCH", "NAVIGATE"]:
                target_url = determine_url(action, parameter)
                print(f"🌐 Driving Playwright to cross-domain target: {target_url}")
                try:
                    await load_page(page, target_url)
                    current_url = page.url

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
                set_note(conversation, f"{result}\n\n### CURRENT ENVIRONMENT (replaces the earlier one)\n"
                                       f"Active URL: {current_url}\n{environment}", current_url)

            elif action == "READ_MORE":
                target = read_more_target(parameter, page_state)
                page_state["index"] = target
                print(f"📖 Showing part {target + 1} of {len(page_state['parts'])} of the current page.")
                journal.append({"turn": turn_counter, "thought": thought, "action": "READ_MORE", "part": f"{target + 1} of {len(page_state['parts'])}"})
                set_note(conversation, f"RESULT: READ_MORE done. The CURRENT ENVIRONMENT now shows:\n{render_part(page_state)}\n"
                                       f"The links listed earlier for this page still apply.", current_url)

            elif action == "DISCOVER":
                print(f"📝 Logging critical discovery element: {parameter}")
                journal.append({"turn": turn_counter, "thought": thought, "action": "DISCOVER", "source": current_url, "extracted_data": parameter})
                set_note(conversation, "RESULT: Your DISCOVER was recorded and is now part of DISCOVERIES SO FAR. "
                                       "The CURRENT ENVIRONMENT above is unchanged. Do not record the same information again.", current_url)

            # A single refusal gets a short warning; repeated refusals get an explicit list of other options
            last_entry = journal[-1]["action"] if journal else ""
            if last_entry.startswith(("BLOCKED_", "UNKNOWN_")) or last_entry == "DUPLICATE_DISCOVER":
                consecutive_refusals += 1
                if consecutive_refusals >= 2:
                    print(f"⚠️ {consecutive_refusals} refused actions in a row. Escalating loop warning.")
                    loop_msg = escalation_message(consecutive_refusals, action, journal[-1].get("reason", ""), page_state, current_url)
            else:
                consecutive_refusals = 0

            write_research_log(objective, journal)

            turn_counter += 1
            print("\n⏱️ Cycle pacing check complete. Refreshing navigation loop pipeline...")
            await asyncio.sleep(TURN_PAUSE_SECONDS)

        # Written again here because FINISH leaves the loop before the per-turn write
        write_research_log(objective, journal)
        print("\n🔒 Shutting down browser instance. Research log finalized in research_log.txt.")
        await browser.close()

if __name__ == "__main__":
    asyncio.run(main())
