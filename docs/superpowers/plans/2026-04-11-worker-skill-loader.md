# Worker Skill Loader Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make MemoX `WorkerAgent` load real Claude Code-format skills from `data/skills/`, install them from GitHub via a CLI, and expose them to the LLM lazily via a `load_skill` tool.

**Architecture:** New `src/skills/` module with a pure-function loader (no cache → hot reload), a `LoadSkillTool` registered per-worker with a skill whitelist, and an `argparse`-based CLI that shells out to `git clone`. `WorkerAgent` changes are minimal: one tool registration in `__init__`, one enriched section in `_build_system_prompt`.

**Tech Stack:** Python 3.12, `PyYAML` (already a dep via config), system `git` command, `pytest` + `tmp_path` fixtures.

**Spec reference:** `docs/superpowers/specs/2026-04-11-worker-skill-loader-design.md`

---

## File Structure

**New files:**
- `src/skills/__init__.py` — re-exports `Skill`, `list_skills`, `load_skill`
- `src/skills/loader.py` — `Skill` dataclass, `list_skills`, `load_skill` (pure functions, no cache)
- `src/skills/tool.py` — `LoadSkillTool(BaseTool)` with per-worker `allowed_skills` whitelist
- `src/skills/installer.py` — `install_from_github`, `remove_skill`, `update_skill`; subprocess-based git
- `src/skills/cli.py` — `argparse` entrypoint wired via `__main__.py`
- `src/skills/__main__.py` — thin `python -m src.skills` dispatcher
- `tests/test_skills_loader.py`
- `tests/test_skills_tool.py`
- `tests/test_skills_installer.py`
- `tests/test_worker_skill_loading.py`

**Modified files:**
- `src/config/__init__.py` — add `skills_dir: str = "./data/skills"` to `KnowledgeBaseConfig`
- `src/agents/worker_pool.py` — register `LoadSkillTool`, enrich `skills_info` block in `_build_system_prompt`
- `config.yaml` — add `knowledge_base.skills_dir`
- `src/web/api.py` — ensure `data/skills/` exists at startup (piggyback on existing mkdir block)

---

## Task 1: Add `skills_dir` to KnowledgeBaseConfig

**Files:**
- Modify: `src/config/__init__.py` (around line 73)
- Test: `tests/test_skills_config.py` (new, tiny)

- [ ] **Step 1: Write the failing test**

Create `tests/test_skills_config.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_skills_config.py -v`
Expected: FAIL with `AttributeError: 'KnowledgeBaseConfig' object has no attribute 'skills_dir'`

- [ ] **Step 3: Add the field**

In `src/config/__init__.py`, extend `KnowledgeBaseConfig`:

```python
@dataclass
class KnowledgeBaseConfig:
    """知识库配置"""
    vector_store: str = "chroma"
    persist_directory: str = "./data/chroma"
    upload_directory: str = "./data/uploads"
    skills_dir: str = "./data/skills"
    embedding_provider: str = "sentence-transformer"
    embedding_model: str = "sentence-transformers/all-MiniLM-L6-v2"
    chunk_size: int = 500
    chunk_overlap: int = 50
    top_k: int = 5
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_skills_config.py -v`
Expected: PASS (both tests)

- [ ] **Step 5: Commit**

```bash
git add src/config/__init__.py tests/test_skills_config.py
git commit -m "feat(config): add skills_dir to KnowledgeBaseConfig"
```

---

## Task 2: Skill dataclass + list_skills (happy path)

**Files:**
- Create: `src/skills/__init__.py`
- Create: `src/skills/loader.py`
- Test: `tests/test_skills_loader.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_skills_loader.py`:

```python
from pathlib import Path

from skills.loader import Skill, list_skills


def _make_skill(root: Path, name: str, description: str, body: str) -> Path:
    skill_dir = root / name
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n{body}",
        encoding="utf-8",
    )
    return skill_dir


def test_list_skills_returns_parsed_skill(tmp_path):
    _make_skill(tmp_path, "code-review", "Review a PR", "# How to review\nSteps...")

    skills = list_skills(tmp_path)

    assert len(skills) == 1
    skill = skills[0]
    assert isinstance(skill, Skill)
    assert skill.name == "code-review"
    assert skill.description == "Review a PR"
    assert "# How to review" in skill.body
    assert skill.path == tmp_path / "code-review"
    assert skill.references == []


def test_list_skills_empty_dir(tmp_path):
    assert list_skills(tmp_path) == []


def test_list_skills_missing_dir(tmp_path):
    missing = tmp_path / "does-not-exist"
    assert list_skills(missing) == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_skills_loader.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'skills'`

- [ ] **Step 3: Create the module**

Create `src/skills/__init__.py`:

```python
"""Skill loading subsystem for WorkerAgents."""

from .loader import Skill, list_skills, load_skill

__all__ = ["Skill", "list_skills", "load_skill"]
```

Create `src/skills/loader.py`:

```python
"""Load Claude Code format skills from the filesystem."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml
from loguru import logger


@dataclass
class Skill:
    """A parsed skill ready to be injected into a worker's context."""
    name: str
    description: str
    body: str
    path: Path
    references: list[str] = field(default_factory=list)


def _parse_skill_md(skill_md: Path) -> tuple[dict, str]:
    """Return (frontmatter_dict, body_markdown). Raises ValueError on malformed input."""
    text = skill_md.read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        raise ValueError("SKILL.md must start with '---' frontmatter block")
    end = text.find("\n---", 4)
    if end == -1:
        raise ValueError("SKILL.md frontmatter block is not closed")
    frontmatter_raw = text[4:end]
    body = text[end + 4:].lstrip("\n")
    try:
        frontmatter = yaml.safe_load(frontmatter_raw) or {}
    except yaml.YAMLError as e:
        raise ValueError(f"invalid YAML frontmatter: {e}") from e
    if not isinstance(frontmatter, dict):
        raise ValueError("frontmatter must be a YAML mapping")
    return frontmatter, body


def _load_one(skill_dir: Path) -> Skill | None:
    """Load a single skill directory, or return None (with a warning) if invalid."""
    skill_md = skill_dir / "SKILL.md"
    if not skill_md.is_file():
        return None
    try:
        frontmatter, body = _parse_skill_md(skill_md)
    except ValueError as e:
        logger.warning(f"skipping skill {skill_dir.name}: {e}")
        return None

    name = frontmatter.get("name")
    description = frontmatter.get("description")
    if not name or not description:
        logger.warning(
            f"skipping skill {skill_dir.name}: frontmatter missing name or description"
        )
        return None

    references: list[str] = []
    ref_dir = skill_dir / "references"
    if ref_dir.is_dir():
        references = sorted(
            p.name for p in ref_dir.iterdir() if p.is_file()
        )

    return Skill(
        name=name,
        description=description,
        body=body,
        path=skill_dir,
        references=references,
    )


def list_skills(skills_dir: Path) -> list[Skill]:
    """Scan skills_dir, parse each subdirectory as a Skill. Missing dir → empty list."""
    if not skills_dir.is_dir():
        return []
    out: list[Skill] = []
    for child in sorted(skills_dir.iterdir()):
        if not child.is_dir():
            continue
        skill = _load_one(child)
        if skill is not None:
            out.append(skill)
    return out


def load_skill(skills_dir: Path, name: str, ref: str | None = None) -> str:
    """Stub — implemented in Task 3."""
    raise NotImplementedError
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_skills_loader.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add src/skills/ tests/test_skills_loader.py
git commit -m "feat(skills): add Skill dataclass and list_skills loader"
```

