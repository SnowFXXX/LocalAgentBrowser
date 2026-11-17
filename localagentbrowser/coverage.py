"""The coverage check before finishing: each checklist item against the discoveries, verified in code (unanswered
items, counts, proof of conditions), and what is reported as not found or not proven."""
import json
import re
from urllib.parse import urlparse
from .config import (COVERAGE_MAX_TOKENS, COVERAGE_RETRY_MAX_TOKENS, MAX_CONDITION_WORDS, MAX_MISSING_CLAIM_WORDS,
                     MAX_THING_NAME_WORDS, MODEL)
from .prompts import (CITED_FORMAT, CITED_QUESTION, COVERAGE_FORMAT, COVERAGE_ITEM_CHECK, COVERAGE_QUOTE, COVERAGE_SHORTER,
                      SYSTEM_PROMPT)
from .text import MISSING_STOPWORDS, OPINION_REQUEST, asked_count, extract_message_content, plain_words, word_pairs
from .pages import ITEM_VERBS
from .dates import SPAN_QUESTION, dates_in

UNANSWERED_GAP = "nothing in DISCOVERIES SO FAR answers it"
UNCHECKED_NOTE = "not checked (not counted as covered)"
COVERAGE_RETRY_LINE = "↻ A coverage reply was not complete JSON (cut off or malformed); asking once more, with a higher limit."
# Items asking for amounts, and what an amount looks like: a cold brew result listed "coffee beans, water, a strainer"
# for "what I need with the quantities" without saying no quantities were found, and the check's own "missing
# quantities" verdicts were too often wrong to report (they named things the discoveries had)
AMOUNT_ASKED = re.compile(r"\b(?:quantit(?:y|ies)|amounts?|how (?:much|many|long)|measurements?|ratio|height|in "
                          r"(?:meters|metres|feet|grams|cups))\b", re.I)
AMOUNT_GIVEN = re.compile(r"\d|\b(?:one|two|three|four|five|six|seven|eight|nine|ten|twelve|half|quarter|dozen|"
                          r"minutes?|hours?|days?|weeks?|months?|years?|overnight)\b", re.I)

AMOUNT_GAP = ("no discovery recorded for it gives the amounts it asks for; DISCOVER the words that give them, as the "
              "page writes them")

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

def names_copied(quote, text, item):
    """Whether a quote of short lines (a name per line: 'GIMP', 'carbohydrates') is copied from the text: each line
    of one or two words is in the text, and at least one of them is not a word of the ITEM itself."""
    lines = [plain_words(line) for line in str(quote).splitlines()]
    lines = [words for words in lines if words]
    if not lines or any(len(words) > 2 for words in lines):
        return False
    known, asked = set(plain_words(text)), set(plain_words(item))
    return all(set(words) <= known for words in lines) and any(set(words) - asked for words in lines)

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
    return quote if said and re.search(r"\w", quote) and any(
        quoted_from(quote, c) or names_copied(quote, c, item) for c in cited) else ""

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

CONFIRMED_MISSING = ": named by no discovery; DISCOVER it where a page gives it"

def concrete_term(part):
    """Whether a missing part is a concrete thing a word check can confirm: at most three words, one of them with a
    digit, an all-capitals abbreviation, or a capital inside the phrase ("O2", "GA3P", "PowerShell activation
    command"), not a description ("the specific steps of the Calvin cycle")."""
    words = part.replace(",", " ").split()
    return 0 < len(words) <= 3 and any(
        re.search(r"\d", w) or re.fullmatch(r"[A-Z][A-Z0-9]+", w) or (i > 0 and re.search(r"[A-Z]", w))
        for i, w in enumerate(words))

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
    """Whether the text contains every word of a condition ("open-source" matches "open source").

    "Free" in "free, open-source" is met by "open source": an open-source licence lets anyone have the program at no
    cost, and lists of "open-source alternatives" do not repeat the word. Phi-4 listed GIMP, Krita and Darktable
    from such a list, each with the caution "not shown in the pages read to be free and open-source"."""
    words = plain_words(text)
    pairs = set(zip(words, words[1:], strict=False))
    return all(w in words or (w == "free" and ("open", "source") in pairs)
               for w in plain_words(condition) if len(w) > 2)

