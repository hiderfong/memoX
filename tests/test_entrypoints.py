"""Packaging and console entry point regressions."""

from importlib.metadata import entry_points


def test_memox_console_entrypoint_is_importable() -> None:
    matches = list(entry_points(group="console_scripts", name="memox"))
    assert len(matches) == 1

    target = matches[0].load()

    from src.main import main

    assert target is main
