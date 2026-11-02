"""Decisions refused or confirmed before they are carried out (leaving, reading on, re-reading, finishing, repeated
searches, discoveries not on the page) and the notes that go with them."""
import re
from .config import GAP_REFUSAL_LIMIT, LONG_PAGE_UNREAD, MAX_CHECK_DISCOVERY_CHARS, MAX_VISITS_PER_URL, OBJECTIVE_ECHO_SHARE
from .prompts import FINAL_RESULT, GIVE_UP_RULE
from .text import MISSING_STOPWORDS, shorten
from .pages import determine_url, is_search_page, normalize_query, normalize_url, outline_pointer, read_more_target, search_query, where_you_are
from .grounding import DESCRIBES_PAGE, discovery_lines, grounded_share, grounding_check, is_known_discovery, page_hint
from .checklist import check_parts, every_item_has_discoveries, open_items

ECHO_WORD = r"[A-Za-z0-9][A-Za-z0-9'.-]*"
ECHO_STOPWORDS = MISSING_STOPWORDS | {"how", "what", "where", "do", "does", "i", "you", "your", "be", "by", "as", "this",
                                      "that", "it", "can", "should", "need"}


CHECK_WORDING = re.compile(r"\bcovered by\s*\[\d|\bnot covered\b|\bwritten at FINISH\b|\bchecklist item", re.I)

QUESTION_START = re.compile(r"(?:what|how|where|which|why|when|who|does)\b", re.I)  # Not "do", "can", "is": "Do not grind too fine"

def is_question_discovery(parameter):
    """True when every sentence of a discovery starts like a question and none gives an answer (a colon or number)."""
    sentences = [s for line in content_lines(parameter) for s in re.split(r"(?<=[.!?])\s+", line) if s.strip()]
    return bool(sentences) and all(QUESTION_START.match(s) and not re.search(r"[:\d]", s) for s in sentences)

def content_lines(parameter):
    """A discovery's lines with web addresses taken out ("from https://...", "URL: https://..."), empty ones dropped."""
    # A page title with " from https://..." added went through as a finding: the title alone matched the results page
    lines = (re.sub(r"\s*\b(?:from|at|on|url:)?\s*https?://\S+", "", line).strip(" -|:,") for line in discovery_lines(parameter))
    return {line for line in lines if line}

def echoes_objective(parameter, page_state, objective, queries=()):
    # Scored against the objective, the copied question and checklist parts shared 48-100% of their word pairs and
    # real discoveries in every suite run up to 43%; one that matches the page as well is an answer, not an echo.
    # The model's own search queries count here too: a 3B model recorded a page title with its query after it
    # ("...with quantities, steps and shelf life in fridge official documentation site recipes guides").
    if not objective:
        return False
    shown = [part for i, part in enumerate(page_state.get("parts") or []) if i in page_state.get("seen_parts", {0})]
    asked_text = " ".join([objective, *queries])
    if grounded_share(parameter, [asked_text]) >= OBJECTIVE_ECHO_SHARE and grounded_share(parameter, shown) < OBJECTIVE_ECHO_SHARE:
        return True
    # Short labels of the 3B's own ("What you need for cold brew coffee at home") and the 7B's "administration" had
    # too few word pairs for either score; every discovery in the suite runs adding at most one lowercase word to the
    # objective's was one of these. A new name or number ("LibreOffice is free and open source") is information.
    # Queries are left out here, as this does not look at the page: a 7B model once searched with a sentence it then
    # found on a page and recorded.
    asked = {w.lower() for w in re.findall(ECHO_WORD, objective)}
    new = [w for w in re.findall(ECHO_WORD, parameter) if w.lower() not in asked and w.lower() not in ECHO_STOPWORDS]
    return len(new) == 0 or (len(new) == 1 and new[0].islower())

