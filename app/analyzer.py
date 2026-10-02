"""Analysis orchestration with an LLM and a deterministic keyword fallback."""

from __future__ import annotations

import json
import logging
import re
import unicodedata
from collections.abc import Iterable

from app.config import Settings
from app.keywords import (
    CATEGORY_KEYWORDS,
    CRITICAL_SEVERITY_KEYWORDS,
    HIGH_SEVERITY_KEYWORDS,
)
from app.models import (
    AnalysisSource,
    AnalyzeResponse,
    Category,
    LlmClassification,
    Severity,
    StructuredRecord,
)

logger = logging.getLogger("ai_service.analyzer")

# Arabizi digit-letter substitutions, applied before matching so keywords written with digits
# (e.g. "l9itch") line up with keywords written phonetically (e.g. "lkhitch" wouldn't, but
# "kharban"/"5arban" now both normalize to "kharban").
_ARABIZI_DIGIT_MAP = {"9": "q", "7": "h", "5": "kh", "3": "a"}
_WHITESPACE_RE = re.compile(r"\s+")


def normalize_text(text: str) -> str:
    """Casefold, map Arabizi digits to their letter equivalents, strip accents, collapse whitespace."""
    normalized = text.casefold()
    for digit, letters in _ARABIZI_DIGIT_MAP.items():
        normalized = normalized.replace(digit, letters)
    normalized = unicodedata.normalize("NFKD", normalized)
    normalized = "".join(ch for ch in normalized if not unicodedata.combining(ch))
    return _WHITESPACE_RE.sub(" ", normalized).strip()


def _compile_keyword_patterns(keywords: Iterable[str]) -> list[tuple[str, re.Pattern[str]]]:
    """Normalize each keyword the same way as the input text, dedupe, and compile a
    word-boundary pattern per keyword so "rain" no longer matches inside "train"."""
    patterns: list[tuple[str, re.Pattern[str]]] = []
    seen: set[str] = set()
    for keyword in keywords:
        normalized = normalize_text(keyword)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        patterns.append((keyword, re.compile(rf"\b{re.escape(normalized)}\b")))
    return patterns


_CATEGORY_PATTERNS: dict[Category, list[tuple[str, re.Pattern[str]]]] = {
    category: _compile_keyword_patterns(keywords) for category, keywords in CATEGORY_KEYWORDS.items()
}
_HIGH_SEVERITY_PATTERNS = _compile_keyword_patterns(HIGH_SEVERITY_KEYWORDS)
_CRITICAL_SEVERITY_PATTERNS = _compile_keyword_patterns(CRITICAL_SEVERITY_KEYWORDS)


# Few-shot examples for the LLM path, targeting CRITICAL severity (the baseline had 50% CRITICAL
# recall -- real model error, not a taxonomy gap). 4 CRITICAL cases (injury, collision with
# injured, fire with the driver inside, hazardous cargo leak) plus 2 deliberate contrast cases
# (routine police check, ordinary breakdown) so the model doesn't overcorrect into calling
# everything CRITICAL.
#
# HARD RULE: none of these sentences may appear in data/eval_set.csv, nor be a near-paraphrase of
# an eval row. An earlier version used 6 verbatim eval rows (ids 3, 23, 38, 69, 72, 73), which made
# every CRITICAL number measured against that set train-on-test (see PROGRESS.md). Held-out
# validation sentences live outside the repo's eval set and are also disjoint from this list.
# The Tunisian (arabizi / arabic-script) examples were written by an LLM, not a native speaker.
#
# Each entry: (message, reasoning, severity, category). Sent as real assistant-turn examples in the
# same reduced shape the model is asked to produce (reasoning, severity, category only).
_SEVERITY_FEW_SHOT_EXAMPLES: tuple[tuple[str, str, str, str], ...] = (
    (
        "el chauffeur w9a3 men el camion w yenzef, lezem is3af",
        "The driver fell from the truck, is bleeding and needs emergency medical help -- an injury "
        "needing medical attention is CRITICAL. The problem is the driver, not the vehicle.",
        "CRITICAL", "DRIVER_ISSUE",
    ),
    (
        "contrôle de police de routine sur la nationale, papiers ok, on attend dans la file",
        "A routine police document check with nothing wrong and nobody hurt -- a short wait, not an "
        "emergency, so LOW. A police checkpoint is not by itself CRITICAL.",
        "LOW", "ROAD_TRAFFIC",
    ),
    (
        "le chauffeur est coincé dans la cabine après un choc, il saigne, les pompiers arrivent",
        "The driver is trapped and bleeding after a crash and firefighters are on the way -- a "
        "safety emergency, CRITICAL. The driver is the one hurt, so DRIVER_ISSUE.",
        "CRITICAL", "DRIVER_ISSUE",
    ),
    (
        "moteur en panne sur la route, dépanneuse demandée",
        "The engine has broken down and a tow truck was requested -- an operational problem causing "
        "delay, with no sign of injury or danger, so HIGH, not CRITICAL.",
        "HIGH", "VEHICLE_ISSUE",
    ),
    (
        "الكميون شعلت فيه النار والسواق مازال داخلو",
        "The truck is on fire with the driver still inside -- an immediate danger to life, CRITICAL, "
        "even though no injury is mentioned yet. The truck itself is the problem, so VEHICLE_ISSUE.",
        "CRITICAL", "VEHICLE_ISSUE",
    ),
    (
        "gas is leaking from the cargo and the driver feels dizzy, evacuating the area",
        "A hazardous leak from the cargo is making the driver dizzy and the area is being evacuated "
        "-- an immediate danger to people, CRITICAL. The source is the cargo, so CARGO_ISSUE.",
        "CRITICAL", "CARGO_ISSUE",
    ),
)