def page_states(source, condition):
    """Whether the address of the page a thing was copied from states the condition for everything the page lists:
    "itsfoss.com/open-source-photoshop-alternatives/" for each program on it. The page's claim, like its text."""
    try:
        address = urlparse(source or "")
    except ValueError:
        return False
    # A list filtered by licence says it too: alternativeto.net/software/adobe-photoshop/?license=opensource
    claim = f"{address.path} {address.query}".replace("opensource", "open source")
    return bool(address.path.strip("/") or address.query) and states(claim, condition)

def site(url):
    try:
        name = urlparse(url or "").hostname or ""
    except ValueError:
        return ""
    return name.removeprefix("www.")

# Objectives a single website cannot settle: a comparison, a view or recommendation, or "several sources"
SECOND_SOURCE_ASKED = re.compile(r"\bcompar\w*|\bversus\b|\bvs\.?\s|\b(?:several|multiple|different|various|other) "
                                 r"sources\b", re.I)
ONE_SITE_GAP = ("asks for a comparison, a view or several sources, and every discovery so far comes from one website, "
                "{site}: SEARCH for a second, independent source and DISCOVER what it says on the same points, then FINISH")

def one_site(objective, journal):
    """The one website every discovery came from, when the objective needs more than one; otherwise None.

    Phi-4 used exactly one website for each of its 14 finished suite tasks, the owl task that asks for "several
    sources" among them, and so did GPT-4o-mini and Gemini. Every line was cited, but a comparison or a view resting
    on one page is that page's view. The agent is sent back once to find a second source; a run that still finishes
    with one says so in its result."""
    if not (SECOND_SOURCE_ASKED.search(objective) or OPINION_REQUEST.search(objective)):
        return None
    sites = {site(e.get("source")) for e in journal if e.get("action") == "DISCOVER"}
    return sites.pop() or None if len(sites) == 1 else None

async def coverage_reply(client, messages):
    """The model's coverage verdict for one item, read as JSON, and the reply it came from.

    A reply cut off at the length limit is asked for again once, with a higher limit and shorter evidence: Opus 5.5
    copied long passages as evidence, its replies stopped mid-JSON at COVERAGE_MAX_TOKENS ("Unterminated string"), and
    9 checklist items in four tasks went unchecked. A second failure raises, and the item is reported as not checked."""
    response = await client.chat.completions.create(
        model=MODEL, temperature=0.2, max_tokens=COVERAGE_MAX_TOKENS, response_format=COVERAGE_FORMAT, messages=messages)
    text = extract_message_content(response)
    try:
        return response, json.loads(text)
    except ValueError:
        print(COVERAGE_RETRY_LINE)
    retry = messages + [{"role": "assistant", "content": text}, {"role": "user", "content": COVERAGE_SHORTER}]
    response = await client.chat.completions.create(
        model=MODEL, temperature=0.2, max_tokens=COVERAGE_RETRY_MAX_TOKENS, response_format=COVERAGE_FORMAT,
        messages=retry)
    return response, json.loads(extract_message_content(response))

