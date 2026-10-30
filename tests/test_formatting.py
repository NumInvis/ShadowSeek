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
    assert "已确认同图" in text
    assert "可信度：高" in text
    assert "账号：@alice · Alice" in text
    assert "原帖：https://x.com/alice/status/1" in text
    assert "依据：同图匹配" in text


def test_ambiguous_result_lists_alternate() -> None:
    primary = Candidate(platform="X", account="@a", url="https://x.com/a", level=2)
    alternate = Candidate(platform="X", account="@b", url="https://x.com/b", level=2)
    text = format_result(
        Selection(primary=primary, alternates=[alternate], ambiguous=True),
        Report(candidates=[primary, alternate]),
    )
    assert "暂不判定归属" in text
    assert "并列候选：@b" in text


def test_no_primary_mentions_pages_without_x() -> None:
    text = format_result(
        Selection(found_pages_without_x=True),
        Report(exact_pages=2),
    )
    assert "没有找到可确认的来源" in text
    assert "同图页面" in text


def test_diagnostics_show_errors() -> None:
    text = format_result(
        Selection(found_pages_without_x=False),
        Report(errors={"baidu": "搜索服务要求验证码"}, engine_counts={"baidu": 0}),
    )
    assert "异常：" in text
    assert "验证码" in text
