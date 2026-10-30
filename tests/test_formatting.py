from shadow_lens.models import Report, Candidate, Selection
from shadow_lens.formatting import format_result


def test_verified_result_layout() -> None:
    candidate = Candidate(
        platform="X",
        account="@alice",
        name="Alice",
        url="https://x.com/alice/status/1",
        verified=True,
        kind="exact",
        level=3,
        chosen_reason="同图匹配，优先展示具体帖子",
    )
    report = Report(candidates=[candidate], engine_counts={"baidu": 1})
    text = format_result(Selection(primary=candidate), report)
    assert "同图实锤" in text
    assert "可信度：高" in text
    assert "本尊：@alice · Alice" in text
    assert "首发现场：https://x.com/alice/status/1" in text
    assert "线索：同图匹配" in text


def test_ambiguous_result_lists_alternate() -> None:
    primary = Candidate(platform="X", account="@a", url="https://x.com/a", level=2)
    alternate = Candidate(platform="X", account="@b", url="https://x.com/b", level=2)
    text = format_result(
        Selection(primary=primary, alternates=[alternate], ambiguous=True),
        Report(candidates=[primary, alternate]),
    )
    assert "先不急着定罪" in text
    assert "同期嫌疑人：@b" in text


def test_no_primary_mentions_pages_without_x() -> None:
    text = format_result(
        Selection(found_pages_without_x=True),
        Report(exact_pages=2),
    )
    assert "没逮到本尊" in text
    assert "图是找着了" in text


def test_diagnostics_show_errors() -> None:
    text = format_result(
        Selection(found_pages_without_x=False),
        Report(errors={"baidu": "搜索服务要求验证码"}, engine_counts={"baidu": 0}),
    )
    assert "小状况：" in text
    assert "验证码" in text
