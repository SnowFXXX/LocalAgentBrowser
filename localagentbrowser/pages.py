"""Reading the web: search results, page text split into parts, the outline and its pointer to the part an open
checklist item needs, links, and what the model is shown of a page."""
import asyncio
import re
import urllib.parse
from .config import CONTENT_LINK_WORDS, MAX_LINKS, MAX_OUTLINE_LABEL, MAX_OUTLINE_LINES, MAX_SEARCH_RESULTS, MAX_SNIPPET_CHARS, OUTLINE_MIN_PARTS, PAGE_PART_CHARS, SEARCH_URL
from .text import MISSING_STOPWORDS, OPINION_REQUEST

BLOCK_START = "[[[BLOCK_START {}]]]"

BLOCK_END = "[[[BLOCK_END]]]"

BLOCK_PATTERN = re.compile(r'\[\[\[BLOCK_START (\w+)\]\]\](.*?)\[\[\[BLOCK_END\]\]\]', re.S)

HEADING_MARK = "[[[H]]]"

# Hides navigation menus, footers and cookie/consent banners, wraps code blocks and tables in markers
# so they are never split, marks headings for the page outline, reads the visible text, then restores
# the page exactly as it was.
PAGE_TEXT_JS = r"""([blockStart, blockEnd, headingMark]) => {
    const clutter = new Set();
    document.querySelectorAll('nav, footer, [role=navigation], [role=contentinfo]').forEach(el => clutter.add(el));
    document.querySelectorAll('#onetrust-banner-sdk, #onetrust-consent-sdk, #CybotCookiebotDialog, #usercentrics-root, .qc-cmp2-container, .fc-consent-root, #truste-consent-track').forEach(el => clutter.add(el));
    // Site notices and fundraising banners: Wikipedia's filled the whole first part of an article
    document.querySelectorAll('#siteNotice, #centralNotice, .cn-fundraising, .frb').forEach(el => clutter.add(el));
    const consentName = /cookie|consent|gdpr/i;
    document.querySelectorAll('body *').forEach(el => {
        const name = (el.id || '') + ' ' + (typeof el.className === 'string' ? el.className : '');
        if (!consentName.test(name)) return;
        const pos = getComputedStyle(el).position;
        if (pos === 'fixed' || pos === 'sticky' || el.getAttribute('role') === 'dialog' || el.getAttribute('aria-modal') === 'true') clutter.add(el);
    });

    const fullText = document.body ? document.body.innerText : '';
    const hidden = [];
    const markers = [];
    try {
        clutter.forEach(el => {
            hidden.push([el, el.style.getPropertyValue('display'), el.style.getPropertyPriority('display')]);
            el.style.setProperty('display', 'none', 'important');
        });
        document.querySelectorAll('pre, table').forEach(el => {
            if (el.parentElement && el.parentElement.closest('pre, table')) return;  // Only mark the outermost block
            const start = document.createTextNode(blockStart.replace('{}', el.tagName.toLowerCase()));
            const end = document.createTextNode(blockEnd);
            el.parentNode.insertBefore(start, el);
            el.parentNode.insertBefore(end, el.nextSibling);
            markers.push(start, end);
        });
        document.querySelectorAll('h1, h2, h3, h4').forEach(el => {
            if (el.closest('pre, table')) return;
            const mark = document.createTextNode(headingMark);
            el.insertBefore(mark, el.firstChild);
            markers.push(mark);
        });
        let text = document.body ? document.body.innerText : '';
        // Safety net: if hiding clutter removed most of the page, the "clutter" was the content
        if (text.length < fullText.length * 0.2) {
            hidden.forEach(([el, value, priority]) => el.style.setProperty('display', value, priority));
            hidden.length = 0;
            text = document.body.innerText;
        }
        return text;
    } finally {
        markers.forEach(node => node.remove());
        hidden.forEach(([el, value, priority]) => {
            if (value) el.style.setProperty('display', value, priority); else el.style.removeProperty('display');
        });
    }
}"""

def normalize_url(url):
    return url.split("#")[0].rstrip("/") if url else url

def normalize_query(query):
    return " ".join(str(query).lower().split())

def determine_url(action, parameter):
    if isinstance(parameter, list):
        clean_param = str(parameter[0]) if len(parameter) > 0 else ""
    else:
        clean_param = str(parameter)

    clean_param = clean_param.strip().strip('"').strip("'")

    if action == "NAVIGATE":
        if "#" in clean_param:
            clean_param = clean_param.split("#")[0]

        if clean_param.startswith(('http://', 'https://')):
            return clean_param
        return f"https://{clean_param}"
    elif action == "SEARCH":
        return f"{SEARCH_URL}{urllib.parse.quote_plus(search_query(clean_param))}"
    return None

