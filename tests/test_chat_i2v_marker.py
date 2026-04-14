from src.web.api import parse_i2v_markers


def test_parses_single_marker():
    text = "here: [[I2V: https://x/a.png | slow zoom]] done"
    matches = parse_i2v_markers(text)
    assert matches == [("https://x/a.png", "slow zoom")]


def test_parses_multiple_markers():
    text = "[[I2V: https://a | p1]]\n[[I2V: https://b | p2]]"
    assert parse_i2v_markers(text) == [("https://a", "p1"), ("https://b", "p2")]


def test_ignores_invalid_url():
    text = "[[I2V: not-a-url | p]]"
    assert parse_i2v_markers(text) == []


def test_ignores_empty_prompt():
    text = "[[I2V: https://x |    ]]"
    assert parse_i2v_markers(text) == []
