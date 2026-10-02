"""Regression tests for the keyword fallback's word-boundary matching and OTHER default.

Stdlib unittest only -- no new dependency, consistent with the keyword fallback needing to
stay functional with zero external dependencies. Run with:
    python -m unittest discover -s tests
"""

from __future__ import annotations

import unittest

from app.analyzer import analyze_with_keywords, classify_category
from app.models import Category


class SubstringFalsePositiveTests(unittest.TestCase):
    """Each of these used to misclassify because the old matcher did `keyword in text`,
    so a keyword could match inside an unrelated word."""

    def test_train_is_not_weather(self) -> None:
        category, _ = classify_category("the train crossing was closed")
        self.assertNotEqual(category, Category.WEATHER, '"rain" inside "train" must not match')

    def test_damaged_is_not_critical(self) -> None:
        # CRITICAL is a severity, not a category -- check severity via classify_severity through
        # the full response, since "dam" is a CRITICAL_SEVERITY_KEYWORDS entry.
        from app.analyzer import classify_severity
        from app.models import Severity

        severity = classify_severity("the box is damaged")
        self.assertNotEqual(severity, Severity.CRITICAL, '"dam" inside "damaged" must not match')

    def test_madame_is_not_critical(self) -> None:
        from app.analyzer import classify_severity
        from app.models import Severity

        severity = classify_severity("package left with madame next door")
        self.assertNotEqual(severity, Severity.CRITICAL, '"dam" inside "madame" must not match')

    def test_window_is_not_weather(self) -> None:
        category, _ = classify_category("window broken by customer")
        self.assertNotEqual(category, Category.WEATHER)

    def test_entire_is_not_vehicle_issue(self) -> None:
        category, _ = classify_category("the entire load is fine")
        self.assertNotEqual(category, Category.VEHICLE_ISSUE, '"tire" inside "entire" must not match')


class OtherDefaultTests(unittest.TestCase):
    def test_no_keyword_match_returns_other_needs_review(self) -> None:
        category, needs_review = classify_category("xyz completely unrelated text")
        self.assertEqual(category, Category.OTHER)
        self.assertTrue(needs_review)

    def test_response_sets_needs_review_flag(self) -> None:
        response = analyze_with_keywords("xyz completely unrelated text")
        self.assertEqual(response.structuredRecord.category, Category.OTHER)
        self.assertTrue(response.structuredRecord.needsReview)

    def test_matched_category_does_not_need_review(self) -> None:
        category, needs_review = classify_category("le camion est en panne")
        self.assertEqual(category, Category.VEHICLE_ISSUE)
        self.assertFalse(needs_review)


class NormalizationTests(unittest.TestCase):
    def test_arabizi_digit_and_keyword_variant_agree(self) -> None:
        # "kharban" (spelled out) and "5arban" (arabizi digit) must classify the same way.
        spelled, _ = classify_category("el camion kharban")
        digit, _ = classify_category("el camion 5arban")
        self.assertEqual(spelled, digit)
        self.assertEqual(spelled, Category.VEHICLE_ISSUE)

    def test_accents_do_not_prevent_a_match(self) -> None:
        category, _ = classify_category("pneu crevé")
        self.assertEqual(category, Category.VEHICLE_ISSUE)


class RealNumberDigitTests(unittest.TestCase):
    """Drivers write both Arabizi (digits standing in for letters, e.g. "l9itch") and real
    numbers (quantities, times) in the same message. The digit->letter substitution must not
    turn a real number into an accidental keyword match."""

    REAL_NUMBER_TEXTS = [
        "5 km",
        "3 tonnes",
        "9h30",
        "wait 7 hours",
        "20 colis",
    ]

    def test_real_numbers_do_not_trigger_a_category(self) -> None:
        from app.analyzer import classify_severity
        from app.models import Severity

        for text in self.REAL_NUMBER_TEXTS:
            with self.subTest(text=text):
                category, needs_review = classify_category(text)
                severity = classify_severity(text)
                self.assertEqual(category, Category.OTHER, f"{text!r} must not match a category keyword")
                self.assertTrue(needs_review)
                self.assertEqual(severity, Severity.LOW, f"{text!r} must not match a severity keyword")