def search_query(parameter):
    # If the model hands back a full search engine URL, pull the query back out of it
    clean_param = str(parameter).strip().strip('"').strip("'")
    if clean_param.startswith(('http://', 'https://')) and ("duckduckgo.com" in clean_param or "google.com/search" in clean_param):
        query_dict = urllib.parse.parse_qs(urllib.parse.urlparse(clean_param).query)
        if 'q' in query_dict:
            clean_param = query_dict['q'][0]
    return clean_param.replace('+', ' ')

def decode_ddg_link(href):
    # DuckDuckGo wraps result links as //duckduckgo.com/l/?uddg=<encoded-target>&rut=...
    if not href:
        return ""
    full = urllib.parse.urljoin("https://duckduckgo.com", href)
    parsed = urllib.parse.urlparse(full)
    if parsed.netloc.endswith("duckduckgo.com") and parsed.path.startswith("/l/"):
        target = urllib.parse.parse_qs(parsed.query).get("uddg")
        if target:
            return target[0]
    return full

async def count_search_results(page):
    try:
        return await page.locator(".result:not(.result--ad) a.result__a").count()
    except Exception:
        return 0

async def extract_search_results(page, visited_urls):
    results = await page.evaluate("""() => Array.from(document.querySelectorAll('.result:not(.result--ad)')).map(r => {
        const a = r.querySelector('a.result__a');
        const s = r.querySelector('.result__snippet');
        return a ? {title: a.innerText.trim(), href: a.getAttribute('href'), snippet: s ? s.innerText.trim() : ''} : null;
    }).filter(Boolean)""")

    lines = []
    for i, r in enumerate(results[:MAX_SEARCH_RESULTS], start=1):
        url = decode_ddg_link(r['href'])
        visited = " (ALREADY VISITED)" if normalize_url(url) in visited_urls else ""
        snippet = r['snippet'][:MAX_SNIPPET_CHARS]
        lines.append(f"[{i}] {r['title']}{visited}\n    URL: {url}\n    {snippet}")
    return "\n\n".join(lines)

def text_units(text):
    """Split page text into units that must stay whole: code blocks, tables, and paragraphs."""
    units = []
    def paragraphs(chunk):
        return [p.strip() for p in re.split(r'\n\s*\n', chunk) if p.strip()]

    pos = 0
    for m in BLOCK_PATTERN.finditer(text):
        units += paragraphs(text[pos:m.start()])
        body = m.group(2).strip("\n")
        if body.strip():
            units.append(f"```\n{body}\n```" if m.group(1) == "pre" else body)
        pos = m.end()
    units += paragraphs(text[pos:])
    return units

def split_oversized(unit, limit):
    """Last resort for a single unit bigger than a whole part: split at line, then sentence, boundaries."""
    if len(unit) <= limit:
        return [unit]
    fenced = unit.startswith("```\n") and unit.endswith("\n```")
    body = unit[4:-4] if fenced else unit
    budget = limit - 40 if fenced else limit

    pieces, current = [], ""
    lines = body.split("\n") if "\n" in body else re.split(r'(?<=[.!?])\s+', body)
    joiner = "\n" if "\n" in body else " "
    for line in lines:
        while len(line) > budget:  # A single line longer than a part: hard cut is unavoidable
            if current:
                pieces.append(current)
                current = ""
            pieces.append(line[:budget])
            line = line[budget:]
        if current and len(current) + len(joiner) + len(line) > budget:
            pieces.append(current)
            current = line
        else:
            current = f"{current}{joiner}{line}" if current else line
    if current:
        pieces.append(current)

    if fenced:
        return [f"```\n{p}\n```" + ("\n(code block continues in next part)" if i < len(pieces) - 1 else "") for i, p in enumerate(pieces)]
    return pieces

def split_into_parts(text, limit=PAGE_PART_CHARS):
    parts, current = [], ""
    for unit in text_units(text):
        for piece in split_oversized(unit, limit):
            if current and len(current) + 2 + len(piece) > limit:
                parts.append(current)
                current = piece
            else:
                current = f"{current}\n\n{piece}" if current else piece
    if current:
        parts.append(current)
    return parts

async def extract_page_parts(page):
    """Returns the page split into parts, and an outline label for each part."""
    text = await page.evaluate(PAGE_TEXT_JS, [BLOCK_START, BLOCK_END, HEADING_MARK])
    return build_outline(split_into_parts(text or ""))

def build_outline(parts):
    """Strips the heading markers and labels each part with its headings (or its first line if it has none)."""
    clean, outline = [], []
    for part in parts:
        headings = [" ".join(h.split()) for h in re.findall(re.escape(HEADING_MARK) + r"([^\n]*)", part) if h.strip()]
        text = part.replace(HEADING_MARK, "")
        if headings:
            label = " / ".join(headings)
        else:
            # First line with real text, skipping code-fence lines (```)
            label = next((line.strip() for line in text.splitlines() if line.strip().strip("`").strip()), "(no text)")
        clean.append(text)
        outline.append(label if len(label) <= MAX_OUTLINE_LABEL else label[:MAX_OUTLINE_LABEL] + "...")
    return clean, outline

