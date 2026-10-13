"""Skills: instructions an agent loads only when it needs them (LangChain's skills pattern).

Each skill is `<dir>/<name>/SKILL.md` with a small frontmatter:

    ---
    name: structured-report
    description: One line telling the agent when to use it.
    ---
    The instructions...

The prompt lists only names and descriptions (`skills_section`); the agent calls
`load_skill` to read one, so long instructions cost tokens only when used. Short skills that
must always be followed can go into the prompt whole instead (`skills_inline`).
"""

from dataclasses import dataclass
from pathlib import Path

from langchain.tools import tool
from langchain_core.tools import BaseTool


@dataclass(frozen=True)
class Skill:
    name: str
    description: str
    body: str


def parse_skill(text: str, default_name: str) -> Skill:
    meta: dict[str, str] = {}
    body = text.strip()
    if body.startswith("---"):
        header, _, body = body[3:].partition("\n---")
        for line in header.strip().splitlines():
            key, sep, value = line.partition(":")
            if sep:
                meta[key.strip()] = value.strip()
    return Skill(name=meta.get("name", default_name), description=meta.get("description", ""), body=body.strip())


def load_skills(directory: Path) -> dict[str, Skill]:
    skills = (parse_skill(path.read_text(encoding="utf-8"), path.parent.name)
              for path in sorted(directory.glob("*/SKILL.md")))
    return {skill.name: skill for skill in skills}


def skills_section(skills: dict[str, Skill]) -> str:
    lines = "\n".join(f"- {skill.name}: {skill.description}" for skill in skills.values())
    return f"SKILLS (call `load_skill` with the name to read the full instructions before using one):\n{lines}"


def skills_inline(skills: dict[str, Skill], title: str = "FORMATS") -> str:
    """Every skill's full instructions in the prompt, for agents that skip optional `load_skill`
    calls (small models do) and whose skills are short."""
    blocks = "\n\n".join(f"### {skill.name}\nUse for: {skill.description}\n{skill.body}" for skill in skills.values())
    return f"{title}:\n\n{blocks}"


def make_load_skill_tool(skills: dict[str, Skill]) -> BaseTool:
    @tool
    def load_skill(name: str) -> str:
        """Read the full instructions of one of your skills.

        Args:
            name: A skill name from the SKILLS list in your instructions.
        """
        skill = skills.get(name.strip())
        if not skill:
            return f"Unknown skill {name!r}. Available: {', '.join(skills)}."
        return skill.body

    return load_skill
