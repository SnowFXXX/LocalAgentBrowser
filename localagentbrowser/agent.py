"""The agent loop: each turn's conversation with the model, the fresh prompt, in-turn retries, and main()."""
import asyncio
import ipaddress
import json
import re
import socket
import sys
import time
import urllib.parse
from openai import APIConnectionError, APITimeoutError, AsyncOpenAI, BadRequestError, InternalServerError
# cloud:start
from openai import NotFoundError, RateLimitError
# cloud:end
from playwright.async_api import async_playwright
from .config import API_KEY, BASE_URL, CHROMIUM_ARGS, MAX_CHEAP_RETRIES, MAX_CONVERSATION_TOKENS, MAX_DISCOVERY_PROMPT_CHARS, MAX_FOLLOWUPS, MAX_JSON_RETRIES, MAX_QUERY_WORDS, MAX_VISITS_PER_URL, MODEL, MODEL_RETRY_WAITS, MODEL_TIMEOUT_SECONDS, TURN_MAX_TOKENS, OBJECTIVE_CHECK_EVERY, REPORT_FILE, STALL_TURNS, TURN_PAUSE_SECONDS
# cloud:start
from .config import EXTRA_BODY, EXTRA_HEADERS, RATE_LIMIT_WAITS
# cloud:end
from .prompts import CUT_OFF, CUT_OFF_DISCOVER, CUT_OFF_DISCOVER_WORDS, CUT_REPLY, FINAL_RESULT, FIRST_TURN_INSTRUCTION, GIVE_UP_RULE, READ_FIRST, RESPONSE_FORMAT, SYSTEM_PROMPT, TURN_INSTRUCTIONS
from .text import extract_message_content, was_cut_off, without_control_characters
from .pages import build_environment, count_search_results, determine_url, is_search_page, load_page, normalize_url, read_more_target, render_part, search_query, section_heading, where_you_are, with_inner_headings, mark_refusing, site_of, site_refusal
from .checklist import after_discovery_advice, check_parts, checklist_lines, link_discovery, split_objective
from .coverage import amounts_not_found, coverage_refusal, not_found_items, record_install_line, remember_install_lines
from .checks import check_decision, escalation_message, numbered_discoveries, numbered_lines, refusal_correction, source_note
from . import windows
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
        print(f"Continuing the conversation (follow-up {conversation['followups']}/{MAX_FOLLOWUPS}, ~{estimated_tokens} tokens)")
        messages = conversation["messages"] + [{"role": "user", "content": f"{note}{warning_prompt}\n\n{TURN_INSTRUCTIONS}"}]
    else:
        conversation.update(followups=0, shown_links=set())
        messages = await build_full_prompt(page, objective, journal, current_url, tried, page_state, warning_prompt, conversation["shown_links"])
    conversation["note"] = None

    decision, refused, messages, tokens = await request_decision(client, messages, journal, tried, page_state, current_url)
    conversation.update(messages=messages, tokens=tokens)
    return decision, refused

async def build_full_prompt(page, objective, journal, current_url, tried, page_state, warning_prompt, shown_links=None):
    print("Scraping and preparing current environment text layout...")
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

def cut_decision(raw, on_search_page=False):
    """The SEARCH, NAVIGATE or READ_MORE in a reply cut off inside its parameter, the parameter ending where it
    starts to repeat itself; None when the cut came earlier, or for a DISCOVER or FINISH, whose cut text is never
    sound (every cut DISCOVER in the 3B's runs was its own looping words, and one of them got recorded). On the
    results page a DISCOVER is kept: it is always refused there, and the refusal says to open a result, which the
    plain cut-off note did not (three cut DISCOVERs there lost a turn)."""
    found = re.match(r'\s*\{\s*"thought_process"\s*:\s*"((?:[^"\\]|\\.)*)"\s*,\s*"action"\s*:\s*"([A-Z_]+)"\s*,'
                     r'\s*"parameter"\s*:\s*"((?:[^"\\]|\\.)*)', raw)
    if not found or found.group(2) not in ("SEARCH", "NAVIGATE", "READ_MORE", *(("DISCOVER",) if on_search_page else ())):
        return None
    thought, action, parameter = (json.loads(f'"{text.rstrip(chr(92))}"') for text in found.groups())
    words = parameter.split()
    # The cut parameters looped ("...comprehensive guide for beginners professionals cold brew coffee making..."
    # again and again): the first six words seen twice mark where the loop began
    seen = set()
    for i in range(len(words) - 5):
        run = tuple(w.lower() for w in words[i:i + 6])
        if run in seen:
            words = words[:i]
            break
        seen.add(run)
    if action == "SEARCH":
        words = words[:MAX_QUERY_WORDS]
    return {"thought_process": thought, "action": action, "parameter": " ".join(words), "checklist_items": []} if words else None

