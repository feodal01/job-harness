from __future__ import annotations

import unittest
from pathlib import Path

_PLUGIN_ROOT = Path(__file__).resolve().parents[2]


class RuntimeSkillContractTests(unittest.TestCase):
    def test_search_workflow_revalidates_saved_briefs_against_current_cli(self) -> None:
        workflow = (_PLUGIN_ROOT / "skills" / "job-search-workflow" / "SKILL.md").read_text(
            encoding="utf-8"
        )

        self.assertIn("job-harness-v2 search --help", workflow)
        self.assertIn("Technical notes in a saved brief are non-authoritative", workflow)
        self.assertIn('"remote_scopes":["global"]', workflow)
        self.assertIn("filtered-out title matches", workflow)

    def test_briefing_skill_keeps_runtime_limitations_out_of_saved_preferences(self) -> None:
        briefing = (_PLUGIN_ROOT / "skills" / "user-briefing" / "SKILL.md").read_text(
            encoding="utf-8"
        )

        self.assertIn("business preferences", briefing)
        self.assertIn("must not preserve runtime or CLI limitations", briefing)


if __name__ == "__main__":
    unittest.main()