async def coverage_gaps(client, objective, checklist, journal, only=None, evidence=None, unchecked=None):
    """What the discoveries still lack for each checklist item, judged item by item just before finishing.

    The finish check takes the model's word that an item is covered: on an explanation task it finished from one
    overview paragraph it said covered all four items, without oxygen or water, and on a list it counted WPS
    Office as "free, open-source". Asked about all items at once, the model skipped items and judged both ways
    wrong; asked about one item at a time it named such gaps. Conditions are not taken on its word: for each
    thing it names, it must copy words from the discoveries, and the code checks they are there and state the
    condition. A condition counts only if the item itself states it (the model copied one item's conditions to
    the next). Items asking for an opinion or recommendation are not checked. A check whose reply fails twice
    (see coverage_reply) reports no gap for its item and adds it to `unchecked`: not checked, never covered.

    Each item is asked in a prompt of its own, with the item last so the long instructions before it are read once.
    Asked as a continuation of the browsing conversation instead (which kept that conversation in LM Studio's
    cache), the check was more lenient: on an explanation task's finish it found the gaps twice out of twice in
    separate prompts and never inside the conversation, which also overflowed the context near its limit.
    """
    found = [e for e in journal if e.get("action") == "DISCOVER"]
    listing = "\n\n".join(f"[{n}] {e['extracted_data']}" for n, e in enumerate(found, 1))
    items = "\n".join(f"{n}. {item['text']}" for n, item in enumerate(checklist, 1))
    gaps, unanswered, unproven, answered_with = [], [], [], {}
    for n, item in enumerate(checklist, 1):
        if OPINION_REQUEST.search(item["text"]) or (only is not None and n not in only):
            continue
        # The discoveries come before the item, so after the first item LM Studio only reads the question
        request = (f"### OBJECTIVE\n{objective}\n\n### DISCOVERIES\n{listing}\n\n### CHECKLIST\n{items}\n\n"
                   f"{COVERAGE_ITEM_CHECK}\n\nThe item to check: ITEM {n}. {item['text']}")
        messages = [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": request}]
        try:
            response, verdict = await coverage_reply(client, messages)
            # Only discovery numbers ("[1]") as evidence is not taken on its word either: a 7B model gave "[1]" for
            # "handling of many simultaneous users", [1] being a page's opening paragraph that never mentions it, and
            # the final result filled the item from the model's own knowledge, cited to [1]. Asked to copy the words,
            # it must give words that are in the discoveries, or the item counts as unanswered.
            if is_bare_citation(verdict.get("evidence", "")):
                bare = str(verdict["evidence"])
                messages += [{"role": "assistant", "content": extract_message_content(response)},
                             {"role": "user", "content": COVERAGE_QUOTE.format(n=n)}]
                response, verdict = await coverage_reply(client, messages)
                cited = [found[int(c) - 1]["extracted_data"] for c in re.findall(r"\[(\d+)\]", bare)
                         if 0 < int(c) <= len(found)]
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
            # Not checked is not covered: it sends no one back and adds no "not found" line (nothing says the item is
            # unanswered), but the report shows it, so a reader knows this part was never verified
            print(f"⚠️ Coverage check of item {n} failed ({err}); {UNCHECKED_NOTE}.")
            item["unchecked"] = True
            if unchecked is not None:
                unchecked.add(n)
            continue
        item.pop("unchecked", None)
        # Nothing copied for the item, not even a discovery number, means nothing recorded answers it: on a how-to
        # the check gave empty evidence and nothing missing for "care in the first two weeks", the agent had linked
        # that item to "the best time to repot is spring and summer", and the final result invented a care routine
        # An item asking how to install packages is answered by an install command, decided in code: Phi-4's docs run
        # had "The ExtendedEnvBuilder class installs setuptools and pip into the virtual environment" accepted for "how
        # to install packages into a virtual environment", was never sent back, and finished without `pip install`
        # Decided both ways: with "pip install -r requirements.txt" recorded, the model's verdict still called the item
        # unanswered, and the result said "not found" over the discovery that gave the command
        if INSTALL_ASKED.search(item["text"]):
            commands = [e["extracted_data"] for e in found if INSTALL_COMMAND.search(e["extracted_data"])]
            verdict["evidence"] = commands[0] if commands else ""
        if not re.search(r"\w", str(verdict.get("evidence", ""))):
            unanswered.append(n)
            gaps.append(f"Item {n} ({item['text']}): {UNANSWERED_GAP}")
        elif missing:
            # A concrete thing ("O2", "GA3P", "Step 3") the check says is missing, and no discovery names, is confirmed
            # by the code, so it sends the agent back: an explanation finished in 4 turns from one overview paragraph,
            # without oxygen, while the check said "missing O2". Vague verdicts ("the specific steps") stay soft: about
            # half were wrong. Of 117 saved "missing" verdicts, 2 were concrete (this one and "Step 3")
            concrete = [p for p in missing.split(", ") if concrete_term(p)]
            gaps.append(f"Item {n} ({item['text']}): missing {missing}"
                        + (f" ({', '.join(concrete)}{CONFIRMED_MISSING})" if concrete else ""))
        if n not in unanswered:
            answered_with[n] = re.sub(r"\[\d+\]", " ", str(verdict.get("evidence", "")))
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
            # Or a discovery about this thing alone states it, checked by the code: Phi-4's list had "GIMP: Free
            # open-source suite ..." recorded, the check's proof did not copy those words, and GIMP got the caution
            # "not shown to be free and open-source". A discovery naming another listed thing too is not used: "OnlyOffice
            # is free and open source" said of LibreOffice must not prove it
            others = [{w for w in plain_words(t["name"]) if len(w) > 2} - name for t in things]
            about = [e for e in found if name & set(plain_words(e["extracted_data"]))
                     and not any(o and o <= set(plain_words(e["extracted_data"])) for o in others)]
            # Or the page the thing was copied from says it of everything it lists, in its address
            listed_on = [e.get("source") for e in found if name & set(plain_words(e["extracted_data"]))]
            unmet = [c for c in conditions if not ((copied and states(proof, c))
                                                   or any(states(e["extracted_data"], c) for e in about)
                                                   or any(page_states(s, c) for s in listed_on))]
            if unmet:
                condition = " and ".join(unmet)
                unproven.append((str(thing["name"]).strip(), condition))
                gaps.append(f"Item {n}: {thing['name']}: no words in DISCOVERIES SO FAR show it is {condition}. SEARCH "
                            f"for '{thing['name']} {condition}' to check it, or replace it with one that is")
    for n in shared_evidence_items(answered_with, checklist):
        unanswered.append(n)
        gaps.append(shared_gap(n, checklist[n - 1]["text"]))
    return gaps, unanswered, unproven

def shared_gap(n, text):
    """The gap for an item whose only evidence was another item's words. It ends with UNANSWERED_GAP, the mark of a
    gap that sends the agent back: with the reason after it, v9 found such items on Box 1's comparison but did not
    send the agent back, and the final result filled all three items from memory."""
    return (f"Item {n} ({text}): the words given for it are the same as for another item and say nothing of its own, "
            f"so {UNANSWERED_GAP}")

# Units as pages abbreviate them after a number, by the word an item asks with (in item_words' s-stripped form)
UNIT_WORDS = {"m": "meter", "metres": "metre", "km": "kilometer", "cm": "centimeter", "mm": "millimeter",
              "ft": "feet", "mi": "mile", "in": "inche", "kg": "kilogram", "g": "gram", "lb": "pound", "lbs": "pound",
              "oz": "ounce", "l": "liter", "ml": "milliliter", "min": "minute", "h": "hour", "hr": "hour",
              "hrs": "hour", "c": "celsiu", "f": "fahrenheit"}
UNIT_AFTER_NUMBER = re.compile(r"\d\s*(" + "|".join(sorted(UNIT_WORDS, key=len, reverse=True)) + r")\b", re.I)

def units_written(text):
    """The unit words a text gives by abbreviation after a number: "5,895 m (19,341 ft)" gives meter and feet. A
    Kilimanjaro sentence answering both "in meters" and "in feet" was dropped for both, as if it said neither."""
    found = {UNIT_WORDS[m.group(1).lower()] for m in UNIT_AFTER_NUMBER.finditer(str(text))}
    return found | ({"foot"} if "feet" in found else set())

# Only words that point at the things another item finds: "his" in "the start date of his tenure" names the person
# Installing software packages, and the commands that do it as pages write them ("python -m pip install",
# "$ pip install -r requirements.txt", "npm install", "sudo apt install")
INSTALL_ASKED = re.compile(r"\binstall\w*\b.*\b(?:packages?|librar(?:y|ies)|modules?|dependenc(?:y|ies))\b|"
                           r"\b(?:packages?|librar(?:y|ies)|modules?|dependenc(?:y|ies))\b.*\binstall", re.I)
INSTALL_COMMAND = re.compile(r"\b(?:pip3?|python3?(?:\.\d+)?\s+-m\s+pip|py\s+-m\s+pip|npm|pnpm|yarn|apt(?:-get)?|brew|"
                             r"conda|choco|winget|dnf|yum|gem|cargo|go)\s+(?:install|add)\b", re.I)

REFERS_BACK = re.compile(r"\b(?:each one|each of them|each of these|of each|for each|each item)\b", re.I)
DATE_ASKED = re.compile(r"\b(?:date|dates|when|year)\b", re.I)
DATE_PART_ASKED = re.compile(r"\b(?:day|month|year)\b", re.I)

def shared_evidence_items(answered_with, checklist):
    """Items whose only evidence is the same words given for other items, none of its own words in them.

    A comparison finished from one sentence ("I'd pick SQLite for any single-server app under roughly 10,000 daily
    active users, and PostgreSQL the moment concurrent writes ... enter the picture"), given as the evidence for
    setup, many users and features alike, and pasted under all three. Of items sharing the same words, those whose
    own words appear in them keep them; the others count as unanswered. Items about the same thing ("Compare SQLite
    and PostgreSQL features", "Name three features PostgreSQL has") may share words: they share a word of their own,
    one that no more than half of the checklist items have ("compare", "sqlite" and "postgresql" in 3 of 4 do not
    count)."""
    def words_of(text):
        return {w.rstrip("s") for w in plain_words(text) if len(w) > 2} - MISSING_STOPWORDS - ITEM_VERBS
    texts = {n: words_of(item["text"]) for n, item in enumerate(checklist, 1)}
    common = {w for w in set().union(*texts.values()) if sum(w in t for t in texts.values()) > len(texts) / 2}
    dropped = set()
    for n, words in answered_with.items():
        same = [m for m, other in answered_with.items()
                if m != n and quoted_from(words, other) and quoted_from(other, words)]
        if not same or any((texts[n] & texts[m]) - common for m in same):
            continue
        # An item about the things another item finds ("Summarize the main strengths of each one" after "Find three
        # free, open-source alternatives") is answered by the same discoveries: Phi-4's list said its strengths were
        # "not found" over feature lists it had cited for each program
        if REFERS_BACK.search(checklist[n - 1]["text"]):
            continue
        # Items asking for dates may share a sentence that gives a date for each: "from 12 July 1985 until his
        # retirement on 20 December 1986" answered both the start and the end of a tenure, and both were reported
        # "not found"
        # (A length worked out from those dates may share it too: "Calculate the length of his tenure" had the same one)
        dated = [m for m in [n] + same if DATE_ASKED.search(checklist[m - 1]["text"])]
        if (n in dated or SPAN_QUESTION.search(checklist[n - 1]["text"])) and \
                len({d or y for _, d, y in dates_in(words)}) >= max(len(dated), 2 if n not in dated else 1):
            continue
        # Items asking for one part of a date each: one full date answers all of them. q10 asked "What day ...",
        # "What month ...", "What year ..." over "July 9, 2016", and the day and month were reported "not found"
        parts = [m for m in [n] + same if DATE_PART_ASKED.search(checklist[m - 1]["text"])]
        if len(parts) == len(same) + 1 and any(d for _, d, _ in dates_in(words)):
            continue
        given = item_words(words) | units_written(words)
        overlap = {m: len(item_words(checklist[m - 1]["text"], checklist) & given) for m in [n] + same}
        if overlap[n] == 0 or overlap[n] < max(overlap.values()):
            dropped.add(n)
    return sorted(dropped)

SHORT_COUNT = re.compile(r"^Item (\d+) \(.*\): only (\d+) of (\d+) found \(")

def short_counts(gaps):
    """{item number: (things found, things asked for)} from the coverage gaps."""
    return {int(m.group(1)): (int(m.group(2)), int(m.group(3))) for m in map(SHORT_COUNT.match, gaps) if m}

async def coverage_refusal(client, objective, tried, journal):
    # Once per run, so a noisy verdict costs one turn at most and a gap that cannot be filled never blocks finishing
    checklist = tried.get("checklist")
    if tried.get("coverage_checked") and not tried.get("search_exhausted") and checklist:
        return looked_nowhere_else(tried, journal)
    if tried.get("coverage_checked") or tried.get("search_exhausted") or not checklist:
        return None
    tried["coverage_checked"] = True
    tried["coverage_at"] = len(journal)
    if any(e.get("action") == "DISCOVER" for e in journal):
        print("🔎 Checking each checklist item against the discoveries before finishing...")
        gaps, tried["unanswered"], unproven = await coverage_gaps(client, objective, checklist, journal,
                                                                    evidence=tried.setdefault("evidence", {}),
                                                                    unchecked=tried.setdefault("unchecked", set()))
        tried["short_counts"] = short_counts(gaps)
        # Open again until something new is linked to it: after "nothing answers item 4", the read-on check told Phi-4
        # "every item of the OBJECTIVE CHECKLIST already has the discoveries it asks for. Choose FINISH now", because
        # the discovery the check had rejected was still linked to the item
        for n in tried["unanswered"]:
            if 0 < n <= len(checklist):
                checklist[n - 1]["unanswered"] = True
        # Items whose named things lacked proof of a condition are checked again before the final result
        tried["unproven_items"] = sorted({n for n, item in enumerate(checklist, 1) if any(
            g.startswith(f"Item {n}: ") and "no words in DISCOVERIES SO FAR show it is" in g for g in gaps)})
        # Amounts asked for and given by no discovery of the item, decided in code: a cold brew run on Box 1 had the
        # check's "missing quantities" (not sent back), its own FINISH drafts gave "1 cup (113 grams)", and the
        # final result said the amounts were not found, because nothing made it record the card that gave them
        gaps += [f"Item {n} ({checklist[n - 1]['text']}): {AMOUNT_GAP}"
                 for n in amounts_not_found(checklist, journal, skip=set(tried["unanswered"]))]
        only_site = one_site(objective, journal)
        if only_site:
            gaps.append("The objective " + ONE_SITE_GAP.format(site=only_site))
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
            or g.startswith("Nothing has been recorded") or g.endswith(AMOUNT_GAP) or g.startswith("The objective asks")
            or g.endswith(CONFIRMED_MISSING + ")")]
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
                                "result." + search_suggestions(checklist, tried.get("unanswered")))

