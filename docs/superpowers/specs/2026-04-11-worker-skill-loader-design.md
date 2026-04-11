# Worker Skill Loader — Design Spec

**Date**: 2026-04-11
**Status**: Draft (pending user review)
**Author**: brainstormed with Claude

## Background

MemoX's `WorkerAgent` (`src/agents/worker_pool.py`) already has a `WorkerConfig.skills: list[str]` field, populated from worker templates in `config.yaml` (e.g. `skills: ["code-review", "docx"]`). However, this field is purely cosmetic today: `_build_system_prompt()` only joins the names into the system prompt as `"已启用的技能: code-review, docx"`, without loading any actual content. The LLM must guess what those names mean.

This spec defines a real skill loading mechanism that:

1. Adopts the **Claude Code skill format** (`SKILL.md` with YAML frontmatter) so GitHub-hosted skills from the existing ecosystem (e.g. `anthropics/skills`) can be reused verbatim.
2. Provides an **install CLI** that fetches a skill from a GitHub URL (with optional subpath) into `data/skills/<name>/`.
3. Exposes skills to workers via **lazy loading**: the system prompt only lists `name + description`, and the worker calls a `load_skill` tool to pull the full content when it decides it needs it.
4. Supports **hot reload**: newly installed skills become available on the worker's next task without restarting the backend.

## Goals

- Enable MemoX workers to consume Claude Code-format skills from GitHub.
- Keep system prompt footprint small even when many skills are enabled.
- Zero-config fallback: missing skills warn and skip rather than block startup.
- Per-worker skill scoping: a worker can only load skills listed in its own `WorkerConfig.skills`.

## Non-Goals (YAGNI)

Deliberately **not in scope** for this change:

1. Skill marketplace / remote registry — only direct GitHub URL installs
2. Skill version locking — `update` just re-clones latest
3. Executable scripts inside skills — markdown only (`SKILL.md` + `references/*.md`)
4. Automatic skill discovery beyond `config.skills` whitelist
5. Frontend UI for skill management — CLI only
6. HTTP `/api/skills/install` endpoint — avoids exposing git to the web
7. Claude Code plugin manifest parsing — raw `SKILL.md` only

## Architecture Overview

New module `src/skills/`, peer to `src/agents/` and `src/knowledge/`:

```
src/skills/
├── __init__.py
├── loader.py       # SkillLoader: scan data/skills/, parse SKILL.md frontmatter
├── installer.py    # git clone + subpath copy (used by CLI)
├── cli.py          # python -m src.skills {install|list|remove|update}
└── tool.py         # LoadSkillTool: lazy-load tool registered into ToolRegistry
```

On-disk layout under `data/skills/`:

```
data/skills/
├── code-review/
│   ├── SKILL.md           # frontmatter: name, description; body: markdown
│   ├── .install.json      # {source_url, installed_at}
│   └── references/        # optional sub-resources
│       └── checklist.md
└── docx/
    └── SKILL.md
```

### Design principles

1. `src/skills/` is a self-contained module; business logic does **not** leak into `worker_pool.py`.
2. `SkillLoader` reads the filesystem on every call (no in-memory cache) — enables hot reload at negligible IO cost.
3. Worker-side changes are minimal: register one extra tool, enrich one system-prompt section.
4. Skill content only flows through standard tool-call plumbing — no changes to `_run_agent_loop`.

## Component Design

### 1. `src/skills/loader.py`

Pure functions, one dataclass, no classes:

```python
@dataclass
class Skill:
    name: str              # directory name (== frontmatter.name)
    description: str       # frontmatter.description (single line, injected into system prompt)
    body: str              # markdown after the frontmatter block
    path: Path             # data/skills/<name>/
    references: list[str]  # filenames under references/ (may be empty)

def list_skills(skills_dir: Path) -> list[Skill]:
    """Scan skills_dir; parse each subdirectory's SKILL.md.
    Subdirectories without SKILL.md or with broken frontmatter are logged and skipped."""

def load_skill(skills_dir: Path, name: str, ref: str | None = None) -> str:
    """
    ref=None          → returns full SKILL.md body (without frontmatter)
    ref='foo.md'      → returns content of references/foo.md
    not found         → raises FileNotFoundError
    path traversal    → raises ValueError
    """
```

