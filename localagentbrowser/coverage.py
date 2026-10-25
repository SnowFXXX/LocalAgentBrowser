"""The coverage check before finishing: each checklist item against the discoveries, verified in code (unanswered
items, counts, proof of conditions), and what is reported as not found or not proven."""
import json
import re
from .config import COVERAGE_MAX_TOKENS, MAX_CONDITION_WORDS, MAX_MISSING_CLAIM_WORDS, MAX_THING_NAME_WORDS, MODEL
from .prompts import COVERAGE_FORMAT, COVERAGE_ITEM_CHECK, SYSTEM_PROMPT
from .text import MISSING_STOPWORDS, OPINION_REQUEST, asked_count, extract_message_content, plain_words, word_pairs

UNANSWERED_GAP = "nothing in DISCOVERIES SO FAR answers it"

def quoted_from(quote, text):
    """Whether a quote (in other spacing or punctuation) is copied from the text: 80% of its word pairs."""
    pairs = word_pairs(" ".join(plain_words(quote)))
    return bool(pairs) and len(pairs & word_pairs(" ".join(plain_words(text)))) >= 0.8 * len(pairs)

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
