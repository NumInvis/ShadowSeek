from shadow_lens.urls import (
    status_id,
    public_url,
    canonical_x,
    platform_of,
    profile_handle,
)


def test_public_url_blocks_ssrf_and_credentials() -> None:
    assert public_url("http://127.0.0.1/a") == ""
    assert public_url("http://localhost/a") == ""
    assert public_url("http://10.0.0.1/a") == ""
    assert public_url("https://user:pass@example.com/a") == ""
    assert public_url("ftp://example.com/a") == ""
    assert public_url("https://example.com/a") == "https://example.com/a"


def test_canonical_x_normalizes_variants() -> None:
    assert canonical_x("https://twitter.com/a/status/123/photo/1") == "https://x.com/a/status/123"
    assert canonical_x("https://mobile.twitter.com/a/status/123") == "https://x.com/a/status/123"
    assert canonical_x("https://x.com/i/status/123") == "https://x.com/i/status/123"
    assert canonical_x("https://x.com/intent/user?user_id=42") == "https://x.com/i/user/42"
    assert canonical_x("https://x.com/home") == ""
    assert canonical_x("https://x.com/a") == "https://x.com/a"


def test_platform_and_identifiers() -> None:
    platform, account = platform_of("https://x.com/a/status/123")
    assert (platform, account) == ("X", "@a")
    platform, account = platform_of("https://x.com/i/status/123")
    assert (platform, account) == ("X", "#status:123")
    assert status_id("https://x.com/a/status/123/photo/1") == "123"
    assert status_id("https://x.com/a") == ""
    assert profile_handle("https://x.com/a") == "a"
    assert profile_handle("https://x.com/a/status/123") == ""


def test_platform_of_other_sites() -> None:
    assert platform_of("https://space.bilibili.com/12") == ("哔哩哔哩", "UID 12")
    assert platform_of("https://weibo.com/u/12") == ("微博", "UID 12")
    assert platform_of("https://example.com/a") == ("other", "")
