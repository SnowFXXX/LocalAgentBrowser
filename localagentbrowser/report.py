"""The journal shown to the model, the research log, the final result written in its own call (with the code's
caveats), and the report file."""
import json
import os
import re
from .config import COMPOSE_MAX_TOKENS, JOURNAL_BATCH, MAX_EVIDENCE_CHARS, MAX_JOURNAL_DETAIL_CHARS, MAX_JOURNAL_REASON_CHARS, MAX_THOUGHT_CHARS, MODEL, REPORT_FILE, VIEW_MAX_TOKENS
from .prompts import COMPOSE_SYSTEM, NO_RESULT, RESULT_FORMAT, RESULT_REPLY, RESULT_RETRY, RESULT_UNCITED, RESULT_UNFINISHED, UNFINISHED_NOTE
from .text import MISSING_STOPWORDS, NUMBER_WORDS, OPINION_REQUEST, SPELLED_NUMBERS, extract_message_content, plain_words, shorten, was_cut_off, with_digits, word_pairs
from .pages import normalize_url
from .grounding import code_text, invented_code
from .checklist import checklist_lines
from .coverage import cited_answer, quoted_from

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

# Not a digit in a name ("sqlite3", "H2O", "CO2", "IPv6"): cutting invented numbers, a comparison result lost its
# sentence naming the `sqlite3` tool, reported as the number "3"
NUMBER_PATTERN = re.compile(r"(?<![A-Za-z_])\d+(?:[.,/]\d+)*")

def unverified_numbers(text, discoveries, objective=""):
    """Numbers in the final result that appear in none of the discoveries.

    A small model writing a how-to filled a gap with an invented quantity ("45 grams of lukewarm water (about
    1/4 cup)"); numbers are where such errors do the most harm and the easiest place to check them.
    """
    text = re.sub(r"\[\d+(?:\s*[-–,;]\s*\d+)*\]", " ", str(text))  # Citation markers: [2], [4, 5], [1-4]
    # Numbered list items and headings ("#### 1. Setup"), bold ones too: "**1. Explain what goes into
    # photosynthesis:**" had its "1" taken for an invented number and cut, mangling the label
    text = re.sub(r"(?m)^\s*(?:#+\s*)?(?:[*_]{1,2})?\d+[.)]\s", " ", text)
    text = re.sub(r"```.*?```|`[^`\n]*`", " ", text, flags=re.S)  # Code is checked as code, by unverified_code
    # A discovery's "four cups" gives the result's "4 cups"
    # The objective's own numbers too: a repotting result headed "Care During First Two Weeks" from the question
    sources = [e["extracted_data"] for e in discoveries] + [str(objective)]
    known = {n.replace(",", "") for t in sources for n in NUMBER_PATTERN.findall(with_digits(t))}
    unverified = []
    for number in NUMBER_PATTERN.findall(text):
        if number.replace(",", "") not in known and number not in unverified:
            unverified.append(number)
    # An amount in words is a number too: a cold brew result said it keeps "for up to two weeks [not found]", from
    # no discovery. Only with a unit: "two databases" counts things the result lists, "three" may echo the objective
    for match in SPELLED_AMOUNT.finditer(text):
        if str(SPELLED_NUMBERS[match.group(1).lower()]) not in known and match.group() not in unverified:
            unverified.append(match.group())
    # A time must be given as a time: a cold brew result said it keeps "up to 2 weeks [1]", and its discovery had a
    # "2" only in "a 2L mason jar"
    durations = {(n.replace(",", ""), unit) for t in sources for n, unit in durations_in(with_digits(t))}
    for number, unit in durations_in(with_digits(text)):
        amount = f"{number} {unit}" + ("" if number == "1" else "s")
        if number.replace(",", "") in known and (number.replace(",", ""), unit) not in durations \
                and amount not in unverified:
            unverified.append(amount)
    return unverified

DURATION = re.compile(r"(?<![A-Za-z_])(\d+(?:[.,/]\d+)*)((?:\s*(?:-|–|to|or|and)\s*\d+(?:[.,/]\d+)*)*)\s*"
                      r"(minute|hour|day|week|month|year)s?\b", re.I)

def durations_in(text):
    """(number, unit) for each time given: "12 to 24 hours" gives both 12 and 24 as hours."""
    for match in DURATION.finditer(text):
        for number in [match.group(1)] + NUMBER_PATTERN.findall(match.group(2)):
            yield number, match.group(3).lower()

SPELLED_AMOUNT = re.compile(r"\b(" + "|".join(SPELLED_NUMBERS) + r")\s+(?:minute|hour|day|week|month|year|cup|ounce|"
                            r"gram|tablespoon|teaspoon|liter|litre|quart|pound|meter|metre|foot|feet|mile|kilometer|"
                            r"degree)s?\b", re.I)