def _build_few_shot_messages() -> list[dict]:
    messages = []
    for i, (example_text, reasoning, severity, category) in enumerate(_SEVERITY_FEW_SHOT_EXAMPLES, start=1):
        response = {"reasoning": reasoning, "structuredRecord": {"severity": severity, "category": category}}
        messages.append({"role": "user", "content": f"Shipment ID: EXAMPLE-{i}. Exception: {example_text}"})
        messages.append({"role": "assistant", "content": json.dumps(response, ensure_ascii=False)})
    return messages


_FEW_SHOT_MESSAGES = _build_few_shot_messages()


class AnalysisError(Exception):
    """An expected API error with a safe public representation."""

    def __init__(self, message: str, code: str, status_code: int = 502, request_id: str | None = None):
        self.message = message
        self.code = code
        self.status_code = status_code
        self.request_id = request_id
        super().__init__(message)


def _matches(normalized_text: str, patterns: list[tuple[str, re.Pattern[str]]]) -> int:
    """Count word-boundary matches from a compiled keyword pattern list."""
    return sum(1 for _, pattern in patterns if pattern.search(normalized_text))


def _matching_keywords(normalized_text: str, patterns: list[tuple[str, re.Pattern[str]]]) -> list[str]:
    """Return the original (pre-normalization) keywords found in text."""
    return [keyword for keyword, pattern in patterns if pattern.search(normalized_text)]

def classify_category(text: str) -> tuple[Category, bool]:
    """Return (category, needs_review). needs_review is True when no category keyword
    matched at all -- OTHER is returned instead of guessing."""
    normalized = normalize_text(text)
    best_category: Category | None = None
    best_score = 0
    for category, patterns in _CATEGORY_PATTERNS.items():
        score = _matches(normalized, patterns)
        if score > best_score:
            best_category, best_score = category, score
    if best_category is None:
        return Category.OTHER, True
    return best_category, False


def classify_severity(text: str) -> Severity:
    normalized = normalize_text(text)
    if _matches(normalized, _CRITICAL_SEVERITY_PATTERNS):
        return Severity.CRITICAL
    if _matches(normalized, _HIGH_SEVERITY_PATTERNS):
        return Severity.HIGH
    return Severity.LOW


def _coerce_category(raw: str | None, shipment_id: str) -> Category:
    """An unknown or missing category (e.g. the retired ADDRESS_ISSUE) becomes OTHER, which also sets
    needsReview -- what the taxonomy wants for anything it cannot place. Logged, never silent."""
    try:
        return Category((raw or "").strip().upper())
    except ValueError:
        logger.warning("Unknown model category %r -> OTHER for shipment=%s", raw, shipment_id)
        return Category.OTHER


def _coerce_severity(raw: str | None, shipment_id: str) -> Severity | None:
    """Returns None when the model's severity is unusable (e.g. "OTHER"), meaning no model signal:
    the caller lets the keyword rules decide. Logged, never silent."""
    try:
        return Severity((raw or "").strip().upper())
    except ValueError:
        logger.warning("Unusable model severity %r -> keyword rules decide, shipment=%s", raw, shipment_id)
        return None


_SEVERITY_RANK = {Severity.LOW: 0, Severity.HIGH: 1, Severity.CRITICAL: 2}