def is_heading_discovery(parameter, page_state):
    """True when every word of a discovery comes from the page's headings (a sentence ending in "." is not one)."""
    # Matched by words, not whole lines: a 3B model joined the page title to one step heading after another
    # ("How to Make Cold Brew Coffee: Step-by-Step Grind Your Coffee"), dropping the "Step 1:"
    headings = list(page_state.get("headings") or ()) + [h for label in page_state.get("outline") or () for h in label.split(" / ")]
    heading_words = {w.lower() for w in re.findall(ECHO_WORD, " ".join(headings))}
    # The outline's own notes are not words of a discovery: a 3B model recorded three labels as "The grind: coarse,
    # like sea salt (part 2), The cold brew ratio explained (part 3), Method (part 4)"
    # Section numbers too ("10.4: The Light-Dependent Reactions / How the Light-Dependent Reactions Work (read)")
    lines = {re.sub(r"^\d+(?:\.\d+)*[.:)]?\s+|\s*\((?:read|part \d+(?: of \d+)?)\)", "", line)
             for line in content_lines(parameter)}
    # Section and page numbers are not new words here: an outline label came as "4.1.06: Light-independent
    # Reactions/3" (only those: a height such as "5,895" next to a heading's words is a fact)
    lines = {re.sub(r"\b\d+(?:\.\d+)+\b|/\d+\b", " ", line) for line in lines}
    words = {w for line in lines for w in re.findall(ECHO_WORD, line)} - ECHO_STOPWORDS
    return bool(words) and words <= heading_words and not any(line.endswith(".") for line in lines)

def is_snippet_discovery(parameter, page_state):
    """True when every line of a discovery, URL lines aside, is a title or description from a results page."""
    lines = content_lines(parameter)
    return bool(lines) and lines <= discovery_lines("\n".join(page_state.get("snippets", ())))

def leave_check(action, page_state, current_url, journal=()):
    # The model often left content pages without recording what it needed from them. The first attempt to
    # leave a page it has recorded nothing from gets a cheap retry asking it to confirm; repeating the same
    # action then goes through, so tasks where leaving without a discovery is normal lose only one retry.
    # Search result pages are exempt (page_state only tracks content pages).
    parts = page_state["parts"]
    on_page = current_url and page_state["url"] == normalize_url(current_url) and parts
    # Anything recorded from this page counts, on this visit or an earlier one
    recorded_here = any(e.get("action") == "DISCOVER" and normalize_url(e.get("source") or "") == normalize_url(current_url)
                        for e in journal)
    if not on_page or recorded_here or page_state.get("visit_discoveries") or page_state.get("leave_confirmed"):
        return None
    page_state["leave_confirmed"] = True  # Asked once per visit
    unread = len(parts) - len(page_state.get("seen_parts", {0}))
    if unread > LONG_PAGE_UNREAD:
        # Counting unread parts of a long page pushed the model to read all of them in order
        unread_note = (f" It is a long page ({len(parts)} parts, {unread} unread): use its outline to judge "
                       "whether a part is worth reading.")
        read_more = ", or READ_MORE with the number of a part from the outline that is likely to have it"
    elif unread:
        unread_note = f" {unread} of its {len(parts)} parts are still unread."
        read_more = ", or READ_MORE if the unread parts may have it"
    else:
        unread_note = read_more = ""
    return ("CONFIRM_LEAVE", f"You are about to leave {current_url} without recording anything from it.{unread_note} "
            f"If it has information relevant to the objective, DISCOVER it now{read_more}. "
            f"If it truly has nothing relevant, repeat your {action} to leave.")