---

## Task 3: `load_skill` with path-traversal safety

**Files:**
- Modify: `src/skills/loader.py`
- Test: `tests/test_skills_loader.py` (extend)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_skills_loader.py`:

```python
import pytest

from skills.loader import load_skill


def test_load_skill_returns_body(tmp_path):
    _make_skill(tmp_path, "code-review", "Review a PR", "# How to review\nSteps...")

    body = load_skill(tmp_path, "code-review")

    assert body.startswith("# How to review")
    assert "---" not in body.split("\n")[0]  # no frontmatter leak


def test_load_skill_with_reference(tmp_path):
    skill_dir = _make_skill(tmp_path, "code-review", "desc", "body")
    ref_dir = skill_dir / "references"
    ref_dir.mkdir()
    (ref_dir / "checklist.md").write_text("- item 1\n- item 2\n", encoding="utf-8")

    content = load_skill(tmp_path, "code-review", ref="checklist.md")

    assert content == "- item 1\n- item 2\n"


def test_load_skill_unknown_name(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_skill(tmp_path, "nope")


def test_load_skill_unknown_ref(tmp_path):
    _make_skill(tmp_path, "code-review", "desc", "body")
    with pytest.raises(FileNotFoundError):
        load_skill(tmp_path, "code-review", ref="missing.md")


def test_load_skill_rejects_path_traversal(tmp_path):
    _make_skill(tmp_path, "code-review", "desc", "body")
    # Create a secret file outside the skill
    (tmp_path / "secret.txt").write_text("leaked", encoding="utf-8")

    with pytest.raises(ValueError, match="ref must stay inside"):
        load_skill(tmp_path, "code-review", ref="../../secret.txt")


def test_load_skill_rejects_absolute_ref(tmp_path):
    _make_skill(tmp_path, "code-review", "desc", "body")
    with pytest.raises(ValueError, match="ref must stay inside"):
        load_skill(tmp_path, "code-review", ref="/etc/passwd")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_skills_loader.py -v`
Expected: 6 FAIL (NotImplementedError), 3 PASS (from Task 2)

- [ ] **Step 3: Implement `load_skill`**

Replace the `load_skill` stub in `src/skills/loader.py`:

```python
def load_skill(skills_dir: Path, name: str, ref: str | None = None) -> str:
    """Return the skill body (ref=None) or a references/ file content.

    Raises:
        FileNotFoundError: skill or ref file does not exist.
        ValueError: ref attempts to escape the skill directory.
    """
    skill_dir = (skills_dir / name).resolve()
    skills_root = skills_dir.resolve()
    # Defensive: the skill name itself must not escape skills_dir
    try:
        skill_dir.relative_to(skills_root)
    except ValueError as e:
        raise ValueError("skill name must stay inside skills_dir") from e

    skill_md = skill_dir / "SKILL.md"
    if not skill_md.is_file():
        raise FileNotFoundError(f"skill not found: {name}")

    if ref is None:
        _, body = _parse_skill_md(skill_md)
        return body

    target = (skill_dir / "references" / ref).resolve()
    try:
        target.relative_to(skill_dir.resolve())
    except ValueError as e:
        raise ValueError("ref must stay inside skill directory") from e
    if not target.is_file():
        raise FileNotFoundError(f"reference not found: {name}/{ref}")
    return target.read_text(encoding="utf-8")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_skills_loader.py -v`
Expected: PASS (9 tests total)

- [ ] **Step 5: Commit**

```bash
git add src/skills/loader.py tests/test_skills_loader.py
git commit -m "feat(skills): add load_skill with path-traversal protection"
```

---

## Task 4: Loader edge cases (malformed, missing fields)

**Files:**
- Test: `tests/test_skills_loader.py` (extend)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_skills_loader.py`:

```python
def test_list_skills_skips_missing_skill_md(tmp_path):
    (tmp_path / "empty-dir").mkdir()
    assert list_skills(tmp_path) == []


def test_list_skills_skips_malformed_frontmatter(tmp_path):
    d = tmp_path / "broken"
    d.mkdir()
    (d / "SKILL.md").write_text("no frontmatter here", encoding="utf-8")

    assert list_skills(tmp_path) == []


def test_list_skills_skips_missing_name_field(tmp_path):
    d = tmp_path / "broken"
    d.mkdir()
    (d / "SKILL.md").write_text(
        "---\ndescription: has no name\n---\nbody",
        encoding="utf-8",
    )

    assert list_skills(tmp_path) == []


def test_list_skills_skips_missing_description_field(tmp_path):
    d = tmp_path / "broken"
    d.mkdir()
    (d / "SKILL.md").write_text(
        "---\nname: nodesc\n---\nbody",
        encoding="utf-8",
    )

    assert list_skills(tmp_path) == []


def test_list_skills_skips_unclosed_frontmatter(tmp_path):
    d = tmp_path / "broken"
    d.mkdir()
    (d / "SKILL.md").write_text("---\nname: x\ndescription: y\nbody never closed", encoding="utf-8")

    assert list_skills(tmp_path) == []


def test_list_skills_populates_references(tmp_path):
    d = _make_skill(tmp_path, "code-review", "desc", "body")
    ref_dir = d / "references"
    ref_dir.mkdir()
    (ref_dir / "a.md").write_text("a", encoding="utf-8")
    (ref_dir / "b.md").write_text("b", encoding="utf-8")

    skills = list_skills(tmp_path)
    assert skills[0].references == ["a.md", "b.md"]
```

- [ ] **Step 2: Run tests**

Run: `pytest tests/test_skills_loader.py -v`
Expected: all PASS (loader already handles these via the `_load_one` warnings + early returns). If any fail, fix the loader — do NOT weaken the test.

- [ ] **Step 3: Commit**

```bash
git add tests/test_skills_loader.py
git commit -m "test(skills): add loader edge-case coverage"
```

---

## Task 5: `LoadSkillTool` with per-worker whitelist

**Files:**
- Create: `src/skills/tool.py`
- Test: `tests/test_skills_tool.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_skills_tool.py`:

```python
from pathlib import Path

import pytest

from skills.tool import LoadSkillTool


def _make_skill(root: Path, name: str, description: str, body: str) -> Path:
    d = root / name
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n{body}",
        encoding="utf-8",
    )
    return d


@pytest.mark.asyncio
async def test_load_skill_tool_returns_body(tmp_path):
    _make_skill(tmp_path, "code-review", "desc", "# body")
    tool = LoadSkillTool(tmp_path, allowed_skills={"code-review"})

    result = await tool.execute({"name": "code-review"})

    assert result.startswith("# body")


@pytest.mark.asyncio
async def test_load_skill_tool_rejects_unlisted_skill(tmp_path):
    _make_skill(tmp_path, "secret", "desc", "body")
    tool = LoadSkillTool(tmp_path, allowed_skills={"code-review"})

    result = await tool.execute({"name": "secret"})

    assert "not enabled" in result
    assert "secret" in result


@pytest.mark.asyncio
async def test_load_skill_tool_missing_skill_returns_error_string(tmp_path):
    tool = LoadSkillTool(tmp_path, allowed_skills={"code-review"})

    result = await tool.execute({"name": "code-review"})

    assert result.startswith("Error:")


@pytest.mark.asyncio
async def test_load_skill_tool_reference_file(tmp_path):
    d = _make_skill(tmp_path, "code-review", "desc", "body")
    ref_dir = d / "references"
    ref_dir.mkdir()
    (ref_dir / "checklist.md").write_text("checklist content", encoding="utf-8")
    tool = LoadSkillTool(tmp_path, allowed_skills={"code-review"})

    result = await tool.execute({"name": "code-review", "ref": "checklist.md"})

    assert result == "checklist content"


@pytest.mark.asyncio
async def test_load_skill_tool_path_traversal_returns_error_string(tmp_path):
    _make_skill(tmp_path, "code-review", "desc", "body")
    tool = LoadSkillTool(tmp_path, allowed_skills={"code-review"})

    result = await tool.execute({"name": "code-review", "ref": "../../../etc/passwd"})

    assert result.startswith("Error:")


def test_load_skill_tool_has_input_schema():
    tool = LoadSkillTool(Path("/tmp"), allowed_skills=set())
    assert tool.name == "load_skill"
    assert isinstance(tool.description, str) and tool.description
    schema = tool.input_schema
    assert schema["type"] == "object"
    assert "name" in schema["properties"]
    assert "ref" in schema["properties"]
    assert schema["required"] == ["name"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_skills_tool.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'skills.tool'`

- [ ] **Step 3: Create `src/skills/tool.py`**

```python
"""LoadSkillTool — lazy skill loading via ToolRegistry."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from agents.base_agent import BaseTool

from .loader import load_skill


class LoadSkillTool(BaseTool):
    """Worker-scoped tool for loading skill content on demand.

    Each worker gets its own instance with an `allowed_skills` whitelist derived
    from its WorkerConfig.skills. This prevents cross-worker skill leakage even
    when the same skills_dir is shared across the pool.
    """

    def __init__(self, skills_dir: Path, allowed_skills: set[str]):
        self._skills_dir = skills_dir
        self._allowed = set(allowed_skills)

    @property
    def name(self) -> str:
        return "load_skill"

    @property
    def description(self) -> str:
        return (
            "Load the full content of a skill by name. Use this when you see a "
            "skill listed in '可用技能' and want its detailed instructions. Pass "
            "`ref` to fetch a sub-reference file from the skill's references/ dir."
        )

    @property
    def input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Name of the skill to load (must be enabled for this worker).",
                },
                "ref": {
                    "type": "string",
                    "description": "Optional sub-reference filename under the skill's references/ directory.",
                },
            },
            "required": ["name"],
        }

    async def execute(self, arguments: dict) -> Any:
        name = arguments.get("name")
        if not isinstance(name, str) or not name:
            return "Error: 'name' is required."
        if name not in self._allowed:
            return f"Error: skill '{name}' is not enabled for this worker."
        ref = arguments.get("ref")
        try:
            return load_skill(self._skills_dir, name, ref)
        except FileNotFoundError as e:
            return f"Error: {e}"
        except ValueError as e:
            return f"Error: {e}"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_skills_tool.py -v`
Expected: PASS (6 tests). If pytest complains about `@pytest.mark.asyncio`, add `pytest-asyncio` to the dev deps — but check first whether other tests in the repo already use it:

```bash
grep -r "pytest.mark.asyncio" tests/ | head -3
```

If existing tests use it, `pytest-asyncio` is already installed; otherwise install with `pip install pytest-asyncio` and add it to `requirements.txt` or `pyproject.toml`.

- [ ] **Step 5: Commit**

```bash
git add src/skills/tool.py tests/test_skills_tool.py
git commit -m "feat(skills): add LoadSkillTool with per-worker whitelist"
```

---

## Task 6: Installer — `install_from_github` happy path

**Files:**
- Create: `src/skills/installer.py`
- Test: `tests/test_skills_installer.py`

Uses a local bare-ish git repo via `file://` URL as a fake GitHub — no network in tests.

- [ ] **Step 1: Write the failing test**

Create `tests/test_skills_installer.py`:

```python
import json
import subprocess
from pathlib import Path

import pytest

from skills.installer import install_from_github
from skills.loader import list_skills


def _init_remote_repo(repo_dir: Path, skill_name: str = "code-review") -> str:
    """Create a local git repo containing a single skill; return its file:// URL."""
    repo_dir.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo_dir)], check=True)
    subprocess.run(["git", "-C", str(repo_dir), "config", "user.email", "t@t"], check=True)
    subprocess.run(["git", "-C", str(repo_dir), "config", "user.name", "t"], check=True)

    skill_md = repo_dir / "SKILL.md"
    skill_md.write_text(
        f"---\nname: {skill_name}\ndescription: Test skill\n---\n# body\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "-C", str(repo_dir), "add", "SKILL.md"], check=True)
    subprocess.run(["git", "-C", str(repo_dir), "commit", "-q", "-m", "init"], check=True)
    return f"file://{repo_dir}"


def test_install_from_github_happy_path(tmp_path):
    remote = tmp_path / "remote"
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    url = _init_remote_repo(remote)

    skill = install_from_github(url, skills_dir)

    assert skill.name == "code-review"
    assert (skills_dir / "code-review" / "SKILL.md").is_file()
    meta = json.loads((skills_dir / "code-review" / ".install.json").read_text())
    assert meta["source_url"] == url
    assert "installed_at" in meta
    # loader sees it
    assert [s.name for s in list_skills(skills_dir)] == ["code-review"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_skills_installer.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'skills.installer'`

- [ ] **Step 3: Create `src/skills/installer.py`**

```python
"""Install Claude Code format skills from GitHub into data/skills/."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path

from loguru import logger

from .loader import Skill, _load_one, _parse_skill_md

_GITHUB_URL_RE = re.compile(
    r"^(?:https?://)?(?:www\.)?github\.com/(?P<owner>[^/]+)/(?P<repo>[^/]+?)(?:\.git)?"
    r"(?:/(?:tree|blob)/[^/]+)?(?:/(?P<subpath>.*))?$"
)


def _parse_github_url(url: str) -> tuple[str, str]:
    """Return (clone_url, subpath).

    Accepts all of:
      github.com/owner/repo
      https://github.com/owner/repo
      github.com/owner/repo/subpath/x
      github.com/owner/repo/tree/main/subpath/x
      file:///tmp/fake-repo   (for tests; no subpath)
    """
    if url.startswith("file://"):
        return url, ""
    m = _GITHUB_URL_RE.match(url.strip())
    if not m:
        raise ValueError(f"unrecognized GitHub URL: {url}")
    owner = m.group("owner")
    repo = m.group("repo")
    subpath = (m.group("subpath") or "").strip("/")
    clone_url = f"https://github.com/{owner}/{repo}.git"
    return clone_url, subpath


def _git_clone(clone_url: str, dest: Path) -> None:
    """Shallow clone; surfaces a clean error on failure."""
    try:
        subprocess.run(
            ["git", "clone", "--depth", "1", clone_url, str(dest)],
            check=True,
            capture_output=True,
            text=True,
        )
    except subprocess.CalledProcessError as e:
        raise RuntimeError(
            f"git clone failed for {clone_url}: {e.stderr.strip() or e.stdout.strip()}"
        ) from e


def install_from_github(
    url: str,
    skills_dir: Path,
    name: str | None = None,
    force: bool = False,
) -> Skill:
    """Clone a skill from GitHub into skills_dir. Returns the loaded Skill.

    Args:
        url: GitHub URL (optionally with subpath) or file:// URL (tests).
        skills_dir: target data/skills/ directory.
        name: override the skill name (default: frontmatter.name).
        force: overwrite existing target.

    Raises:
        FileNotFoundError: source has no SKILL.md.
        FileExistsError: target already exists and force is False.
        ValueError: malformed URL or skill frontmatter.
        RuntimeError: git clone failed.
    """
    clone_url, subpath = _parse_github_url(url)
    skills_dir.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory() as tmp:
        tmp_clone = Path(tmp) / "clone"
        _git_clone(clone_url, tmp_clone)

        source = tmp_clone / subpath if subpath else tmp_clone
        source_skill_md = source / "SKILL.md"
        if not source_skill_md.is_file():
            raise FileNotFoundError(
                f"no SKILL.md found at {subpath or '<repo root>'}"
            )

        # Resolve the skill name
        frontmatter, _ = _parse_skill_md(source_skill_md)
        fm_name = frontmatter.get("name")
        if not fm_name:
            raise ValueError("SKILL.md frontmatter missing 'name' field")
        final_name = name or fm_name

        target = skills_dir / final_name
        if target.exists():
            if not force:
                raise FileExistsError(
                    f"skill '{final_name}' already installed at {target} — use force=True"
                )
            shutil.rmtree(target)

        # Copy only the skill subtree, not .git
        shutil.copytree(source, target, ignore=shutil.ignore_patterns(".git"))

        meta = {
            "source_url": url,
            "installed_at": datetime.now().isoformat(timespec="seconds"),
        }
        (target / ".install.json").write_text(
            json.dumps(meta, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    skill = _load_one(target)
    if skill is None:
        # Cleanup the half-installed directory so the user can retry
        shutil.rmtree(target, ignore_errors=True)
        raise ValueError(
            f"installed skill at {target} failed to load — check frontmatter"
        )
    logger.info(f"installed skill '{skill.name}' from {url}")
    return skill
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_skills_installer.py -v`
Expected: PASS. If `git` isn't installed in the test environment, the test will error — that's acceptable (CI must have git).

- [ ] **Step 5: Commit**

```bash
git add src/skills/installer.py tests/test_skills_installer.py
git commit -m "feat(skills): add install_from_github installer"
```

---

## Task 7: Installer edge cases — subpath, name override, force, missing SKILL.md

**Files:**
- Test: `tests/test_skills_installer.py` (extend)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_skills_installer.py`:

```python
def _init_multi_skill_repo(repo_dir: Path) -> str:
    repo_dir.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo_dir)], check=True)
    subprocess.run(["git", "-C", str(repo_dir), "config", "user.email", "t@t"], check=True)
    subprocess.run(["git", "-C", str(repo_dir), "config", "user.name", "t"], check=True)

    for sub in ("code-review", "docx"):
        d = repo_dir / sub
        d.mkdir()
        (d / "SKILL.md").write_text(
            f"---\nname: {sub}\ndescription: {sub} skill\n---\n# {sub}\n",
            encoding="utf-8",
        )
    subprocess.run(["git", "-C", str(repo_dir), "add", "."], check=True)
    subprocess.run(["git", "-C", str(repo_dir), "commit", "-q", "-m", "init"], check=True)
    return f"file://{repo_dir}"


