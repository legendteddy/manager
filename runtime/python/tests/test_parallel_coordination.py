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
        self.assertEqual(len(results), 17)

    def test_changed_file_must_be_within_declared_write_set(self) -> None:
        codes = {
            finding["code"]
            for finding in coordination.lint_state(
                coordination.scenario("silent-scope-expansion-rejected")
            )
        }
        self.assertIn("OUT_OF_SCOPE_CHANGE", codes)

    def test_clean_sequenced_hotspot_packet_is_valid(self) -> None:
        self.assertEqual(
            coordination.lint_state(
                coordination.scenario("sequenced-shared-hotspot-accepted")
            ),
            [],
        )

    def test_superseded_history_order_does_not_change_current_claim(self) -> None:
        for case_id in (
            "superseded-history-before-current-accepted",
            "superseded-history-after-current-accepted",
        ):
            with self.subTest(case_id=case_id):
                self.assertEqual(
                    coordination.lint_state(coordination.scenario(case_id)),
                    [],
                )

    def test_ambiguous_claim_history_fails_closed(self) -> None:
        expected = {
            "duplicate-claim-revision-rejected": "CLAIM_REVISION_DUPLICATE",
            "multiple-current-claims-rejected": "MULTIPLE_CURRENT_CLAIMS",
            "current-claim-not-highest-revision-rejected": "CLAIM_CURRENT_NOT_HIGHEST",
        }
        for case_id, code in expected.items():
            with self.subTest(case_id=case_id):
                codes = {
                    finding["code"]
                    for finding in coordination.lint_state(
                        coordination.scenario(case_id)
                    )
                }
                self.assertIn(code, codes)


if __name__ == "__main__":
    unittest.main()
