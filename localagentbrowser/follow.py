"""The agent's browser follows what the agent reads, for the person watching it: on READ_MORE the page scrolls to the
part now read and tints its first words for a moment, and each recorded DISCOVER is highlighted on the page in mint.

Nothing on the page changes. The highlights are the browser's own (the CSS Custom Highlight API, with a stylesheet
adopted by the document, not added to it), and the page's text was read when the page was opened (pages.py), so what
the agent reads is the same with or without them. A failure here never reaches the run, and LAB_FOLLOW=0 turns it off.
"""
import os
import re

FIRST_WORDS = 8          # The words of a part looked for on the page, to scroll to it
READING_TINT_MS = 2500   # How long the first words of the part read stay tinted
MIN_QUOTE_CHARS = 12     # Shorter sentences of a discovery are not looked for (they match too much)

# Finds the text on the page (spaces and case aside, so a paragraph break is no obstacle), highlights it under the
# given name, and scrolls to the first one found. Returns whether it found anything.
FOLLOW_JS = r"""([texts, name, scroll, tintMs]) => { // labFollow
  try {
    const squash = s => s.replace(/\s+/g, "").toLowerCase();
    const skip = /^(SCRIPT|STYLE|NOSCRIPT|TEMPLATE)$/;
    const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT,
      { acceptNode: n => n.parentElement && !skip.test(n.parentElement.tagName) ? 1 : 2 });
    let flat = "", map = [];
    for (let n = walker.nextNode(); n; n = walker.nextNode()) {
      const v = n.nodeValue;
      for (let i = 0; i < v.length; i++) if (!/\s/.test(v[i])) { flat += v[i].toLowerCase(); map.push([n, i]); }
    }
    if (!document.labFollowSheet && window.CSSStyleSheet) {
      const sheet = new CSSStyleSheet();
      sheet.replaceSync("::highlight(lab-found){background-color:rgba(95,211,166,.42);color:inherit}" +
                        "::highlight(lab-reading){background-color:rgba(127,179,230,.3);color:inherit}");
      document.adoptedStyleSheets = [...document.adoptedStyleSheets, sheet];
      document.labFollowSheet = sheet;
    }
    const ranges = [];
    for (const text of texts) {
      const want = squash(text);
      const at = want ? flat.indexOf(want) : -1;
      if (at < 0) continue;
      const [a, ai] = map[at], [b, bi] = map[at + want.length - 1];
      const range = document.createRange();
      range.setStart(a, ai); range.setEnd(b, bi + 1);
      ranges.push(range);
    }
    if (!ranges.length) return false;
    if (window.CSS && CSS.highlights && window.Highlight) {
      const highlight = CSS.highlights.get(name) || new Highlight();
      ranges.forEach(r => highlight.add(r));
      CSS.highlights.set(name, highlight);
      if (tintMs) setTimeout(() => ranges.forEach(r => highlight.delete(r)), tintMs);
    }
    if (scroll) {
      const box = ranges[0].getBoundingClientRect();
      window.scrollTo({ top: window.scrollY + box.top - window.innerHeight * 0.2, behavior: "smooth" });
    }
    return true;
  } catch (err) { return false; }
}"""


def enabled():
    return os.environ.get("LAB_FOLLOW", "1") != "0"


def first_words(text, count=FIRST_WORDS):
    return " ".join(str(text).split()[:count])


def quote_pieces(text):
    """The parts of a discovery to look for on the page: the whole text, then each sentence (a discovery may join
    sentences the page has apart, or add a heading of its own), without the heading the code adds in front."""
    text = re.sub(r'^\(Under the heading ".*?"\)\s*', "", str(text))
    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", text) if len(s.strip()) >= MIN_QUOTE_CHARS]
    return [text] + [s for s in sentences if s != text]


async def show_part(page, part_text):
    """Scrolls the page to the part the agent reads now, and tints its first words for a moment."""
    if not enabled() or not part_text:
        return False
    try:
        return bool(await page.evaluate(FOLLOW_JS, [[first_words(part_text)], "lab-reading", True, READING_TINT_MS]))
    except Exception:
        return False


async def show_discovery(page, text):
    """Highlights a recorded discovery on the page, and scrolls to it: the whole of it, or failing that each of its
    sentences that is on the page."""
    if not enabled() or not text:
        return False
    whole, *sentences = quote_pieces(text)
    try:
        return (bool(await page.evaluate(FOLLOW_JS, [[whole], "lab-found", True, 0]))
                or bool(sentences and await page.evaluate(FOLLOW_JS, [sentences, "lab-found", True, 0])))
    except Exception:
        return False