def test_install_from_github_subpath(tmp_path):
    remote = tmp_path / "remote"
    skills_dir = tmp_path / "skills"
    url = _init_multi_skill_repo(remote)

    # file:// URL with subpath — use a synthetic form the parser accepts
    # For file:// we pass the subpath separately via a custom URL syntax:
    # file:///path/to/remote#subpath=code-review
    # But install_from_github only understands URLs. Simplest: install twice using
    # a helper that routes file:// + subpath through the parser.
    # Instead, extend _parse_github_url to accept "file:///path#subpath=X".
    skill = install_from_github(f"{url}#subpath=code-review", skills_dir)

    assert skill.name == "code-review"
    assert (skills_dir / "code-review" / "SKILL.md").is_file()
    assert not (skills_dir / "docx").exists()


def test_install_from_github_name_override(tmp_path):
    remote = tmp_path / "remote"
    skills_dir = tmp_path / "skills"
    url = _init_remote_repo(remote, skill_name="code-review")

    skill = install_from_github(url, skills_dir, name="my-review")

    assert skill.name == "code-review"  # frontmatter name unchanged in Skill object
    assert (skills_dir / "my-review" / "SKILL.md").is_file()


def test_install_from_github_refuses_existing_without_force(tmp_path):
    remote = tmp_path / "remote"
    skills_dir = tmp_path / "skills"
    url = _init_remote_repo(remote)
    install_from_github(url, skills_dir)

    with pytest.raises(FileExistsError):
        install_from_github(url, skills_dir)


