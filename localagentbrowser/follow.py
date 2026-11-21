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
# A discovery the model put in its own words (the checks accept one whose facts are on the page) is marked at the page
# sentence closest to it: one with every number the discovery gives and at least this share of its words
NEAREST_SHARE = 0.6
NEAREST_MAX_CHARS = 600  # Longer stretches without a full stop (lists, tables) are not taken for a sentence

# Finds, for each text, the page sentence sharing most of its words, with all of its numbers, and highlights it under
# the given name; scrolls to the first. Returns how many were found. (Seen live, Nov 21: Qwen2.5 7B recorded "Mount
# Kilimanjaro is the highest mountain in Africa, at an elevation of 5,895 m (19,341 ft) above sea level" from a page
# saying "It is the highest mountain in Africa and ... at 5,895 m (19,341 ft) above sea level", and nothing was marked.)
NEAREST_JS = r"""([texts, name, share, maxChars]) => { // labFollowNearest
  try {
    const skip = /^(SCRIPT|STYLE|NOSCRIPT|TEMPLATE)$/;
    const words = s => [...new Set((s.toLowerCase().match(/[\p{L}\p{N}](?:[\p{L}\p{N},.'-]*[\p{L}\p{N}])?/gu) || [])
      .filter(w => w.length > 2 || /\d/.test(w)))];
    const numbers = ws => ws.filter(w => /\d/.test(w));
    const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT,
      { acceptNode: n => n.parentElement && !skip.test(n.parentElement.tagName) && n.nodeValue.trim() ? 1 : 2 });
    let full = "", map = [];
    for (let n = walker.nextNode(); n; n = walker.nextNode()) {
      for (let i = 0; i < n.nodeValue.length; i++) { full += n.nodeValue[i]; map.push([n, i]); }
      full += " "; map.push(null);
    }
    const sentences = [];
    let start = 0;
    for (let i = 0; i <= full.length; i++) {
      if (i === full.length || (/[.!?]/.test(full[i]) && (i + 1 === full.length || /\s/.test(full[i + 1])))) {
        const text = full.slice(start, i + 1);
        if (text.trim().length > 0 && text.length <= maxChars) sentences.push([start, i, text]);
        start = i + 1;
      }
    }
    const ranges = [];
    for (const want of texts) {
      const ws = words(want), nums = numbers(ws);
      if (ws.length < 4) continue;
      let best = null;
      for (const [a, b, text] of sentences) {
        const have = new Set(words(text));
        if (!nums.every(n => have.has(n))) continue;
        const shared = ws.filter(w => have.has(w)).length / ws.length;
        if (shared >= share && (!best || shared > best[0] || (shared === best[0] && b - a < best[2] - best[1])))
          best = [shared, a, b];
      }
      if (!best) continue;
      let a = best[1], b = best[2];
      while (a <= b && (!map[a] || /\s/.test(full[a]))) a++;
      while (b >= a && (!map[b] || /\s/.test(full[b]))) b--;
      if (a > b) continue;
      const range = document.createRange();
      range.setStart(map[a][0], map[a][1]); range.setEnd(map[b][0], map[b][1] + 1);
      ranges.push(range);
    }
    if (!ranges.length) return 0;
    if (!document.labFollowSheet && window.CSSStyleSheet) {
      const sheet = new CSSStyleSheet();
      sheet.replaceSync("::highlight(lab-found){background-color:rgba(95,211,166,.42);color:inherit}" +
                        "::highlight(lab-reading){background-color:rgba(127,179,230,.3);color:inherit}");
      document.adoptedStyleSheets = [...document.adoptedStyleSheets, sheet];
      document.labFollowSheet = sheet;
    }
    if (window.CSS && CSS.highlights && window.Highlight) {
      const highlight = CSS.highlights.get(name) || new Highlight();
      ranges.forEach(r => highlight.add(r));
      CSS.highlights.set(name, highlight);
    }
    const box = ranges[0].getBoundingClientRect();
    window.scrollTo({ top: window.scrollY + box.top - window.innerHeight * 0.2, behavior: "smooth" });
    return ranges.length;
  } catch (err) { return 0; }
}"""

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
    sentences that is on the page, or failing that (a discovery in the model's own words) the page sentence closest
    to each."""
    if not enabled() or not text:
        return False
    whole, *sentences = quote_pieces(text)
    try:
        return (bool(await page.evaluate(FOLLOW_JS, [[whole], "lab-found", True, 0]))
                or bool(sentences and await page.evaluate(FOLLOW_JS, [sentences, "lab-found", True, 0]))
                or bool(await page.evaluate(NEAREST_JS, [sentences or [whole], "lab-found", NEAREST_SHARE,
                                                         NEAREST_MAX_CHARS])))
    except Exception:
        return False