PROMPT_WORDS = {"objective", "checklist", "discoveries", "discovery", "item", "items", "finish", "result", "evidence",
                "findings", "environment"}
CLAUSE_OPENERS = {"this", "these", "that", "while", "although", "though", "found", "not", "however", "when", "where",
                  "which", "it", "they", "both", "each", "also", "and", "but", "or", "the", "you", "your",
                  # Section labels the model writes in bold ("**Description:**")
                  "description", "describe", "summary", "overview", "details", "note", "notes", "example",
                  "conclusion", "steps", "step", "ingredients", "equipment", "storage", "sources", "references",
                  "findings", "result", "results", "answer", "pros", "cons", "strengths", "features"}
NAME_PATTERN = re.compile(r"\b[A-Z][A-Za-z0-9]*[A-Z0-9][A-Za-z0-9]*\b|\b[A-Z][a-z]{2,}\b")

def other_form(word, known):
    """Whether two words are forms of one ("egypt", "egyptians"; "comparison", "compare"): of 4 letters or more,
    with a shared start of at least 80% of the shorter. "photoscape" and "photosplice" share only 60%."""
    short = min(len(word), len(known))
    shared = len(os.path.commonprefix([word, known]))
    return short >= 4 and shared >= 0.8 * short

def unnamed_names(text, discoveries, objective=""):
    """Capitalized names in the result that no discovery and not the objective contain.

    A Photoshop list named "GIMP" and "Krita" over two discoveries that were feature lists naming no tool; the
    names came from the model's memory, cited as if the discoveries gave them. Common words that start a sentence
    are not names; a name counts as given if any discovery or the objective has it, in any case."""
    # A discovery's own address names its subject too: Inkscape's feature list, copied from inkscape.org/about/features,
    # never says "Inkscape", and the name was cut from a correct list, leaving the features under a blank heading
    known = set(plain_words(objective + " " + " ".join(f"{e['extracted_data']} {e.get('source') or ''}"
                                                       for e in discoveries)))
    text = re.sub(r"```.*?```|`[^`\n]*`", " ", str(text), flags=re.S)
    unnamed = []
    for match in NAME_PATTERN.finditer(text):
        name, word = match.group(), match.group().lower()
        # A capital that only starts a sentence, line, list item, heading or label ("**Recommendation:**",
        # "2. Pour the water") is not a name; an acronym (ATP, NADPH) is one wherever it stands
        # A chemical formula (H2O, CO2, C6H12O6) writes a name the discovery may give in words ("water"): cutting
        # "and water (H2O)" took the water with it
        if re.search(r"\d", name):
            continue
        before = text[:match.start()].rstrip(" *_#(\"'")
        starts = not before or before[-1] in ".!?:\n" or re.search(r"(?:^|\n)\s*(?:[-*]|\d+[.)])$", before)
        # A bold word is a name even at a line's start: lists bold each thing ("- **Krita** ..."), and bold labels
        # that are not things ("**Recommendation:**") come from the objective's own words
        bold = text[:match.start()].endswith("**")
        # A bold span ending in a colon inside it ("**What goes in:**", "**Recommendation:**") is a label;
        # "**GIMP**:" names a thing
        closing = text.find("**", match.end())
        if bold and closing >= 0 and text[match.end():closing].rstrip().endswith(":"):
            continue
        starts = starts and not bold
        line = text[text.rfind("\n", 0, match.start()) + 1:text.find("\n", match.end()) % (len(text) + 1)]
        # Headings ("### Culture") name parts of the answer, not things; a bold label ("**GIMP**:") can be a thing,
        # and the labels that are not ("**Recommendation:**") come from the objective's own words
        # A short heading with a citation is a thing's name, though: a list headed its third pick
        # "#### PhotoScape [3]" over a discovery about PhotoSplice, another program. Longer ones are title-case
        # labels ("#### Setup and Administration Differences [2]")
        if line.lstrip().startswith("#"):
            label = re.sub(r"[#*_:]", " ", CITATION.sub(" ", line)).split()
            if not (CITATION.search(line) and len(label) <= 2):
                continue
            starts = False
        if (starts and not re.fullmatch(r"[A-Z0-9]{2,}[a-z]?", name)) or word in CLAUSE_OPENERS:
            continue
        # Another form of the word counts ("Christian" for "Christianity", "Egypt" for "Egyptian"): one starts with
        # the other. Sharing only a start does not: "PhotoScape" passed as "PhotoSplice" by their first five letters
        if word in known or any(other_form(word, k) for k in known):
            continue
        # The prompt's own section names are not names a discovery must give: an explanation result echoed "the
        # OBJECTIVE CHECKLIST", and "Left out ...: CHECKLIST" ended the result
        if word in PROMPT_WORDS:
            continue
        if name not in unnamed:
            unnamed.append(name)
    return unnamed