def test_install_from_github_overwrites_with_force(tmp_path):
    remote = tmp_path / "remote"
    skills_dir = tmp_path / "skills"
    url = _init_remote_repo(remote)
    install_from_github(url, skills_dir)

    skill = install_from_github(url, skills_dir, force=True)

    assert skill.name == "code-review"


def test_install_from_github_missing_skill_md(tmp_path):
    remote = tmp_path / "remote-empty"
    remote.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main", str(remote)], check=True)
    subprocess.run(["git", "-C", str(remote), "config", "user.email", "t@t"], check=True)
    subprocess.run(["git", "-C", str(remote), "config", "user.name", "t"], check=True)
    (remote / "README.md").write_text("no skill here", encoding="utf-8")
    subprocess.run(["git", "-C", str(remote), "add", "README.md"], check=True)
    subprocess.run(["git", "-C", str(remote), "commit", "-q", "-m", "init"], check=True)

    skills_dir = tmp_path / "skills"
    with pytest.raises(FileNotFoundError, match="no SKILL.md"):
        install_from_github(f"file://{remote}", skills_dir)
```

- [ ] **Step 2: Run tests**

Run: `pytest tests/test_skills_installer.py -v`
Expected: the subpath test FAILS (parser doesn't handle `file://...#subpath=`); others pass.

