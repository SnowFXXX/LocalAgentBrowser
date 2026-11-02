"""The coverage check before finishing: each checklist item against the discoveries, verified in code (unanswered
items, counts, proof of conditions), and what is reported as not found or not proven."""
import json
import re
from .config import COVERAGE_MAX_TOKENS, MAX_CONDITION_WORDS, MAX_MISSING_CLAIM_WORDS, MAX_THING_NAME_WORDS, MODEL
from .prompts import CITED_FORMAT, CITED_QUESTION, COVERAGE_FORMAT, COVERAGE_ITEM_CHECK, COVERAGE_QUOTE, SYSTEM_PROMPT
from .text import MISSING_STOPWORDS, OPINION_REQUEST, asked_count, extract_message_content, plain_words, word_pairs
from .pages import ITEM_VERBS

UNANSWERED_GAP = "nothing in DISCOVERIES SO FAR answers it"
# Items asking for amounts, and what an amount looks like: a cold brew result listed "coffee beans, water, a strainer"
# for "what I need with the quantities" without saying no quantities were found, and the check's own "missing
# quantities" verdicts were too often wrong to report (they named things the discoveries had)
AMOUNT_ASKED = re.compile(r"\b(?:quantit(?:y|ies)|amounts?|how (?:much|many|long)|measurements?|ratio|height|in "
                          r"(?:meters|metres|feet|grams|cups))\b", re.I)
AMOUNT_GIVEN = re.compile(r"\d|\b(?:one|two|three|four|five|six|seven|eight|nine|ten|twelve|half|quarter|dozen|"
                          r"minutes?|hours?|days?|weeks?|months?|years?|overnight)\b", re.I)

def amounts_not_found(checklist, journal, skip=()):
    """The checklist items that ask for amounts while no discovery linked to them gives one, decided in code."""
    found = [e for e in journal if e.get("action") == "DISCOVER"]
    return [n for n, item in enumerate(checklist or [], 1) if n not in skip and item["discoveries"]
            and AMOUNT_ASKED.search(item["text"]) and not any(
                AMOUNT_GIVEN.search(found[d - 1]["extracted_data"]) for d in item["discoveries"] if 0 < d <= len(found))]

COUNT_GENERIC_WORDS = {"culture", "cultures", "religion", "religions", "people", "peoples", "tradition", "traditions",
                       "office", "suite", "docs", "the", "and", "ancient", "native"}

def named_in(thing, found):
    """Whether a discovery names the thing, by the first word of its name that is not a generic one ("Chinese" of
    "Chinese culture", "WPS" of "WPS Office")."""
    words = [w for w in plain_words(thing["name"]) if len(w) > 2 and w not in COUNT_GENERIC_WORDS]
    return bool(words) and any(re.search(rf"(?<![a-z]){re.escape(words[0])}(?![a-z])", e["extracted_data"].lower())
                               for e in found)

def quoted_from(quote, text):
    """Whether a quote (in other spacing or punctuation) is copied from the text: 80% of its word pairs."""
    pairs = word_pairs(" ".join(plain_words(quote)))
    return bool(pairs) and len(pairs & word_pairs(" ".join(plain_words(text)))) >= 0.8 * len(pairs)