async def extract_page_links(page, shown_links=None):
    # Links come from the whole page, menus included, since navigation menus are how sites are used
    # a.href resolves relative links (e.g. /docs/intro) to absolute URLs
    # shown_links holds links already listed earlier in the running conversation; they are skipped so a
    # new page on the same site lists its own links instead of repeating the site menu
    links = await page.evaluate("""() => Array.from(document.querySelectorAll('a[href]'))
        .map(a => ({text: a.innerText.trim().replace(/\\s+/g, ' '), href: a.href.split('#')[0]}))
        .filter(l => l.href.startsWith('http') && l.text.length >= 4)""")
    seen = set()
    link_lines = []
    omitted = 0
    # Links with a few words of text (article titles, "Next: ...") before short ones (menu items such as "GUIDES"),
    # each group in page order: the list is capped, and menus come first on most pages. In 4 regression suites the
    # agent opened 39 pages, all from search results and none from page links, while the links were more than half
    # of every new page it was shown
    links = sorted(links, key=lambda link: len(link["text"].split()) < CONTENT_LINK_WORDS)
    for link in links:
        if link["href"] in seen or normalize_url(link["href"]) == normalize_url(page.url):
            continue
        seen.add(link["href"])
        # Image links whose text is the image's HTML ('<img src="...') say nothing and took about a third of a
        # site's link list (shop links, mostly)
        if link["text"].startswith("<"):
            continue
        if shown_links is not None and link["href"] in shown_links:
            omitted += 1
            continue
        if len(link_lines) < MAX_LINKS:
            link_lines.append(f"- {link['text'][:60]} -> {link['href']}")
            if shown_links is not None:
                shown_links.add(link["href"])
    if omitted:
        link_lines.append(f"({omitted} links already listed earlier in this conversation are not repeated)")
    return "\n".join(link_lines)

async def build_environment(page, current_url, page_state, visited_urls=frozenset(), shown_links=None):
    if not current_url:
        return "[Empty or No Target Page Loaded Yet]"
    try:
        if "duckduckgo.com" in current_url:
            results = await extract_search_results(page, visited_urls)
            if results:
                return f"Search Results:\n{results}"
            parts, _ = await extract_page_parts(page)
            return f"[No search results parsed - possibly no matches or a search engine error]\n{parts[0][:1500] if parts else ''}"

        # Parts are cached per URL so part numbers stay stable while the model reads through a page
        if page_state["url"] != normalize_url(current_url):
            parts, outline = await extract_page_parts(page)
            page_state.update(url=normalize_url(current_url), parts=parts, outline=outline, index=0,
                              seen_parts={0}, visit_discoveries=0, leave_confirmed=False, reread_confirmed=set(),
                              read_on_confirmed=set(), grounding_asked=[],
                              # Kept per URL: on a return visit the read-on check asked about a part already recorded
                              recorded_parts=page_state.setdefault("recorded_by_url", {}).setdefault(
                                  normalize_url(current_url), set()))
        link_lines = await extract_page_links(page, shown_links)
        return f"{render_part(page_state, with_outline=True)}\n\nLinks On Page:\n{link_lines or '[None]'}"
    except Exception as e:
        return f"[Error reading page content: {e}]"

def render_part(page_state, with_outline=False):
    parts, index = page_state["parts"], page_state["index"]
    if not parts:
        return "Page Text:\n[No readable text]"
    if index < len(parts) - 1:
        # A part ending "Just follow these 6 easy steps" led the model to write the steps itself
        footer = (f"[Page continues: READ_MORE shows part {index + 2} of {len(parts)}. "
                  "What comes next on the page has not been shown to you yet.]")
    else:
        footer = "[End of page]"
    text = f"Page Text (part {index + 1} of {len(parts)}):\n{parts[index]}\n{footer}"
    if with_outline:
        text = f"{render_outline(page_state)}{text}"
    return text

def render_outline(page_state):
    # On a long page the model read part after part in order (part 4 of 33, nothing new) because it could
    # not see where anything was; the outline lets it jump to the part it needs, like skimming headings
    outline = page_state.get("outline") or []
    if len(outline) < OUTLINE_MIN_PARTS:
        return ""
    # Read parts are marked: after a fresh prompt the model no longer sees its earlier reading and went back
    # to re-read parts it had read half an hour before
    seen = page_state.get("seen_parts") or set()
    lines = [f"  {i}. {label}{' (read)' if i - 1 in seen else ''}"
             for i, label in enumerate(outline[:MAX_OUTLINE_LINES], start=1)]
    if len(outline) > MAX_OUTLINE_LINES:
        lines.append(f"  ... ({len(outline) - MAX_OUTLINE_LINES} more parts)")
    return (f"Page Outline ({len(outline)} parts; READ_MORE with a part number jumps straight to that part):\n"
            + "\n".join(lines) + "\n\n")

