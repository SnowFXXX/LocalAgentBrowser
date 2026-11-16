"""Checked arithmetic on dates: ages and lengths of time worked out by the code from the dates the discoveries give.

The number check cuts every number no discovery contains, and in two cloud SimpleQA runs it cut three right answers
each time: an age at death (80), a tenure (1 year and 161 days) and an age at a first publication (34), each worked
out from dates a page gave. The model is not trusted to do the sum (Phi-4's draft for the tenure counted 30-day
months and got 158 days); the code does it, shows the model the result with its working, and the number check
accepts the numbers it worked out, and no others."""
import datetime
import re

MONTHS = {m: i for i, m in enumerate(["january", "february", "march", "april", "may", "june", "july", "august",
                                      "september", "october", "november", "december"], start=1)}
MONTH = r"(January|February|March|April|May|June|July|August|September|October|November|December)"
DAY_FIRST = re.compile(rf"\b(\d{{1,2}})(?:st|nd|rd|th)?\s+(?:of\s+)?{MONTH},?\s+(\d{{4}})\b", re.I)
MONTH_FIRST = re.compile(rf"\b{MONTH}\s+(\d{{1,2}})(?:st|nd|rd|th)?,?\s+(\d{{4}})\b", re.I)
ISO = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
YEAR = re.compile(r"(?<![\d,.])(1[0-9]{3}|20[0-9]{2})(?![\d,]|\.\d)")

# Questions about a span of time: "How old was ...", "the length of ...'s tenure ... in years and days"
SPAN_QUESTION = re.compile(r"\bhow (?:old|long)\b|\bages?\b|\blength\b|\bduration\b|\btenure\b|\bhow many (?:years|"
                           r"months|days)\b|\bin years\b", re.I)
MAX_DATES = 8  # Dates taken from the discoveries, in order; more pairs would make chance matches likely

def dates_in(text):
    """(label, date or None, year) for each date in the text, in order: a full date, or a year standing alone."""
    found, taken = [], []
    for pattern, order in ((DAY_FIRST, "dmy"), (MONTH_FIRST, "mdy"), (ISO, "ymd")):
        for m in pattern.finditer(text):
            parts = dict(zip(order, m.groups(), strict=True))
            month = MONTHS.get(parts["m"].lower()) if not parts["m"].isdigit() else int(parts["m"])
            try:
                date = datetime.date(int(parts["y"]), month, int(parts["d"]))
            except (TypeError, ValueError):
                continue
            found.append((m.start(), m.group(), date, date.year))
            taken.append(range(m.start(), m.end()))
    for m in YEAR.finditer(text):
        if not any(m.start() in span for span in taken):
            found.append((m.start(), m.group(), None, int(m.group())))
    return [(label, date, year) for _, label, date, year in sorted(found)]

def whole_years(start, end):
    """Whole years from one date to another, and the days left over after the last anniversary."""
    years = end.year - start.year - ((end.month, end.day) < (start.month, start.day))
    try:
        anniversary = start.replace(year=start.year + years)
    except ValueError:  # 29 February in a year without one
        anniversary = start.replace(year=start.year + years, day=28)
    return years, (end - anniversary).days

def date_spans(discoveries, objective):
    """[(working, numbers)] for the spans between the dates the discoveries give, when the objective asks for an age
    or a length of time; numbers are (number, unit) pairs the number check then accepts."""
    if not SPAN_QUESTION.search(str(objective)):
        return []
    dates, seen = [], set()
    for i, e in enumerate(discoveries, start=1):
        for label, date, year in dates_in(e["extracted_data"]):
            if (date or year) not in seen:
                seen.add(date or year)
                dates.append((f"{label} [{i}]", date, year))
    # A year with a full date of its own in the discoveries says nothing more
    dates = [d for d in dates if d[1] or not any(o[1] and o[2] == d[2] for o in dates)][:MAX_DATES]
    spans = []
    for a in range(len(dates)):
        for b in range(a + 1, len(dates)):
            (la, da, ya), (lb, db, yb) = sorted((dates[a], dates[b]), key=lambda d: (d[2], d[1] or datetime.date(d[2], 1, 1)))
            if ya == yb:
                continue
            if da and db:
                years, days = whole_years(da, db)
                total = (db - da).days
                spans.append((f"from {la} to {lb}: {years} year{'s' if years != 1 else ''} and {days} day"
                              f"{'s' if days != 1 else ''} ({total:,} days in all)",
                              {(str(years), "year"), (str(days), "day"), (str(total), "day")}))
            else:
                # Without both full dates the anniversary may or may not have passed: both counts are given
                years = yb - ya
                spans.append((f"from {la} to {lb}: {years - 1} or {years} years (a full date for both is not given)",
                              {(str(years - 1), "year"), (str(years), "year")}))
    return spans

def span_numbers(discoveries, objective):
    return set().union(*(numbers for _, numbers in date_spans(discoveries, objective))) if discoveries else set()

def span_section(discoveries, objective):
    """The spans for the final result's prompt, or "" when there are none."""
    spans = date_spans(discoveries, objective)
    if not spans:
        return ""
    return ("### WORKED OUT FROM THE DATES IN THE DISCOVERIES (calculated by the code, exactly: use these numbers and "
            "do not work out your own)\n" + "\n".join(f"- {working}" for working, _ in spans) + "\n\n")

def span_working(text, discoveries, objective):
    """The code's working for each span whose numbers the result gives, as a note under the result, or ""."""
    given = re.sub(r"(?<=\d),(?=\d{3})", "", str(text))
    used = [working for working, numbers in date_spans(discoveries, objective)
            if any(re.search(rf"(?<![\d.]){n}\s+{unit}s?\b", given) or
                   (unit == "year" and re.search(rf"\b(?:age|aged)\s+(?:of\s+)?{n}\b|(?<![\d.]){n}\s*years?[- ]old\b", given))
                   for n, unit in numbers)]
    return ("Worked out by the code from the dates in the discoveries: " + "; ".join(used) + ".") if used else ""