def escalate_severity(model_severity: Severity, text: str) -> Severity:
    """Escalate-only merge of the model's severity with the keyword rules: the result is the higher
    of the two, so a rule hit can raise a low model answer but never lower a high one."""
    rule_severity = classify_severity(text)
    return max(model_severity, rule_severity, key=_SEVERITY_RANK.__getitem__)


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
        Category.ROAD_TRAFFIC: "Check for an alternate route and estimate the added delay.",
        Category.CARGO_ISSUE: "Inspect the cargo and document the missing or damaged items.",
        Category.DRIVER_ISSUE: "Check on the driver's condition and arrange relief or medical help if needed.",
        Category.OTHER: "Review the message manually; the automatic classifier found no matching category.",
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
        Category.ROAD_TRAFFIC: "road traffic conditions",
        Category.CARGO_ISSUE: "a cargo issue",
        Category.DRIVER_ISSUE: "a driver issue",
        Category.OTHER: "an issue our team is reviewing",
    }[category]
    urgency = " We are treating this as urgent." if severity is Severity.CRITICAL else ""
    return f"Your delivery may be delayed due to {cause}. We will share an updated ETA shortly.{urgency}"


def _keyword_reasoning(text: str, category: Category, severity: Severity, needs_review: bool) -> str:
    normalized = normalize_text(text)
    category_matches = {candidate: _matching_keywords(normalized, patterns) for candidate, patterns in _CATEGORY_PATTERNS.items()}
    scores = ", ".join(f"{candidate.value}={len(matches)}" for candidate, matches in category_matches.items())
    if needs_review:
        category_reason = "no category keyword matched; returning OTHER with needsReview=true"
    else:
        selected = category_matches.get(category, [])
        category_reason = f"matched keywords: {', '.join(selected)}"
    critical = _matching_keywords(normalized, _CRITICAL_SEVERITY_PATTERNS)
    high = _matching_keywords(normalized, _HIGH_SEVERITY_PATTERNS)
    return (f"Keyword fallback selected {category.value} because {category_reason}. Category scores: {scores}. "
            f"Severity is {severity.value}; critical matches: {', '.join(critical) or 'none'}; high matches: {', '.join(high) or 'none'}.")

def _confidence(needs_review: bool) -> float:
    """Keyword matching has no real probability model behind it -- 0.0 means "nothing matched,
    don't trust this category", 0.7 means "a keyword matched" without overclaiming calibration."""
    return 0.0 if needs_review else 0.7


