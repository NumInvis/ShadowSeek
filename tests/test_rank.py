from shadow_lens.rank import dedupe, select
from shadow_lens.models import Candidate


def test_dedupe_merges_same_status() -> None:
    first = Candidate(
        platform="X",
        account="@a",
        url="https://x.com/a/status/1",
        engine="baidu",
        kind="exact",
        level=3,
        verified=True,
        hosts=("site1",),
    )
    second = Candidate(
        platform="X",
        account="@a",
        url="https://x.com/a/status/1",
        engine="copyseeker",
        kind="exact",
        level=3,
        verified=True,
        hosts=("site2",),
    )
    merged = dedupe([first, second])
    assert len(merged) == 1
    assert set(merged[0].hosts) == {"site1", "site2"}


def test_verified_beats_text_clue() -> None:
    verified = Candidate(
        platform="X",
        account="@a",
        url="https://x.com/a/status/1",
        verified=True,
        kind="exact",
        level=3,
    )
    clue = Candidate(
        platform="X",
        account="@b",
        url="https://x.com/b/status/2",
        level=3,
        hosts=("h1", "h2", "h3"),
    )
    selection = select([clue, verified])
    assert selection.primary is not None
    assert selection.primary.url == verified.url
    assert "同图" in selection.primary.chosen_reason


def test_equal_evidence_is_ambiguous() -> None:
    first = Candidate(
        platform="X",
        account="@a",
        url="https://x.com/a/status/1",
        level=2,
        hosts=("h1",),
    )
    second = Candidate(
        platform="X",
        account="@b",
        url="https://x.com/b/status/2",
        level=2,
        hosts=("h1",),
    )
    selection = select([first, second])
    assert selection.ambiguous
    assert selection.primary is not None
    assert selection.alternates


def test_followers_only_break_ties() -> None:
    counted = Candidate(
        platform="X",
        account="@a",
        url="https://x.com/a",
        level=1,
        followers=1000,
    )
    stronger = Candidate(
        platform="X",
        account="@b",
        url="https://x.com/b/status/2",
        level=3,
        hosts=("h1", "h2"),
    )
    selection = select([counted, stronger])
    assert selection.primary is not None
    assert selection.primary.url == counted.url
    assert "关注者" in selection.primary.chosen_reason


def test_pages_without_x() -> None:
    selection = select([], exact_pages=2)
    assert selection.primary is None
    assert selection.found_pages_without_x
