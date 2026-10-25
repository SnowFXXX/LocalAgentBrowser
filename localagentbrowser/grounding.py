"""Whether a DISCOVER is really on the page shown, and whether it is already recorded; the closest page sentence or the
names never mentioned, quoted when one is refused."""
import re
import unicodedata
from .config import GROUNDED_PAIR_SHARE, GROUNDED_SENTENCE_SHARE, KNOWN_LINE_THRESHOLD, MAX_CLOSEST_CHARS, MIN_GROUNDING_PAIRS, MIN_SENTENCE_PAIRS, UNGROUNDED_PAIR_SHARE, UNGROUNDED_SENTENCE_SHARE
from .text import plain_words, shorten, word_pairs
from .pages import normalize_url

QUOTE_STYLES = str.maketrans({"‘": "'", "’": "'", "“": "'", "”": "'", '"': "'", "`": "'"})

def discovery_lines(text):
    # Case, spacing and quote style (curly, straight, single, double) do not make a line new
    text = unicodedata.normalize("NFKC", str(text)).translate(QUOTE_STYLES)
    return {" ".join(line.lower().split()) for line in text.splitlines() if line.strip()}

def grounded_share(text, parts):
    """The share of a discovery's consecutive word pairs that also appear in the given page parts."""
    mine = word_pairs(str(text).lower())
    return len(mine & word_pairs(" ".join(parts).lower())) / len(mine) if mine else 1.0

def grounding_check(parameter, page_state, current_url, objective=""):
    # Shown part 2 of a guide, which ended "Just follow these 6 easy steps", the model recorded six steps it
    # wrote itself (the real ones were in part 3), then a care routine the page never gave. Real discoveries
    # from earlier runs shared 64-100% of their word pairs with the page; these shared 11-44%, and a rerun's
    # mix of real pot-size advice with invented steps shared 57%. A DISCOVER that matches the parts shown less
    # than that gets one cheap retry; repeating it then goes through, since a heavy paraphrase can score low too.
    parts = page_state.get("parts") or []
    if not (current_url and page_state.get("url") == normalize_url(current_url) and parts):
        return None
    # The objective's wording counts as well: "Mount Kilimanjaro is the tallest mountain in Africa..." echoed the
    # question's "tallest" where the page says "highest", and was sent back although it was accurate
    shown = [parts[i] for i in sorted(page_state.get("seen_parts", {0})) if i < len(parts)] + [objective]
    long_enough = len(word_pairs(parameter)) >= MIN_GROUNDING_PAIRS
    whole = grounded_share(parameter, shown) if long_enough else 1.0
    # An invented sentence can hide behind copied ones: an opinion task's discovery was 75% copied, but its last
    # sentence ("...depicted alongside other wise figures like the prophet Samuel") was not on the page (31%).
    # Real sentences scored 40-100% against their page, invented ones 0-41%.
    sentences = [s for s in re.split(r"(?<=[.!?])\s+|\n+", parameter) if len(word_pairs(s)) >= MIN_SENTENCE_PAIRS]
    lowest = min((grounded_share(s, shown) for s in sentences), default=1.0)
    if whole >= GROUNDED_PAIR_SHARE and lowest >= GROUNDED_SENTENCE_SHARE:
        return None
    # Far below anything real (whole discoveries 50%+, sentences 40%+), repeating does not confirm it: asked once,
    # the model sent its invented repotting steps again (32% overall, one step 9%) and they went through
    if whole < UNGROUNDED_PAIR_SHARE or lowest < UNGROUNDED_SENTENCE_SHARE:
        return "BLOCKED_NOT_ON_PAGE", ("That DISCOVER is mostly not in the page text you have been shown, so it comes "
                                       "from your own knowledge. DISCOVER only what the parts shown actually say, in the "
                                       f"page's own wording.{page_hint(parameter, shown[:-1], objective)} If what you need "
                                       "is not there, READ_MORE (a later part may have it) or look elsewhere.")
    # Only repeating the same DISCOVER confirms it: asked again, the model once sent a different text that was
    # still invented ("water sparingly", which the page never said), and a per-part pass let it through
    asked = page_state.setdefault("grounding_asked", [])
    mine = word_pairs(parameter.lower())
    if any(len(mine & earlier) >= 0.8 * len(mine) for earlier in asked):
        return None
    asked.append(mine)
    # Pointed to READ_MORE as well, the model left a part holding the real materials list and steps unrecorded
    return "CONFIRM_GROUNDED", (f"Some of that DISCOVER does not match the page text you have been shown, so part of it "
                                "seems to come from your own knowledge. DISCOVER again with only what the page actually "
                                f"says in part {page_state['index'] + 1} (shown now) or the parts before it, using the "
                                "page's own wording, for example its lists and steps. Do not add steps, amounts or "
                                "advice that the page does not give. If your DISCOVER really is what the page says, "
                                "repeat it.")

