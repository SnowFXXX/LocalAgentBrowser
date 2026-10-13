import asyncio
import json
import urllib.parse
import re
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
2. Never repeat a query or URL listed under ALREADY TRIED; repeats are blocked automatically.
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

async def extract_page_links(page):
    # Links come from the whole page, menus included, since navigation menus are how sites are used
    # a.href resolves relative links (e.g. /docs/intro) to absolute URLs
    links = await page.evaluate("""() => Array.from(document.querySelectorAll('a[href]'))
        .map(a => ({text: a.innerText.trim().replace(/\\s+/g, ' '), href: a.href.split('#')[0]}))
        .filter(l => l.href.startsWith('http') && l.text.length >= 4)""")
    seen = set()
    link_lines = []
    for l in links:
        if l["href"] in seen or normalize_url(l["href"]) == normalize_url(page.url):
            continue
        seen.add(l["href"])
        link_lines.append(f"- {l['text'][:60]} -> {l['href']}")
        if len(link_lines) >= MAX_LINKS:
            break
    return "\n".join(link_lines)

async def build_environment(page, current_url, page_state, visited_urls=frozenset()):
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
        parts, index = page_state["parts"], page_state["index"]
        link_lines = await extract_page_links(page)

        if not parts:
            return f"Page Text:\n[No readable text]\n\nLinks On Page:\n{link_lines or '[None]'}"
        if index < len(parts) - 1:
            footer = f"[Page continues: READ_MORE shows part {index + 2} of {len(parts)}]"
        else:
            footer = "[End of page]"
        return f"Page Text (part {index + 1} of {len(parts)}):\n{parts[index]}\n{footer}\n\nLinks On Page:\n{link_lines or '[None]'}"
    except Exception as e:
        return f"[Error reading page content: {e}]"

def discovery_lines(text):
    return {" ".join(line.lower().split()) for line in str(text).splitlines() if line.strip()}

def is_known_discovery(parameter, journal):
    # A discovery is a repeat when every one of its lines was already recorded, in any order or grouping
    new_lines = discovery_lines(parameter)
    known_lines = set()
    for e in journal:
        if e.get("action") == "DISCOVER":
            known_lines |= discovery_lines(e["extracted_data"])
    return bool(new_lines) and new_lines <= known_lines

def escalation_message(refusals, last_action, last_reason, page_state, current_url):
    on_page = current_url and page_state["url"] == normalize_url(current_url) and page_state["parts"]
    if on_page and page_state["index"] < len(page_state["parts"]) - 1:
        read_more = f"READ_MORE to see part {page_state['index'] + 2} of {len(page_state['parts'])} of this page"
    elif on_page:
        read_more = "READ_MORE with a part number to revisit an earlier part (you are on the last part of this page)"
    else:
        read_more = "READ_MORE is not available here"
    return (f"Your last {refusals} actions were all refused. Do NOT choose {last_action} again with the same or similar parameter. "
            f"Last refusal reason: {last_reason} Choose a different action:\n"
            f"- {read_more}\n"
            f"- NAVIGATE to a link not listed under ALREADY TRIED\n"
            f"- SEARCH with a new query\n"
            f"- FINISH if DISCOVERIES SO FAR already satisfies the objective")

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

async def run_agent_turn(client, page, objective, journal, current_url, tried, page_state, loop_warning=None):
    print("🔍 Scraping and preparing current environment text layout...")
    environment = await build_environment(page, current_url, page_state, {normalize_url(u) for u in tried["urls"]})

    warning_prompt = f"\n\n⚠️ LOOP DETECTION WARNING: {loop_warning}" if loop_warning else ""
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

### ALREADY TRIED (Do not repeat)
Queries: {json.dumps(tried["queries"]) if tried["queries"] else "[None]"}
URLs: {json.dumps(tried["urls"]) if tried["urls"] else "[None]"}

### CURRENT ENVIRONMENT
Active URL: {current_url if current_url else "None (Loop Initializing)"}
{environment}{warning_prompt}

