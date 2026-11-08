"""Splitting page text into parts for READ_MORE without cutting through code blocks or tables, and
listing page links without repeating ones the model has already seen."""
import asyncio

import agent_browser as ab
from fakes import FakeLinksPage


def test_code_blocks_stay_whole_and_fenced():
    text = "Intro paragraph.\n\n" + ab.BLOCK_START.format("pre") + "line1\n\nline2 after blank\n" + ab.BLOCK_END + "\n\nOutro."
    assert ab.text_units(text) == ["Intro paragraph.", "```\nline1\n\nline2 after blank\n```", "Outro."]


def test_oversized_code_block_is_split_at_lines_and_refenced():
    big = "```\n" + "\n".join(f"code line {i}" for i in range(600)) + "\n```"
    pieces = ab.split_oversized(big, 2500)
    assert len(pieces) > 1
    assert all(p.startswith("```\n") and "\n```" in p and len(p) <= 2500 for p in pieces)
    assert all("continues in next part" in p for p in pieces[:-1])


def test_parts_respect_the_size_limit_and_keep_all_text():
    paragraphs = [f"Paragraph {i}. " + "word " * 80 for i in range(40)]
    parts = ab.split_into_parts("\n\n".join(paragraphs), limit=2500)
    assert len(parts) > 1 and all(len(p) <= 2500 for p in parts)
    assert all(f"Paragraph {i}." in "\n".join(parts) for i in range(40))


def test_outline_labels_parts_by_their_headings():
    mark = ab.HEADING_MARK
    parts = [f"{mark}Getting started\nIntro text.", "Plain paragraph with no heading at all.",
             f"Text before.\n{mark}Planner\nMore.\n{mark}  Generator  \nEven more.", "```\ncode first\n```"]
    clean, outline, headings = ab.build_outline(parts)
    assert outline == ["Getting started", "Plain paragraph with no heading at all.", "Planner / Generator", "code first"]
    assert headings == ["Getting started", "Planner", "Generator"]
    assert all(mark not in part for part in clean) and clean[0] == "Getting started\nIntro text."


def test_outline_is_shown_only_for_long_pages():
    short = {"parts": ["a"] * 4, "outline": [f"Section {i}" for i in range(4)], "index": 0}
    long = {"parts": ["a"] * 33, "outline": [f"Section {i}" for i in range(33)], "index": 0}
    assert "Page Outline" not in ab.render_part(short, with_outline=True)
    rendered = ab.render_part(long, with_outline=True)
    assert rendered.startswith("Page Outline (33 parts") and "  33. Section 32" in rendered
    assert "Page Outline" not in ab.render_part(long), "READ_MORE notes do not repeat the outline"


def test_render_part_footer():
    page = {"parts": ["one", "two"], "index": 0}
    assert "READ_MORE shows part 2 of 2" in ab.render_part(page)
    page["index"] = 1
    assert "[End of page]" in ab.render_part(page)


MENU = [{"text": f"Menu item {i}", "href": f"https://site.example/menu{i}"} for i in range(ab.MAX_LINKS)]
CONTENT = [{"text": f"Content link {i}", "href": f"https://site.example/content{i}"} for i in range(5)]


def test_first_page_lists_up_to_the_link_limit():
    shown = set()
    links = asyncio.run(ab.extract_page_links(FakeLinksPage("https://site.example/a", MENU + CONTENT), shown))
    assert links.count("->") == ab.MAX_LINKS and "Menu item 0" in links and "not repeated" not in links


def test_next_page_on_the_same_site_lists_new_links_instead_of_the_menu():
    shown = set()
    asyncio.run(ab.extract_page_links(FakeLinksPage("https://site.example/a", MENU + CONTENT), shown))
    links = asyncio.run(ab.extract_page_links(FakeLinksPage("https://site.example/b", MENU + CONTENT), shown))
    assert links.count("->") == 5 and "Content link 0" in links and "Menu item" not in links
    assert f"({ab.MAX_LINKS} links already listed" in links


def test_without_a_running_conversation_the_full_list_is_shown():
    links = asyncio.run(ab.extract_page_links(FakeLinksPage("https://site.example/b", MENU + CONTENT)))
    assert links.count("->") == ab.MAX_LINKS and "not repeated" not in links


def test_links_whose_text_is_html_are_skipped():
    images = [{"text": '<img src="https://site.example/x.jpg">', "href": f"https://shop.example/{i}"} for i in range(5)]
    links = asyncio.run(ab.extract_page_links(FakeLinksPage("https://site.example/a", images + CONTENT)))
    assert "<img" not in links and links.count("->") == 5


def test_links_with_a_few_words_come_before_menu_items():
    menu = [{"text": f"Menu{i}", "href": f"https://site.example/m{i}"} for i in range(10)]
    article = [{"text": "How to repot a houseplant", "href": "https://site.example/repot"}]
    links = asyncio.run(ab.extract_page_links(FakeLinksPage("https://site.example/a", menu + article)))
    assert links.splitlines()[0] == "- How to repot a houseplant -> https://site.example/repot"
    assert links.count("->") == ab.MAX_LINKS