def analyze_with_keywords(text: str) -> AnalyzeResponse:
    """Create a validated response without network access or an API key."""
    category, needs_review = classify_category(text)
    severity = classify_severity(text)
    return AnalyzeResponse(
        reasoning=_keyword_reasoning(text, category, severity, needs_review),
        structuredRecord=StructuredRecord(
            severity=severity,
            category=category,
            etaImpact=_eta_impact(severity),
            needsReview=needs_review,
            confidence=_confidence(needs_review),
        ),
        actionPlan=_action_plan(category, severity),
        customerNotification=_customer_notification(category, severity),
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
                # The SDK's own default retry-with-backoff (max_retries=2) sleeps silently inside
                # this process for 10s-20s+ per attempt on a 429, invisible to our own timeout and
                # to callers -- on a rate-limited free-tier key this stacked with the caller's own
                # timeout/retry (e.g. evaluate.py's) and multiplied real request volume 2-3x per
                # logical call. max_retries=0 means one bounded attempt: fail fast within
                # OPENAI_TIMEOUT_SECONDS and let our own fallback-to-keywords handle it.
                max_retries=0,
                default_headers={"X-OpenRouter-Title": "AI Service"},
            )

        prompt = (
            "Classify this shipment exception. Return one valid JSON object, with no markdown. "
            "It must exactly follow this shape, with reasoning as the FIRST key -- think through "
            "the message before committing to a category: "
            '{"reasoning":"brief explanation, written before you decide the category",'
            '"structuredRecord":{"severity":"LOW|HIGH|CRITICAL",'
            '"category":"VEHICLE_ISSUE|CUSTOMER_ABSENT|WEATHER|ROAD_TRAFFIC|CARGO_ISSUE|DRIVER_ISSUE|OTHER"}}. '
            "Use category OTHER when the message gives no concrete signal for any other category "
            "(e.g. it is too vague, or just an acknowledgement). "
            f"Shipment ID: {shipment_id}. Exception: {text}"
        )
        system_prompt = (
            "You are a logistics exception analyst for a Tunisian trucking company. "
            "Driver messages are often Tunisian Arabic written in Latin letters (Arabizi) mixed with French. "
            "Common terms: ta9s=weather, khayeb/khaib=bad, barsha=a lot/very, wa9t=time, sel3a=goods/cargo, "
            "manjmsh=cannot, nwasel=deliver/continue, 3andou/3andha=has, camion/tomobile=truck. "
            "Categories: VEHICLE_ISSUE (the truck itself is broken down), WEATHER (rain/storm/flood/snow "
            "blocking or slowing the route), ROAD_TRAFFIC (construction, congestion, a strike, an accident "
            "or police checkpoint blocking the route -- not the driver's own vehicle), CUSTOMER_ABSENT "
            "(nobody available at the delivery address), CARGO_ISSUE (goods missing, damaged, or wrong), "
            "DRIVER_ISSUE (the driver themself is sick, injured, or too fatigued to continue), OTHER "
            "(none of the above fit, or the message is too vague to tell). "
            "A wrong or incomplete address is OTHER, never CUSTOMER_ABSENT -- CUSTOMER_ABSENT means the "
            "address was right but nobody was there. "
            "Severity is about safety, not inconvenience -- judge it independently from category. "
            "CRITICAL means an immediate risk to a person's life or physical safety: an injury, an "
            "accident with injury, a driver in active danger (e.g. a vehicle stuck in rising "
            "floodwater), or a case where medical help (ambulance) is genuinely needed. Never "
            'downgrade an injury to HIGH because the driver describes it as "minor" or "small" -- '
            "any injury needing medical attention is CRITICAL. A routine mention of police (a "
            "checkpoint or inspection, nobody hurt) is NOT by itself CRITICAL. HIGH means a real "
            "operational problem causing significant delay or cost, but with no immediate danger to "
            "anyone: a breakdown, bad weather causing delay, damaged cargo, an exhausted but unhurt "
            "driver. LOW means a minor issue with little delay or impact. "
            "Read the whole sentence for meaning before classifying; do not assume VEHICLE_ISSUE by default. "
            "The examples that follow show the expected reasoning and format -- match their severity "
            "judgment exactly, especially for injury/danger cases. "
            "Be concise and operational."
        )
        try:
            completion = await self._client.chat.completions.create(
                model=self.settings.openrouter_model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    *_FEW_SHOT_MESSAGES,
                    {"role": "user", "content": prompt},
                ],
                response_format={"type": "json_object"},
                temperature=0,
            )
            content = completion.choices[0].message.content
            if not content:
                raise AnalysisError("The model returned no analysis.", "INVALID_OUTPUT")
            classified = LlmClassification.model_validate(json.loads(content))
            category = _coerce_category(classified.structuredRecord.category, shipment_id)
            model_severity = _coerce_severity(classified.structuredRecord.severity, shipment_id)
            reasoning = classified.reasoning
            if model_severity is None:
                # The model gave no usable severity, so there is no model signal: the keyword rules decide.
                severity = classify_severity(text)
                reasoning += f" [Model gave no usable severity; keyword rules decided: {severity.value}.]"
            # Rule-first severity: the keyword rules run on every message and can only ESCALATE the
            # model's answer, never lower it. allam-2-7b missed half of held-out emergencies (see
            # PROGRESS.md) and a missed emergency is the worst error this system makes.
            else:
                severity = escalate_severity(model_severity, text)
            if model_severity is not None and severity is not model_severity:
                reasoning += (f" [Severity raised from {model_severity.value} to {severity.value} "
                              "by the keyword safety rules.]")
                logger.warning(
                    "Severity escalated %s -> %s by keyword rules for shipment=%s",
                    model_severity.value, severity.value, shipment_id,
                )
            # The model only decides severity + category. Everything else is derived by the same
            # deterministic helpers the keyword fallback uses, and the source is controlled by this
            # service, never trusted from the model output. needsReview is "no concrete category";
            # confidence stays None -- the model no longer emits one and we don't invent a substitute.
            return AnalyzeResponse(
                reasoning=reasoning,
                structuredRecord=StructuredRecord(
                    severity=severity,
                    category=category,
                    etaImpact=_eta_impact(severity),
                    needsReview=category is Category.OTHER,
                    confidence=None,
                ),
                actionPlan=_action_plan(category, severity),
                customerNotification=_customer_notification(category, severity),
                analysisSource=AnalysisSource.LLM,
            )
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
