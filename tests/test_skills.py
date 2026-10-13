from app.agents.research.agent import REPORTER_SKILLS, RESEARCHER_SKILLS
from app.agents.skills import load_skills, make_load_skill_tool, parse_skill, skills_inline, skills_section


def test_parse_skill_reads_frontmatter_and_body():
    skill = parse_skill("---\nname: brief\ndescription: Short: one line.\n---\n# Steps\n1. Do it.\n", "dir-name")
    assert skill.name == "brief"
    assert skill.description == "Short: one line."  # only the first colon splits
    assert skill.body == "# Steps\n1. Do it."


def test_parse_skill_without_frontmatter_uses_the_directory_name():
    skill = parse_skill("Just instructions.", "fallback")
    assert (skill.name, skill.description, skill.body) == ("fallback", "", "Just instructions.")


def test_load_skills_from_directory(tmp_path):
    (tmp_path / "a").mkdir()
    (tmp_path / "a" / "SKILL.md").write_text("---\nname: alpha\ndescription: First.\n---\nBody A", encoding="utf-8")
    (tmp_path / "notes.md").write_text("ignored", encoding="utf-8")
    assert list(load_skills(tmp_path)) == ["alpha"]


def test_load_skill_tool_returns_body_or_the_valid_names():
    skills = {"alpha": parse_skill("---\nname: alpha\ndescription: d\n---\nBody A", "alpha")}
    load_skill = make_load_skill_tool(skills)
    assert load_skill.invoke({"name": " alpha "}) == "Body A"
    assert "Available: alpha" in load_skill.invoke({"name": "beta"})


def test_research_skills_are_complete_and_listed():
    assert set(RESEARCHER_SKILLS) == {"web-research", "market-data", "academic"}
    assert set(REPORTER_SKILLS) == {"summary", "list", "structured-report", "comparison", "company-brief"}
    for skills in (RESEARCHER_SKILLS, REPORTER_SKILLS):
        section = skills_section(skills)
        for skill in skills.values():
            assert skill.description and skill.body
            assert f"- {skill.name}: {skill.description}" in section


def test_reporter_formats_are_inlined_in_full():
    formats = skills_inline(REPORTER_SKILLS)
    for skill in REPORTER_SKILLS.values():
        assert f"### {skill.name}\nUse for: {skill.description}\n{skill.body}" in formats