class CriticalSeverityPrecisionTests(unittest.TestCase):
    """Bare "police" used to be a CRITICAL_SEVERITY_KEYWORDS entry, which would have falsely
    scored CRITICAL for a routine police checkpoint (id 38 in the eval set is LOW). Ambulance
    stays an unambiguous CRITICAL signal; police only counts when it's an explicit emergency call."""

    def test_routine_police_checkpoint_is_not_critical(self) -> None:
        from app.analyzer import classify_severity
        from app.models import Severity

        severity = classify_severity("police blocked the road for an inspection")
        self.assertNotEqual(severity, Severity.CRITICAL)

    def test_explicit_ambulance_request_is_critical(self) -> None:
        from app.analyzer import classify_severity
        from app.models import Severity

        severity = classify_severity("chauffeur blesse besoin d une ambulance")
        self.assertEqual(severity, Severity.CRITICAL)

    def test_calling_the_police_for_an_emergency_is_still_critical(self) -> None:
        from app.analyzer import classify_severity
        from app.models import Severity

        severity = classify_severity("accident grave, on a appelé la police")
        self.assertEqual(severity, Severity.CRITICAL)


class SeverityEscalationTests(unittest.IsolatedAsyncioTestCase):
    """Rule-first severity: keyword rules can raise the LLM's severity, never lower it."""

    def test_rules_raise_a_low_model_answer(self) -> None:
        from app.analyzer import escalate_severity
        from app.models import Severity

        self.assertEqual(escalate_severity(Severity.LOW, "chauffeur blesse besoin d une ambulance"), Severity.CRITICAL)
        self.assertEqual(escalate_severity(Severity.HIGH, "chauffeur blesse besoin d une ambulance"), Severity.CRITICAL)

    def test_rules_never_lower_the_model_answer(self) -> None:
        from app.analyzer import classify_severity, escalate_severity
        from app.models import Severity

        # No rule fires -> the model's answer stands, including CRITICAL.
        self.assertEqual(classify_severity("customer not home will retry tomorrow"), Severity.LOW)
        self.assertEqual(escalate_severity(Severity.CRITICAL, "customer not home will retry tomorrow"), Severity.CRITICAL)
        self.assertEqual(escalate_severity(Severity.HIGH, "customer not home will retry tomorrow"), Severity.HIGH)
        # A rule that only reaches HIGH must not downgrade a model CRITICAL.
        text = "truck delayed and the road is blocked"
        self.assertEqual(classify_severity(text), Severity.HIGH)
        self.assertEqual(escalate_severity(Severity.CRITICAL, text), Severity.CRITICAL)
        self.assertEqual(escalate_severity(Severity.LOW, text), Severity.HIGH)

    async def _run_llm_path(self, model_json: dict, text: str):
        import json
        from types import SimpleNamespace

        from app.analyzer import Analyzer
        from app.config import Settings

        async def create(**_kwargs):
            message = SimpleNamespace(content=json.dumps(model_json))
            return SimpleNamespace(choices=[SimpleNamespace(message=message)])

        analyzer = Analyzer(Settings())
        analyzer._client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
        return await analyzer._analyze_with_llm(text, "TEST-1")

    async def test_llm_path_escalates_and_says_so(self) -> None:
        from app.models import AnalysisSource, Severity

        model_json = {"reasoning": "vague", "structuredRecord": {"severity": "LOW", "category": "OTHER"}}
        result = await self._run_llm_path(model_json, "chauffeur blesse besoin d une ambulance")
        self.assertEqual(result.structuredRecord.severity, Severity.CRITICAL)
        self.assertEqual(result.analysisSource, AnalysisSource.LLM)
        self.assertIn("raised from LOW to CRITICAL", result.reasoning)
        self.assertIn("urgent", result.customerNotification)

    async def test_severity_other_means_no_model_signal_rules_decide(self) -> None:
        """Real failure shape from allam-2-7b on vague messages: {"severity":"OTHER","category":"OTHER"}."""
        from app.models import AnalysisSource, Category, Severity

        model_json = {"reasoning": "too vague", "structuredRecord": {"severity": "OTHER", "category": "OTHER"}}
        with self.assertLogs("ai_service.analyzer", level="WARNING") as logs:
            vague = await self._run_llm_path(model_json, "something is wrong not sure what")
        self.assertEqual(vague.analysisSource, AnalysisSource.LLM)  # not INVALID_OUTPUT / fallback
        self.assertEqual(vague.structuredRecord.severity, Severity.LOW)  # rules found nothing
        self.assertEqual(vague.structuredRecord.category, Category.OTHER)
        self.assertTrue(vague.structuredRecord.needsReview)
        self.assertIn("no usable severity", vague.reasoning)
        self.assertTrue(any("Unusable model severity 'OTHER'" in line for line in logs.output))

        # When the rules do fire, they decide -- an unusable model severity must not hide an emergency.
        with self.assertLogs("ai_service.analyzer", level="WARNING"):
            urgent = await self._run_llm_path(model_json, "chauffeur blesse besoin d une ambulance")
        self.assertEqual(urgent.structuredRecord.severity, Severity.CRITICAL)

    async def test_invented_category_becomes_other_with_needs_review(self) -> None:
        """Real failure shape: the retired ADDRESS_ISSUE label. Wrong address is OTHER by the taxonomy."""
        from app.models import AnalysisSource, Category, Severity

        model_json = {"reasoning": "wrong address", "structuredRecord": {"severity": "LOW", "category": "ADDRESS_ISSUE"}}
        with self.assertLogs("ai_service.analyzer", level="WARNING") as logs:
            result = await self._run_llm_path(model_json, "wrong address given by customer")
        self.assertEqual(result.analysisSource, AnalysisSource.LLM)
        self.assertEqual(result.structuredRecord.category, Category.OTHER)
        self.assertTrue(result.structuredRecord.needsReview)
        self.assertEqual(result.structuredRecord.severity, Severity.LOW)  # valid severity is kept
        self.assertTrue(any("Unknown model category 'ADDRESS_ISSUE'" in line for line in logs.output))

    async def test_both_invalid_and_casing_tolerated(self) -> None:
        from app.models import Category, Severity

        both = {"reasoning": "?", "structuredRecord": {"severity": "OTHER", "category": "MADE_UP"}}
        with self.assertLogs("ai_service.analyzer", level="WARNING") as logs:
            result = await self._run_llm_path(both, "kolchay behi normal")
        self.assertEqual(result.structuredRecord.category, Category.OTHER)
        self.assertEqual(result.structuredRecord.severity, Severity.LOW)
        self.assertEqual(len(logs.output), 2)  # one log line per coerced field
        # A valid answer in the wrong case is accepted, not treated as invalid.
        cased = {"reasoning": "ok", "structuredRecord": {"severity": " critical ", "category": "driver_issue"}}
        result = await self._run_llm_path(cased, "customer not home will retry tomorrow")
        self.assertEqual(result.structuredRecord.severity, Severity.CRITICAL)
        self.assertEqual(result.structuredRecord.category, Category.DRIVER_ISSUE)

    async def test_missing_reasoning_is_still_invalid_output(self) -> None:
        from app.analyzer import AnalysisError

        with self.assertRaises(AnalysisError) as ctx:
            await self._run_llm_path({"structuredRecord": {"severity": "LOW", "category": "OTHER"}}, "x")
        self.assertEqual(ctx.exception.code, "INVALID_OUTPUT")

    async def test_llm_path_keeps_model_answer_when_rules_find_nothing(self) -> None:
        from app.models import Severity

        model_json = {"reasoning": "ok", "structuredRecord": {"severity": "CRITICAL", "category": "OTHER"}}
        result = await self._run_llm_path(model_json, "customer not home will retry tomorrow")
        self.assertEqual(result.structuredRecord.severity, Severity.CRITICAL)
        self.assertNotIn("raised from", result.reasoning)


if __name__ == "__main__":
    unittest.main()
