import asyncio
import json
import urllib.parse
import re
from openai import AsyncOpenAI
from playwright.async_api import async_playwright

SEARCH_URL = "https://html.duckduckgo.com/html/?q="
JOURNAL_WINDOW = 6       # Only the most recent journal entries are sent to the model
MAX_THOUGHT_CHARS = 120  # Old reasoning is trimmed in the prompt; the full text stays in research_log.txt
MAX_SEARCH_RESULTS = 8
MAX_SNIPPET_CHARS = 200
MAX_PAGE_CHARS = 4500
MAX_LINKS = 15
MAX_JSON_RETRIES = 3

SYSTEM_PROMPT = """You are an autonomous web discovery agent. Your job is to achieve the high-level OBJECTIVE by executing sequential actions.
On every turn, evaluate the CURRENT ENVIRONMENT and your RESEARCH JOURNAL. You must output exactly ONE valid JSON block matching this structure:

{
  "thought_process": "Your active reasoning about what to discover next and why.",
  "action": "SEARCH" | "NAVIGATE" | "DISCOVER" | "FINISH",
  "parameter": "The search query string, the full target URL, the extracted summary data, or an empty string for FINISH"
}

Action guide:
- SEARCH: parameter is a plain-text query (not a URL). Results appear as a numbered list with URLs.
- NAVIGATE: parameter is a full URL copied exactly from the search results or page links you were shown.
- DISCOVER: parameter is the concrete text you found on the current page: commands, config blocks, file names, parameters. Copy them exactly. You may group several related details from the same page into one DISCOVER.
- FINISH: only when your discoveries satisfy the objective.

Strictly follow these rules:
1. If the current page contains any command, config, or file convention relevant to the objective that is not already in DISCOVERIES SO FAR, your action MUST be DISCOVER before you leave the page.
2. Never repeat a query or URL listed under ALREADY TRIED; repeats are blocked automatically.
3. Prefer official documentation sources over third-party blogs and mirrors.
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
                "action": {"type": "string", "enum": ["SEARCH", "NAVIGATE", "DISCOVER", "FINISH"]},
                "parameter": {"type": "string"}
            },
            "required": ["thought_process", "action", "parameter"]
        }
    }
}

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

async def extract_page_content(page):
    body_text = await page.evaluate("""() => {
        const root = document.querySelector('main, article, [role=main]') || document.body;
        return root ? root.innerText : '';
    }""")
    body_text = re.sub(r'\n\s*\n+', '\n\n', body_text or "").strip()
    if len(body_text) > MAX_PAGE_CHARS:
        body_text = body_text[:MAX_PAGE_CHARS] + "\n\n[... Text Truncated for Context Security ...]"

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

    return body_text, "\n".join(link_lines)

async def build_environment(page, current_url, visited_urls=frozenset()):
    if not current_url:
        return "[Empty or No Target Page Loaded Yet]"
    try:
        if "duckduckgo.com" in current_url:
            results = await extract_search_results(page, visited_urls)
            if results:
                return f"Search Results:\n{results}"
            body_text, _ = await extract_page_content(page)
            return f"[No search results parsed - possibly no matches or a bot-check page]\n{body_text[:1500]}"

        body_text, link_lines = await extract_page_content(page)
        return f"Page Text:\n{body_text or '[No readable text]'}\n\nLinks On Page:\n{link_lines or '[None]'}"
    except Exception as e:
        return f"[Error reading page content: {e}]"

def compact_journal(journal):
    compact = []
    for entry in journal[-JOURNAL_WINDOW:]:
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

async def run_agent_turn(client, page, objective, journal, current_url, tried, loop_warning=None):
    print("🔍 Scraping and preparing current environment text layout...")
    environment = await build_environment(page, current_url, {normalize_url(u) for u in tried["urls"]})

    warning_prompt = f"\n\n⚠️ LOOP DETECTION WARNING: {loop_warning}" if loop_warning else ""
    discoveries = [e["extracted_data"] for e in journal if e.get("action") == "DISCOVER"]
    recent_journal = compact_journal(journal)

    # Stable sections come first so LM Studio can reuse its cached prompt prefix between turns;
    # the page content goes last, where small models pay the most attention.
    user_payload = f"""### HIGH-LEVEL OBJECTIVE
{objective}

### DISCOVERIES SO FAR ({len(discoveries)})
{json.dumps(discoveries, indent=2) if discoveries else "[None yet]"}

### ALREADY TRIED (Do not repeat)
Queries: {json.dumps(tried["queries"]) if tried["queries"] else "[None]"}
URLs: {json.dumps(tried["urls"]) if tried["urls"] else "[None]"}

### RECENT RESEARCH JOURNAL (Last {len(recent_journal)} of {len(journal)} entries)
{json.dumps(recent_journal, indent=2)}

### CURRENT ENVIRONMENT
Active URL: {current_url if current_url else "None (Loop Initializing)"}
{environment}{warning_prompt}

If the CURRENT ENVIRONMENT contains commands, config, or file conventions relevant to the objective that are not in DISCOVERIES SO FAR, choose DISCOVER now.
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
    loop_msg = None

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
                decision = await run_agent_turn(client, page, objective, journal, current_url, tried, loop_warning=loop_msg)
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
                    if action == "SEARCH":
                        tried["queries"].append(search_query(parameter))
                    print(f"🌐 Driving Playwright to cross-domain target: {target_url}")
                    try:
                        await page.goto(target_url, wait_until="domcontentloaded", timeout=30000)
                        try:
                            await page.wait_for_load_state("networkidle", timeout=5000)
                        except Exception:
                            pass  # Busy pages rarely go fully idle; the DOM is already usable
                        await asyncio.sleep(1)
                        current_url = page.url
                        if action == "NAVIGATE":
                            tried["urls"].append(target_url)
                            if normalize_url(current_url) != normalize_url(target_url):
                                tried["urls"].append(current_url)  # Also remember where redirects landed
                        journal.append({"turn": turn_counter, "thought": thought, "action": action, "requested": parameter, "destination": current_url})
                    except Exception as b_err:
                        print(f"❌ Failed to reach target destination: {b_err}")
                        if action == "NAVIGATE":
                            tried["urls"].append(target_url)
                        journal.append({"turn": turn_counter, "thought": thought, "action": f"FAILED_{action}", "requested": parameter, "error": str(b_err)})

            elif action == "DISCOVER":
                known = [e["extracted_data"].strip().lower() for e in journal if e.get("action") == "DISCOVER"]
                if parameter.lower() in known:
                    print("⚠️ Duplicate discovery ignored.")
                    journal.append({"turn": turn_counter, "thought": thought, "action": "DUPLICATE_DISCOVER", "reason": "Already recorded in DISCOVERIES SO FAR."})
                    loop_msg = "That discovery is already recorded. Find a new detail, move to a new page, or FINISH if the objective is met."
                else:
                    print(f"📝 Logging critical discovery element: {parameter}")
                    journal.append({"turn": turn_counter, "thought": thought, "action": "DISCOVER", "source": current_url, "extracted_data": parameter})

            else:
                print(f"⚠️ Unknown action '{action}' ignored.")
                journal.append({"turn": turn_counter, "thought": thought, "action": f"UNKNOWN_{action}", "requested": parameter})

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
