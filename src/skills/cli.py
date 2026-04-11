"""Command-line interface: python -m skills ..."""

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
    parser = argparse.ArgumentParser(prog="python -m skills")
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
    logger.remove()
    logger.add(sys.stderr, level="WARNING")
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)
