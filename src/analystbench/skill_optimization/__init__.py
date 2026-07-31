"""Isolated Skill self-optimization subsystem.

The package owns Skill packaging, versioning, mutation, comparison and
promotion. Existing evaluation modules interact with it only through optional
protocols that are injected by the application composition roots.
"""

from analystbench.skill_optimization.registry import SkillRegistryService
from analystbench.skill_optimization.sandbox import SkillWorkspacePreparer

__all__ = ["SkillRegistryService", "SkillWorkspacePreparer"]
