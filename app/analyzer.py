"""Analysis orchestration with an LLM and a deterministic keyword fallback."""

from __future__ import annotations

import json
import logging
from collections.abc import Iterable

from app.config import Settings
from app.keywords import (
    CATEGORY_KEYWORDS,
    CRITICAL_SEVERITY_KEYWORDS,
    HIGH_SEVERITY_KEYWORDS,
)
from app.models import AnalysisSource, AnalyzeResponse, Category, Severity, StructuredRecord

logger = logging.getLogger("ai_service.analyzer")


class AnalysisError(Exception):
    """An expected API error with a safe public representation."""

    def __init__(self, message: str, code: str, status_code: int = 502, request_id: str | None = None):
        self.message = message
        self.code = code
        self.status_code = status_code
        self.request_id = request_id
        super().__init__(message)


def _matches(text: str, keywords: Iterable[str]) -> int:
    """Count case-insensitive substring matches from a keyword list."""
    return sum(keyword.casefold() in text for keyword in keywords)


def _matching_keywords(text: str, keywords: Iterable[str]) -> list[str]:
    """Return the configured keywords found in text."""
    return [keyword for keyword in keywords if keyword.casefold() in text]

def classify_category(text: str) -> Category:
    normalized = text.casefold()
    # VEHICLE_ISSUE is the deterministic default when no keyword matches.
    best_category, best_score = Category.VEHICLE_ISSUE, -1
    for category, keywords in CATEGORY_KEYWORDS.items():
        score = _matches(normalized, keywords)
        if score > best_score:
            best_category, best_score = category, score
    return best_category


def classify_severity(text: str) -> Severity:
    normalized = text.casefold()
    if _matches(normalized, CRITICAL_SEVERITY_KEYWORDS):
        return Severity.CRITICAL
    if _matches(normalized, HIGH_SEVERITY_KEYWORDS):
        return Severity.HIGH
    return Severity.LOW


def _eta_impact(severity: Severity) -> str:
    return {
        Severity.LOW: "Minor impact; delivery ETA should remain under review.",
        Severity.HIGH: "Likely delay; update the delivery ETA after dispatch review.",
        Severity.CRITICAL: "Major delay likely; delivery requires immediate replanning.",
    }[severity]


def _action_plan(category: Category, severity: Severity) -> str:
    first_step = {
        Category.VEHICLE_ISSUE: "Secure the vehicle and assess the mechanical issue.",
        Category.CUSTOMER_ABSENT: "Try contacting the customer using the available channels.",
        Category.WEATHER: "Check route safety and local weather restrictions.",
    }[category]
    escalation = (
        "Escalate immediately to the operations lead."
        if severity is Severity.CRITICAL
        else "Update the shipment status and monitor progress."
    )
    return f"1. {first_step}\n2. {escalation}\n3. Confirm the revised ETA with dispatch."


def _customer_notification(category: Category, severity: Severity) -> str:
    cause = {
        Category.VEHICLE_ISSUE: "a vehicle issue",
        Category.CUSTOMER_ABSENT: "a delivery coordination issue",
        Category.WEATHER: "weather conditions",
    }[category]
    urgency = " We are treating this as urgent." if severity is Severity.CRITICAL else ""
    return f"Your delivery may be delayed due to {cause}. We will share an updated ETA shortly.{urgency}"


def _keyword_reasoning(text: str, category: Category, severity: Severity) -> str:
    normalized = text.casefold()
    category_matches = {candidate: _matching_keywords(normalized, words) for candidate, words in CATEGORY_KEYWORDS.items()}
    scores = ", ".join(f"{candidate.value}={len(matches)}" for candidate, matches in category_matches.items())
    selected = category_matches[category]
    category_reason = f"matched keywords: {', '.join(selected)}" if selected else "no category keyword matched; VEHICLE_ISSUE is the configured default"
    critical = _matching_keywords(normalized, CRITICAL_SEVERITY_KEYWORDS)
    high = _matching_keywords(normalized, HIGH_SEVERITY_KEYWORDS)
    return (f"Keyword fallback selected {category.value} because {category_reason}. Category scores: {scores}. "
            f"Severity is {severity.value}; critical matches: {', '.join(critical) or 'none'}; high matches: {', '.join(high) or 'none'}.")

def analyze_with_keywords(text: str) -> AnalyzeResponse:
    """Create a validated response without network access or an API key."""
    category = classify_category(text)
    severity = classify_severity(text)
    return AnalyzeResponse(
        structuredRecord=StructuredRecord(
            severity=severity,
            category=category,
            etaImpact=_eta_impact(severity),
        ),
        actionPlan=_action_plan(category, severity),
        customerNotification=_customer_notification(category, severity),
        reasoning=_keyword_reasoning(text, category, severity),
    )


