"""The journal shown to the model, the research log, the final result written in its own call (with the code's
caveats), and the report file."""
import json
import re
from .config import COMPOSE_MAX_TOKENS, JOURNAL_BATCH, MAX_JOURNAL_DETAIL_CHARS, MAX_JOURNAL_REASON_CHARS, MAX_THOUGHT_CHARS, MODEL, REPORT_FILE, VIEW_MAX_TOKENS
from .prompts import COMPOSE_SYSTEM
from .text import OPINION_REQUEST, extract_message_content, shorten
from .pages import normalize_url
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