async def cited_answer(client, item, cited):
    """Words from the cited discoveries that say what the item asks for, by a yes-or-no question about them alone;
    '' when the answer is no, the reply is not valid, or its words are not in what was cited."""
    if not cited:
        return ""
    request = f"### ITEM\n{item}\n\n### DISCOVERY\n" + "\n\n".join(cited) + f"\n\n{CITED_QUESTION}"
    try:
        response = await client.chat.completions.create(
            model=MODEL, temperature=0.2, max_tokens=COVERAGE_MAX_TOKENS, response_format=CITED_FORMAT,
            messages=[{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": request}])
        reply = json.loads(extract_message_content(response))
    except Exception:
        return ""
    quote = str(reply.get("quote", ""))
    said = str(reply.get("answer", "")).strip().lower() == "yes"
    return quote if said and re.search(r"\w", quote) and any(quoted_from(quote, c) for c in cited) else ""

def item_words(text, checklist=()):
    """An item's own words: content words (s-stripped) that no other checklist item has."""
    def words(t):
        return {w.rstrip("s") for w in plain_words(t) if len(w) > 2} - MISSING_STOPWORDS - ITEM_VERBS
    others = set().union(*(words(i["text"]) for i in checklist if i["text"] != text))
    return words(text) - others

def is_bare_citation(evidence):
    """Whether evidence gives discovery numbers ("[1]", "[1], [3]") and no words."""
    evidence = str(evidence)
    return bool(re.search(r"\d", evidence)) and not re.sub(r"\[\d+\]|[\s,;.]|\band\b", "", evidence)

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

async def coverage_gaps(client, objective, checklist, journal, only=None, evidence=None):
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
            # Only discovery numbers ("[1]") as evidence is not taken on its word either: a 7B model gave "[1]" for
            # "handling of many simultaneous users", [1] being a page's opening paragraph that never mentions it, and
            # the final result filled the item from the model's own knowledge, cited to [1]. Asked to copy the words,
            # it must give words that are in the discoveries, or the item counts as unanswered.
            if is_bare_citation(verdict.get("evidence", "")):
                bare = str(verdict["evidence"])
                messages += [{"role": "assistant", "content": extract_message_content(response)},
                             {"role": "user", "content": COVERAGE_QUOTE.format(n=n)}]
                response = await client.chat.completions.create(
                    model=MODEL, temperature=0.2, max_tokens=COVERAGE_MAX_TOKENS, response_format=COVERAGE_FORMAT,
                    messages=messages)
                cited = [found[int(c) - 1]["extracted_data"] for c in re.findall(r"\[(\d+)\]", bare)
                         if 0 < int(c) <= len(found)]
                verdict = json.loads(extract_message_content(response))
                # Each cited part is checked on its own: quoting three discoveries at once, an owl task's evidence
                # matched no single one of them and the item was wrongly sent back as unanswered
                quotes = [q for q in re.split(r"\[\d+\]", str(verdict.get("evidence", ""))) if re.search(r"\w", q)]
                if quotes:
                    if not all(any(quoted_from(q, e["extracted_data"]) for e in found) for q in quotes):
                        verdict["evidence"] = ""
                # Asked again, the 7B often gave the numbers again. Matching the item's own words against what it
                # cited then sent back a correct finish (a table saying "POSIX ... source <venv>/bin/activate" for
                # "activate it on macOS or Linux"), so the cited text is shown with the item and the model is asked
                # only whether it says it; a yes counts with words copied from it. A no still counts when a word of the
                # item's own (one no other item has) is in what it cited: asked about "Detail the light-dependent
                # reactions and their location", the 7B said no 3 times out of 3 for "the light-dependent reactions,
                # which take place in the thylakoids", while the opening paragraph cited for "many simultaneous
                # users" has none of "handling", "many", "simultaneous" and stays unanswered
                else:
                    shared = item_words(item["text"], checklist) & item_words(" ".join(cited))
                    verdict["evidence"] = await cited_answer(client, item["text"], cited) or (bare if shared else "")
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
        # Words the check copied for an answered item, kept where they really are in the discoveries: the final
        # result is shown them, as it said "the discoveries do not specify what goes into photosynthesis" over a
        # discovery that gave water, sunlight and carbon dioxide
        if evidence is not None and n not in unanswered:
            quotes = [q.strip() for q in re.split(r"\[\d+\]", str(verdict.get("evidence", ""))) if re.search(r"\w", q)]
            kept = [q for q in quotes if any(quoted_from(q, e["extracted_data"]) for e in found)]
            if kept:
                evidence[n] = " ... ".join(kept)
        # A number the item asks for is counted, not judged: a list run recorded only LibreOffice, twice, and the
        # check found nothing missing from "three free, open-source alternatives"
        wanted = asked_count(item["text"])
        # Only things a discovery names are counted: the check also listed things no discovery mentioned (Llama gave
        # "cultures" as one, a 7B "OpenOffice" with a proof that named another suite), which made a short count look
        # complete; of 86 things listed in the suite runs these 3 were the only ones not named
        names = list(dict.fromkeys(str(t["name"]).strip() for t in things if named_in(t, found)))
        # Also without a condition: an owl task finished from one discovery naming two tribes for "at least three
        # cultures", with the two named as things and nothing missing, because the count ran only with conditions
        if wanted and things and len(names) < wanted:
            gaps.append(f"Item {n} ({item['text']}): only {len(names)} of {wanted} found ({', '.join(names) or 'none'})")
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
        gaps, tried["unanswered"], unproven = await coverage_gaps(client, objective, checklist, journal,
                                                                    evidence=tried.setdefault("evidence", {}))
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
    _, unanswered, unproven = await coverage_gaps(client, objective, checklist, journal, only=set(rechecked),
                                                  evidence=tried.setdefault("evidence", {}))
    return [n for n in unanswered if n in candidates or n in rechecked], unproven