def test_a_discovery_from_a_named_section_gets_its_heading():
    # As on itsfoss.com: each tool's heading, then a repeated "Key Features:" heading, then the feature lines
    part = ("The list is in no particular order of ranking.\n\n1. GIMP: The true Photoshop alternative\n\nKey Features:\n\n"
            "Customizable Interface\nDigital Retouching\n\n2. Krita\n\nKey Features:\n\nLayer management support\n"
            "Transformation tools")
    state = {"parts": [part], "index": 0, "headings": ["1. GIMP: The true Photoshop alternative", "Key Features:",
                                                       "2. Krita", "Key Features:"]}
    assert ab.section_heading(state, "Customizable Interface, Digital Retouching") == "1. GIMP: The true Photoshop alternative"
    assert ab.section_heading(state, "Layer management support, Transformation tools") == "2. Krita"
    # Already named, not on the page, or before any heading: nothing to add
    assert ab.section_heading(state, "Krita: Layer management support, Transformation tools") is None
    assert ab.section_heading(state, "Something the page never says at all") is None
    assert ab.section_heading(state, "The list is in no particular order") is None
    # Copied from a heading on, it is its own section: not the heading above ("1. GIMP")
    assert ab.section_heading(state, "Krita\nKey Features:\n- Layer management support") is None


def test_a_discovery_running_into_the_next_section_gets_that_heading_too():
    part = ("2. Krita\n\nKey Features:\n\nLayer management support, Transformation tools, Variety of brushes\n\n"
            "3. Darktable\n\nKey Features:\n\nDevelop RAW images, Variety of Image formats supported\n")
    state = {"parts": [part], "index": 0, "headings": ["2. Krita", "Key Features:", "3. Darktable", "Key Features:"]}
    text = "Layer management support, Transformation tools, Variety of brushes\nDevelop RAW images, Variety of Image formats"
    assert ab.with_inner_headings(state, text) == (
        "Layer management support, Transformation tools, Variety of brushes\n"
        '(Under the heading "3. Darktable") Develop RAW images, Variety of Image formats')
    # Lines from one section, or a heading the line already names: unchanged
    assert ab.with_inner_headings(state, "Develop RAW images\nVariety of Image formats supported") == (
        "Develop RAW images\nVariety of Image formats supported")
    assert ab.with_inner_headings(state, "Layer management support, Transformation tools\nDarktable: Develop RAW images"
                                  ) == "Layer management support, Transformation tools\nDarktable: Develop RAW images"


def test_the_heading_can_come_from_an_earlier_part():
    state = {"parts": ["Intro.\n\nDarktable\n\nIt develops RAW", "images and manages photo collections well."],
             "index": 1, "headings": ["Darktable"]}
    assert ab.section_heading(state, "images and manages photo collections well") == "Darktable"


def test_blocked_sites_match_their_subdomains_and_paths(monkeypatch):
    from localagentbrowser import pages
    monkeypatch.setattr(pages, "BLOCKED_SITES", ["huggingface.co/datasets", "github.com/openai/simple-evals"])
    assert pages.blocked_site("https://huggingface.co/datasets/basicv8vc/SimpleQA")
    assert pages.blocked_site("https://www.github.com/openai/simple-evals/blob/main/README.md")
    assert not pages.blocked_site("https://huggingface.co/audiomagic/yamnet-onnx")
    assert not pages.blocked_site("https://en.wikipedia.org/wiki/IEEE")
    monkeypatch.setattr(pages, "BLOCKED_SITES", [])
    assert not pages.blocked_site("https://huggingface.co/datasets/x")


def test_a_byline_is_not_a_section_heading():
    part = "Symbols of the Owl in Different Cultures\n\nBY ISABELLA HU\n\nIn Western culture owls stand for wisdom and learning."
    state = {"parts": [part], "index": 0, "headings": ["Symbols of the Owl in Different Cultures", "BY ISABELLA HU"]}
    assert ab.section_heading(state, "In Western culture owls stand for wisdom") == "Symbols of the Owl in Different Cultures"


def test_a_discovery_s_lines_are_found_from_where_it_starts_not_in_an_earlier_section():
    part = ("1. GIMP\n\nKey Features:\n\nLayer management support\nCustomizable Interface\n\n2. Krita\n\nKey Features:\n\n"
            "Layer management support\nTransformation tools\nVariety of brushes and drawing tools\n\n3. Darktable\n\n"
            "Key Features:\n\nDevelop RAW images\nVariety of Image formats supported\n")
    state = {"parts": [part], "index": 0,
             "headings": ["1. GIMP", "Key Features:", "2. Krita", "Key Features:", "3. Darktable", "Key Features:"]}
    text = ("Krita\nKey Features:\n- Layer management support\n- Transformation tools\n- Variety of brushes and drawing "
            "tools\n- Develop RAW images")
    assert ab.with_inner_headings(state, text) == (
        "Krita\nKey Features:\n- Layer management support\n- Transformation tools\n- Variety of brushes and drawing "
        'tools\n(Under the heading "3. Darktable") - Develop RAW images')


def test_a_sentence_marked_up_as_a_heading_is_not_a_section_heading():
    long = ("3. The light-independent reactions (Calvin cycle) use stored chemical energy from ATP and NADPH to make "
            "glucose from carbon dioxide.")
    part = f"Photosynthesis Reactions\n\n{long}\n\nRuBisCO fixes carbon dioxide onto RuBP in the stroma."
    state = {"parts": [part], "index": 0, "headings": ["Photosynthesis Reactions", long]}
    assert ab.section_heading(state, "RuBisCO fixes carbon dioxide onto RuBP") == "Photosynthesis Reactions"