def read_on_check(page_state, current_url, target, checklist=None):
    # The model read long pages part after part without recording anything, even though every prompt ends by
    # asking it to DISCOVER relevant information first: on an explanation task it read four parts of a page that
    # had the inputs, outputs and cell locations it needed and recorded them only when a progress check fired.
    # Moving on from a part nothing was recorded from gets one cheap retry that makes the model name what the
    # part says about the objective; repeating the READ_MORE then goes through. It also offers FINISH: with three
    # cultures recorded where three were asked for, the check pushed an opinion task to keep collecting more, and
    # it offers leaving: shown an unrelated "agent" project page, the model read on part after part.
    index = page_state["index"]
    # Only moving forward counts: going back to an earlier part is covered by the re-read check
    if target <= index or index in page_state.get("recorded_parts", set()):
        return None
    confirmed = page_state.setdefault("read_on_confirmed", set())
    if index in confirmed:
        return None
    confirmed.add(index)
    # Naming the items still open: on an explanation task the model answered 'none' for every part of an overview
    # that gave the inputs and outputs it still needed, while it was thinking about the Calvin cycle
    # With every item holding what it asks for, a documentation task got "choose FINISH now" after a discovery and
    # read four more parts anyway, answering each of these checks 'none' and repeating READ_MORE: then this check
    # asks for FINISH first and no longer offers reading on
    if every_item_has_discoveries(checklist):
        return "CONFIRM_READ_ON", (f"Before reading on: every item of the OBJECTIVE CHECKLIST already has the discoveries "
                                   f"it asks for. Choose FINISH now with the complete final result. Only if part "
                                   f"{index + 1}, which is shown now, gives something an item still clearly lacks, "
                                   "DISCOVER that instead.")
    still_open = open_items(checklist)
    serves = f"the OBJECTIVE (above all the checklist items with nothing recorded yet: {still_open})" if still_open else "the OBJECTIVE"
    pointer = outline_pointer(page_state, checklist)
    if pointer and pointer[0] != index + 2:
        page_state["pointed"] = ((page_state.get("url"), index), pointer[0] - 1)
    read_on = (f"repeat your READ_MORE: it will show part {pointer[0]}, not the next part, because the Page Outline "
               f"names '{pointer[1]}' there, from an item with nothing recorded yet" if pointer and pointer[0] != index + 2
               else "repeat your READ_MORE")
    return "CONFIRM_READ_ON", (f"Before reading on: you have recorded nothing from part {index + 1}, which is shown now. "
                               f"In thought_process, list each fact in it that serves {serves}, or write 'none'. If any "
                               f"of them are not in DISCOVERIES SO FAR, DISCOVER them now. If there are none, {read_on}. "
                               "But if DISCOVERIES SO FAR already cover every item of the OBJECTIVE CHECKLIST, choose "
                               "FINISH instead of reading on, and if this page is not about the OBJECTIVE at all, leave "
                               "it with SEARCH or NAVIGATE.")

def gap_check(thought, tried):
    # The model is asked to write 'not covered' for missing parts; on a comparison it wrote "Available Libraries:
    # Not covered" and finished anyway. A FINISH whose own reasoning says that is sent back to work on the gap,
    # at most GAP_REFUSAL_LIMIT times per run so a part that cannot be found does not block finishing for good
    if ("not covered" not in str(thought).lower() or tried.get("gap_refusals", 0) >= GAP_REFUSAL_LIMIT
            or tried.get("search_exhausted")):
        return None
    tried["gap_refusals"] = tried.get("gap_refusals", 0) + 1
    return ("BLOCKED_FINISH", "Your own check marks a part of the objective as 'not covered'. Do not finish yet: work on "
            "that part, for example with a SEARCH aimed at it. Only if you have already looked for it without success, "
            "FINISH and say in the final result what could not be found.")