def without_missing_citations(text, count):
    """The result without citation markers for discoveries that do not exist: two of three factual results in one
    evening cited [2] (one also [3]) over a single discovery. Which one was meant cannot be known, so none is put
    in their place."""
    def keep(match):
        numbers = [n for n in re.findall(r"\d+", match.group(2)) if 1 <= int(n) <= count]
        # The space before a removed marker goes with it ("5,895 m [2]." -> "5,895 m.")
        return f"{match.group(1)}[{', '.join(numbers)}]" if numbers else ""
    return re.sub(r"([ \t]*)(\[\d+(?:\s*,\s*\d+)*\])", keep, str(text))

CITATION = re.compile(r"\[\d+(?:\s*[-–,;]\s*\d+)*\]")
MATCHED_LINE_SHARE = 0.5  # Share of a line's word pairs that must be in one discovery for the code to cite it
MIN_MATCHED_PAIRS = 4  # Shorter lines ("**Setup:**") are not cited by the code
SENTENCE_OPENERS = {"the", "this", "these", "that", "those", "its", "it's", "both", "each", "here", "there", "they",
                    "you", "your", "for", "with", "when", "while", "after", "before", "then", "also", "and", "but",
                    "however", "use", "using", "all", "some", "most", "one", "two", "three", "step", "note"}

def with_matched_citations(text, discoveries):
    """The result with a citation added by the code to each line most of whose wording is in one discovery.

    For a result the model wrote without any citation twice: the line is cited to the discovery holding the most of
    its word pairs, when that is at least half of them; other lines stay uncited (and are counted as such)."""
    found = [word_pairs(" ".join(plain_words(e["extracted_data"]))) for e in discoveries]
    lines = []
    for line in str(text).split("\n"):
        pairs = word_pairs(" ".join(plain_words(line)))
        # Not a heading or a line introducing what follows ("Here are the steps to make cold brew coffee:"): its words
        # echo the objective, so they match a discovery without coming from it
        lead = line.strip().rstrip("*_ ").endswith(":") or line.lstrip().startswith("#")
        if len(pairs) >= MIN_MATCHED_PAIRS and not CITATION.search(line) and not lead:
            # A name in the line must be in the discovery: "LibreOffice: Best at comprehensive document handling ...
            # text documents, spreadsheets" shared those words with a discovery about ONLYOFFICE Docs
            # Every capitalized word counts, the first one too ("LibreOffice has ..."), except common sentence openers;
            # an ordinary first word the discovery lacks only leaves the line uncited
            names = {w.lower() for w in re.findall(r"\b[A-Z][A-Za-z]{2,}", line)} - SENTENCE_OPENERS
            shares = [len(pairs & f) / len(pairs) if names <= set(plain_words(e["extracted_data"])) else 0
                      for f, e in zip(found, discoveries, strict=True)]
            best = max(range(len(shares)), key=shares.__getitem__) if shares else None
            if best is not None and shares[best] >= MATCHED_LINE_SHARE:
                line = f"{line.rstrip()} [{best + 1}]"
        lines.append(line)
    return "\n".join(lines)

# A line's list number, bullet or heading mark, bold or not ("2. ", "- ", "**1. "): not a sentence of its own
LIST_MARKER = re.compile(r"\s*(?:#+\s*)?(?:[*_]{1,2}(?=\d))?(?:\d+[.)]|[-*])?\s*")

def without_invented_numbers(text, discoveries, objective=""):
    """The final result with each sentence giving a number no discovery contains cut, and those numbers named.

    A how-to result said to fill the jar "about 3/4 full" and that cold brew keeps "for up to 2 weeks [1]", with
    neither in its discoveries, and the same storage time came back in earlier runs ("up to 2 weeks", "within 7
    days", "1-2 weeks"), the model's own memory. They were reported under the result but left in it, where a reader
    would follow them; a number is exact, like a command, so one no discovery gives goes the same way.
    """
    invented = unverified_numbers(text, discoveries, objective)
    if not invented:
        return text
    kept = []
    for line in str(text).split("\n"):
        # A list number ("2. ") is not a sentence of its own, nor a number the discoveries must give
        marker = re.match(LIST_MARKER, line).group()
        sentences = [s for s in re.split(r"(?<=[.!?])\s+", line[len(marker):])
                     if not unverified_numbers(s, discoveries, objective)]
        kept.append(marker + " ".join(sentences) if sentences or not line[len(marker):].strip() else "")
    note = ("Left out, because no discovery gives these numbers (the pages read may still have them): "
            + ", ".join(invented) + ".")
    body = re.sub(r"\n{3,}", "\n\n", "\n".join(kept)).strip()
    return f"{body}\n\n{note}" if body else note

