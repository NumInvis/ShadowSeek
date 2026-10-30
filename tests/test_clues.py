from shadow_lens.clues import mentions, parse_html, page_candidates

HTML = """
<html><body>
<p>作者：@alice</p>
<p>来源：weibo</p>
<a href="https://twitter.com/bob/status/42">原帖</a>
<a href="javascript:void(0)">忽略</a>
<script>var secret = 'https://x.com/hidden/status/9';</script>
</body></html>
"""


def test_parse_html_extracts_text_links_and_source_lines() -> None:
    clues = parse_html(HTML)
    assert "作者：@alice" in clues.text
    assert "secret" not in clues.text
    assert clues.links == ["https://twitter.com/bob/status/42"]
    assert any("作者" in line for line in clues.source_lines)
    assert any("来源" in line for line in clues.source_lines)


def test_mentions_dedupe() -> None:
    assert mentions("@Alice and @alice and @bob") == ["Alice", "bob"]


def test_page_candidates_levels() -> None:
    clues = parse_html(HTML)
    items = page_candidates("https://example.com/post", clues)
    by_account = {item.account: item for item in items}
    assert by_account["@bob"].level == 2
    assert by_account["@bob"].kind == "link"
    assert by_account["@alice"].level == 1
    assert all(item.platform == "X" for item in items)


def test_mentions_not_confused_with_email() -> None:
    assert mentions("mail me at a@b.com") == []
