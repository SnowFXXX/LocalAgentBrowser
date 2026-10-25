"""The agent loop: each turn's conversation with the model, the fresh prompt, in-turn retries, and main()."""
import asyncio
import json
import re
from openai import AsyncOpenAI
from playwright.async_api import async_playwright
from .config import API_KEY, BASE_URL, MAX_CHEAP_RETRIES, MAX_CONVERSATION_TOKENS, MAX_DISCOVERY_PROMPT_CHARS, MAX_FOLLOWUPS, MAX_JSON_RETRIES, MAX_VISITS_PER_URL, MODEL, OBJECTIVE_CHECK_EVERY, REPORT_FILE, STALL_TURNS, TURN_PAUSE_SECONDS
from .prompts import FINAL_RESULT, FIRST_TURN_INSTRUCTION, GIVE_UP_RULE, READ_FIRST, RESPONSE_FORMAT, SYSTEM_PROMPT, TURN_INSTRUCTIONS
from .text import extract_message_content
from .pages import build_environment, count_search_results, determine_url, load_page, normalize_url, read_more_target, render_part, search_query, where_you_are
from .checklist import after_discovery_advice, check_parts, checklist_lines, link_discovery, split_objective
from .coverage import coverage_refusal, not_found_items
from .checks import check_decision, escalation_message, numbered_discoveries, numbered_lines, refusal_correction, source_note
from .report import compact_journal, compose_final_result, visited_summary, write_report, write_research_log

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