**Frontmatter parsing**: use `PyYAML` (already a transitive dep) to parse the `---\n...\n---` block. Only `name` and `description` are consumed; any other frontmatter keys are left as part of the body so the LLM can still see them.

**Path safety**: for the `ref` parameter, `(skills_dir / name / "references" / ref).resolve()` must still be within `(skills_dir / name).resolve()`. Otherwise raise `ValueError("ref must stay inside skill directory")`. This blocks `ref="../../../etc/passwd"`-style traversal.

**No caching**: every call re-scans / re-reads. Worker concurrency is low; IO is negligible; the benefit is automatic hot reload.

### 2. `src/skills/tool.py`

```python
class LoadSkillTool(BaseTool):
    name = "load_skill"
    description = "Load the full content of a skill by name, or a sub-reference file under the skill's references/ directory."
    parameters = {
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "Skill name"},
            "ref":  {"type": "string", "description": "Optional sub-reference filename under references/"},
        },
        "required": ["name"],
    }

    def __init__(self, skills_dir: Path, allowed_skills: set[str]):
        self.skills_dir = skills_dir
        self.allowed_skills = allowed_skills  # per-worker whitelist

    async def execute(self, name: str, ref: str | None = None) -> str:
        if name not in self.allowed_skills:
            return f"Error: skill '{name}' is not enabled for this worker."
        try:
            return load_skill(self.skills_dir, name, ref)
        except (FileNotFoundError, ValueError) as e:
            return f"Error: {e}"
```

The `allowed_skills` whitelist is populated from `WorkerConfig.skills`, so a worker can never load a skill it wasn't granted — even if another worker on the same host has different permissions.

### 3. `src/agents/worker_pool.py` changes

Two small edits only.

**`WorkerAgent.__init__`** — after `self.tools` is initialized:

```python
if config.skills:
    from skills.tool import LoadSkillTool
    from config import get_config
    skills_dir = Path(get_config().knowledge_base.skills_dir)
    self.tools.register(LoadSkillTool(skills_dir, set(config.skills)))
```

**`WorkerAgent._build_system_prompt`** — replace the current `skills_info` block:

```python
skills_info = ""
if self.config.skills:
    from skills.loader import list_skills
    from config import get_config
    skills_dir = Path(get_config().knowledge_base.skills_dir)
    available = list_skills(skills_dir)                    # live scan
    enabled = [s for s in available if s.name in self.config.skills]
    if enabled:
        lines = [f"- **{s.name}**: {s.description}" for s in enabled]
        skills_info = (
            "\n\n## 可用技能（use the load_skill tool to fetch full content）\n"
            + "\n".join(lines)
        )
    missing = set(self.config.skills) - {s.name for s in available}
    if missing:
        logger.warning(f"Worker {self.id}: skills not installed: {sorted(missing)}")
```

Because `_build_system_prompt` is called at the start of each task execution, `list_skills()` runs fresh every time — newly installed skills appear automatically on the next task.

`_run_agent_loop` is unchanged.

### 4. `src/skills/installer.py`