def check_decision(action, parameter, journal, tried, page_state, current_url, thought=""):
    """Refusals that can be decided before acting. Returns (journal action, reason), or None if the decision may proceed."""
    if action == "SEARCH":
        query = search_query(parameter)
        if not query.strip():
            return "BLOCKED_SEARCH", "The SEARCH query was empty."
        # Repeats are blocked outright; a warning alone does not stop small models from looping
        if normalize_query(query) in [normalize_query(q) for q in tried["queries"]]:
            # On a page it has opened and recorded nothing from, the search already did its work: a 3B model repeated
            # its search twice on the official venv page, was told it could finish with "not found", and did
            on_unrecorded_page = (current_url and not is_search_page(current_url) and page_state.get("parts")
                                  and page_state.get("url") == normalize_url(current_url)
                                  and not page_state.get("visit_discoveries"))
            if on_unrecorded_page:
                return "BLOCKED_SEARCH", (f"This exact query was already searched, and it led to the page you are on. "
                                          f"{where_you_are(page_state, current_url)} Nothing has been recorded from it "
                                          "yet: DISCOVER what it says for the OBJECTIVE, READ_MORE to read on, or SEARCH "
                                          "with clearly different words for another part of the OBJECTIVE.")
            # A model looking for something the web does not have kept resubmitting the same search (three times
            # in one turn); from the second repeat it is told plainly that finishing with the gap is an option
            repeats = tried.setdefault("repeated_searches", {})
            repeats[normalize_query(query)] = repeats.get(normalize_query(query), 0) + 1
            if repeats[normalize_query(query)] >= 2:
                # Told to finish with the gap, the model tried FINISH, was asked by the finish check to work on the
                # uncovered part, and repeated the search again (five times on a strict objective); from here a
                # FINISH goes straight through
                tried["search_exhausted"] = True
                return "BLOCKED_SEARCH", (f"You have now tried to repeat the search '{query}' "
                                          f"{repeats[normalize_query(query)]} times. It was already done and did not find "
                                          "what you need. Either SEARCH with clearly different words, or accept that it "
                                          "may not be available: FINISH and say in the final result that it could not be "
                                          "found.")
            return "BLOCKED_SEARCH", "This exact query was already searched. Use a new query, or DISCOVER from the current page."
    elif action == "NAVIGATE":
        if not parameter.strip():
            return "BLOCKED_NAVIGATE", "NAVIGATE needs a full URL in the parameter."
        target = normalize_url(determine_url(action, parameter))
        # Shown a long page's outline, the model kept trying to jump to a section with a made-up anchor
        # ("NAVIGATE ...#sqlite-vs-postgresql", then "NAVIGATE #sqlite-vs-postgresql")
        section = "#" in parameter and (parameter.strip().startswith("#") or target == normalize_url(current_url or ""))
        if current_url and (target == normalize_url(current_url) or section):
            jump = (" To go to a section of this page, use READ_MORE with that section's part number from the outline."
                    if section else "")
            return "BLOCKED_NAVIGATE", (f"{where_you_are(page_state, current_url)} You are already on this page.{jump} "
                                        "Use READ_MORE to see its other parts, DISCOVER from it, or go somewhere else.")
        # Going back to an earlier page is allowed; the cap stops back-and-forth loops between pages
        if [normalize_url(u) for u in tried["urls"]].count(target) >= MAX_VISITS_PER_URL:
            return "BLOCKED_NAVIGATE", f"This URL was already visited {MAX_VISITS_PER_URL} times. Pick a different page."
    if action in ("SEARCH", "NAVIGATE"):
        return leave_check(action, page_state, current_url, journal)
    elif action == "READ_MORE":
        total = len(page_state["parts"])
        target = read_more_target(parameter, page_state)
        if not (current_url and page_state["url"] == normalize_url(current_url)) or total == 0:
            # On the results page a 3B model kept choosing READ_MORE to "read more on one of these sources" and, told
            # only that there were no parts, repeated its search; it never opened a result
            if current_url and is_search_page(current_url):
                return "BLOCKED_READ_MORE", (f"{where_you_are(page_state, current_url)} A results page has no parts to "
                                             "read: to read one of the results, NAVIGATE to its URL.")
            return "BLOCKED_READ_MORE", f"{where_you_are(page_state, current_url)} This page has no parts to read."
        if not 0 <= target < total:
            return "BLOCKED_READ_MORE", (f"There is no part {target + 1}. {where_you_are(page_state, current_url)} "
                                         f"This page has {total} part(s).")
        # On part 3 the model asked for "READ_MORE 3", which only showed the same part again and cost a turn
        if target == page_state["index"]:
            following = (f"For part {target + 2}, use READ_MORE with an empty parameter. "
                         if target < total - 1 else "It is the last part of this page. ")
            return "BLOCKED_READ_MORE", (f"Part {target + 1} is the part already shown. {following}"
                                         "Otherwise choose DISCOVER or another action.")
        # Re-reading a part is allowed, but confirmed once per part (the same pattern as the leave check)
        confirmed = page_state.setdefault("reread_confirmed", set())
        if target in page_state.get("seen_parts", set()) and target != page_state["index"] and target not in confirmed:
            confirmed.add(target)
            return "CONFIRM_REREAD", (f"You already read part {target + 1} of this page; anything useful from it should "
                                      "already be in DISCOVERIES SO FAR. Choose an unread part or another action, or "
                                      "repeat this READ_MORE if you really need to read it again.")
        return read_on_check(page_state, current_url, target, tried.get("checklist"))
    elif action == "DISCOVER":
        if not discovery_lines(parameter):
            # A 3B model described the page's facts in thought_process and left the parameter empty four times in one
            # task; told only that it was empty, it kept doing so
            if str(thought).strip():
                # Told this, the 3B still sent empty DISCOVERs seven times on the venv page ("I need to DISCOVER
                # information from this page"): it gets a page sentence to copy, and the other way on as well
                shown = [p for i, p in enumerate(page_state.get("parts") or []) if i in page_state.get("seen_parts", {0})]
                hint = page_hint("", shown, tried.get("objective", "")) if not is_search_page(current_url) else ""
                return "BLOCKED_DISCOVER", ("The DISCOVER parameter was empty. DISCOVER does not extract anything: "
                                            "only the parameter is recorded, not thought_process, so put the facts "
                                            "themselves in the parameter, in the page's own wording. If the part "
                                            f"shown has nothing for the OBJECTIVE, READ_MORE or SEARCH instead.{hint}")
            return "BLOCKED_DISCOVER", "The DISCOVER parameter was empty. Put the actual information in the parameter."
        # A result's snippet is a summary written to be clicked, not the source: in the 2026-10-25 model tests a 3B
        # model recorded snippets in 5 of 7 tasks without opening a page, and its final results cited the search page.
        # It comes first: refused there as repeating its search, the 3B searched again and lost the turn, while
        # this refusal was followed by NAVIGATE in every run
        if current_url and is_search_page(current_url):
            return "BLOCKED_DISCOVER_SEARCH", ("That is the search results page: its short descriptions are summaries "
                                               "written to be clicked, not sources. NAVIGATE to the result that looks "
                                               "right and DISCOVER from the page itself.")
        # A 3B model recorded a bare web address and then the objective's own question, and both counted as findings
        # (the grounding check accepts the objective's wording, so a paraphrase is not sent back)
        def fact_hint(text):
            # The refusals below are for text that is not a fact; quoting the page sentence closest to it and the
            # objective is what got such refusals followed by a real discovery (a 3B model repeated a description of
            # the page, refused without one, until the turn was lost)
            shown = [p for i, p in enumerate(page_state.get("parts") or []) if i in page_state.get("seen_parts", {0})]
            return page_hint(text, shown, tried.get("objective", ""))
        if not content_lines(parameter):
            # A 3B model gave the page's address to "DISCOVER more from this page", as if DISCOVER looked it up, and
            # did so three turns running; a quoted page sentence is what got such refusals recorded before
            return "BLOCKED_DISCOVER_URL", ("That DISCOVER is only a web address. DISCOVER does not open or look up "
                                            "anything: it records the text you write in its parameter. Write in the "
                                            f"facts the page gives for the OBJECTIVE, in its own wording.{fact_hint('')}")
        # Headings alone ("Method", "How long to keep cold brew in the fridge?") were recorded by the 3B, the 7B and
        # Llama, and the checklist then counted the item as covered
        if is_heading_discovery(parameter, page_state):
            return "BLOCKED_DISCOVER_HEADING", ("That DISCOVER is a heading of the page, not what the page says under "
                                                "it. DISCOVER the facts given under that heading, or READ_MORE to the "
                                                f"part it heads if they are not shown.{fact_hint(parameter)}")
        # "...Guide from https://... contains a list of what is needed, step-by-step instructions, and information on
        # how long it keeps": sent back as not matching the page, the 3B repeated it and it was recorded. In every
        # suite run this wording appeared only in such descriptions, never in a real discovery.
        if DESCRIBES_PAGE.search(parameter):
            return "BLOCKED_DISCOVER_DESCRIBES", ("That DISCOVER describes what the page contains instead of giving "
                                                  "it. DISCOVER the facts themselves, in the page's own wording: the "
                                                  f"amounts, steps, names or dates it gives.{fact_hint(parameter)}")
        # "what are the quantities needed for cold brew coffee at home, what are the steps in order...": in every suite
        # run a discovery made only of questions was a restated objective or a heading; the one real one gave its
        # answer after a colon ("How to activate a virtual environment on macOS or Linux: $ source ...")
        if is_question_discovery(parameter):
            return "BLOCKED_DISCOVER_QUESTION", ("That DISCOVER only asks questions. DISCOVER the page's answers to "
                                                 f"them, in its own wording.{fact_hint(parameter)}")
        # The finish check's own wording copied into a DISCOVER ("not covered by [1, 2, 3]"): in 471 suite
        # discoveries this appeared only in such 3B notes, and they were recorded as findings
        if CHECK_WORDING.search(parameter):
            return "BLOCKED_DISCOVER_CHECK", ("That DISCOVER is a note about the checklist, not something the page "
                                              "says. Write such notes in thought_process; DISCOVER only the page's "
                                              f"facts, in its own wording.{fact_hint(parameter)}")
        if echoes_objective(parameter, page_state, tried.get("objective", ""), tried.get("queries", ())):
            return "BLOCKED_DISCOVER_OBJECTIVE", ("That DISCOVER repeats the OBJECTIVE or your search, not something the page says. "
                                                  "DISCOVER what the page text shown says in answer to it, or READ_MORE "
                                                  f"or look elsewhere if it does not say.{fact_hint(parameter)}")
        if is_known_discovery(parameter, journal):
            # The model has weighed this part: without this, the read-on check asked it to DISCOVER again
            # right after this refusal, and it repeated the duplicate until the turn was lost
            page_state.setdefault("read_on_confirmed", set()).add(page_state.get("index", 0))
            # Told only that, a 3B model sent the same DISCOVER three times and lost the turn
            return "DUPLICATE_DISCOVER", ("Everything in that DISCOVER is already recorded in DISCOVERIES SO FAR. "
                                          "DISCOVER a fact from the page that is not recorded yet, READ_MORE to read "
                                          "on, or FINISH if DISCOVERIES SO FAR cover every item of the OBJECTIVE "
                                          "CHECKLIST.")
        # Refused on the search page, a 3B model opened the result and recorded the same title and description
        # there, which the page's own heading let through; its final result then came from its own knowledge
        if is_snippet_discovery(parameter, page_state):
            return "BLOCKED_DISCOVER_SNIPPET", ("That DISCOVER is the search result's title and description, not what "
                                                "this page says. DISCOVER the facts the page text shown gives for the "
                                                "OBJECTIVE, in its own wording, or READ_MORE if they are further on.")
        return grounding_check(parameter, page_state, current_url, tried.get("objective", ""))
    elif action == "FINISH":
        return finish_check(tried, journal, page_state, current_url) or gap_check(thought, tried)
    else:
        return f"UNKNOWN_{action}", f"'{action}' is not a valid action."
    return None

