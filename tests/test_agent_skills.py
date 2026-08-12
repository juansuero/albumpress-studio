from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SKILLS_ROOT = ROOT / ".agents" / "skills"
EXPECTED_SKILLS = {
    "use-albumpress-studio",
    "manage-album-project",
    "choose-separation-candidates",
    "review-instrumental-outputs",
    "recover-albumpress-job",
    "prepare-instrumental-video",
    "export-instrumental-audio",
    "maintain-project-storage",
    "produce-instrumental-album",
}


def test_agent_skill_pack_is_complete_and_discoverable() -> None:
    assert {path.name for path in SKILLS_ROOT.iterdir() if path.is_dir()} == EXPECTED_SKILLS
    for name in EXPECTED_SKILLS:
        skill_text = (SKILLS_ROOT / name / "SKILL.md").read_text(encoding="utf-8")
        metadata_text = (SKILLS_ROOT / name / "agents" / "openai.yaml").read_text(encoding="utf-8")
        frontmatter = re.match(r"^---\n(.*?)\n---\n", skill_text, re.DOTALL)
        assert frontmatter, name
        fields = [line.split(":", 1)[0] for line in frontmatter.group(1).splitlines()]
        assert fields == ["name", "description"], name
        assert f"name: {name}" in frontmatter.group(1)
        assert "TODO" not in skill_text
        assert f"${name}" in metadata_text


def test_skill_runtime_contract_and_public_index_exist() -> None:
    contract = ROOT / "docs" / "agents" / "skill-runtime-contract.md"
    index = ROOT / "docs" / "agents" / "skills.md"
    assert contract.is_file()
    assert index.is_file()
    for name in EXPECTED_SKILLS:
        assert f"${name}" in index.read_text(encoding="utf-8")
    contract_text = contract.read_text(encoding="utf-8")
    assert "Do not assume `GET` means observational" in contract_text
    assert "reconcile orphaned jobs" in contract_text


def test_public_copy_explains_the_agent_first_workflow() -> None:
    readme_text = (ROOT / "README.md").read_text(encoding="utf-8")
    opening = readme_text.split("## Setup", 1)[0]
    assert "Agent-first" in opening
    assert "Codex is the reference integration" in opening
    assert "$use-albumpress-studio" in opening
    assert "Integration details vary by harness" in opening
    assert "MP3 and MP4" in opening

    project_metadata = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert "packaging instrumental albums as MP3 and MP4" in project_metadata

    html = (ROOT / "frontend" / "index.html").read_text(encoding="utf-8")
    assert 'name="description"' in html
    assert "agent-first AlbumPress Studio" in html


def test_fast_export_runtime_is_part_of_the_public_repository() -> None:
    worker = ROOT / "frontend" / "scripts" / "fast-export-web-worker.mjs"
    entry = ROOT / "frontend" / "scripts" / "fast-export-web-entry.tsx"
    assert worker.is_file()
    assert entry.is_file()

    implementation = (ROOT / "app" / "video_fast_export.py").read_text(encoding="utf-8")
    assert worker.name in implementation
    assert entry.name in implementation
