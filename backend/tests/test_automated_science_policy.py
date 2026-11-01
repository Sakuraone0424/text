"""Offline tests; no database, internet or paid model connection."""
import unittest
from app.automated_science_policy import decide_admission, select_science_authority


class AutomatedSciencePolicyTests(unittest.TestCase):
    def complete(self):
        return dict(six_source_sha_verified=True, exact_textbook_quote_verified=True,
                    science_ai_round_passed=True, answer_ai_round_passed=True,
                    independent_round_evidence_separation=True,
                    rubric_and_unique_answer_verified=True,
                    duplicate_guard_passed=True, source_media_resolved=True,
                    external_overrode_textbook=False, textbook_has_unresolved_conflict=False)

    def test_no_human_or_expert_signature_needed(self):
        r = self.complete()
        r["human_attested"] = False
        r["independent_expert_signed"] = False
        self.assertTrue(decide_admission(r).allowed)

    def test_all_automated_checks_required(self):
        for key in ("six_source_sha_verified", "exact_textbook_quote_verified",
                    "science_ai_round_passed", "answer_ai_round_passed",
                    "independent_round_evidence_separation",
                    "rubric_and_unique_answer_verified",
                    "duplicate_guard_passed", "source_media_resolved"):
            with self.subTest(key=key):
                r = self.complete()
                r[key] = False
                self.assertFalse(decide_admission(r).allowed)

    def test_external_cannot_override_textbook(self):
        r = self.complete()
        r["external_overrode_textbook"] = True
        self.assertFalse(decide_admission(r).allowed)

    def test_ambiguous_textbook_conflict_blocks_scoring(self):
        r = self.complete()
        r["textbook_has_unresolved_conflict"] = True
        self.assertFalse(decide_admission(r).allowed)

    def test_book_first_separate_external_note(self):
        out = select_science_authority("教材原文A", "网络说法B")
        self.assertEqual(out["quote"], "教材原文A")
        self.assertEqual(out["review_state"], "CONFLICT_FLAGGED_NOT_AUTO_CORRECTED")

    def test_no_book_cannot_score(self):
        self.assertEqual(
            select_science_authority(None, "网络说法"),
            {"authoritative": "none", "quote": None, "web_note": "网络说法",
             "review_state": "NO_TEXTBOOK_SUPPORT_NOT_SCORABLE"},
        )


if __name__ == "__main__":
    unittest.main()
