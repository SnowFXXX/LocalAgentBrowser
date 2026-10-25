"""The objective as a fixed checklist: splitting it once, linking discoveries to items, and the advice drawn from what
each item has."""
import json
from .config import MAX_CHECKLIST_ITEMS, MAX_CHECKLIST_ITEM_CHARS, MODEL, SPLIT_MAX_TOKENS
from .prompts import CHECKLIST_FORMAT, CHECKLIST_SPLIT, CHECK_PARTS, SYSTEM_PROMPT
from .text import OPINION_REQUEST, asked_count, extract_message_content, shorten

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
