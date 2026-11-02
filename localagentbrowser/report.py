"""The journal shown to the model, the research log, the final result written in its own call (with the code's
caveats), and the report file."""
import json
import re
from .config import COMPOSE_MAX_TOKENS, JOURNAL_BATCH, MAX_EVIDENCE_CHARS, MAX_JOURNAL_DETAIL_CHARS, MAX_JOURNAL_REASON_CHARS, MAX_THOUGHT_CHARS, MODEL, REPORT_FILE, VIEW_MAX_TOKENS
from .prompts import COMPOSE_SYSTEM, NO_RESULT, RESULT_FORMAT, RESULT_REPLY, RESULT_RETRY
from .text import OPINION_REQUEST, extract_message_content, shorten, word_pairs
from .pages import normalize_url
from .grounding import code_text, invented_code
from .checklist import checklist_lines

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

def visited_summary(urls):
    counts = {}
    for u in urls:
        counts[normalize_url(u)] = counts.get(normalize_url(u), 0) + 1
    if not counts:
        return "[None]"
    return json.dumps([u if n == 1 else f"{u} (visited {n} times)" for u, n in counts.items()])

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

def unverified_code(text, discoveries):
    """Backticked commands or code in the final result that none of the discoveries contain."""
    return invented_code(str(text), [e["extracted_data"] for e in discoveries])

def without_invented_code(text, discoveries):
    """The final result with each sentence giving a command no discovery contains cut, and those commands named.

    The 14B's venv result told Windows users to run "<venv>/Scripts/activate", in none of its discoveries, and a 7B
    result ended in a code block holding "pip install package_name", which no discovery gave either: a command is
    exact text, so one written from memory is either right by luck or wrong, and a reader would run it.
    """
    invented = unverified_code(text, discoveries)
    if not invented:
        return text
    found = [e["extracted_data"] for e in discoveries]
    known = code_text(" ".join(found))
    kept, fenced = [], False
    for line in str(text).split("\n"):
        if line.strip().startswith("```"):
            fenced = not fenced
            kept.append(line)
        elif fenced:
            # A line of a code block is code whatever it looks like
            kept.append(line if not line.strip() or code_text(line.strip()) in known else "")
        else:
            kept.append(" ".join(s for s in re.split(r"(?<=[.!?])\s+", line) if not invented_code(s, found)))
    note = ("Left out, because no discovery gives them (the pages read may still have them): "
            + ", ".join(f"`{c}`" for c in invented) + ".")
    text = re.sub(r"```[^\n]*\n\s*```\n?", "", "\n".join(kept))  # A code block left empty
    return re.sub(r"\n{3,}", "\n\n", text).strip() + "\n\n" + note

def write_report(objective, journal, checklist=None):
    """A readable report with the final result and every discovery, so nothing depends on the model's
    summary being complete. Written however the run ends."""
    finish = next((e for e in reversed(journal) if e.get("action") == "FINISH"), None)
    found = [e for e in journal if e.get("action") == "DISCOVER"]
    last_turn = journal[-1].get("turn", 0) if journal else 0
    lines = ["# Research report", "", f"**Objective:** {objective}", ""]
    if finish and finish.get("writing"):
        lines += [f"**Status:** stopped while the final result was being checked and written (turn {finish.get('turn')})",
                  "", "## Final result", "", "_(Not written. The agent's unchecked draft, which the checks before the "
                  "final result did not finish with:)_", "", str(finish.get("finding") or "")]
    elif finish:
        final = finish.get("finding") or "_(The agent finished without writing a final result.)_"
        lines += [f"**Status:** finished by the agent on turn {finish.get('turn')}", "", "## Final result", "", final]
        unchecked = unverified_numbers(final, found)
        if unchecked:
            lines += ["", f"> ⚠️ Numbers not found in the recorded discoveries: {', '.join(unchecked)}. "
                          "Check them before relying on this result."]
        code = unverified_code(final, found)
        if code:
            lines += ["", f"> ⚠️ Commands not found in the recorded discoveries: {', '.join(f'`{c}`' for c in code)}. "
                          "Check them before running them."]
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