CUT_ACTION = re.compile(r'"action"\s*:\s*"([A-Z_]+)"')

def cut_action(raw):
    """The action a reply cut off at the length limit had named before the cut, or "" if it was cut before naming
    one (inside a long thought_process)."""
    found = CUT_ACTION.search(str(raw))
    return found.group(1) if found else ""

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
            max_tokens=TURN_MAX_TOKENS,
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
            # A 3B model's search queries looped through the same words until the cap, three times in a row; their
            # start is a usable decision, which the checks below judge like any other
            action_decision = cut_decision(raw_content, is_search_page(current_url)) if was_cut_off(response) else None
            if action_decision:
                print(f"The reply was cut off inside its parameter; going on with its start: [{action_decision['action']}] {action_decision['parameter'][:120]}")
            elif was_cut_off(response) and cut_action(raw_content) == "FINISH":
                # Opus 5.5 wrote whole answers into FINISH's parameter and was cut off, and asked again it wrote them
                # again: the final result is written from the discoveries in a step of its own, so the FINISH stands
                # and its cut text is not used
                print("The reply was cut off inside its FINISH; going on with FINISH (the final result is written "
                      "in its own step).")
                action_decision = {"thought_process": "", "action": "FINISH", "parameter": "", "checklist_items": []}
            else:
                json_failures += 1
                print(f"Warning: Model returned invalid JSON (attempt {json_failures}/{MAX_JSON_RETRIES}): {raw_content[:200]}")
                if json_failures >= MAX_JSON_RETRIES:
                    raise ValueError(f"No valid JSON decision after {MAX_JSON_RETRIES} attempts.") from parse_err
                if was_cut_off(response):
                    # A 3B model's replies ran past the cap in 25 replies of one suite; asked only for valid JSON, it
                    # wrote the same long reply again, and 8 turns were lost. The cut reply is left out, so the model
                    # is not shown a long reply to continue; a short stand-in keeps the turns alternating.
                    correction = (CUT_OFF_DISCOVER.format(limit=TURN_MAX_TOKENS, words=CUT_OFF_DISCOVER_WORDS)
                                  if cut_action(raw_content) == "DISCOVER" else CUT_OFF.format(limit=TURN_MAX_TOKENS))
                    messages += [{"role": "assistant", "content": CUT_REPLY}, {"role": "user", "content": correction}]
                    continue
                messages += [{"role": "assistant", "content": original_content},
                             {"role": "user", "content": f"That reply was not a valid JSON object ({parse_err}). Reply with only the JSON object in the mandatory format."}]
                continue

        action = str(action_decision.get("action", "FINISH")).upper()
        parameter = without_control_characters(action_decision.get("parameter", "")).strip()
        refusal = check_decision(action, parameter, journal, tried, page_state, current_url,
                                 action_decision.get("thought_process", ""))
        if not refusal or len(refused) >= MAX_CHEAP_RETRIES:
            usage = getattr(response, "usage", None)
            tokens = (getattr(usage, "prompt_tokens", 0) or 0) + (getattr(usage, "completion_tokens", 0) or 0)
            return action_decision, refused, messages + [{"role": "assistant", "content": original_content}], tokens

        refused.append({"action": action, "parameter": parameter, "journal_action": refusal[0], "reason": refusal[1]})
        print(f"Cheap retry {len(refused)}/{MAX_CHEAP_RETRIES}: [{action}] refused. {refusal[1]}")
        correction = refusal_correction(action, refusal[1], page_state, current_url)
        if refusal[0].startswith("CONFIRM_"):
            # Not a true refusal: repeating the action is an allowed answer, so no "do not repeat" wording
            correction = f"CHECK: {refusal[1]} Reply with only the JSON object."
        elif refusal[0] in ("BLOCKED_NOT_ON_PAGE", "BLOCKED_CODE_NOT_ON_PAGE") and len(refused) < 2:
            # The refusal says what to do instead (DISCOVER the quoted sentence or read on); with the usual "Do NOT
            # choose DISCOVER ... choose a different action" around it, the model read on 3 times out of 3 past the
            # sentence it was shown
            correction = f"REFUSED: {refusal[1]} Reply with only the JSON object."
        elif len(refused) >= 2:
            correction = escalation_message(len(refused), action, refusal[1], page_state, current_url) + "\nReply with only the JSON object."
        messages += [{"role": "assistant", "content": original_content}, {"role": "user", "content": correction}]

