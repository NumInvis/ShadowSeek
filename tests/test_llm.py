from shadow_lens.llm import _normalize, _json_object


def test_json_object_extracts_embedded_payload() -> None:
    assert _json_object('result: {"source_line": "abc"} ok') == {"source_line": "abc"}
    assert _json_object("no json") == {}
    assert _json_object('{"broken": ') == {}
    assert _json_object("[1, 2]") == {}


def test_normalize_collapses_spaces_and_case() -> None:
    assert _normalize("Hello   World") == "helloworld"
