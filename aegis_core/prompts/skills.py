"""Skills-as-catalog with content-hash-versioned lazy loading: skills are
advertised as a small index (name, description, path, content hash); the
model reads the full SKILL.md body only on demand via the memory backend's
`read`, and only needs to re-read when the hash in the index changes.
Convergent pattern: openclaw and deepagents both independently arrived at
this. See ARCHITECTURE.md / FEATURE_MATRIX.md.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from aegis_core.memory.backend import BackendProtocol


@dataclass
class SkillEntry:
    name: str
    description: str
    path: str
    content_hash: str


def _parse_frontmatter(content: str) -> tuple[str, str]:
    name, description = "unknown", ""
    if content.startswith("---"):
        end = content.find("---", 3)
        if end != -1:
            for line in content[3:end].splitlines():
                if ":" in line:
                    key, _, value = line.partition(":")
                    key = key.strip().lower()
                    value = value.strip()
                    if key == "name":
                        name = value
                    elif key == "description":
                        description = value
    return name, description


@dataclass
class SkillCatalog:
    memory: BackendProtocol
    skills_dir: str = "/skills"

    async def discover(self) -> list[SkillEntry]:
        """Scan skills_dir for SKILL.md files, returning only the
        lightweight index (never the full body — that's read on demand)."""
        entries: list[SkillEntry] = []
        for path in await self.memory.glob(f"{self.skills_dir}/*/SKILL.md"):
            content = await self.memory.read(path)
            name, description = _parse_frontmatter(content)
            content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()[:12]
            entries.append(
                SkillEntry(name=name or path, description=description, path=path, content_hash=content_hash)
            )
        return entries

    def render_index(self, entries: list[SkillEntry]) -> str:
        if not entries:
            return ""
        lines = [
            "Available skills — read the file at its path to use one; "
            "re-read only if you see the hash has changed since you last read it:"
        ]
        for e in entries:
            lines.append(f"- {e.name} (path={e.path}, hash={e.content_hash}): {e.description}")
        return "\n".join(lines)