def finish_check(tried, journal, page_state=None, current_url=None):
    # Asked to judge whether its discoveries "satisfy" the objective, a small model says yes too easily: on an
    # opinion task it finished after one source and gave a summary instead of the opinion asked for. The first
    # FINISH of each turn gets a cheap retry asking for a part-by-part check; repeating FINISH then goes through.
    if tried.get("finish_confirmed") or tried.get("search_exhausted"):
        return None
    tried["finish_confirmed"] = True  # Reset at the start of every turn
    # How many pages the discoveries came from is shown only while some item still lacks them: with every item
    # covered from one long page, the note sent the 7B away from its finish four times in one task, and cost Llama
    # a retry on a factual question one page fully answered
    sources = "" if every_item_has_discoveries(tried.get("checklist")) else source_note(journal)
    return "CONFIRM_FINISH", (f"{sources}{unread_note(page_state or {}, current_url)}"
                              f"{numbered_discoveries(journal)} {finish_check_text(tried)}")

def numbered_discoveries(journal, limit=MAX_CHECK_DISCOVERY_CHARS):
    # Asked to name the discovery covering each part of the objective, the model marked the light reactions
    # "not covered" although its first discovery described them: discoveries were an unnumbered list, far back
    # in the prompt. Checks now carry the list, numbered, so each part can be matched to a number.
    lines = numbered_lines(journal, limit)
    if not lines:
        return ""
    return "\nYour discoveries, numbered:\n" + "\n".join(lines) + "\n"