def read_more_target(parameter, page_state):
    # The model sometimes writes "2 of 2" or "part 3"; the first number is the part it means
    number = re.search(r'\d+', parameter)
    target = int(number.group()) - 1 if number else page_state["index"] + 1
    # Told by the read-on check to read the outline part that names an open item, the model repeated a plain
    # READ_MORE for the next part in 2 to 3 of 6 replays; a plain READ_MORE after that advice reads the named part
    pointed = page_state.get("pointed")
    if not number and pointed and pointed[0] == (page_state.get("url"), page_state["index"]):
        target = pointed[1]
    # Asking for the part already shown nearly always meant the next one ("READ_MORE 9" while on part 9 came up
    # in most long-page runs, and refusing it cost a retry each time), so it is read as that, except on the last part
    if target == page_state["index"] and target < len(page_state["parts"]) - 1:
        target += 1
    return target

# Words that say what to do with an item, not what it is about ("Compare ... features" matched "How do they compare")
ITEM_VERBS = {"compare", "find", "explain", "describe", "detail", "list", "give", "name", "say", "what", "which", "each",
              "about", "into", "does", "make"}

def outline_words(text):
    # Hyphenated words stay whole: "open-source" split into "open" matched "Can I open and edit ..." in an FAQ
    return re.findall(r"[a-z0-9]+(?:-[a-z0-9]+)*", str(text).lower())

def outline_word_parts(outline):
    """For each word in the outline labels, the part numbers (1-based) whose label has it."""
    parts = {}
    for number, label in enumerate(outline, 1):
        for word in set(outline_words(label)):
            parts.setdefault(word.rstrip("s"), set()).add(number)
    return parts

def outline_pointer(page_state, checklist):
    """The first unread part whose outline label names a word of an open checklist item, as (part, word), or None.

    Told to use the outline to reach an open item, the model still read the next part 6 of 6 times on a 10-part
    comparison page whose outline named "Administration" and "Features" in parts 5 and 6. Words in many labels
    ("SQLite", "PostgreSQL" in every heading of that page) say nothing about where an item is, so only words in
    at most a third of the labels count.
    """
    outline = page_state.get("outline") or []
    if len(outline) < OUTLINE_MIN_PARTS:
        return None
    seen = page_state.get("seen_parts") or set()
    parts = page_state.get("parts") or []
    where = outline_word_parts(outline)
    # Topic words point anywhere: the page's own title (its first label: "photosynthesis" on the Photosynthesis
    # article, "free" on a page of free office suites) and words every item shares ("SQLite" in each item of a
    # comparison) say what the whole page is about, not where one item is
    to_find = [item for item in checklist or [] if not OPINION_REQUEST.search(item["text"])]
    topic = {w.rstrip("s") for w in outline_words(outline[0])}
    if len(to_find) > 1:
        topic |= set.intersection(*({w.rstrip("s") for w in outline_words(item["text"])} for item in to_find))
    for item in to_find:
        if item["discoveries"]:
            continue
        for word in outline_words(item["text"]):
            key = word.rstrip("s")
            if len(key) < 4 or word in MISSING_STOPWORDS or word in ITEM_VERBS or key in topic:
                continue
            # A part that starts inside code is labelled by its first code line, not a heading: on the venv docs,
            # "Install pip in the virtual environment." was a docstring in the module's source
            numbers = sorted(n for n in where.get(key, ()) if n - 1 not in seen
                             and not str(parts[n - 1] if n <= len(parts) else "").lstrip().startswith("```"))
            if numbers and len(where[key]) <= len(outline) / 3:
                return numbers[0], word
    return None

def where_you_are(page_state, current_url):
    # With several pages in one conversation the model lost track of which one was current (it paged through
    # a 3-part article asking for "part 3 of 4" of a page it had left), so notes and refusals always say so
    if not current_url:
        return "No page is open yet."
    if page_state["url"] == normalize_url(current_url) and page_state["parts"]:
        return f"You are on {current_url} (part {page_state['index'] + 1} of {len(page_state['parts'])})."
    return f"You are on {current_url}."

async def load_page(page, url=None):
    """Loads a URL (or reloads the page) and returns Playwright's response, which may be None."""
    if url:
        response = await page.goto(url, wait_until="domcontentloaded", timeout=30000)
    else:
        response = await page.reload(wait_until="domcontentloaded", timeout=30000)
    try:
        await page.wait_for_load_state("networkidle", timeout=5000)
    except Exception:
        pass  # Busy pages rarely go fully idle; the DOM is already usable
    await asyncio.sleep(1)
    return response
