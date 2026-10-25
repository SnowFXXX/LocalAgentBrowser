"""Decisions refused or confirmed before they are carried out (leaving, reading on, re-reading, finishing, repeated
searches, discoveries not on the page) and the notes that go with them."""
from .config import GAP_REFUSAL_LIMIT, LONG_PAGE_UNREAD, MAX_CHECK_DISCOVERY_CHARS, MAX_VISITS_PER_URL
from .prompts import FINAL_RESULT, GIVE_UP_RULE
from .text import shorten
from .pages import determine_url, normalize_query, normalize_url, outline_pointer, read_more_target, search_query, where_you_are
from .grounding import discovery_lines, grounding_check, is_known_discovery
from .checklist import check_parts, every_item_has_discoveries, open_items

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
            return "BLOCKED_DISCOVER", "The DISCOVER parameter was empty. Put the actual information in the parameter."
        if is_known_discovery(parameter, journal):
            # The model has weighed this part: without this, the read-on check asked it to DISCOVER again
            # right after this refusal, and it repeated the duplicate until the turn was lost
            page_state.setdefault("read_on_confirmed", set()).add(page_state.get("index", 0))
            return "DUPLICATE_DISCOVER", "Everything in that DISCOVER is already recorded in DISCOVERIES SO FAR."
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
    return "CONFIRM_FINISH", (f"{source_note(journal)}{unread_note(page_state or {}, current_url)}"
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