# cloud:start
class ModelRefused(Exception):
    """The model service answered with nothing because its content filter stopped the reply."""
# cloud:end

def is_local_server():
    return bool(re.match(r"https?://(localhost|127\.0\.0\.1|\[::1\])(:|/|$)", BASE_URL))

def on_this_pc_or_network(url):
    """Whether a server address is on this PC or the local network: every address its name stands for is a
    loopback, private (home or office network) or link-local one. A name that cannot be looked up is not."""
    host = urllib.parse.urlsplit(url).hostname
    if not host:
        return False
    try:
        addresses = {info[4][0] for info in socket.getaddrinfo(host, None)}
    except (OSError, UnicodeError):
        return False
    for address in addresses:
        ip = ipaddress.ip_address(address.split("%")[0])
        if not (ip.is_loopback or ip.is_private or ip.is_link_local):
            return False
    return bool(addresses)

def server_allowed():
    """Whether the run may use the model server it is set to. The Clean Room edition (a private edition) only talks
    to a model server on this PC or the local network, whatever LMSTUDIO_BASE_URL says; the full edition takes any."""
    # cloud:start
    return True
    # cloud:end
    return on_this_pc_or_network(BASE_URL)

# cloud:start
def retry_after(err):
    """The wait a rate-limited service asks for (its Retry-After header, in seconds), or None."""
    headers = getattr(getattr(err, "response", None), "headers", None) or {}
    try:
        return min(float(headers.get("retry-after")), max(RATE_LIMIT_WAITS))
    except (TypeError, ValueError):
        return None
# cloud:end

def failure_advice(err):
    """What a failed model call means and what to do, for the server the run actually uses."""
    # cloud:start
    # Every failure used to say "Check that LM Studio is running at https://openrouter.ai/api/v1", which is wrong
    # for a cloud run
    if not is_local_server():
        return cloud_failure_advice(err)
    # cloud:end
    return f"Check that LM Studio is running at {BASE_URL} and model '{MODEL}' is loaded."

# cloud:start
def cloud_failure_advice(err):
    if isinstance(err, ModelRefused):
        return (f"The service's content filter stopped '{MODEL}''s reply; the same prompt gets the same answer, so the run "
                "ends here instead of paying for repeats. Try another model, or another provider for this one.")
    if isinstance(err, RateLimitError):
        return (f"'{MODEL}' was still rate-limited after {len(RATE_LIMIT_WAITS)} waits (new OpenRouter accounts get fewer "
                "requests a minute on some models). Run it again later, or add credits to raise the limit.")
    status = getattr(err, "status_code", None)
    if status in (401, 403):
        return "The service refused the key: check OPENROUTER_API_KEY."
    if status == 402:
        return "The account has run out of credits."
    if status == 404:
        return f"No provider for '{MODEL}' can take this request: check the model name on the service's model list."
    return f"The model service at {BASE_URL} failed the call for '{MODEL}'."
# cloud:end

# cloud:start
# Sent once and refused by every provider of the model: dropped for the rest of the run (see with_retries)
UNSUPPORTED_PARAMETERS = set()
# cloud:end

