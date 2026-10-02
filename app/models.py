"""HTTP contracts for the AI analysis service."""

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


class Severity(str, Enum):
    LOW = "LOW"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class Category(str, Enum):
    VEHICLE_ISSUE = "VEHICLE_ISSUE"
    CUSTOMER_ABSENT = "CUSTOMER_ABSENT"
    WEATHER = "WEATHER"
    ROAD_TRAFFIC = "ROAD_TRAFFIC"
    CARGO_ISSUE = "CARGO_ISSUE"
    DRIVER_ISSUE = "DRIVER_ISSUE"
    OTHER = "OTHER"


class AnalysisSource(str, Enum):
    """The component that produced the final analysis."""

    LLM = "LLM"
    KEYWORD_FALLBACK = "KEYWORD_FALLBACK"


class AnalyzeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(..., min_length=1, description="Exception description to analyse")
    shipmentId: str = Field(..., min_length=1)
    requestId: str | None = None


class StructuredRecord(BaseModel):
    severity: Severity
    category: Category
    etaImpact: str
    needsReview: bool = False
    # None on the LLM path: the model no longer emits a confidence (small models don't produce a
    # calibrated one, and asking for it cost schema complexity), and we don't invent a substitute.
    # The keyword fallback still sets a coarse 0.7/0.0 signal.
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)


class LlmStructuredRecord(BaseModel):
    """The only fields we ask the LLM for. Everything else in the response is derived.

    Deliberately plain strings, not the enums: a 7B model was seen writing "OTHER" into `severity`
    and inventing the retired `ADDRESS_ISSUE` category (~10% of rows, see PROGRESS.md), which made
    strict validation throw the whole answer away. analyzer.py coerces both leniently instead."""

    severity: str | None = None
    category: str | None = None


class LlmClassification(BaseModel):
    """Reduced LLM output: reasoning first, then severity and category. A 7B model got severity
    wrong on an injury/ambulance sentence under the full 7-field schema and right under this one
    (see PROGRESS.md, row-73 isolation)."""

    reasoning: str
    structuredRecord: LlmStructuredRecord


class AnalyzeResponse(BaseModel):
    # reasoning is emitted first (and requested first from the LLM, see analyzer.py's prompt) so
    # the model has to reason before it commits to a category/severity, not after.
    reasoning: str
    structuredRecord: StructuredRecord
    actionPlan: str
    customerNotification: str
    analysisSource: AnalysisSource = AnalysisSource.KEYWORD_FALLBACK


class ErrorBody(BaseModel):
    message: str
    code: str | None = None
    requestId: str | None = None


class ErrorResponse(BaseModel):
    error: ErrorBody