def numbered_lines(journal, limit):
    found = [e for e in journal if e.get("action") == "DISCOVER"]
    return [f"[{i}] {shorten(e['extracted_data'], limit)}" for i, e in enumerate(found, 1)]

def finish_check_text(tried):
    return (f"Before finishing, {check_parts(tried)} If any part is 'not covered', do not finish: choose the action that "
            f"works on that part ({GIVE_UP_RULE}) If every other part is covered, repeat FINISH with {FINAL_RESULT} "
            "in the parameter.")

def unread_note(page_state, current_url):
    # On a how-to the agent finished after 3 of 6 parts of the recipe, missing the amounts on the unread parts
    parts = page_state.get("parts") or []
    if not (current_url and page_state.get("url") == normalize_url(current_url) and parts):
        return ""
    unread = len(parts) - len(page_state.get("seen_parts", {0}))
    if unread > LONG_PAGE_UNREAD:
        # A bare count pushed the model to read every remaining part in order, including ones about other topics
        return (f" The current page still has {unread} unread parts: use its outline to judge whether any of them "
                "is relevant before reading them.")
    return f" The current page still has {unread} unread part(s) (READ_MORE)." if unread > 0 else ""

def source_note(journal):
    # Facts, not a rule: some objectives need one source and some need many, so the model is only shown how
    # many pages its discoveries came from (it judged one blog as covering "traditions around the world")
    sources = []
    for e in journal:
        if e.get("action") == "DISCOVER":
            source = normalize_url(e.get("source") or "") or "an unknown page"
            if source not in sources:
                sources.append(source)
    found = sum(1 for e in journal if e.get("action") == "DISCOVER")
    if not found:
        return "You have no discoveries yet."
    if found == 1:
        return f"Your only discovery comes from {sources[0]}. Consider whether the objective needs more."
    if len(sources) == 1:
        return (f"All {found} of your discoveries come from a single page ({sources[0]}). "
                "Consider whether the objective needs other sources.")
    return f"Your {found} discoveries come from {len(sources)} different pages."

