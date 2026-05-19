"""Worker template persistence regression tests."""

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from web.routers import workers  # noqa: E402

BASE_CONFIG = """# MemoX test config
app:
  name: MemoX

providers:
  openai:
    api_key: test-key
    base_url: https://api.openai.com/v1

# Worker templates edited by the API.
worker_templates:
  alpha:
    provider: openai
    model: old-model
    temperature: 0.3
    skills:
      - old-skill
    tools:
      - shell

# Knowledge section must survive worker updates.
knowledge_base:
  persist_directory: ./data/chroma

auth:
  enabled: false
"""


def _payload(model: str = "new-model") -> dict:
    return {
        "provider": "openai",
        "model": model,
        "temperature": 0.8,
        "skills": ["code-review", "docs"],
        "tools": ["filesystem"],
        "icon": "W",
        "display_name": "Worker",
    }


def _load(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def test_persist_new_worker_template_preserves_other_sections(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(BASE_CONFIG, encoding="utf-8")

    workers._persist_new_worker_template(config_path, "beta", _payload())

    text = config_path.read_text(encoding="utf-8")
    data = _load(config_path)
    assert data["worker_templates"]["alpha"]["model"] == "old-model"
    assert data["worker_templates"]["beta"]["model"] == "new-model"
    assert data["knowledge_base"]["persist_directory"] == "./data/chroma"
    assert "# MemoX test config" in text
    assert "# Knowledge section must survive worker updates." in text


def test_persist_worker_template_updates_existing_worker(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(BASE_CONFIG, encoding="utf-8")

    workers._persist_worker_template(config_path, "alpha", _payload("updated-model"))

    data = _load(config_path)
    assert data["worker_templates"]["alpha"] == _payload("updated-model")


def test_delete_worker_template_removes_only_requested_worker(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(BASE_CONFIG, encoding="utf-8")
    workers._persist_new_worker_template(config_path, "beta", _payload("beta-model"))

    workers._delete_worker_template(config_path, "alpha")

    data = _load(config_path)
    assert "alpha" not in data["worker_templates"]
    assert data["worker_templates"]["beta"]["model"] == "beta-model"
    assert data["providers"]["openai"]["api_key"] == "test-key"


def test_persist_worker_template_rejects_missing_worker_without_rewrite(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(BASE_CONFIG, encoding="utf-8")
    before = config_path.read_text(encoding="utf-8")

    with pytest.raises(workers.WorkerConfigPersistenceError, match="不存在 Worker 模板"):
        workers._persist_worker_template(config_path, "missing", _payload())

    assert config_path.read_text(encoding="utf-8") == before


def test_atomic_write_keeps_original_when_replace_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text("original\n", encoding="utf-8")

    def fail_replace(src: str | Path, dst: str | Path) -> None:
        raise OSError("replace failed")

    monkeypatch.setattr(workers.os, "replace", fail_replace)

    with pytest.raises(OSError, match="replace failed"):
        workers._atomic_write_text(config_path, "new\n")

    assert config_path.read_text(encoding="utf-8") == "original\n"
    assert list(tmp_path.glob(".config.yaml.*.tmp")) == []


@pytest.mark.asyncio
async def test_update_worker_config_does_not_mutate_runtime_when_persist_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    worker_config = SimpleNamespace(
        provider_type="openai",
        model="old-model",
        skills=["old-skill"],
        tools=["shell"],
        temperature=0.3,
        max_tokens=1024,
        icon="O",
        display_name="Old",
    )
    worker = SimpleNamespace(
        is_busy=False,
        config=worker_config,
        tools=SimpleNamespace(register=lambda *args, **kwargs: None, unregister=lambda *args, **kwargs: None),
        provider=object(),
    )
    worker_pool = SimpleNamespace(_workers={"alpha": worker})
    runtime_config = SimpleNamespace(
        providers={
            "openai": SimpleNamespace(
                resolve_api_key=lambda: "test-key",
                base_url="https://api.openai.com/v1",
                headers={},
            )
        },
        worker_templates={"alpha": SimpleNamespace(model="old-model")},
        knowledge_base=SimpleNamespace(skills_dir=str(tmp_path / "skills")),
    )

    monkeypatch.setattr(workers, "_get_config", lambda: runtime_config)
    monkeypatch.setattr(workers, "get_worker_pool", lambda: worker_pool)
    monkeypatch.setattr(workers, "_config_path", lambda: tmp_path / "missing.yaml")

    body = workers.WorkerConfigUpdate(
        provider="openai",
        model="new-model",
        skills=["new-skill"],
        tools=["filesystem"],
        temperature=0.8,
        max_tokens=2048,
        icon="N",
        display_name="New",
    )

    with pytest.raises(HTTPException) as exc:
        await workers.update_worker_config("alpha", body, SimpleNamespace())

    assert exc.value.status_code == 500
    assert worker.config.model == "old-model"
    assert worker.config.skills == ["old-skill"]
    assert worker.config.tools == ["shell"]
    assert worker.config.temperature == 0.3