def closest_sentence(text, parts):
    """The page sentence sharing the most content words (4+ letters) with a text, or None below two."""
    mine = {w for w in plain_words(text) if len(w) > 3}
    # In page order, so equal scores always pick the same sentence (max keeps the first)
    sentences = list(dict.fromkeys(s.strip() for part in parts for s in re.split(r"(?<=[.!?])\s+|\n+", part)
                                   if len(s.split()) >= 4))
    score, best = max(((len(mine & {w for w in plain_words(s) if len(w) > 3}), s) for s in sentences),
                      key=lambda pair: pair[0], default=(0, None))
    return best if score >= 2 else None

def unseen_names(text, parts):
    """Names in a text (capitalised words not starting a sentence) that no part shown mentions."""
    seen = set(plain_words(" ".join(parts)))
    names = re.findall(r"(?<![.!?:]\s)(?<!^)\b([A-Z][a-z]{2,})", text.strip())
    return list(dict.fromkeys(n for n in names if n.lower() not in seen))

def page_hint(text, parts, objective=""):
    # Refused for an aftercare routine it wrote itself, the model read on past the page's one real line about it;
    # told to record the part the page gives, it still read on, and with two close sentences quoted it recorded one
    # half the time. One sentence, picked for the objective as well, was recorded 6 times out of 6, while a refusal
    # for steps that a later part held still led to READ_MORE 6 times out of 6. But on an opinion task the model had
    # written about Japan before the page said anything about it, and a quoted fragment ("This symbolism is
    # particularly prevalent...") was recorded instead of reading on: names the page never mentions mean the page
    # has not got there, so they are named instead
    missing = unseen_names(text, parts)
    if missing:
        return f" The parts shown never mention {', '.join(missing[:4])}."
    best = closest_sentence(f"{text} {objective}", parts)
    if not best:
        return ""
    return (f' What the page does say about it: "{shorten(best, MAX_CLOSEST_CHARS)}". If that gives part of what the '
            "OBJECTIVE asks for, DISCOVER exactly that now; what the page does not say is reported as not found at "
            "FINISH, never filled in.")

def is_known_discovery(parameter, journal):
    # Compared by consecutive word pairs, so line breaks, commas, punctuation and the order of lines do not
    # make old information look new. A line is known when 90% of its word pairs were already recorded;
    # the discovery is a repeat only when every line is known, so one genuinely new line keeps it.
    # A long line (8+ words) made entirely of recorded words is also known: it catches a block rejoined
    # after its recorded version had other lines (such as comments) between its parts. Short lines like
    # commands are judged by word pairs only, so a new command built from familiar words stays new.
    known_pairs, known_words = set(), set()
    for e in journal:
        if e.get("action") == "DISCOVER":
            lines = discovery_lines(e["extracted_data"])
            known_pairs |= word_pairs(" ".join(lines))  # Pairs across line breaks, for text later rejoined
            for line in lines:
                known_pairs |= word_pairs(line)
                known_words |= set(re.findall(r"\w+", line))

    def line_is_known(line):
        pairs, words = word_pairs(line), set(re.findall(r"\w+", line))
        if len(re.findall(r"\w+", line)) >= 8 and words <= known_words:
            return True
        return len(pairs & known_pairs) >= KNOWN_LINE_THRESHOLD * len(pairs)

    new_lines = [line for line in discovery_lines(parameter) if re.search(r"\w", line)]
    return bool(new_lines) and all(line_is_known(line) for line in new_lines)