def action_options(page_state, current_url):
    on_page = current_url and page_state["url"] == normalize_url(current_url) and page_state["parts"]
    if on_page and page_state["index"] < len(page_state["parts"]) - 1:
        read_more = f"READ_MORE to see part {page_state['index'] + 2} of {len(page_state['parts'])} of this page"
    elif on_page:
        read_more = "READ_MORE with a part number to revisit an earlier part (you are on the last part of this page)"
    else:
        read_more = "READ_MORE is not available here"
    return (f"- {read_more}\n"
            f"- NAVIGATE to another page: a new link, or an earlier page that still has information you need\n"
            f"- SEARCH with a new query\n"
            f"- FINISH if DISCOVERIES SO FAR already satisfies the objective")

def refusal_correction(action, reason, page_state, current_url):
    # Listing the alternatives on the first refusal stops the model resubmitting the same action
    return (f"REFUSED: {reason} Do NOT choose {action} with the same or similar parameter again. Choose a different action:\n"
            f"{action_options(page_state, current_url)}\nReply with only the JSON object.")

def escalation_message(refusals, last_action, last_reason, page_state, current_url):
    return (f"Your last {refusals} actions were all refused. Do NOT choose {last_action} again with the same or similar parameter. "
            f"Last refusal reason: {last_reason} Choose a different action:\n"
            f"{action_options(page_state, current_url)}")