- [ ] **Step 3: Extend `_parse_github_url` to accept `file://` + subpath fragment**

In `src/skills/installer.py`, replace `_parse_github_url` with:

```python
def _parse_github_url(url: str) -> tuple[str, str]:
    """Return (clone_url, subpath).

    Accepts:
      github.com/owner/repo[/subpath]
      https://github.com/owner/repo[/tree/BRANCH/subpath]
      file:///path/to/repo[#subpath=some/path]   (tests)
    """
    if url.startswith("file://"):
        if "#subpath=" in url:
            base, _, frag = url.partition("#subpath=")
            return base, frag.strip("/")
        return url, ""
    m = _GITHUB_URL_RE.match(url.strip())
    if not m:
        raise ValueError(f"unrecognized GitHub URL: {url}")
    owner = m.group("owner")
    repo = m.group("repo")
    subpath = (m.group("subpath") or "").strip("/")
    clone_url = f"https://github.com/{owner}/{repo}.git"
    return clone_url, subpath
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_skills_installer.py -v`
Expected: PASS (6 tests).

- [ ] **Step 5: Commit**

```bash
git add src/skills/installer.py tests/test_skills_installer.py
git commit -m "feat(skills): installer supports subpath, name override, force"
```

---

## Task 8: `remove_skill` and `update_skill`

**Files:**
- Modify: `src/skills/installer.py`
- Test: `tests/test_skills_installer.py` (extend)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_skills_installer.py`:

```python
from skills.installer import remove_skill, update_skill


def test_remove_skill_happy_path(tmp_path):
    remote = tmp_path / "remote"
    skills_dir = tmp_path / "skills"
    url = _init_remote_repo(remote)
    install_from_github(url, skills_dir)

    remove_skill(skills_dir, "code-review")

    assert not (skills_dir / "code-review").exists()


def test_remove_skill_missing_raises(tmp_path):
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    with pytest.raises(FileNotFoundError):
        remove_skill(skills_dir, "nope")


def test_update_skill_re_clones(tmp_path):
    remote = tmp_path / "remote"
    skills_dir = tmp_path / "skills"
    url = _init_remote_repo(remote)
    install_from_github(url, skills_dir)

    # Mutate the remote
    (remote / "SKILL.md").write_text(
        "---\nname: code-review\ndescription: Updated\n---\n# updated body\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "-C", str(remote), "add", "SKILL.md"], check=True)
    subprocess.run(["git", "-C", str(remote), "commit", "-q", "-m", "update"], check=True)

    updated = update_skill(skills_dir, "code-review")

    assert updated.description == "Updated"
    body_on_disk = (skills_dir / "code-review" / "SKILL.md").read_text()
    assert "updated body" in body_on_disk


def test_update_skill_missing_install_json(tmp_path):
    skills_dir = tmp_path / "skills"
    d = skills_dir / "manual"
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(
        "---\nname: manual\ndescription: d\n---\nbody",
        encoding="utf-8",
    )

    with pytest.raises(FileNotFoundError, match="install.json"):
        update_skill(skills_dir, "manual")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_skills_installer.py -v`
Expected: `ImportError: cannot import name 'remove_skill'`

- [ ] **Step 3: Add `remove_skill` and `update_skill`**

Append to `src/skills/installer.py`:

```python
def remove_skill(skills_dir: Path, name: str) -> None:
    """Delete data/skills/<name>/. Raises FileNotFoundError if missing."""
    target = skills_dir / name
    if not target.is_dir():
        raise FileNotFoundError(f"skill not installed: {name}")
    shutil.rmtree(target)
    logger.info(f"removed skill '{name}'")


def update_skill(skills_dir: Path, name: str) -> Skill:
    """Re-install a skill from its recorded source_url.

    Raises:
        FileNotFoundError: target doesn't exist, or .install.json is missing.
    """
    target = skills_dir / name
    meta_path = target / ".install.json"
    if not meta_path.is_file():
        raise FileNotFoundError(
            f".install.json not found for '{name}' — reinstall manually with install_from_github"
        )
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    source_url = meta.get("source_url")
    if not source_url:
        raise FileNotFoundError(f"source_url missing in .install.json for '{name}'")
    # Note: rmtree + copytree, not an atomic rename. Brief window where the skill
    # directory is absent is acceptable (see design spec).
    return install_from_github(source_url, skills_dir, name=name, force=True)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_skills_installer.py -v`
Expected: PASS (10 tests total).

- [ ] **Step 5: Commit**

```bash
git add src/skills/installer.py tests/test_skills_installer.py
git commit -m "feat(skills): add remove_skill and update_skill"
```

---

## Task 9: CLI — `python -m src.skills {install|list|remove|update}`

**Files:**
- Create: `src/skills/cli.py`
- Create: `src/skills/__main__.py`
- Test: `tests/test_skills_cli.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_skills_cli.py`:

```python
import subprocess
import sys
from pathlib import Path


def _init_remote(repo_dir: Path) -> str:
    repo_dir.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo_dir)], check=True)
    subprocess.run(["git", "-C", str(repo_dir), "config", "user.email", "t@t"], check=True)
    subprocess.run(["git", "-C", str(repo_dir), "config", "user.name", "t"], check=True)
    (repo_dir / "SKILL.md").write_text(
        "---\nname: code-review\ndescription: Test\n---\nbody",
        encoding="utf-8",
    )
    subprocess.run(["git", "-C", str(repo_dir), "add", "SKILL.md"], check=True)
    subprocess.run(["git", "-C", str(repo_dir), "commit", "-q", "-m", "init"], check=True)
    return f"file://{repo_dir}"


