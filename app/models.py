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


class AnalyzeResponse(BaseModel):
    structuredRecord: StructuredRecord
    actionPlan: str
    customerNotification: str
    analysisSource: AnalysisSource = AnalysisSource.KEYWORD_FALLBACK
    reasoning: str


class ErrorBody(BaseModel):
    message: str
    code: str | None = None
    requestId: str | None = None


class ErrorResponse(BaseModel):
    error: ErrorBody