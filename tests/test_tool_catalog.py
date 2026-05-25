import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from tools.catalog import resolve_tool_names


def test_tool_aliases_expand_to_concrete_tools():
    available = {"read_file", "write_file", "list_files", "run_shell", "send_mail", "read_mail", "web_search"}

    selected, unknown = resolve_tool_names(
        ["filesystem", "shell", "git", "mail", "web_search"],
        available,
    )

    assert selected == available
    assert unknown == set()


def test_empty_tool_config_allows_all_available_tools():
    available = {"read_file", "write_file"}

    selected, unknown = resolve_tool_names([], available)

    assert selected == available
    assert unknown == set()