# Where a sentence's clauses part: ", which", ", and", ", while", "; " and the like
NAMED_CLAUSE = re.compile(r"(?:,\s*(?:which|and|while|but|where|so|as|including|such as|like)\b|;)\s*", re.I)
MIN_KEPT_WORDS = 5


def without_named_clause(sentence, pattern):
    """The sentence without the clause that holds an unbacked name, or '' when the name comes before any clause
    boundary (the whole sentence rests on it). "The light-dependent reactions take place in the thylakoids [1] and
    convert light energy into chemical energy, which is stored in molecules like ATP and NADPH" lost its cited
    location with the whole sentence; only ", which is stored in molecules like ATP and NADPH" rests on the names."""
    found = pattern.search(sentence)
    if not found:
        return sentence
    cuts = [m.start() for m in NAMED_CLAUSE.finditer(sentence, 0, found.start())]
    if not cuts:
        return ""
    head = sentence[:cuts[-1]].rstrip(" ,;")
    if len(head.split()) < MIN_KEPT_WORDS or pattern.search(head):
        return ""
    return head + ("" if head.endswith((".", "!", "?")) else ".")


LABELLED_LINE = re.compile(r"^(?P<lead>\s*(?:[-*]|\d+[.)])\s*)\*\*(?P<label>[^*\n:]{2,40}?):?\*\*:?\s*(?P<body>.+)$")
SWAP_SHARE = 0.5

def swapped_sentences(text, discoveries):
    """(line number, sentence, label, other) for each sentence under a bold label ("- **PostgreSQL**: ...") copied
    from a discovery sentence that names another label of the result ("SQLite") and not this one.

    A comparison put "Offers minimal, zero-configuration administration suitable for embedded databases" under
    PostgreSQL; its discovery said it of SQLite. Only labels the result itself uses count as the other subject, and
    only where they start the discovery sentence: "It excels in ... without the complexity of suites like
    LibreOffice or WPS Office" is about the suite named before it, and "I'd pick PostgreSQL the moment you need more
    than one concurrent writer" says nothing of PostgreSQL alone. A sentence counts as copied when half its word
    pairs are in the discovery sentence; labels of more than three words are not names."""
    lines = str(text).split("\n")
    labelled = {i: m for i, line in enumerate(lines)
                if (m := LABELLED_LINE.match(line)) and len(m.group("label").split()) <= 3}
    names = {m.group("label").strip().lower(): m.group("label").strip() for m in labelled.values()}
    labels = set(names)
    source = [s for e in discoveries for s in re.split(r"(?<=[.!?])\s+|\n", e["extracted_data"]) if s.strip()]
    swaps = []
    for i, m in labelled.items():
        label = m.group("label").strip().lower()
        for sentence in re.split(r"(?<=[.!?])\s+", m.group("body")):
            pairs = word_pairs(" ".join(plain_words(sentence)))
            if len(pairs) < 5:
                continue
            best = max(source, key=lambda s: len(pairs & word_pairs(" ".join(plain_words(s)))), default="")
            if len(pairs & word_pairs(" ".join(plain_words(best)))) < SWAP_SHARE * len(pairs):
                continue
            named = set(plain_words(best))
            opening = plain_words(re.sub(r'^\(Under the heading "[^"]*"\)\s*', "", best))
            opening = opening[1:] if opening[:1] == ["the"] else opening
            others = [o for o in labels if o != label and plain_words(o) == opening[:len(plain_words(o))]]
            if others and not set(plain_words(label)) <= named:
                swaps.append((i, sentence, m.group("label").strip(), names[others[0]]))
    return swaps

def without_swapped_subjects(text, discoveries):
    """The final result without sentences put under one subject that their discovery says of another, and a note
    naming them."""
    swaps = swapped_sentences(text, discoveries)
    if not swaps:
        return text
    lines = str(text).split("\n")
    for i, sentence, _, _ in swaps:
        lines[i] = lines[i].replace(sentence, "").rstrip()
        if not LABELLED_LINE.match(lines[i]) and re.fullmatch(r"\s*(?:[-*]|\d+[.)])?\s*\*\*[^*]+\*\*:?\s*", lines[i]):
            lines[i] = ""
    labels = "; ".join(f"{label} (the discovery says it of {other})"
                       for _, _, label, other in swaps)
    body = re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()
    return f"{body}\n\nLeft out, because the discovery says it of another subject: {labels}."

