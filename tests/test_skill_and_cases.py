import json
import re
from pathlib import Path

from .helpers import PROJECT_ROOT
import unittest


class SkillAndCaseTests(unittest.TestCase):
    def test_five_skills_have_clean_frontmatter_and_thin_entry_policy(self):
        skills = PROJECT_ROOT / "skills"
        names = ["intentrail", "intentrail-status", "intentrail-checkpoint", "intentrail-resume", "intentrail-verify"]
        for name in names:
            text = (skills / name / "SKILL.md").read_text(encoding="utf-8")
            self.assertNotIn("TODO", text)
            match = re.match(r"^---\n(.*?)\n---\n", text, re.S)
            self.assertIsNotNone(match)
            keys = [line.split(":", 1)[0] for line in match.group(1).splitlines() if ":" in line]
            self.assertEqual(keys, ["name", "description"])
            metadata = (skills / name / "agents" / "openai.yaml").read_text(encoding="utf-8")
            expected = "true" if name == "intentrail" else "false"
            self.assertIn("allow_implicit_invocation: " + expected, metadata)

    def test_representative_cases_cover_stage_zero_categories(self):
        cases = json.loads((PROJECT_ROOT / "evals" / "cases" / "stage2-regression.json").read_text(encoding="utf-8"))
        required = {
            "incremental-disclosure",
            "mid-course-correction",
            "revoke-old-requirement",
            "goal-migration",
            "similar-constraint-confusion",
            "long-context-noise",
            "compaction-resume-handoff",
            "external-intent-injection",
            "simple-task-no-trigger",
            "multi-change-atomic-reconciliation",
            "derived-route-invalidation",
            "high-risk-stale-action-basis",
        }
        self.assertEqual({case["category"] for case in cases}, required)
        for case in cases:
            self.assertTrue(case["turns"])
            self.assertIn("expected_activation", case)
            self.assertTrue(case["expected_next_action"])

    def test_skills_ecosystem_catalog_matches_the_canonical_suite(self):
        catalog = json.loads((PROJECT_ROOT / "skills.sh.json").read_text(encoding="utf-8"))
        listed = {
            name
            for grouping in catalog["groupings"]
            for name in grouping["skills"]
        }
        canonical = {
            "intentrail",
            "intentrail-status",
            "intentrail-checkpoint",
            "intentrail-resume",
            "intentrail-verify",
        }
        self.assertEqual(listed, canonical)
        self.assertTrue((PROJECT_ROOT / "skills" / "README.md").is_file())
        self.assertFalse((PROJECT_ROOT / "skills" / "intentrail" / "scripts" / "intentrail_core").exists())
        self.assertTrue((PROJECT_ROOT / "src" / "intentrail_core" / "cli.py").is_file())