TIMER_AFTER_SECONDS = 3  # A call that answers sooner shows no timer (no flicker for quick replies)
TIMER_TICK_SECONDS = 1

async def ticking(call):
    """The model call, with a timer counting its seconds on one console line while it runs. On a CPU a call can take
    minutes, and a console showing nothing looked frozen. Only in a person's console window: a run whose output goes
    to a file (the regression suite's console.txt) gets no timer lines."""
    if not sys.stdout.isatty():
        return await call
    task = asyncio.ensure_future(call)
    started, shown = time.monotonic(), False
    try:
        while not (await asyncio.wait({task}, timeout=TIMER_TICK_SECONDS))[0]:
            waited = int(time.monotonic() - started)
            if waited >= TIMER_AFTER_SECONDS:
                print(f"\r   Waiting for the model: {waited} s", end="", flush=True)
                shown = True
    except BaseException:
        task.cancel()
        raise
    finally:
        if shown:
            print("\r" + " " * 40 + "\r", end="", flush=True)
    return task.result()

def with_retries(client):
    """The client, with each model call tried again when the server fails it (no connection, or a server error).

    A failed call used to end the run at once. LM Studio's server loads a model by itself when a request names one
    that is not loaded (it unloads idle ones too), so waiting and trying again carries a run through an unloaded
    model or a restarted app. Only failures of the server are retried: a reply that cannot be used is handled where
    it is read, as before. A model unloaded in the middle of a call does not drop the connection: LM Studio answers
    that call with "400 - Model unloaded by user or API request", so that refusal is retried too. A timeout is not
    retried: the server is up but the model has not finished, and a 3B model that kept writing for 10 minutes kept
    writing when the same request was sent again.
    """
    # cloud:start
    # Cloud services fail in other ways, and three of eleven cloud models lost tasks to them in one week:
    # - 429, rate limited: GPT-4o and Opus 5.5 hit OpenRouter's 20 requests a minute for new accounts, and each task
    #   after that ended on its first call. A 429 is waited out (the service's Retry-After, or RATE_LIMIT_WAITS).
    # - 400 "Provider returned error": Gemma 3 4B lost a task to one, on a provider that answered the next call. It
    #   is the provider failing, not the request, so it is tried again like a server error.
    # - 404 "No endpoints found that can handle the requested parameters": Opus 5.5's providers stopped taking a
    #   temperature, and with the cloud rule that every parameter be honoured, no provider was left. The parameter is
    #   dropped for the rest of the run, said once on the console, and the call sent again.
    # - An empty reply stopped by the content filter: Opus 5.5 got 18 of them, each read as invalid JSON, retried and
    #   billed. The same prompt gets the same answer, so it is not retried: ModelRefused ends the run with that reason.
    # cloud:end
    create = client.chat.completions.create

    async def create_with_retries(*args, **kwargs):
        server_waits = iter(MODEL_RETRY_WAITS)
        # cloud:start
        if EXTRA_BODY:
            kwargs["extra_body"] = {**EXTRA_BODY, **(kwargs.get("extra_body") or {})}
        rate_waits = iter(RATE_LIMIT_WAITS)
        # cloud:end
        while True:
            # cloud:start
            for name in UNSUPPORTED_PARAMETERS:
                kwargs.pop(name, None)
            # cloud:end
            try:
                response = await ticking(create(*args, **kwargs))
            # cloud:start
            except RateLimitError as err:
                wait = next(rate_waits, None)
                if wait is None:
                    raise
                wait = retry_after(err) or wait
                print(f"Rate limited by the model service ({err}). Waiting {wait:g} s before trying again.")
                await asyncio.sleep(wait)
                continue
            except NotFoundError as err:
                if "temperature" in kwargs and not is_local_server() and re.search(r"requested parameters", str(err), re.I):
                    UNSUPPORTED_PARAMETERS.add("temperature")
                    print(f"Warning: No provider of '{MODEL}' takes a temperature setting; sending calls without it from now on "
                          "(the model's own default is used).")
                    continue
                raise
            # cloud:end
            except (APIConnectionError, InternalServerError, BadRequestError) as err:
                retried = isinstance(err, BadRequestError) and re.search(r"\b(unloaded|not loaded)\b", str(err), re.I)
                # cloud:start
                retried = retried or (isinstance(err, BadRequestError) and re.search(r"provider returned error", str(err), re.I))
                # cloud:end
                wait = next(server_waits, None)
                if wait is None or isinstance(err, APITimeoutError) or (isinstance(err, BadRequestError) and not retried):
                    raise
                hint = "LM Studio loads the model again by itself if it was unloaded."
                # cloud:start
                if not is_local_server():
                    hint = "The provider may answer the next call."
                # cloud:end
                print(f"Warning: The model server failed the call ({type(err).__name__}: {err}). Trying again in {wait} s; {hint}")
                await asyncio.sleep(wait)
                continue
            # cloud:start
            choice = (getattr(response, "choices", None) or [None])[0]
            if getattr(choice, "finish_reason", None) == "content_filter" and not extract_message_content(response):
                raise ModelRefused(f"the content filter of {getattr(response, 'provider', None) or 'the provider'} "
                                   f"stopped the reply (empty, finish reason content_filter)")
            # cloud:end
            return response

    client.chat.completions.create = create_with_retries
    return client