def without_unnamed_names(text, discoveries, objective=""):
    """The final result with each sentence naming something no discovery names cut, and those names listed.

    A Photoshop list gave "GIMP" its features from a discovery that never named it, a factual result named
    Kilimanjaro over a discovery saying only "It is also the highest free-standing mountain", and three
    photosynthesis results named ATP and NADPH, in none of their discoveries: in 136 finished results these were the
    only kinds of hit. A name is checked like a number, since a reader takes it as found."""
    names = unnamed_names(text, discoveries, objective)
    if not names:
        return text
    pattern = re.compile(r"\b(?:" + "|".join(map(re.escape, names)) + r")\b")
    kept, dropped_level = [], 0
    for line in str(text).split("\n"):
        # A cited heading naming it goes with its section: kept, the lines under it would read as the heading above's
        level = len(re.match(r"\s*(#*)", line).group(1))
        if dropped_level and not (level and level <= dropped_level):
            continue
        dropped_level = 0
        if level and pattern.search(line):
            dropped_level = level
            continue
        marker = re.match(LIST_MARKER, line).group()
        sentences = [trimmed for s in re.split(r"(?<=[.!?])\s+", line[len(marker):])
                     if (trimmed := without_named_clause(s, pattern))]
        kept.append(marker + " ".join(sentences) if sentences or not line[len(marker):].strip() else "")
    note = ("Left out, because no discovery names them (the pages read may still have them): " + ", ".join(names)
            + ".")
    body = re.sub(r"\n{3,}", "\n\n", "\n".join(kept)).strip()
    return f"{body}\n\n{note}" if body else note

LEFT_OUT_NOTE = re.compile(r"(?m)^Left out, because .*$")

def unverified_code(text, discoveries):
    """Backticked commands or code in the final result that none of the discoveries contain. The checks' own "Left
    out, because ..." notes are not the result's claims: a docs result whose invented activate commands were cut
    was flagged for the very commands its note named as left out."""
    return invented_code(LEFT_OUT_NOTE.sub("", str(text)), [e["extracted_data"] for e in discoveries])

CODE_CITATION = re.compile(r"(`+)([^`\n]*?)\s*(\[\d+(?:\s*[-–,;]\s*\d+)*\])\s*\1")

def with_citations_outside_code(text):
    """Citation markers moved out of the code they were written into: asked to cite, the 7B wrote
    `venv\\Scripts\\activate [2]`, and the command check, reading "[2]" as part of the command, cut the correct
    activation commands as invented. Lines of a fenced block lose a trailing marker the same way."""
    text = CODE_CITATION.sub(lambda m: f"{m.group(1)}{m.group(2).rstrip()}{m.group(1)} {m.group(3)}", str(text))
    lines, fenced = [], False
    for line in text.split("\n"):
        if line.strip().startswith("```"):
            fenced = not fenced
        elif fenced:
            line = re.sub(r"\s*\[\d+(?:\s*[-–,;]\s*\d+)*\]\s*$", "", line)
        lines.append(line)
    return "\n".join(lines)

LONE_CITATION = re.compile(r"(?<=[^\n])\n[ \t]*(?:[-*][ \t]*)?(\[\d+(?:\s*[-–,;]\s*\d+)*\](?:[ \t,]*\[\d+\])*)[ \t]*(?=\n|$)")

def with_lone_citations_joined(text):
    """A citation written on a line of its own ("  - [1]" under a paragraph) joined to the line above, the one it
    cites: a comparison result put every citation on its own sub-bullet, and its paragraphs read as uncited."""
    return LONE_CITATION.sub(lambda m: " " + m.group(1), str(text))

