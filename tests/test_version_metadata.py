"""Version metadata regression tests."""

from pathlib import Path

import tomllib

import src

ROOT = Path(__file__).parents[1]


def test_project_version_matches_runtime_and_api_metadata() -> None:
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    version = pyproject["project"]["version"]
    api_source = (ROOT / "src" / "web" / "api.py").read_text(encoding="utf-8")

    assert src.__version__ == version
    assert f'version="{version}"' in api_source
    assert f'"version": "{version}"' in api_source
