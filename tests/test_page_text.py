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


def test_render_part_footer():
    page = {"parts": ["one", "two"], "index": 0}
    assert "READ_MORE shows part 2 of 2" in ab.render_part(page)
    page["index"] = 1
    assert "[End of page]" in ab.render_part(page)


MENU = [{"text": f"Menu item {i}", "href": f"https://site.example/menu{i}"} for i in range(15)]
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
    assert "(15 links already listed" in links


def test_without_a_running_conversation_the_full_list_is_shown():
    links = asyncio.run(ab.extract_page_links(FakeLinksPage("https://site.example/b", MENU + CONTENT)))
    assert links.count("->") == ab.MAX_LINKS and "not repeated" not in links