async def compose_final_result(client, objective, journal, checklist=None, not_found=(), unproven=(), no_amounts=(),
                               evidence=None):
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
    # Where the coverage check found an item answered, with words checked against the discoveries: a result said "the
    # discoveries do not specify what goes into photosynthesis" over a discovery naming water, sunlight and CO2
    answered = [(checklist[n - 1]["text"], quote) for n, quote in sorted((evidence or {}).items())
                if checklist and 0 < n <= len(checklist) and n not in not_found]
    if answered:
        parts += ("### WHERE THE DISCOVERIES ANSWER THESE (words checked against the discoveries: use them, and do not "
                  "say these were not found)\n" + "\n".join(f"- {text}: \"{shorten(quote, MAX_EVIDENCE_CHARS)}\""
                                                            for text, quote in answered) + "\n\n")
    amountless = [checklist[n - 1]["text"] for n in no_amounts if checklist and 0 < n <= len(checklist)]
    if amountless:
        parts += ("### NO AMOUNTS FOUND (the discoveries for these give no amounts, numbers or times: say so, and do "
                  "not supply any)\n" + "\n".join(f"- {text}" for text in amountless) + "\n\n")
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
                  "7. " if parts else "6. ") + RESULT_REPLY)
    # A fresh prompt with only the discoveries, not the browsing conversation: continuing that conversation let
    # the model use page text it had read but not recorded, and cite discovery numbers that did not say it
    messages = [{"role": "system", "content": COMPOSE_SYSTEM}, {"role": "user", "content": request}]
    response = await client.chat.completions.create(model=MODEL, messages=messages, temperature=0.3,
                                                    max_tokens=COMPOSE_MAX_TOKENS, response_format=RESULT_FORMAT)
    text = result_text(extract_message_content(response))
    # Asked once more when the reply is not a result: a 3B model "finished" a how-to with its own prompt repeated
    # back (the objective, the checklist and the discoveries), and a result still in JSON is not one either
    if not usable_result(text):
        retry = messages + [{"role": "assistant", "content": extract_message_content(response)},
                            {"role": "user", "content": RESULT_RETRY}]
        response = await client.chat.completions.create(model=MODEL, messages=retry, temperature=0.3,
                                                        max_tokens=COMPOSE_MAX_TOKENS, response_format=RESULT_FORMAT)
        text = result_text(extract_message_content(response))
        if not usable_result(text):
            text = NO_RESULT
    # Asked to "give your own opinion" about owls, the result listed the traditions and ended without one,
    # although rule 6 asks for a 'My view:' paragraph whenever the objective wants a view. A short second call
    # writes only that paragraph, from the result just written
    if OPINION_REQUEST.search(objective) and "my view:" not in text.lower() and text != NO_RESULT:
        view = await client.chat.completions.create(
            model=MODEL, temperature=0.3, max_tokens=VIEW_MAX_TOKENS, response_format=RESULT_FORMAT, messages=[
                {"role": "system", "content": COMPOSE_SYSTEM},
                {"role": "user", "content": f"### OBJECTIVE\n{objective}\n\n### RESULT\n{text}\n\nThe OBJECTIVE asks for "
                                            "your own view, opinion or recommendation, and the RESULT does not give it "
                                            "yet. Write only that, as one short paragraph that starts with 'My view:', "
                                            "based on the RESULT. " + RESULT_REPLY}])
        paragraph = result_text(extract_message_content(view))
        # Asked for its view after a result that already recommended SQLite ("I recommend using SQLite"), Llama
        # wrote the whole result again under "My view:"; a paragraph mostly repeating the result is left out
        mine = word_pairs(paragraph.lower()) if paragraph else set()
        repeated = bool(mine) and len(mine & word_pairs(text.lower())) >= 0.5 * len(mine)
        if paragraph and usable_result(paragraph) and not repeated:
            text += "\n\n" + (paragraph if paragraph.lower().startswith("my view:") else f"My view: {paragraph}")
    # Reported by the code as well, whatever the result says
    unfound_lines = [f"- {t}" for t in unfound] + [f"- {t}: the amounts it asks for" for t in amountless]
    if unfound_lines:
        text += "\n\nNot found in the pages read:\n" + "\n".join(unfound_lines)
    return with_caveats(without_invented_code(without_filled_gaps(text), found), unproven)

# "X is not specified ..., but <a claim>": every such sentence in the suite's finished results filled the gap from the
# model's own knowledge ("not explicitly stated but generally, it can be stored for several weeks", while the page
# said three days; "not specified in the text, but a common recommendation is 1 part coffee to 4 parts water")
FILLED_GAP = re.compile(r"((?:not (?:\w+ )?(?:been )?(?:specified|mentioned|stated|provided|given|found)|does not (?:\w+ )?"
                        r"(?:specify|say|mention|state))[^.\n]{0,100}?),?\s+but\b[^\n]*?(?:\.(?=\s+[A-Z]|\s*$)|(?=\n)|$)",
                        re.I)  # The sentence ends at a period before a capital or the end ("e.g., 1 cup" stays inside it)

def without_filled_gaps(text):
    """The final result with what follows "not stated ..., but" cut: the gap stays reported, the filling goes."""
    return FILLED_GAP.sub(lambda m: m.group(1).rstrip(" ,") + ".", text)

def result_text(raw):
    """The text of a final-result reply: the "result" field, read leniently (a server may ignore the format, and a
    reply cut off at the token limit is not valid JSON)."""
    raw = str(raw or "").strip()
    if not raw.startswith("{"):
        return raw
    for candidate in (raw, raw + '"}', raw + '}'):
        try:
            reply = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(reply, dict):
            # Also the browsing format, a habit from every earlier turn
            found = reply.get("result") or reply.get("parameter") or reply.get("thought_process")
            if isinstance(found, str) and found.strip():
                return found.strip()
        return raw
    return raw

def usable_result(text):
    """False for a reply that is not a final result: still JSON (a made-up tool call such as
    {"name": "get_mountain_info", ...}), or the prompt's own sections repeated back."""
    return bool(text) and not text.lstrip().startswith("{") and not re.search(
        r"^#+\s*(OBJECTIVE|DISCOVERIES|CHECKLIST)\b", text, re.M)

GENERIC_NAME_WORDS = {"office", "docs", "suite", "free", "open", "source", "open-source", "edition", "community",
                      "online", "desktop", "apache", "microsoft"}

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
        # The check named "ONLYOFFICE Docs" and the result "ONLYOFFICE", which then went unmarked: failing the full
        # name, its distinctive word is looked for (not a generic one such as "Office" or "Docs")
        for word in [w for w in name.split() if len(w) > 3 and w.lower() not in GENERIC_NAME_WORDS] if not match else []:
            match = re.search(r"\b" + re.escape(word) + r"\b(\*\*)?", text, re.I)
            if match:
                break
        if not match:
            unnamed.append(f"- {name}: {note[1:-1]}")
        # Only a note right after this name counts as already there: checked against the whole text, the note added
        # to OnlyOffice left WPS Office, with the same condition, unmarked in a list run's result
        elif not text[match.end():].lstrip().lower().startswith(note.lower()):
            text = text[:match.end()] + f" {note}" + text[match.end():]
    if unnamed:
        text += "\n\nNot shown in the pages read to meet the conditions:\n" + "\n".join(unnamed)
    return text