def remember_install_lines(tried, page_state):
    """Keeps the first line giving an install command on each page read, by address, for record_install_line."""
    lines = tried.setdefault("install_lines", {})
    if not page_state.get("url") or page_state["url"] in lines:
        return
    for part in page_state.get("parts") or []:
        for sentence in re.split(r"(?<=[.!?:])\s+|\n+", part):
            if INSTALL_COMMAND.search(sentence):
                lines[page_state["url"]] = sentence.strip()
                return
    lines[page_state["url"]] = None

def record_install_line(tried, journal, turn):
    """At FINISH, records by code an install command from a page the agent read, for an install item no discovery
    answers with one; returns the entry, or None.

    The venv reference page gives "pip install -r requirements.txt" in its "How venvs work" part. Phi-4 read past it
    in five runs of seven, recorded the EnvBuilder example instead, and finished without an install command; named
    to it in the prompt, the line was still not recorded. The line is the page's own text, so the code records it
    itself, marked as recorded by the code, rather than leaving it to the model."""
    checklist = tried.get("checklist") or []
    items = [n for n, item in enumerate(checklist, 1) if INSTALL_ASKED.search(item["text"])]
    found = [e for e in journal if e.get("action") == "DISCOVER"]
    if not items or any(INSTALL_COMMAND.search(e["extracted_data"]) for e in found):
        return None
    source, line = next(((u, t) for u, t in (tried.get("install_lines") or {}).items() if t), (None, None))
    if not line:
        return None
    entry = {"turn": turn, "action": "DISCOVER", "source": source, "extracted_data": line, "checklist_items": items,
             "by_code": True}
    journal.append(entry)
    for n in items:
        checklist[n - 1]["discoveries"].append(len(found) + 1)
        checklist[n - 1].pop("unanswered", None)
    return entry