def _run_cli(skills_dir: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "skills", *args, "--skills-dir", str(skills_dir)],
        capture_output=True,
        text=True,
        cwd=Path(__file__).resolve().parents[1] / "src",
    )


def test_cli_install_list_remove(tmp_path):
    remote = tmp_path / "remote"
    skills_dir = tmp_path / "skills"
    url = _init_remote(remote)

    # install
    r = _run_cli(skills_dir, "install", url)
    assert r.returncode == 0, r.stderr
    assert "code-review" in r.stdout

    # list
    r = _run_cli(skills_dir, "list")
    assert r.returncode == 0
    assert "code-review" in r.stdout
    assert "Test" in r.stdout

    # remove
    r = _run_cli(skills_dir, "remove", "code-review")
    assert r.returncode == 0
    assert not (skills_dir / "code-review").exists()


def test_cli_install_refuses_existing(tmp_path):
    remote = tmp_path / "remote"
    skills_dir = tmp_path / "skills"
    url = _init_remote(remote)

    _run_cli(skills_dir, "install", url)
    r = _run_cli(skills_dir, "install", url)

    assert r.returncode != 0
    assert "already installed" in (r.stdout + r.stderr).lower() or "exists" in (r.stdout + r.stderr).lower()


def test_cli_install_force(tmp_path):
    remote = tmp_path / "remote"
    skills_dir = tmp_path / "skills"
    url = _init_remote(remote)

    _run_cli(skills_dir, "install", url)
    r = _run_cli(skills_dir, "install", url, "--force")

    assert r.returncode == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_skills_cli.py -v`
Expected: FAIL — `skills` module has no `__main__`.

- [ ] **Step 3: Create CLI**

Create `src/skills/cli.py`:

```python
"""Command-line interface: python -m src.skills ..."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from loguru import logger

from .installer import install_from_github, remove_skill, update_skill
from .loader import list_skills


def _cmd_install(args: argparse.Namespace) -> int:
    try:
        skill = install_from_github(
            args.url,
            Path(args.skills_dir),
            name=args.name,
            force=args.force,
        )
    except FileExistsError as e:
        print(f"error: {e}", file=sys.stderr)
        print("hint: pass --force to overwrite, or run `remove` first.", file=sys.stderr)
        return 2
    except FileNotFoundError as e:
        print(f"error: {e}", file=sys.stderr)
        return 3
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 4
    except RuntimeError as e:
        print(f"error: {e}", file=sys.stderr)
        return 5
    print(f"installed: {skill.name} — {skill.description}")
    print(f"  path: {skill.path}")
    return 0


def _cmd_list(args: argparse.Namespace) -> int:
    skills_dir = Path(args.skills_dir)
    skills = list_skills(skills_dir)
    if not skills:
        print("(no skills installed)")
        return 0
    width = max(len(s.name) for s in skills)
    for s in skills:
        src = ""
        meta_path = s.path / ".install.json"
        if meta_path.is_file():
            try:
                src = json.loads(meta_path.read_text()).get("source_url", "")
            except (OSError, json.JSONDecodeError):
                src = ""
        line = f"{s.name.ljust(width)}  {s.description}"
        if src:
            line += f"  [{src}]"
        print(line)
    return 0


def _cmd_remove(args: argparse.Namespace) -> int:
    try:
        remove_skill(Path(args.skills_dir), args.name)
    except FileNotFoundError as e:
        print(f"error: {e}", file=sys.stderr)
        return 3
    print(f"removed: {args.name}")
    return 0


def _cmd_update(args: argparse.Namespace) -> int:
    try:
        skill = update_skill(Path(args.skills_dir), args.name)
    except FileNotFoundError as e:
        print(f"error: {e}", file=sys.stderr)
        return 3
    except (ValueError, RuntimeError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 4
    print(f"updated: {skill.name} — {skill.description}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m src.skills")
    parser.add_argument(
        "--skills-dir",
        default="./data/skills",
        help="Path to the skills directory (default: ./data/skills)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_install = sub.add_parser("install", help="Install a skill from a GitHub URL")
    p_install.add_argument("url")
    p_install.add_argument("--name", default=None, help="Override the skill name")
    p_install.add_argument("--force", action="store_true", help="Overwrite existing")
    p_install.set_defaults(func=_cmd_install)

    p_list = sub.add_parser("list", help="List installed skills")
    p_list.set_defaults(func=_cmd_list)

    p_remove = sub.add_parser("remove", help="Remove an installed skill")
    p_remove.add_argument("name")
    p_remove.set_defaults(func=_cmd_remove)

    p_update = sub.add_parser("update", help="Re-install a skill from its recorded source")
    p_update.add_argument("name")
    p_update.set_defaults(func=_cmd_update)

    return parser


def main(argv: list[str] | None = None) -> int:
    # Suppress info-level loguru noise in CLI output
    logger.remove()
    logger.add(sys.stderr, level="WARNING")
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)
```

Create `src/skills/__main__.py`:

```python
"""Entrypoint for `python -m skills`."""

from .cli import main

raise SystemExit(main())
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_skills_cli.py -v`
Expected: PASS (3 tests).

If the subprocess can't import `skills`, the `cwd=.../src` trick may not put `src/` on `sys.path`. Fallback: set `env={"PYTHONPATH": str(src_dir), **os.environ}` when calling `subprocess.run`.

- [ ] **Step 5: Commit**

```bash
git add src/skills/cli.py src/skills/__main__.py tests/test_skills_cli.py
git commit -m "feat(skills): add CLI for install/list/remove/update"
```

---

## Task 10: Wire `LoadSkillTool` into `WorkerAgent.__init__`

**Files:**
- Modify: `src/agents/worker_pool.py` (around the `__init__` method, ~line 99–122)
- Test: `tests/test_worker_skill_loading.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_worker_skill_loading.py`:

```python
from pathlib import Path
from unittest.mock import patch

import pytest

from agents.base_agent import ToolRegistry
from agents.worker_pool import WorkerAgent, WorkerConfig


def _make_skill(root: Path, name: str, description: str, body: str) -> None:
    d = root / name
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n{body}",
        encoding="utf-8",
    )


class _FakeProvider:
    async def chat(self, *args, **kwargs):  # pragma: no cover - not exercised
        raise NotImplementedError


def _patch_config(skills_dir: Path):
    """Patch get_config to return a Config-ish object whose knowledge_base.skills_dir points at skills_dir."""
    from config import get_config

    real = get_config()
    real.knowledge_base.skills_dir = str(skills_dir)
    return real


def test_worker_registers_load_skill_tool_when_skills_configured(tmp_path):
    _make_skill(tmp_path, "code-review", "Review", "body")
    _patch_config(tmp_path)

    cfg = WorkerConfig(
        name="reviewer",
        provider_type="anthropic",
        api_key="",
        model="claude-sonnet-4-20250514",
        skills=["code-review"],
    )
    worker = WorkerAgent(cfg, tools=ToolRegistry(), provider=_FakeProvider())

    assert worker.tools.get("load_skill") is not None


def test_worker_skips_load_skill_tool_when_no_skills(tmp_path):
    _patch_config(tmp_path)
    cfg = WorkerConfig(
        name="plain",
        provider_type="anthropic",
        api_key="",
        model="claude-sonnet-4-20250514",
        skills=[],
    )
    worker = WorkerAgent(cfg, tools=ToolRegistry(), provider=_FakeProvider())

    assert worker.tools.get("load_skill") is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_worker_skill_loading.py -v`
Expected: FAIL on `test_worker_registers_load_skill_tool_when_skills_configured` — tool not registered.

- [ ] **Step 3: Wire the tool into `WorkerAgent.__init__`**

In `src/agents/worker_pool.py`, find `WorkerAgent.__init__` (around line 99). After the existing `self.tools = tools or ToolRegistry()` line, add:

```python
        # Register per-worker LoadSkillTool if this worker has skills configured.
        if config.skills:
            from pathlib import Path
            from config import get_config
            from skills.tool import LoadSkillTool

            skills_dir = Path(get_config().knowledge_base.skills_dir)
            self.tools.register(LoadSkillTool(skills_dir, set(config.skills)))
```

Keep the import local to avoid a circular import at module load.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_worker_skill_loading.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add src/agents/worker_pool.py tests/test_worker_skill_loading.py
git commit -m "feat(worker): register LoadSkillTool when worker has skills configured"
```

---

## Task 11: Enrich `_build_system_prompt` with skill name + description

**Files:**
- Modify: `src/agents/worker_pool.py` (`_build_system_prompt`, ~line 307–338)
- Test: `tests/test_worker_skill_loading.py` (extend)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_worker_skill_loading.py`:

```python
def test_system_prompt_lists_skill_name_and_description(tmp_path):
    _make_skill(tmp_path, "code-review", "Review a PR", "# body")
    _patch_config(tmp_path)
    cfg = WorkerConfig(
        name="reviewer",
        provider_type="anthropic",
        api_key="",
        model="claude-sonnet-4-20250514",
        skills=["code-review"],
    )
    worker = WorkerAgent(cfg, tools=ToolRegistry(), provider=_FakeProvider())

    prompt = worker._build_system_prompt()

    assert "code-review" in prompt
    assert "Review a PR" in prompt
    assert "load_skill" in prompt  # tool mention in the "available tools" section


def test_system_prompt_warns_on_missing_skill(tmp_path, caplog):
    # Skill is listed in config but not installed on disk
    _patch_config(tmp_path)
    cfg = WorkerConfig(
        name="reviewer",
        provider_type="anthropic",
        api_key="",
        model="claude-sonnet-4-20250514",
        skills=["ghost"],
    )
    worker = WorkerAgent(cfg, tools=ToolRegistry(), provider=_FakeProvider())

    prompt = worker._build_system_prompt()

    # The missing skill should NOT appear in the 可用技能 section
    # (we check by asserting the section is either absent or does not include 'ghost')
    assert "ghost" not in prompt or "可用技能" not in prompt


def test_system_prompt_skill_section_hot_reloads(tmp_path):
    """Install a skill AFTER worker creation, rebuild prompt, skill appears."""
    _patch_config(tmp_path)
    cfg = WorkerConfig(
        name="reviewer",
        provider_type="anthropic",
        api_key="",
        model="claude-sonnet-4-20250514",
        skills=["code-review"],
    )
    worker = WorkerAgent(cfg, tools=ToolRegistry(), provider=_FakeProvider())

    # Skill absent initially
    assert "Review a PR" not in worker._build_system_prompt()

    # Install skill on disk after worker construction
    _make_skill(tmp_path, "code-review", "Review a PR", "# body")

    # Rebuild prompt — list_skills re-scans the directory
    assert "Review a PR" in worker._build_system_prompt()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_worker_skill_loading.py -v`
Expected: new tests FAIL (current `_build_system_prompt` only comma-joins names, doesn't read disk).

- [ ] **Step 3: Update `_build_system_prompt`**

In `src/agents/worker_pool.py`, replace the current `skills_info` block inside `_build_system_prompt` (lines ~314–316):

```python
        skills_info = ""
        if self.config.skills:
            from pathlib import Path
            from config import get_config
            from skills.loader import list_skills

            skills_dir = Path(get_config().knowledge_base.skills_dir)
            available = list_skills(skills_dir)
            enabled = [s for s in available if s.name in self.config.skills]
            if enabled:
                lines = [f"- **{s.name}**: {s.description}" for s in enabled]
                skills_info = (
                    "\n\n## 可用技能（use the load_skill tool to fetch full content）\n"
                    + "\n".join(lines)
                )
            missing = set(self.config.skills) - {s.name for s in available}
            if missing:
                logger.warning(
                    f"Worker {self.id}: skills not installed: {sorted(missing)}"
                )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_worker_skill_loading.py -v`
Expected: PASS (5 tests total). The `load_skill` string appears in the prompt via the `## 可用工具` section that already iterates `self.tools.get_definitions()`.

- [ ] **Step 5: Commit**

```bash
git add src/agents/worker_pool.py tests/test_worker_skill_loading.py
git commit -m "feat(worker): enrich system prompt with per-skill name and description"
```

---

## Task 12: Ensure `data/skills/` exists at startup

**Files:**
- Modify: `src/web/api.py` (startup lifespan)

- [ ] **Step 1: Locate existing startup mkdir block**

Find the block that creates `data/chroma/` / `data/uploads/`. Run:

```bash
grep -n "mkdir\|upload_directory\|persist_directory" src/web/api.py
```

- [ ] **Step 2: Add `skills_dir` mkdir**

Next to the existing mkdir calls, add:

```python
        Path(config.knowledge_base.skills_dir).mkdir(parents=True, exist_ok=True)
```

(Adjust the `config` variable name to match the local context where the other mkdirs happen.)

- [ ] **Step 3: Smoke check**

Delete `data/skills/` if present, then:

```bash
rm -rf data/skills
python -c "from web.api import app; import asyncio"  # or just start the server briefly
ls -la data/skills
```

Expected: `data/skills/` exists and is empty.

- [ ] **Step 4: Commit**

```bash
git add src/web/api.py
git commit -m "feat(startup): ensure data/skills directory exists"
```

---

## Task 13: Declare `skills_dir` in `config.yaml`

**Files:**
- Modify: `config.yaml`

- [ ] **Step 1: Locate the `knowledge_base` section**

```bash
grep -n "knowledge_base:" config.yaml
```

- [ ] **Step 2: Add the new field**

Add one line under `knowledge_base:` (right below `upload_directory:`):

```yaml
knowledge_base:
  vector_store: chroma
  persist_directory: "./data/chroma"
  upload_directory: "./data/uploads"
  skills_dir: "./data/skills"              # NEW
  embedding_provider: dashscope
  # ... (rest unchanged)
```

- [ ] **Step 3: Verify config still loads**

Run: `python -c "from config import load_config; print(load_config().knowledge_base.skills_dir)"`
Expected: `./data/skills`

- [ ] **Step 4: Commit**

```bash
git add config.yaml
git commit -m "chore(config): declare knowledge_base.skills_dir"
```

---

## Task 14: End-to-end smoke test

**Files:**
- Test: `tests/test_skills_end_to_end.py`

Pulls the entire flow together: install a fake skill via the installer → rebuild a worker's system prompt → invoke `load_skill` tool → assert body is returned.

- [ ] **Step 1: Write the test**

Create `tests/test_skills_end_to_end.py`:

```python
import subprocess
from pathlib import Path

import pytest

from agents.base_agent import ToolRegistry
from agents.worker_pool import WorkerAgent, WorkerConfig
from skills.installer import install_from_github


def _init_remote_repo(repo_dir: Path) -> str:
    repo_dir.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo_dir)], check=True)
    subprocess.run(["git", "-C", str(repo_dir), "config", "user.email", "t@t"], check=True)
    subprocess.run(["git", "-C", str(repo_dir), "config", "user.name", "t"], check=True)
    (repo_dir / "SKILL.md").write_text(
        "---\nname: code-review\ndescription: Review a PR\n---\n# full review guide\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "-C", str(repo_dir), "add", "SKILL.md"], check=True)
    subprocess.run(["git", "-C", str(repo_dir), "commit", "-q", "-m", "init"], check=True)
    return f"file://{repo_dir}"


class _FakeProvider:
    async def chat(self, *args, **kwargs):  # pragma: no cover
        raise NotImplementedError


@pytest.mark.asyncio
async def test_install_then_worker_can_load_via_tool(tmp_path):
    from config import get_config
    get_config().knowledge_base.skills_dir = str(tmp_path / "skills")

    remote = tmp_path / "remote"
    url = _init_remote_repo(remote)
    install_from_github(url, Path(get_config().knowledge_base.skills_dir))

    cfg = WorkerConfig(
        name="reviewer",
        provider_type="anthropic",
        api_key="",
        model="claude-sonnet-4-20250514",
        skills=["code-review"],
    )
    worker = WorkerAgent(cfg, tools=ToolRegistry(), provider=_FakeProvider())

    # System prompt advertises the skill
    prompt = worker._build_system_prompt()
    assert "code-review" in prompt
    assert "Review a PR" in prompt

    # Tool is registered and returns body when invoked
    tool = worker.tools.get("load_skill")
    assert tool is not None
    result = await tool.execute({"name": "code-review"})
    assert "full review guide" in result
```

- [ ] **Step 2: Run test**

Run: `pytest tests/test_skills_end_to_end.py -v`
Expected: PASS.

- [ ] **Step 3: Run the full skills test suite once**

Run: `pytest tests/test_skills_config.py tests/test_skills_loader.py tests/test_skills_tool.py tests/test_skills_installer.py tests/test_skills_cli.py tests/test_worker_skill_loading.py tests/test_skills_end_to_end.py -v`
Expected: all green.

- [ ] **Step 4: Commit**

```bash
git add tests/test_skills_end_to_end.py
git commit -m "test(skills): end-to-end install → worker → tool invocation"
```

---

## Task 15: Update CLAUDE.md with skill mechanism docs

**Files:**
- Modify: `CLAUDE.md`

- [ ] **Step 1: Add a new section**

Append to `CLAUDE.md` (before or after the existing `## Architecture` section, whichever fits):

```markdown
### Worker skills

Each worker can be assigned a list of skills via its template in `config.yaml`:

```yaml
worker_templates:
  reviewer:
    model: claude-sonnet-4-20250514
    provider: anthropic
    skills: ["code-review"]
```

Skills live in `data/skills/<name>/SKILL.md` in Claude Code skill format (YAML frontmatter with `name` + `description`, then markdown body). A worker's system prompt only lists `name + description`; the LLM calls the `load_skill` tool to fetch the full body on demand. This keeps the prompt small and enables hot reload — a newly installed skill is visible to the next task without restarting.

Install skills from GitHub:

```bash
python -m src.skills install github.com/anthropics/skills/code-review
python -m src.skills list
python -m src.skills remove code-review
python -m src.skills update code-review
```

Missing skills (listed in config but not on disk) produce a warning and are silently skipped from the system prompt — the worker still starts. Skill loading is whitelisted per-worker: even if `data/skills/` contains 20 skills, a worker can only `load_skill` the ones in its own `config.skills`.
```

- [ ] **Step 2: Commit**

```bash
git add CLAUDE.md
git commit -m "docs(claude): document worker skills mechanism"
```

---

## Self-Review Checklist

- [x] **Spec coverage**: every Goal, Non-Goal, Component, Error Handling row, Testing Strategy item from the spec is realized in a task above.
  - Config field → Task 1
  - `Skill` dataclass + `list_skills` → Task 2
  - `load_skill` + path traversal → Task 3
  - Loader edge cases → Task 4
  - `LoadSkillTool` + whitelist → Task 5
  - `install_from_github` happy path → Task 6
  - Subpath / name / force / missing → Task 7
  - `remove_skill` / `update_skill` → Task 8
  - CLI → Task 9
  - Worker `__init__` integration → Task 10
  - Worker `_build_system_prompt` integration + hot reload → Task 11
  - Startup mkdir → Task 12
  - `config.yaml` declaration → Task 13
  - End-to-end → Task 14
  - CLAUDE.md doc → Task 15

- [x] **Placeholder scan**: no "TBD", "TODO", "add appropriate error handling", or stub steps.

- [x] **Type consistency**:
  - `Skill` dataclass fields `(name, description, body, path, references)` are consistent across loader, tool, installer, tests.
  - `LoadSkillTool.execute(arguments: dict)` signature matches `BaseTool.execute` abstract.
  - `LoadSkillTool` uses `input_schema` property, not `parameters` — matches the rest of the codebase.
  - `install_from_github(url, skills_dir, name=None, force=False) -> Skill` signature consistent between Task 6, 7, 8 and the CLI.
  - `update_skill(skills_dir, name) -> Skill` signature consistent.
  - `KnowledgeBaseConfig.skills_dir` default `"./data/skills"` matches config.yaml entry and all test patches.
