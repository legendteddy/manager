from __future__ import annotations

from pathlib import Path
import unittest

from coordination import validate as coordination


ROOT = Path(__file__).resolve().parents[3]
CASE_FILE = ROOT / "evals" / "coordination" / "cases.json"


class ParallelCoordinationContractTests(unittest.TestCase):
    def test_fixture_suite_matches_expected_outcomes(self) -> None:
        results = [coordination.evaluate_case(case) for case in coordination.load_cases(CASE_FILE)]
        failures = [result for result in results if not result["passed"]]
        self.assertEqual(failures, [], failures)
        self.assertEqual(len(results), 12)

    def test_changed_file_must_be_within_declared_write_set(self) -> None:
        codes = {finding["code"] for finding in coordination.lint_state(coordination.scenario("silent-scope-expansion-rejected"))}
        self.assertIn("OUT_OF_SCOPE_CHANGE", codes)

    def test_clean_sequenced_hotspot_packet_is_valid(self) -> None:
        self.assertEqual(coordination.lint_state(coordination.scenario("sequenced-shared-hotspot-accepted")), [])


if __name__ == "__main__":
    unittest.main()