def looked_nowhere_else(tried, journal):
    """One more send-back, at most, for an agent that finished again without looking anywhere for an item nothing
    answers: no SEARCH, no other page, and no discovery linked to the item since it was sent back.

    Sent back for "how to install packages", with a query to try, Phi-4 stayed on the venv reference page in two
    runs out of two, recorded the EnvBuilder example again, and finished with the item "not found". The page it
    needed was one search away. A gap it has looked for is not pushed again: finishing then says what was not found."""
    if tried.get("search_pushed") or "coverage_at" not in tried:
        return None
    since = journal[tried["coverage_at"]:]
    if any(e.get("action") in ("SEARCH", "NAVIGATE") for e in since):
        return None
    # Only gaps the code confirms, never the model's verdict alone: the once-per-run rule keeps a wrong verdict from
    # costing more than one turn, and an install item with no install command in any discovery is not a verdict
    found = [e for e in journal if e.get("action") == "DISCOVER"]
    if any(INSTALL_COMMAND.search(e.get("extracted_data", "")) for e in found):
        return None
    checklist = tried["checklist"]
    still = [n for n in tried.get("unanswered") or [] if 0 < n <= len(checklist)
             and INSTALL_ASKED.search(checklist[n - 1]["text"])]
    suggestions = search_suggestions(checklist, still)
    if not suggestions:
        return None
    tried["search_pushed"] = True
    return "BLOCKED_COVERAGE", ("Nothing answers these checklist items yet, and you have not looked anywhere else since "
                                "they were named: " + ", ".join(map(str, still)) + ". Before finishing, SEARCH for "
                                "them once (the current page does not have them, or they would be recorded by now)."
                                + suggestions + "\nIf that search finds nothing, FINISH and say they were not found.")

