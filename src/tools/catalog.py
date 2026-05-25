"""Tool catalog helpers shared by orchestration runtimes."""

from __future__ import annotations

from collections.abc import Iterable

from src.agents.base_agent import BaseTool

TOOL_ALIASES: dict[str, set[str]] = {
    "*": {"read_file", "write_file", "list_files", "run_shell", "send_mail", "read_mail", "web_search", "web_fetch", "database_query", "github_create_issue", "github_search", "playwright_crawler"},
    "all": {"read_file", "write_file", "list_files", "run_shell", "send_mail", "read_mail", "web_search", "web_fetch", "database_query", "github_create_issue", "github_search", "playwright_crawler"},
    "filesystem": {"read_file", "write_file", "list_files"},
    "file": {"read_file", "write_file", "list_files"},
    "files": {"read_file", "write_file", "list_files"},
    "shell": {"run_shell"},
    "terminal": {"run_shell"},
    "cli": {"run_shell"},
    "git": {"run_shell"},
    "mail": {"send_mail", "read_mail"},
    "communication": {"send_mail", "read_mail"},
    "collaboration": {"send_mail", "read_mail"},
    "web": {"web_search", "web_fetch", "playwright_crawler"},
    "browser": {"web_search", "web_fetch", "playwright_crawler"},
    "search": {"web_search"},
    "fetch": {"web_fetch", "playwright_crawler"},
    "crawler": {"playwright_crawler"},
    "database": {"database_query"},
    "sql": {"database_query"},
    "github": {"github_create_issue", "github_search"},
}


def resolve_tool_names(
    configured: Iterable[str] | None,
    available: Iterable[str],
) -> tuple[set[str], set[str]]:
    """Expand configured tool names/aliases against available concrete tools.

    Empty configuration preserves the historical behavior: all available tools
    are enabled. Unknown names are returned for logging.
    """
    available_set = set(available)
    configured_names = [str(name).strip() for name in (configured or []) if str(name).strip()]
    if not configured_names:
        return set(available_set), set()

    selected: set[str] = set()
    unknown: set[str] = set()

    for raw_name in configured_names:
        key = raw_name.lower()
        if key in available_set:
            selected.add(key)
            continue

        expanded = TOOL_ALIASES.get(key)
        if expanded:
            selected.update(expanded & available_set)
            continue

        unknown.add(raw_name)

    return selected, unknown


def select_allowed_tools(
    candidates: Iterable[BaseTool],
    configured: Iterable[str] | None,
) -> tuple[list[BaseTool], set[str]]:
    """Return concrete tools allowed by a worker template."""
    tools = list(candidates)
    selected_names, unknown = resolve_tool_names(configured, (tool.name for tool in tools))
    return [tool for tool in tools if tool.name in selected_names], unknown
