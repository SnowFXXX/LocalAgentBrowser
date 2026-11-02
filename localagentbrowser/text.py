"""Small text helpers shared by the checks: words, word pairs, counts asked for, shortening, and reading a model reply."""
import re

OPINION_REQUEST = re.compile(r"\b(opinion|your (own )?view|recommend\w*|what do you think)\b", re.I)

def word_pairs(text):
    words = re.findall(r"\w+", text)
    return {tuple(words[i:i + 2]) for i in range(len(words) - 1)} or ({tuple(words)} if words else set())

NUMBER_WORDS = {"two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10}

MEASURE_WORDS = {"second", "seconds", "minute", "minutes", "hour", "hours", "day", "days", "week", "weeks", "month",
                 "months", "year", "years", "cup", "cups", "times", "percent"}

def asked_count(text):
    """The number of things an item asks for ("three ...", "5 ..."), or None. "At least three" counts as three.

    A number of time units or amounts ("the first two weeks", "2 cups") is not a number of things to find."""
    words = plain_words(text)
    for word, after in zip(words, words[1:] + [""], strict=True):
        if after in MEASURE_WORDS:
            continue
        if word in NUMBER_WORDS:
            return NUMBER_WORDS[word]
        if word.isdigit() and 2 <= int(word) <= 20:
            return int(word)
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
    return first_choice.get("message", {}).get("content", "").strip()

def was_cut_off(response):
    """True when the reply stopped at its max_tokens limit instead of ending by itself."""
    choices = getattr(response, "choices", None) or []
    first_choice = choices[0] if choices else None
    return getattr(first_choice, "finish_reason", None) == "length"

def shorten(text, limit):
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[:limit].rstrip() + "..."
