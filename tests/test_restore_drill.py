"""Restore drill script tests."""

from pathlib import Path

import yaml

from scripts.restore_drill import PASSWORD, USERNAME, WORKER_ID, write_restore_drill_config


def test_restore_drill_config_uses_deployment_relative_paths(tmp_path: Path) -> None:
    config_path = write_restore_drill_config(tmp_path, port=19090)
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))

    assert config["server"]["host"] == "127.0.0.1"
    assert config["server"]["port"] == 19090
    assert config["app"]["workspace"] == "./workspace"
    assert config["knowledge_base"]["persist_directory"] == "./data/chroma"
    assert config["knowledge_base"]["upload_directory"] == "./data/uploads"
    assert config["knowledge_base"]["hybrid_search"]["bm25_persist_path"] == "./data/bm25_index.pkl"
    assert config["knowledge_base"]["manifest_path"] == "./data/documents_manifest.json"
    assert config["knowledge_base"]["embedding_provider"] == "hash"
    assert config["worker_templates"][WORKER_ID]["provider"] == "openai"
    assert config["auth"]["users"][0]["username"] == USERNAME
    assert config["auth"]["users"][0]["password"] == PASSWORD
    assert (tmp_path / ".env").read_text(encoding="utf-8") == f"MEMOX_ADMIN_PASSWORD={PASSWORD}\n"