If the CURRENT ENVIRONMENT contains information relevant to the objective that is not in DISCOVERIES SO FAR, choose DISCOVER now.
Determine your next tactical move. Output your decision in the mandatory JSON format:"""

    for attempt in range(1, MAX_JSON_RETRIES + 1):
        response = await client.chat.completions.create(
            model="qwen2.5-7b-instruct",
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_payload}
            ],
            temperature=0.2,
            response_format=RESPONSE_FORMAT
        )
        raw_content = extract_message_content(response)

        if raw_content.startswith("```"):
            raw_content = re.sub(r'^```json\s*|^```\s*|```$', '', raw_content, flags=re.MULTILINE).strip()

        try:
            action_decision = json.loads(raw_content)
            if isinstance(action_decision, dict):
                return action_decision
            print(f"⚠️ Model returned non-object JSON (attempt {attempt}/{MAX_JSON_RETRIES}).")
        except json.JSONDecodeError:
            print(f"⚠️ Model returned invalid JSON (attempt {attempt}/{MAX_JSON_RETRIES}): {raw_content[:200]}")

    raise ValueError(f"No valid JSON decision after {MAX_JSON_RETRIES} attempts.")

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
                decision = await run_agent_turn(client, page, objective, journal, current_url, tried, page_state, loop_warning=loop_msg)
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

            print(f"💡 Agent Thoughts: {thought}")
            print(f"🎬 Chosen Action: [{action}] -> Parameter: {parameter}")

            if action == "FINISH":
                print("\n🎯 Agent has signaled mission complete. Writing final summary report...")
                journal.append({"turn": turn_counter, "thought": thought, "action": "FINISH", "finding": parameter})
                break

            elif action in ["SEARCH", "NAVIGATE"]:
                target_url = determine_url(action, parameter)
                visited = {normalize_url(u) for u in tried["urls"]}

                # Repeats are blocked outright; a warning alone does not stop small models from looping
                if action == "SEARCH" and normalize_query(search_query(parameter)) in [normalize_query(q) for q in tried["queries"]]:
                    reason = "This exact query was already searched. Use a new query, or DISCOVER from the current page."
                elif action == "NAVIGATE" and normalize_url(target_url) in visited:
                    reason = "This URL was already visited. Pick a URL not listed under ALREADY TRIED."
                else:
                    reason = None

                if reason:
                    print(f"⚠️ Anti-Loop Block: {action} '{parameter}' was already tried.")
                    journal.append({"turn": turn_counter, "thought": thought, "action": f"BLOCKED_{action}", "requested": parameter, "reason": reason})
                    loop_msg = f"Your {action} '{parameter}' was blocked. {reason}"
                else:
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

            elif action == "READ_MORE":
                total = len(page_state["parts"])
                on_page = current_url and page_state["url"] == normalize_url(current_url)
                target = int(parameter) - 1 if parameter.isdigit() else page_state["index"] + 1

                if not on_page or total == 0:
                    reason = "The current page has no parts to read. Use SEARCH or NAVIGATE first."
                elif not 0 <= target < total:
                    reason = f"There is no part {target + 1}; this page has {total} part(s) and you are on part {page_state['index'] + 1}."
                else:
                    reason = None

                if reason:
                    print(f"⚠️ READ_MORE refused: {reason}")
                    journal.append({"turn": turn_counter, "thought": thought, "action": "BLOCKED_READ_MORE", "requested": parameter, "reason": reason})
                    loop_msg = reason
                else:
                    page_state["index"] = target
                    print(f"📖 Showing part {target + 1} of {total} of the current page.")
                    journal.append({"turn": turn_counter, "thought": thought, "action": "READ_MORE", "part": f"{target + 1} of {total}"})

            elif action == "DISCOVER":
                if not discovery_lines(parameter):
                    print("⚠️ Empty discovery refused.")
                    journal.append({"turn": turn_counter, "thought": thought, "action": "BLOCKED_DISCOVER", "reason": "The DISCOVER parameter was empty."})
                    loop_msg = "Your DISCOVER had an empty parameter. Put the actual information in the parameter, or choose a different action."
                elif is_known_discovery(parameter, journal):
                    print("⚠️ Duplicate discovery ignored.")
                    journal.append({"turn": turn_counter, "thought": thought, "action": "DUPLICATE_DISCOVER", "reason": "Already recorded in DISCOVERIES SO FAR."})
                    loop_msg = "That discovery is already recorded. Find a new detail, read more of the page, move to a new page, or FINISH if the objective is met."
                else:
                    print(f"📝 Logging critical discovery element: {parameter}")
                    journal.append({"turn": turn_counter, "thought": thought, "action": "DISCOVER", "source": current_url, "extracted_data": parameter})

            else:
                print(f"⚠️ Unknown action '{action}' ignored.")
                journal.append({"turn": turn_counter, "thought": thought, "action": f"UNKNOWN_{action}", "requested": parameter})

            # A single refusal gets a short warning; repeated refusals get an explicit list of other options
            last_entry = journal[-1]["action"] if journal else ""
            if last_entry.startswith("BLOCKED_") or last_entry == "DUPLICATE_DISCOVER":
                consecutive_refusals += 1
                if consecutive_refusals >= 2:
                    print(f"⚠️ {consecutive_refusals} refused actions in a row. Escalating loop warning.")
                    loop_msg = escalation_message(consecutive_refusals, action, journal[-1].get("reason", ""), page_state, current_url)
            else:
                consecutive_refusals = 0

            with open("research_log.txt", "w", encoding="utf-8") as log:
                log.write(f"OBJECTIVE: {objective}\n")
                log.write(json.dumps(journal, indent=2))

            turn_counter += 1
            print("\n⏱️ Cycle pacing check complete. Refreshing navigation loop pipeline...")
            await asyncio.sleep(3)

        print("\n🔒 Shutting down browser instance. Research log finalized in research_log.txt.")
        await browser.close()

if __name__ == "__main__":
    asyncio.run(main())
