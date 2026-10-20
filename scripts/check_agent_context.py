"""Validate the repository's single Codex instruction and skill tree."""

from __future__ import annotations

import argparse
import re
import sys
import tomllib
from pathlib import Path

HOST_ONLY_SKILLS = frozenset({"agent-tools", "find-skills", "skill-creator"})


def check_repo(root: Path) -> list[str]:
    """Return actionable context errors without modifying the repository."""
    errors: list[str] = []
    config_path = root / ".codex/config.toml"
    try:
        config = tomllib.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        errors.append(f"invalid .codex/config.toml: {exc}")
        config = {}
    budget = config.get("project_doc_max_bytes", 32768)
    if type(budget) is not int or budget <= 0:
        errors.append("project_doc_max_bytes must be a positive integer")
        budget = 32768
    guide = root / "AGENTS.md"
    if not guide.is_file() or not guide.read_text(encoding="utf-8").strip():
        errors.append("missing or empty AGENTS.md")
    elif guide.stat().st_size > budget:
        errors.append(f"AGENTS.md exceeds the project-doc budget of {budget} bytes")
    skills_root = root / ".agents/skills"
    if not skills_root.is_dir():
        errors.append("missing .agents/skills")
    else:
        for folder in sorted(skills_root.iterdir()):
            if not folder.is_dir() or folder.name in HOST_ONLY_SKILLS:
                continue
            entry = folder / "SKILL.md"
            if not entry.is_file():
                errors.append(f"skill entry point missing: {folder.name}")
                continue
            text = entry.read_text(encoding="utf-8")
            sections = text.split("---", 2)
            if len(sections) != 3 or sections[0].strip():
                errors.append(f"invalid skill frontmatter: {folder.name}")
                continue
            for key in ("name", "description"):
                match = re.search(rf"^{key}: *([^\n]+)$", sections[1], re.MULTILINE)
                value = match.group(1).strip().strip('"\'') if match else ""
                if not value or (key == "name" and value != folder.name):
                    errors.append(f"invalid skill {key}: {folder.name}")
    for path in sorted((root / ".codex/agents").glob("*.toml")):
        try:
            agent = tomllib.loads(path.read_text(encoding="utf-8"))
        except (OSError, tomllib.TOMLDecodeError) as exc:
            errors.append(f"invalid Codex agent TOML: {path.name} ({exc})")
            continue
        for key in ("name", "description", "developer_instructions"):
            if not isinstance(agent.get(key), str) or not agent[key].strip():
                errors.append(f"agent {key} missing: {path.name}")
        if agent.get("name") != path.stem:
            errors.append(f"agent name does not match file: {path.name}")
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    errors = check_repo(parser.parse_args(argv).root.resolve())
    if errors:
        print("Agent context check failed:")
        for error in errors:
            print(f"- {error}")
        return 1
    print("Agent context check passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