```python
def install_from_github(
    url: str,                     # "github.com/owner/repo" or "github.com/owner/repo/subpath"
    skills_dir: Path,
    name: str | None = None,      # override skill name
    force: bool = False,          # overwrite existing
) -> Skill:
    """
    1. Parse url → (owner, repo, subpath)
    2. git clone --depth 1 https://github.com/owner/repo.git  into a TemporaryDirectory
    3. source = tmp_clone_root / subpath   (or tmp_clone_root if no subpath)
    4. Verify source / "SKILL.md" exists
    5. Parse SKILL.md frontmatter → final_name = name or frontmatter.name
    6. target = skills_dir / final_name
       - exists and not force → raise FileExistsError
       - exists and force     → shutil.rmtree(target)
    7. shutil.copytree(source, target)
    8. Write target/.install.json with {source_url, installed_at}
    9. Return the freshly-loaded Skill
    """

def remove_skill(skills_dir: Path, name: str) -> None:
    """shutil.rmtree(skills_dir / name). Raise FileNotFoundError if missing."""

def update_skill(skills_dir: Path, name: str) -> Skill:
    """
    Read data/skills/<name>/.install.json to recover source_url.
    Call install_from_github with force=True to re-clone and replace in place.
    Raise FileNotFoundError if .install.json is missing (user must re-install manually).
    Note: replacement is rmtree + copytree, not a filesystem-level atomic rename —
    there is a brief window where the skill directory does not exist. This is
    acceptable because list_skills() just skips missing directories, and concurrent
    worker tool calls will simply see a transient "skill not found" error.
    """
```

**Git invocation**: via `subprocess.run(["git", "clone", "--depth", "1", repo_url, tmp_dir], check=True)`. Requires system `git` (MemoX is already a git repo, so this assumption holds). No `GitPython` dependency.

**URL parsing**: accept all of `github.com/owner/repo`, `https://github.com/owner/repo`, `github.com/owner/repo/tree/main/subpath`, `github.com/owner/repo/subpath`. Normalize to `(repo_url, subpath)`.

### 5. `src/skills/cli.py`

Entrypoint: `python -m src.skills <command> [args]`. Uses standard-library `argparse`.

| Command | Behavior |
|---|---|
| `install <url> [--name NAME] [--force]` | Call `install_from_github`; print success with final path, or friendly error |
| `list` | Call `list_skills`; table with `name`, `description`, `source_url` (from `.install.json` if present) |
| `remove <name>` | Call `remove_skill`; confirm success or report not found |
| `update <name>` | Call `update_skill`; report result |

CLI layer catches `FileExistsError`, `FileNotFoundError`, `subprocess.CalledProcessError`, `ValueError` and prints short human messages with non-zero exit codes. No stack traces in normal failure paths.

## Configuration

Add one field to `config.yaml` under `knowledge_base`:

```yaml
knowledge_base:
  skills_dir: "data/skills"    # NEW — default shown
  embedding_provider: dashscope
  # ... existing fields ...
```

Add matching field to the `KnowledgeBaseConfig` dataclass in `src/config/__init__.py`:

```python
skills_dir: str = "data/skills"
```

At startup (`src/web/api.py` lifespan or wherever `data/` directories are ensured), `Path(config.knowledge_base.skills_dir).mkdir(parents=True, exist_ok=True)`.

**Backward compatibility**: worker templates that already declare `skills: [...]` keep working unchanged. The semantic shift from "just a label" to "must match an installed skill" is covered by the "missing skill → warn and skip" behavior, so no existing config breaks.

## Error Handling Matrix

| Situation | Behavior | Location |
|---|---|---|
| `config.skills` name has no matching directory | `logger.warning`, worker starts, name omitted from system prompt | `WorkerAgent._build_system_prompt` |
| `data/skills/` directory does not exist | `mkdir` at startup; `list_skills` returns `[]` | startup code + `loader.list_skills` |
| `SKILL.md` frontmatter malformed | `logger.warning(f"skipping {dir}: {err}")`; loader skips | `list_skills` |
| `load_skill` called with skill not in `allowed_skills` | Tool result: `"Error: skill X not enabled for this worker"` (no exception) | `LoadSkillTool.execute` |
| `load_skill` `ref` attempts path traversal | `ValueError` → tool result `"Error: ref must stay inside skill directory"` | `loader.load_skill` |
| CLI `install`: `git clone` fails | Non-zero exit + `"git clone failed: check network or URL"` | `cli.py` |
| CLI `install`: target exists without `--force` | Non-zero exit + `"skill X already installed; use --force or remove first"` | `installer.install_from_github` |
| CLI `install`: subpath has no `SKILL.md` | Non-zero exit + `"no SKILL.md found at <subpath>"` | `installer.install_from_github` |
| CLI `update`: `.install.json` missing | Non-zero exit + `"cannot auto-update: reinstall manually with install <url>"` | `installer.update_skill` |