def with_code_closed(text):
    """A code block the model opened and never closed, closed at its last line: the same docs result opened ``` for
    pip and never closed it, so the command check read the headings after it as code and cut them."""
    if str(text).count("```") % 2 == 0:
        return text
    start = text.rfind("```")
    end = text.find("\n\n", start)
    return text + "\n```" if end < 0 else text[:end] + "\n```" + text[end:]

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
        unchecked = unverified_numbers(final, found, objective)
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
                               evidence=None, short=None):
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
    # Fewer things found than an item asks for: a list result began "Three free, open-source alternatives to
    # Microsoft Office are LibreOffice [1] and Apache OpenOffice [2]" and never said the third was not found
    fewer = [(checklist[n - 1]["text"], k, wanted) for n, (k, wanted) in sorted((short or {}).items())
             if checklist and 0 < n <= len(checklist) and n not in not_found]
    if fewer:
        parts += ("### FEWER FOUND THAN ASKED (give only the ones the discoveries name, and say how many could not be "
                  "found)\n" + "\n".join(f"- {text}: only {k} of {wanted} found" for text, k, wanted in fewer) + "\n\n")
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
               # A comparison put PostgreSQL's data types under "Handling of many simultaneous users", the part its
               # discoveries said nothing about for PostgreSQL
               + ("6. Cover every CHECKLIST item; for an item no discovery supports, say plainly that it was not found. "
                  "Put each fact under the part it answers, never under another part's heading; where one of the "
                  "things compared has nothing for a part, say that it was not found there. Write nothing about the "
                  "NOT FOUND items.\n"
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
    text = without_echoed_objective(text, objective)
    # Asked once more when the result stops partway through a sentence (or at the length limit); if the second one
    # does too, the first is cut back to its last finished sentence and the code says it stopped there
    if text != NO_RESULT and (was_cut_off(response) or ends_mid_sentence(text)):
        ending = shorten(" ".join(text.split()[-8:]), 80)
        retry = messages + [{"role": "assistant", "content": extract_message_content(response)},
                            {"role": "user", "content": RESULT_UNFINISHED.replace("{ending}", ending)}]
        response = await client.chat.completions.create(model=MODEL, messages=retry, temperature=0.3,
                                                        max_tokens=COMPOSE_MAX_TOKENS, response_format=RESULT_FORMAT)
        again = without_echoed_objective(result_text(extract_message_content(response)), objective)
        if usable_result(again) and not was_cut_off(response) and not ends_mid_sentence(again):
            text = again
        else:
            kept = complete_sentences(text)
            # Nothing finished to keep ("How" before a loop) is no result
            text = (kept + "\n\n" + UNFINISHED_NOTE) if SENTENCE_END.search(kept) else NO_RESULT
    # Asked once more when nothing is cited: 15 of 135 finished results with discoveries had no citation marker at
    # all, from every model, so a reader could not tell which page gave what. If the second reply has none either,
    # the code cites the lines whose wording it can match to one discovery
    if text != NO_RESULT and found and not CITATION.search(text):
        retry = messages + [{"role": "assistant", "content": json.dumps({"result": text}, ensure_ascii=False)},
                            {"role": "user", "content": RESULT_UNCITED}]
        response = await client.chat.completions.create(model=MODEL, messages=retry, temperature=0.3,
                                                        max_tokens=COMPOSE_MAX_TOKENS, response_format=RESULT_FORMAT)
        again = without_echoed_objective(result_text(extract_message_content(response)), objective)
        if (usable_result(again) and CITATION.search(again) and not was_cut_off(response)
                and not ends_mid_sentence(again)):
            text = again
        else:
            text = with_matched_citations(text, found)
    if text != NO_RESULT:
        text = await without_false_gaps(client, text, found, answered)
    # Asked to "give your own opinion" about owls, the result listed the traditions and ended without one,
    # although rule 6 asks for a 'My view:' paragraph whenever the objective wants a view. A short second call
    # writes only that paragraph, from the result just written
    if OPINION_REQUEST.search(objective) and "my view:" not in text.lower() and text != NO_RESULT:
        text = with_view_labelled(text)
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
    # Before the code's own notes are added: a checklist item's text may hold a number ("Name 5 tools")
    # After the filled gaps are cut, so "does not specify how long it keeps, but ... within 1-2 weeks" keeps its gap
    if text != NO_RESULT:
        text = with_lone_citations_joined(with_code_closed(with_citations_outside_code(text)))
        text = without_invented_numbers(without_filled_gaps(without_missing_citations(text, len(found))), found, objective)
        text = without_swapped_subjects(without_unnamed_names(text, found, objective), found)
    # Reported by the code as well, whatever the result says
    unfound_lines = ([f"- {t}" for t in unfound] + [f"- {t}: the amounts it asks for" for t in amountless]
                     + [f"- {t}: {in_words(wanted - k)} more (only {in_words(k)} of the {in_words(wanted)} were found)"
                        for t, k, wanted in fewer])
    if unfound_lines:
        text += "\n\nNot found in the pages read:\n" + "\n".join(unfound_lines)
    return with_caveats(without_invented_code(without_filled_gaps(text), found), unproven)

# "X is not specified ..., but <a claim>": every such sentence in the suite's finished results filled the gap from the
# model's own knowledge ("not explicitly stated but generally, it can be stored for several weeks", while the page
# said three days; "not specified in the text, but a common recommendation is 1 part coffee to 4 parts water")
FILLED_GAP = re.compile(r"((?:not (?:\w+ )?(?:been )?(?:specified|mentioned|stated|provided|given|found)|does not (?:\w+ )?"
                        r"(?:specify|say|mention|state))[^.\n]{0,100}?),?\s+but\b[^\n]*?(?:\.(?=\s+[A-Z]|\s*$)|(?=\n)|$)",
                        re.I)  # The sentence ends at a period before a capital or the end ("e.g., 1 cup" stays inside it)

VIEW_HEADING = re.compile(r"^[ \t]*#+[ \t]*[*_]*[ \t]*(?:my\s+)?(?:recommendation|opinion|view|verdict|conclusion)\b[^\n]*\n+",
                          re.I | re.M)

def with_view_labelled(text):
    """The result with a section headed as a recommendation or opinion labelled 'My view:' in place of its heading:
    a comparison put "### Recommendation for a Hobby Project" ahead of its uncited advice, and the view paragraph
    then asked for repeated it and was left out, so the advice stayed unlabelled as the model's own."""
    heading = VIEW_HEADING.search(str(text))
    if not heading:
        return text
    rest = text[heading.end():]
    if not rest.strip() or rest.lstrip().startswith(("#", "-", "*", "|")):
        return text
    return text[:heading.start()] + "My view: " + rest.lstrip()

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

# Words a finished sentence does not end on
DANGLING_WORDS = {"to", "the", "a", "an", "and", "or", "of", "in", "on", "at", "for", "with", "from", "by", "that",
                  "which", "is", "are", "was", "were", "be", "as", "into", "than", "its", "their", "this", "these",
                  "but", "because", "such", "like", "using"}

def ends_mid_sentence(text):
    """True when the result stops partway through a sentence: on a comma or an opening bracket, or on a word such as
    "to" or "the". The 7B ended an explanation of photosynthesis with "...from the light-dependent reactions to",
    with the light-dependent reactions and the Calvin cycle never written, although discoveries gave both; the reply
    ended there by itself, below the length limit. A list ending on a name ("- ONLYOFFICE Docs") is not cut off."""
    lines = [line.strip() for line in str(text).strip().splitlines() if line.strip()]
    if not lines:
        return False
    # Citation markers and lists of them ("[1], [2]") are not where a sentence ends, but a period after them is
    # ("...found at [1].")
    last = re.sub(r"(?:[\s,;]*\[\d+(?:\s*[-–,;]\s*\d+)*\])+[\s,;]*$", "", lines[-1]).rstrip(" *_")
    if not last or last[-1] in ".!?:":
        return False
    words = re.findall(r"[A-Za-z']+", last)
    return last[-1] in ",;(" or (last[-1].isalpha() and bool(words) and words[-1].lower() in DANGLING_WORDS)

SENTENCE_END = re.compile(r"[.!?](?:\s*\[\d+(?:\s*[-–,;]\s*\d+)*\])*(?=\s|$)")
BARE_LINE = re.compile(r"\s*(?:[-*#]+|\d+[.)])?\s*(?:\*\*[^*]*\*\*)?\s*")  # A list number or heading with nothing after it

def complete_sentences(text):
    """The text without its unfinished last sentence: the last line is cut after its last finished sentence, or left
    out when it has none, and a list number or heading left bare above it goes too."""
    lines = str(text).rstrip().split("\n")
    last = lines.pop()
    marker = len(re.match(r"\s*(?:[-*]|\d+[.)])?\s*", last).group())  # A sentence end after the list marker ("2. ")
    ends = [m.end() for m in SENTENCE_END.finditer(last) if m.start() >= marker]
    if ends:
        lines.append(last[:ends[-1]])
    while lines and BARE_LINE.fullmatch(lines[-1]):
        lines.pop()
    return "\n".join(lines).rstrip()

def without_echoed_objective(text, objective):
    """The result without an opening paragraph that only repeats the objective: the cut-off photosynthesis result
    began with the objective word for word."""
    first, _, rest = str(text).strip().partition("\n")
    same = " ".join(first.lower().split()).rstrip(" .:?") == " ".join(str(objective).lower().split()).rstrip(" .:?")
    return rest.strip() if same and rest.strip() else text

# "X is not specified", "X was not found": the result saying the discoveries lack something
# "These reactions do not specify a particular location" slipped past the passive form only
NOT_GIVEN = re.compile(r"\b(?:(?:is|are|was|were)\s+not\s+(?:been\s+)?(?:\w+\s+)?(?:specified|mentioned|stated|provided|"
                       r"given|found|detailed|described|listed)|(?:do|does|did)\s+not\s+(?:\w+\s+)?(?:specify|mention|"
                       r"state|give|provide|say|describe))\b", re.I)
CLAUSE_START = re.compile(r"(?:,\s*(?:but|while|although|though|and)\s+|;\s*|\bhowever,?\s+)", re.I)
GAP_WORDS = {"exact", "specific", "detailed", "information", "details", "detail", "description", "precise", "given",
             "provided", "discoveries", "discovery", "sources", "text", "pages"}
MAX_GAP_CLAIMS = 3

def gap_words(text):
    return {w.rstrip("s") for w in plain_words(text) if len(w) > 2} - MISSING_STOPWORDS - GAP_WORDS

# A part of the result that is only "Not found.": "   - Not found." under "1. **What goes into photosynthesis:**",
# or "**Inputs:** Not found." on one line
ITEM_NOT_FOUND = re.compile(r"^(?P<lead>\s*(?:\d+[.)]|[-*])?\s*)(?:\*\*(?P<head>[^*\n]{3,120}?):?\*\*:?\s*(?:-\s*)?)?"
                            r"not (?:found|specified|given|provided|stated|mentioned)\.?\s*$", re.I)

def answered_quote(subject, answered, texts):
    """The checked words for the checklist item a heading names, with the number of the discovery they are from:
    the item sharing the most key words with the heading, if no other item shares as many."""
    ranked = sorted(((len(gap_words(subject) & gap_words(item)), quote) for item, quote in answered), reverse=True)
    if not ranked or ranked[0][0] == 0 or (len(ranked) > 1 and ranked[1][0] == ranked[0][0]):
        return None
    quote = ranked[0][1].split(" ... ")[0]
    number = next((i for i, t in enumerate(texts, 1) if quoted_from(quote, t)), None)
    return (quote, number) if number else None

async def without_false_gaps(client, text, found, answered=()):
    """The result with each claim that something was not found cut, where a discovery gives it after all.

    The 7B answered "It stands at 5,895 meters (19,341 feet) above sea level [2], but its height above the plateau
    base is not specified in the given discoveries [3]" over its one discovery, which said "4,900 m (16,100 ft)
    above its plateau base". Words alone cannot tell (the discovery never says "height"), so the model is asked
    whether the discoveries give it, and its answer counts only with words copied from them that share at least
    two key words with the claim and half of them: "the third alternative was not found" shares one with any
    sentence naming an alternative, and stays.
    """
    texts = [e["extracted_data"] for e in found]
    if not texts:
        return text
    checked, lines = 0, []
    for line in str(text).split("\n"):
        # A part answered only with "Not found." gets the words the coverage check found for its item: cutting the
        # line would leave the part out without a word. The 7B wrote "Not found." under "What goes into
        # photosynthesis", over a discovery saying chlorophyll captures sunlight to break down water molecules
        item = ITEM_NOT_FOUND.match(line)
        if item:
            heading = item.group("head") or next((p for p in reversed(lines) if p.strip()), "")
            heading = re.sub(r"^\W*(?:\d+[.)]\s*)?|\*\*|[:\s]+$", "", heading)
            given = answered_quote(heading, answered, texts)
            if given:
                head = f"**{item.group('head')}:** " if item.group("head") else ""
                lines.append(f"{item.group('lead')}{head}\"{given[0]}\" [{given[1]}]")
                continue
        kept = []
        for sentence in re.split(r"(?<=[.!?])\s+", line):
            claim = NOT_GIVEN.search(sentence)
            if not claim or checked >= MAX_GAP_CLAIMS:
                kept.append(sentence)
                continue
            starts = [m for m in CLAUSE_START.finditer(sentence, 0, claim.start())]
            start = starts[-1] if starts else None
            subject = sentence[start.end() if start else 0:claim.start()]
            subject = re.sub(r"^\W*(?:\d+[.)]\s*)?", "", re.sub(r"\[\d+(?:\s*[-–,;]\s*\d+)*\]|\*\*", "", subject))
            words = gap_words(subject)
            if len(words) < 2:
                kept.append(sentence)
                continue
            checked += 1
            quote = await cited_answer(client, subject.strip(), texts)
            shared = words & gap_words(quote)
            if not quote or len(shared) < 2 or len(shared) < len(words) / 2:
                kept.append(sentence)
            elif start and start.start() > 0:
                # Only the clause goes: "..., but its height above the plateau base is not specified"
                kept.append(sentence[:start.start()].rstrip(" ,;") + ".")
        lines.append(" ".join(s for s in kept if s))
    return "\n".join(lines)

def in_words(number):
    """A small count in words, so the code's own notes are not taken for numbers missing from the discoveries."""
    return {0: "none", 1: "one", **{v: k for k, v in NUMBER_WORDS.items()}}.get(number, str(number))

def usable_result(text):
    """False for a reply that is not a final result: still JSON (a made-up tool call such as
    {"name": "get_mountain_info", ...}), or the prompt's own sections repeated back."""
    return bool(text) and not text.lstrip().startswith("{") and not re.search(
        r"^#+\s*(OBJECTIVE|DISCOVERIES|CHECKLIST)\b", text, re.M) and not LOOPING.search(text)

# The same characters over and over: a how-to "result" was "How \n\n#010;Do I Make 2023040116570" and about 880
# zeros, the model looping until the length limit
LOOPING = re.compile(r"([^\s\-=_*|#.~])\1{40,}|((?=\S{0,3}\w)\S{2,4})\2{25,}")  # Not a rule of dashes

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
