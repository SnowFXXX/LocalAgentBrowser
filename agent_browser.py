import asyncio
import json
import urllib.parse
import re
from openai import AsyncOpenAI
from playwright.async_api import async_playwright

SYSTEM_PROMPT = """You are an autonomous web discovery agent. Your job is to achieve the high-level OBJECTIVE by executing sequential actions.
On every turn, evaluate the CURRENT ENVIRONMENT and your RESEARCH JOURNAL. You must output exactly ONE valid JSON block matching this structure:

{
  "thought_process": "Your active reasoning about what to discover next and why.",
  "action": "SEARCH" | "NAVIGATE" | "DISCOVER" | "FINISH",
  "parameter": "The search query string, the full target URL, the extracted summary data, or an empty string for FINISH"
}

Strictly follow these rules:
1. Do not repeat actions or URLs you have already visited unless necessary.
2. Avoid circular loops; if a path is a dead end, pivot your search parameters or navigate away.
3. Output ONLY raw JSON. No markdown wrappers, no backticks, no conversational filler."""

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
        if "google.com/search?" in clean_param:
            parsed_url = urllib.parse.urlparse(clean_param)
            query_dict = urllib.parse.parse_qs(parsed_url.query)
            if 'q' in query_dict:
                clean_param = query_dict['q'][0] if isinstance(query_dict['q'], list) else query_dict['q']
        
        clean_query = clean_param.replace('+', ' ')
        encoded = urllib.parse.quote_plus(clean_query)
        return f"https://www.google.com/search?q={encoded}"
    return None

async def run_agent_turn(client, browser_context, objective, journal, current_url, loop_warning=None):
    print("🔍 Scraping and preparing current environment text layout...")
    page_text = ""
    
    if current_url:
        try:
            pages = browser_context.pages
            if pages:
                page = pages[-1]
                locators = await page.locator("p, h1, h2, h3, li, span, div").all_inner_texts()
                content_blocks = [t.strip() for t in locators if len(t.strip()) > 35 and "JavaScript" not in t and "function()" not in t]
                clean_blocks = list(dict.fromkeys(content_blocks))
                
                sliced_blocks = clean_blocks[:6]
                page_text = "\n\n".join(sliced_blocks)
                
                if len(page_text) > 6000:
                    page_text = page_text[:6000] + "\n\n[... Text Truncated for Context Security ...]"
        except Exception as e:
            page_text = f"[Error reading page text layouts: {e}]"

    warning_prompt = f"\n⚠️ LOOP DETECTION WARNING: {loop_warning}\n" if loop_warning else ""

    user_payload = f"""### HIGH-LEVEL OBJECTIVE
{objective}

### CURRENT ENVIRONMENT
Active URL: {current_url if current_url else "None (Loop Initializing)"}
Extracted Layout Text:
{page_text if page_text else "[Empty or No Target Page Loaded Yet]"}{warning_prompt}

### PERSISTENT RESEARCH JOURNAL (Your Historical Actions & Findings)
{json.dumps(journal, indent=2)}

Determine your next tactical move. Output your decision in the mandatory JSON format:"""

    response = await client.chat.completions.create(
        model="qwen2.5-7b-instruct",
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_payload}
        ],
        temperature=0.2
    )
    
    choices = getattr(response, "choices", None)
    if choices is None and isinstance(response, dict):
        choices = response.get("choices", [])
        
    if isinstance(choices, list) and len(choices) > 0:
        first_choice = choices[0]
    else:
        first_choice = choices
    
    if hasattr(first_choice, "message"):
        raw_content = first_choice.message.content.strip()
    elif isinstance(first_choice, dict) and "message" in first_choice:
        msg = first_choice["message"]
        raw_content = msg.get("content", "").strip() if isinstance(msg, dict) else msg.content.strip()
    else:
        raw_content = first_choice.get("message", {}).get("content", "").strip()

    if raw_content.startswith("```"):
        raw_content = re.sub(r'^```json\s*|^```\s*|```$', '', raw_content, flags=re.MULTILINE).strip()
        
    action_decision = json.loads(raw_content)
    return action_decision

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
    parameter_history = {}
    loop_msg = None
    
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        context = await browser.new_context(
            java_script_enabled=True,
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
            viewport={"width": 1280, "height": 720}
        )
        await context.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined});")
        page = await context.new_page()

        while turn_counter <= max_turns:
            print(f"\n🧠 [TURN {turn_counter}/{max_turns}] Active reasoning cycle executing...")
            
            try:
                decision = await run_agent_turn(client, context, objective, journal, current_url, loop_warning=loop_msg)
                thought = decision.get("thought_process", "No reasoning recorded.")
                action = decision.get("action", "FINISH")
                parameter = decision.get("parameter", "")
                loop_msg = None
            except Exception as err:
                print(f"❌ Local AI API transaction failed: {err}")
                print("💡 Check that LM Studio is actively running and model 'qwen2.5-7b-instruct' is loaded.")
                break
            
            print(f"💡 Agent Thoughts: {thought}")
            print(f"🎬 Chosen Action: [{action}] -> Parameter: {parameter}")
            
            param_key = str(parameter).strip()
            parameter_history[param_key] = parameter_history.get(param_key, 0) + 1

            if parameter_history[param_key] > 1 and action != "FINISH":
                print(f"⚠️ Tracked parameter overuse detected! Preparing loop warning context flag.")
                loop_msg = f"You have issued the parameter '{param_key}' multiple times. Pivot your query variations immediately."

            if action == "FINISH":
                print("\n🎯 Agent has signaled mission complete. Writing final summary report...")
                journal.append({"turn": turn_counter, "thought": thought, "action": "FINISH", "finding": parameter})
                break
                
            elif action in ["SEARCH", "NAVIGATE"]:
                target_url = determine_url(action, parameter)
                
                if target_url and current_url and target_url.split("?")[0] == current_url.split("?")[0]:
                    print(f"⚠️ Anti-Loop Warning: Model requested duplicate target URL domain endpoint.")
                    journal.append({"turn": turn_counter, "thought": thought, "action": f"BLOCKED_{action}", "reason": "Already resting on target URL destination layout contents."})
                    action = "DISCOVER"
                    parameter = f"Current page context info verified at destination: {current_url}"
                else:
                    print(f"🌐 Driving Playwright to cross-domain target: {target_url}")
                    try:
                        await page.goto(target_url)
                        await asyncio.sleep(10)  
                        await page.wait_for_load_state("networkidle")
                        current_url = page.url
                        journal.append({"turn": turn_counter, "thought": thought, "action": action, "destination": current_url})
                    except Exception as b_err:
                        print(f"❌ Failed to reach target destination: {b_err}")
                        journal.append({"turn": turn_counter, "thought": thought, "action": f"FAILED_{action}", "error": str(b_err)})
            
            if action == "DISCOVER":
                print(f"📝 Logging critical discovery element: {parameter}")
                journal.append({"turn": turn_counter, "thought": thought, "action": "DISCOVER", "extracted_data": parameter})
            
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
