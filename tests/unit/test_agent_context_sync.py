"""Regression coverage for Codex context validation without a mirrored tree."""

from pathlib import Path

from scripts.check_agent_context import check_repo

REPO_ROOT = Path(__file__).resolve().parents[2]


def _minimal_context_repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    (root / ".agents/skills/example").mkdir(parents=True)
    (root / ".codex/agents").mkdir(parents=True)
    (root / "AGENTS.md").write_text("instructions\n", encoding="utf-8")
    (root / ".codex/config.toml").write_text("project_doc_max_bytes = 32768\n")
    (root / ".agents/skills/example/SKILL.md").write_text(
        "---\nname: example\ndescription: Example\n---\nInstructions\n"
    )
    (root / ".codex/agents/reviewer.toml").write_text(
        'name = "reviewer"\ndescription = "Review"\ndeveloper_instructions = "Review diff"\n'
    )
    return root


def test_checked_in_context_is_valid() -> None:
    assert check_repo(REPO_ROOT) == []


def test_single_tree_needs_no_claude_mirror(tmp_path: Path) -> None:
    assert check_repo(_minimal_context_repo(tmp_path)) == []


def test_detects_missing_skill_entrypoint_and_doc_budget(tmp_path: Path) -> None:
    root = _minimal_context_repo(tmp_path)
    (root / ".agents/skills/example/SKILL.md").unlink()
    (root / ".codex/config.toml").write_text("project_doc_max_bytes = 2\n")
    errors = check_repo(root)
    assert any("skill entry point missing" in error for error in errors)
    assert any("project-doc budget" in error for error in errors)


def test_detects_invalid_skill_metadata(tmp_path: Path) -> None:
    root = _minimal_context_repo(tmp_path)
    (root / ".agents/skills/example/SKILL.md").write_text("---\nname: wrong\n---\n")
    errors = check_repo(root)
    assert any("invalid skill name" in error for error in errors)
    assert any("invalid skill description" in error for error in errors)


def test_detects_invalid_native_agent_files(tmp_path: Path) -> None:
    root = _minimal_context_repo(tmp_path)
    (root / ".codex/agents/reviewer.toml").write_text("developer_instructions = [\n")
    assert any("invalid Codex agent TOML" in error for error in check_repo(root))


def test_detects_missing_agent_instructions(tmp_path: Path) -> None:
    root = _minimal_context_repo(tmp_path)
    (root / ".codex/agents/reviewer.toml").write_text('name = "reviewer"\n')
    assert any("agent developer_instructions missing" in error for error in check_repo(root))