class Analyzer:
    def __init__(self, settings: Settings):
        self.settings = settings
        self._client = None

    @property
    def model_available(self) -> bool:
        return bool(self.settings.openrouter_api_key)

    async def analyze(self, text: str, shipment_id: str, request_id: str | None = None) -> AnalyzeResponse:
        if self.settings.openrouter_api_key:
            try:
                result = await self._analyze_with_llm(text, shipment_id)
                logger.info(
                    "LLM analysis SUCCEEDED for shipment=%s model=%s request=%s",
                    shipment_id, self.settings.openrouter_model, request_id,
                )
                return result
            except AnalysisError as exc:
                logger.warning(
                    "LLM analysis FAILED for shipment=%s model=%s request=%s code=%s message=%s%s",
                    shipment_id, self.settings.openrouter_model, request_id, exc.code, exc.message,
                    " -> falling back to keyword matching" if self.settings.enable_keyword_fallback else "",
                )
                if not self.settings.enable_keyword_fallback:
                    raise
        else:
            logger.warning(
                "No OPENROUTER_API_KEY configured for shipment=%s request=%s -> using keyword fallback",
                shipment_id, request_id,
            )
        if self.settings.enable_keyword_fallback:
            logger.info("Serving KEYWORD_FALLBACK analysis for shipment=%s request=%s", shipment_id, request_id)
            return analyze_with_keywords(text)
        raise AnalysisError("AI analysis is not configured.", "NOT_CONFIGURED", 503, request_id)

    async def _analyze_with_llm(self, text: str, shipment_id: str) -> AnalyzeResponse:
        """Request JSON mode from OpenRouter, then validate it with Pydantic."""
        try:
            from openai import APIConnectionError, APIStatusError, APITimeoutError, AsyncOpenAI, RateLimitError
        except ImportError as exc:
            raise AnalysisError("OpenAI client is not installed.", "NOT_CONFIGURED", 503) from exc

        if self._client is None:
            self._client = AsyncOpenAI(
                api_key=self.settings.openrouter_api_key,
                base_url=self.settings.openrouter_base_url,
                timeout=self.settings.openai_timeout_seconds,
                default_headers={"X-OpenRouter-Title": "AI Service"},
            )

        prompt = (
            "Classify this shipment exception. Return one valid JSON object, with no markdown. "
            "It must exactly follow this shape: "
            '{"structuredRecord":{"severity":"LOW|HIGH|CRITICAL",'
            '"category":"VEHICLE_ISSUE|CUSTOMER_ABSENT|WEATHER","etaImpact":"string"},'
            '"actionPlan":"string","customerNotification":"string","reasoning":"brief explanation"}. '
            f"Shipment ID: {shipment_id}. Exception: {text}"
        )
        system_prompt = (
            "You are a logistics exception analyst for a Tunisian trucking company. "
            "Driver messages are often Tunisian Arabic written in Latin letters (Arabizi) mixed with French. "
            "Common terms: ta9s=weather, khayeb/khaib=bad, barsha=a lot/very, wa9t=time, sel3a=goods/cargo, "
            "manjmsh=cannot, nwasel=deliver/continue, 3andou/3andha=has, camion/tomobile=truck. "
            "Read the whole sentence for meaning before classifying; do not assume VEHICLE_ISSUE by default. "
            "Be concise and operational."
        )
        try:
            completion = await self._client.chat.completions.create(
                model=self.settings.openrouter_model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": prompt},
                ],
                response_format={"type": "json_object"},
                temperature=0,
            )
            content = completion.choices[0].message.content
            if not content:
                raise AnalysisError("The model returned no analysis.", "INVALID_OUTPUT")
            analysis = AnalyzeResponse.model_validate(json.loads(content))
            # The source is controlled by this service, never trusted from the model output.
            return analysis.model_copy(update={"analysisSource": AnalysisSource.LLM})
        except AnalysisError:
            raise
        except APITimeoutError as exc:
            raise AnalysisError("The AI provider timed out.", "TIMEOUT") from exc
        except RateLimitError as exc:
            raise AnalysisError("The AI provider rate limit was reached.", "RATE_LIMITED") from exc
        except APIConnectionError as exc:
            raise AnalysisError("The AI provider could not be reached.", "PROVIDER_UNAVAILABLE") from exc
        except APIStatusError as exc:
            raise AnalysisError("The AI provider rejected the request.", "PROVIDER_ERROR") from exc
        except (json.JSONDecodeError, ValueError) as exc:
            raise AnalysisError("The model returned invalid structured output.", "INVALID_OUTPUT") from exc
        except Exception as exc:
            # Catch-all so any provider/SDK error we didn't anticipate still becomes
            # a clean JSON AnalysisError, never a raw/unhandled response.
            raise AnalysisError("Unexpected error calling the AI provider.", "PROVIDER_ERROR") from exc
