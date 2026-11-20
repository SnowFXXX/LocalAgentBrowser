"""Small text helpers shared by the checks: words, word pairs, counts asked for, shortening, and reading a model reply."""
import re

OPINION_REQUEST = re.compile(r"\b(opinion|your (own )?view|recommend\w*|what do you think)\b", re.I)

def word_pairs(text):
    words = re.findall(r"\w+", text)
    return {tuple(words[i:i + 2]) for i in range(len(words) - 1)} or ({tuple(words)} if words else set())

NUMBER_WORDS = {"two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10}

# Small numbers written out, read as digits: a page's "one cup of beans steeped in four cups of water" was refused
# when the model wrote it "1 cup ... 4 cups", the retry dropped the amounts, and the result said none were found.
# "one of the best" is not an amount, so a word followed by "of" stays a word
SPELLED_NUMBERS = {"one": 1, **NUMBER_WORDS, "eleven": 11, "twelve": 12, "fifteen": 15, "twenty": 20, "thirty": 30}
SPELLED_NUMBER = re.compile(r"\b(" + "|".join(SPELLED_NUMBERS) + r")\b(?!\s+of\b)", re.I)

def with_digits(text):
    return SPELLED_NUMBER.sub(lambda m: str(SPELLED_NUMBERS[m.group(1).lower()]), str(text))

MEASURE_WORDS = {"second", "seconds", "minute", "minutes", "hour", "hours", "day", "days", "week", "weeks", "month",
                 "months", "year", "years", "cup", "cups", "times", "percent"}

# The words a count of things to find follows ("Name three ...", "at least three ...", "What two football teams ...",
# "the two streets"), or the item's first word. A number after anything else is not a count: it names or dates
# something ("track 10", "Episode 12, Season 3", "Rhythm 2", "February 6, 2022", "at 16", "Six Flags", "1905-06")
COUNT_LEADS = {"name", "list", "find", "give", "least", "most", "the", "top", "these", "those", "exactly", "about",
               "suggest", "recommend", "identify", "provide", "show", "describe", "explain", "compare", "pick", "choose",
               "what", "which", "all", "only", "me", "us", "summarize", "summarise", "outline", "mention", "gather",
               "collect", "rank", "select", "offer", "propose", "are", "were", "her", "his", "their", "its", "first",
               "main", "key", "other", "any", "some", "and", "or", "with", "contain", "include", "including"}

def asked_count(text):
    """The number of things an item asks for ("three ...", "5 ..."), or None. "At least three" counts as three.

    A number of time units or amounts ("the first two weeks", "2 cups") is not a number of things to find. Nor is a
    number in a name: "the Yamaha SHS-10" was read as ten things asked for, and a right answer ended "nine more (only
    one of the ten were found)". A word joined to others by a hyphen, a dot or a slash stays one word here. And a
    count comes after a word that leads to one (COUNT_LEADS) or opens the item: in the 200-question SimpleQA run,
    "track 10 on disc 2", "left school at 16" and "Episode 12, Season 3" were read as counts, and 10 right answers
    ended with a garbled note such as "ten more (only none of the ten were found)"."""
    tokens = re.findall(r"[A-Za-z0-9]+(?:[-./][A-Za-z0-9]+)*", str(text))
    words = [t.lower() for t in tokens]
    for i, (word, after) in enumerate(zip(words, words[1:] + [""], strict=True)):
        if after in MEASURE_WORDS:
            continue
        value = NUMBER_WORDS.get(word) or (int(word) if word.isdigit() and 2 <= int(word) <= 20 else None)
        if value is None:
            continue
        # A number word written with a capital inside the item is part of a name ("Six Flags", "Two Rivers")
        if i > 0 and word in NUMBER_WORDS and tokens[i][0].isupper():
            continue
        if i == 0 or words[i - 1] in COUNT_LEADS:
            return value
    return None

def plain_words(text):
    return re.findall(r"[a-z0-9]+", str(text).lower())

MISSING_STOPWORDS = {"the", "and", "or", "of", "from", "in", "on", "at", "to", "a", "an", "its", "their", "for", "with",
                     "is", "are", "not", "mentioned", "specific", "exact", "details", "detail", "information"}

def extract_message_content(response):
    choices = getattr(response, "choices", None)
    if choices is None and isinstance(response, dict):
        choices = response.get("choices", [])

    if isinstance(choices, list) and len(choices) > 0:
        first_choice = choices[0]
    else:
        first_choice = choices

    if hasattr(first_choice, "message"):
        return (first_choice.message.content or "").strip()
    elif isinstance(first_choice, dict) and "message" in first_choice:
        msg = first_choice["message"]
        return msg.get("content", "").strip() if isinstance(msg, dict) else msg.content.strip()
    # A reply with no choice in it (a failing provider's answer): no text, not a crash
    return first_choice.get("message", {}).get("content", "").strip() if isinstance(first_choice, dict) else ""

def was_cut_off(response):
    """True when the reply stopped at its max_tokens limit instead of ending by itself."""
    choices = getattr(response, "choices", None) or []
    first_choice = choices[0] if choices else None
    return getattr(first_choice, "finish_reason", None) == "length"

CONTROL_CHARACTERS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

def without_control_characters(text):
    """Text without control characters other than tabs and line breaks. A reply's JSON "\\b" (a regular expression's
    word boundary, written in a finding) is read as a backspace, which then sat in the finding and the result."""
    return CONTROL_CHARACTERS.sub("", str(text))

def shorten(text, limit):
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[:limit].rstrip() + "..."