async def main():
    if not server_allowed():
        print(f"Error: The model server {BASE_URL} is not on this PC or your local network, and this edition only uses one "
              "that is. Set LMSTUDIO_BASE_URL to LM Studio's address (default http://localhost:1234/v1).")
        return
    # The client's own silent retries are off (its default retried a timed-out runaway reply twice); with_retries
    # decides what is tried again
    client = with_retries(AsyncOpenAI(
        base_url=BASE_URL,
        api_key=API_KEY,
        max_retries=0,
        timeout=MODEL_TIMEOUT_SECONDS,
        # cloud:start
        default_headers=EXTRA_HEADERS or None,
        # cloud:end
    ))

    # A person's run (not a test run): the console full size until the browser opens beside it
    windows.maximize_console()
    print("=" * 56)
    print("Initializing Autonomous Discovery Agent Setup...")
    # Text piped in from PowerShell or a file saved by Notepad can start with a byte-order mark, which is not whitespace
    objective = input("Define the Agent's High-Level Objective: ").lstrip("﻿").strip()
    if not objective:
        print("The objective cannot be empty.")
        return

    journal = []
    current_url = None
    turn_counter = 1
    max_turns = 100
    tried = {"queries": [], "urls": [], "objective": objective}
    # The first model call reads the whole prompt with nothing cached yet: on a CPU it takes a minute or two, and a
    # silent console looked frozen to a first-time user
    print("Splitting the objective into a checklist... (the first step takes a minute or two on a CPU)")
    tried["checklist"] = await split_objective(client, objective)
    print("Objective checklist:\n" + checklist_lines(tried["checklist"]))
    page_state = {"url": None, "parts": [], "index": 0}
    conversation = new_conversation()
    loop_msg = None
    consecutive_refusals = 0
    turns_without_discovery = 0
    turns_since_objective_check = 0
    stop_reason = None

    async with async_playwright() as p:
        # Side by side for a person watching: the browser on the right half and the console on the left, with the
        # page zoomed out so it is laid out about as wide as in a test run (windows.py); test runs are unchanged
        side_by_side = windows.browser_args()
        browser = await p.chromium.launch(headless=False, args=[*side_by_side, *CHROMIUM_ARGS])
        # No user_agent override: headed Chromium already reports a real, version-matched Chrome UA
        context = await browser.new_context(
            java_script_enabled=True,
            **({"no_viewport": True} if side_by_side else {"viewport": {"width": 1280, "height": 720}})
        )
        page = await context.new_page()

        try:
            while turn_counter <= max_turns:
                print(f"\n[TURN {turn_counter}/{max_turns}] Active reasoning cycle executing...")
                tried["finish_confirmed"] = False  # The finish check runs once per turn
                remember_install_lines(tried, page_state)

                try:
                    decision, refused = await run_agent_turn(client, page, objective, journal, current_url, tried, page_state, conversation, loop_warning=loop_msg)
                    thought = decision.get("thought_process", "No reasoning recorded.")
                    action = str(decision.get("action", "FINISH")).upper()
                    parameter = without_control_characters(decision.get("parameter", "")).strip()
                    loop_msg = None
                except ValueError as parse_err:
                    print(f"Warning: {parse_err} Skipping turn.")
                    journal.append({"turn": turn_counter, "action": "INVALID_OUTPUT", "error": str(parse_err)})
                    turn_counter += 1
                    continue
                except Exception as err:
                    print(f"Error: The model call failed: {err}")
                    print(failure_advice(err))
                    stop_reason = f"the model call failed ({str(err)[:300]})"
                    break

                # Replies refused and retried within this turn stay in the journal so the model sees what failed;
                # "check" names the check that refused them, for counting in the regression runs (not shown to the model)
                for r in refused:
                    journal.append({"turn": turn_counter, "action": "REFUSED_IN_TURN", "requested": f"{r['action']} {r['parameter'][:120]}", "reason": r["reason"],
                                    "check": r["journal_action"]})

                print(f"Agent Thoughts: {thought}")
                print(f"Chosen Action: [{action}] -> Parameter: {parameter}")

                refusal = check_decision(action, parameter, journal, tried, page_state, current_url, thought)
                if not refusal and action == "FINISH":
                    refusal = await coverage_refusal(client, objective, tried, journal)
                if refusal:
                    # Only reached once this turn's cheap retries are used up
                    print(f"Action refused: {refusal[1]}")
                    journal.append({"turn": turn_counter, "thought": thought, "action": refusal[0], "requested": parameter, "reason": refusal[1]})
                    loop_msg = f"Your {action} was refused. {refusal[1]}"
                    set_note(conversation, f"RESULT: Nothing changed. {where_you_are(page_state, current_url)}", current_url)
                    if refusal[0] == "BLOCKED_COVERAGE":
                        # The coverage prompts pushed the conversation out of LM Studio's cache, and re-reading it took
                        # 626-651 s; a fresh prompt is shorter and starts with the system prompt they left cached
                        conversation["messages"] = None

                elif action == "FINISH":
                    print("\nAgent has signaled mission complete. Writing the final result...")
                    # "writing" is cleared only once the final result is written: stopped during the checks before it
                    # (a 14B model ran into the test's time limit there), the report said "finished" over the model's
                    # unchecked FINISH text
                    finish_entry = {"turn": turn_counter, "thought": thought, "action": "FINISH", "finding": parameter,
                                    "writing": True}
                    remember_install_lines(tried, page_state)
                    added = record_install_line(tried, journal, turn_counter)
                    if added:
                        print(f"Recorded by the code: the install command the page gives ({added['source']}): "
                              f"{added['extracted_data'][:120]}")
                    journal.append(finish_entry)
                    try:
                        missing, unproven = await not_found_items(client, objective, tried, journal)
                        no_amounts = amounts_not_found(tried.get("checklist"), journal, skip=missing)
                        final = await compose_final_result(client, objective, journal, tried.get("checklist"), missing,
                                                           unproven, no_amounts, tried.get("evidence"),
                                                           tried.get("short_counts"))
                        if final:
                            finish_entry.update(draft=parameter, finding=final)
                    except Exception as err:
                        print(f"Warning: Could not write the final result separately ({err}); keeping the agent's FINISH text.")
                    finish_entry.pop("writing", None)
                    break

                elif action in ["SEARCH", "NAVIGATE"]:
                    target_url = determine_url(action, parameter)
                    print(f"Driving Playwright to cross-domain target: {target_url}")
                    try:
                        response = await load_page(page, target_url)
                        current_url = page.url
                        status = getattr(response, "status", None) or 0

                        if action == "SEARCH":
                            # The search engine sometimes returns an error page; reload once before giving up
                            if await count_search_results(page) == 0:
                                print("Search returned no results. Retrying once...")
                                await asyncio.sleep(3)
                                await load_page(page)
                                current_url = page.url
                            if await count_search_results(page) == 0:
                                print("Error: Search still returned no results.")
                                journal.append({"turn": turn_counter, "thought": thought, "action": "FAILED_SEARCH", "requested": parameter,
                                                "error": "No results (search engine error or no matches). This query may be tried again."})
                            else:
                                tried["queries"].append(search_query(parameter))
                                journal.append({"turn": turn_counter, "thought": thought, "action": action, "requested": parameter, "destination": current_url})
                        else:
                            tried["urls"].append(target_url)
                            if normalize_url(current_url) != normalize_url(target_url):
                                tried["urls"].append(current_url)  # Also remember where redirects landed
                            refused = await site_refusal(page, status)
                            if refused:
                                # The site turned the browser away (403, 429 or a "verify you are human" page): every
                                # later visit to it is refused in code and it leaves the search results. Gemma 3 4B
                                # went back to alternativeto.net six times after its first 403, each a lost turn
                                mark_refusing(current_url, refused)
                                mark_refusing(target_url, refused)
                                print(f"{site_of(target_url)} refused the visit ({refused}); it is blocked for the rest of this run.")
                                journal.append({"turn": turn_counter, "thought": thought, "action": "FAILED_NAVIGATE", "requested": parameter,
                                                "error": f"The site refused the visit ({refused}). It will refuse again, so "
                                                         f"{site_of(target_url)} is blocked for the rest of this run: use another site."})
                            elif status >= 400:
                                # Often a guessed URL; reported as a failure so the model does not treat the
                                # error page as content (and is not asked to record something from it)
                                print(f"Error: The page returned HTTP {status}.")
                                journal.append({"turn": turn_counter, "thought": thought, "action": "FAILED_NAVIGATE", "requested": parameter,
                                                "error": f"The page returned HTTP {status} (for example 404: not found). "
                                                         "Use a link shown on a page or in search results instead of guessing URLs."})
                            else:
                                journal.append({"turn": turn_counter, "thought": thought, "action": action, "requested": parameter, "destination": current_url})
                    except Exception as b_err:
                        print(f"Error: Failed to reach target destination: {b_err}")
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
                    print(f"Showing part {target + 1} of {len(page_state['parts'])} of the current page.")
                    journal.append({"turn": turn_counter, "thought": thought, "action": "READ_MORE", "part": f"{target + 1} of {len(page_state['parts'])}"})
                    set_note(conversation, f"RESULT: READ_MORE done. {where_you_are(page_state, current_url)} "
                                           f"The CURRENT ENVIRONMENT now shows:\n{render_part(page_state)}\n"
                                           f"The links listed earlier for this page still apply.", current_url)

                elif action == "DISCOVER":
                    print(f"Logging critical discovery element: {parameter}")
                    number = sum(1 for e in journal if e.get("action") == "DISCOVER") + 1
                    linked = link_discovery(tried["checklist"], decision.get("checklist_items"), number)
                    # Recorded with its section's heading when the copied text does not name it (the heading is page
                    # text too): every later check and the final result then see what the lines are about
                    heading = section_heading(page_state, parameter)
                    inner = with_inner_headings(page_state, parameter)
                    recorded = f'(Under the heading "{heading}") {inner}' if heading else inner
                    journal.append({"turn": turn_counter, "thought": thought, "action": "DISCOVER", "source": current_url,
                                    "extracted_data": recorded, "checklist_items": linked})
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
                        print(f"{consecutive_refusals} refused actions in a row. Escalating loop warning.")
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
                    print(f"No new discoveries in {turns_without_discovery} turns. Adding progress check.")
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
                        print(f"Objective check after {turn_counter} turns ({count} discoveries).")
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
                print("\nCycle pacing check complete. Refreshing navigation loop pipeline...")
                await asyncio.sleep(TURN_PAUSE_SECONDS)
        finally:
            # Always saved, however the run ends: FINISH, the turn limit, Ctrl+C or an outside time limit
            write_research_log(objective, journal, tried.get("checklist"))
            write_report(objective, journal, tried.get("checklist"), stop_reason)

        print(f"\nShutting down browser instance. Report: {REPORT_FILE}; full journal: research_log.txt.")
        await browser.close()

def run():
    """Command-line entry point (installed as `localagentbrowser`)."""
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nStopped. Findings so far are in research_log.txt.")
