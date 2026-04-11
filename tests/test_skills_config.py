"""Test that KnowledgeBaseConfig exposes a skills_dir field."""
import sys, os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from config import Config


def test_knowledge_base_has_skills_dir_default():
    cfg = Config._from_dict({
        "app": {},
        "server": {},
        "coordinator": {},
        "providers": {},
        "worker_templates": {},
        "knowledge_base": {},
    })
    assert cfg.knowledge_base.skills_dir == "./data/skills"


def test_knowledge_base_skills_dir_override():
    cfg = Config._from_dict({
        "app": {},
        "server": {},
        "coordinator": {},
        "providers": {},
        "worker_templates": {},
        "knowledge_base": {"skills_dir": "/tmp/custom_skills"},
    })
    assert cfg.knowledge_base.skills_dir == "/tmp/custom_skills"