## Testing Strategy

### `tests/test_skills_loader.py`

Uses `tmp_path` to construct fake skill directories:

- Happy path: valid `SKILL.md` with both frontmatter fields → correct `Skill` object
- Missing `SKILL.md` → directory skipped, no exception
- Malformed YAML frontmatter → directory skipped, warning logged
- Missing `name` or `description` frontmatter key → directory skipped
- `load_skill(name=X)` → body without frontmatter block
- `load_skill(name=X, ref="valid.md")` → correct file content
- `load_skill(name=X, ref="../../../etc/passwd")` → `ValueError`
- `load_skill` with unknown name → `FileNotFoundError`

### `tests/test_skills_installer.py`

Tests build a minimal local git repo (`git init` in a `tmp_path` subdir, commit a `SKILL.md`), then point `install_from_github` at `file://<path>` — no real GitHub, no network:

- `install_from_github`: clones, copies, writes `.install.json`, skill is loadable via `list_skills`
- `install_from_github` with `subpath`: only the subpath subtree lands in `data/skills/`
- `install_from_github` with `name` override: target directory uses the override
- Install into existing target without `force` → `FileExistsError`
- Install with `force=True` → previous skill replaced
- `remove_skill`: directory gone
- `update_skill`: re-clones, preserves `.install.json`
- `update_skill` without `.install.json` → `FileNotFoundError`
- Install from a repo whose target `SKILL.md` has broken frontmatter → clean error, target not created

### Worker integration

Add one test to `tests/test_integration_multiagent.py` (or a new `tests/test_worker_skill_loading.py`):

- Create a worker with `skills=["dummy"]` and a fake `data/skills/dummy/SKILL.md`
- Assert `load_skill` tool is registered on the worker's `ToolRegistry`
- Assert `_build_system_prompt()` output contains `dummy` and its description
- Assert that calling the tool with `name="dummy"` returns the body
- Assert that calling the tool with `name="not-allowed"` returns an error string (whitelist enforcement)

## Files Changed

### New files

- `src/skills/__init__.py`
- `src/skills/loader.py`
- `src/skills/installer.py`
- `src/skills/tool.py`
- `src/skills/cli.py` (or `__main__.py`)
- `tests/test_skills_loader.py`
- `tests/test_skills_installer.py`

### Modified files

- `src/config/__init__.py` — add `skills_dir: str = "data/skills"` to `KnowledgeBaseConfig`
- `src/agents/worker_pool.py` — register `LoadSkillTool` in `__init__`, enrich `skills_info` in `_build_system_prompt`
- `config.yaml` — add `knowledge_base.skills_dir`
- `src/web/api.py` (or wherever startup mkdirs happen) — ensure `data/skills/` exists

### Optional

- `CLAUDE.md` — add a short section documenting the skill mechanism and CLI commands

## Size Estimate

- New code: ~400 lines including tests
- Modified code: ~25 lines

## Open Questions

None at design time. All Q1–Q5 clarifications captured above:

- **Q1 (format + source)**: Claude Code skill format from GitHub / local dir
- **Q2 (install mechanism)**: local directory + internal CLI that clones
- **Q3 (context injection)**: lazy load via `load_skill` tool
- **Q4 (CLI granularity)**: URL + optional subpath, one skill per install
- **Q5 (missing / hot reload)**: warn and skip on missing; live filesystem scan on every task for hot reload