def search_suggestions(checklist, unanswered):
    """A query to start from for each item nothing answers, from the item's own wording.

    Sent back with "nothing answers item 4 (install packages into a virtual environment)", Phi-4 stayed on the venv
    reference page, which has no pip command, re-reading its parts until it finished without one; the run before, a
    second search had found the tutorial page that gives it. "Look elsewhere" named no place to start."""
    lines = []
    for n in unanswered or []:
        if not (0 < n <= len(checklist)) or OPINION_REQUEST.search(checklist[n - 1]["text"]):
            continue
        words = [w for w in re.findall(r"[\w'-]+", checklist[n - 1]["text"])
                 if w.lower() not in ITEM_VERBS | {"out", "how", "to", "the", "a", "an", "and", "using", "into", "of"}]
        if words:
            lines.append(f"- Item {n}: if the pages read so far do not have it, SEARCH for it, for example: "
                         f"{' '.join(words[:10])}")
    return ("\nWhere to look:\n" + "\n".join(lines)) if lines else ""

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
    # Items short of the number they ask for are counted again too: what the agent recorded after being sent back
    # may complete them
    short = tried.get("short_counts") or {}
    rechecked = sorted(set(candidates) | set(tried.get("unproven_items") or []) | set(short))
    if not rechecked or not any(e.get("action") == "DISCOVER" for e in journal):
        return candidates, []
    print(f"🔎 Checking checklist item(s) {', '.join(map(str, rechecked))} once more before writing the final result...")
    gaps, unanswered, unproven = await coverage_gaps(client, objective, checklist, journal, only=set(rechecked),
                                                     evidence=tried.setdefault("evidence", {}),
                                                     unchecked=tried.setdefault("unchecked", set()))
    tried["short_counts"] = {n: count for n, count in short_counts(gaps).items() if n not in unanswered}
    return [n for n in unanswered if n in candidates or n in rechecked], unproven
