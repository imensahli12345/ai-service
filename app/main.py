"""FastAPI application entry point."""

from functools import lru_cache
import logging
import secrets

from fastapi import Depends, FastAPI, Header, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.analyzer import AnalysisError, Analyzer
from app.config import Settings, get_settings
from app.models import AnalyzeRequest, AnalyzeResponse

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

app = FastAPI(title="AI Service", version="1.0.0")




@lru_cache
def get_analyzer() -> Analyzer:
    return Analyzer(get_settings())


def require_auth(
    authorization: str | None = Header(default=None),
    settings: Settings = Depends(get_settings),
) -> None:
    expected = settings.ai_service_api_key
    if not expected:  # Deliberately convenient for local development only.
        return
    scheme, _, token = (authorization or "").partition(" ")
    if scheme.casefold() != "bearer" or not secrets.compare_digest(token, expected):
        raise AnalysisError("Unauthorized.", "UNAUTHORIZED", 401)




@app.exception_handler(AnalysisError)
async def handle_analysis_error(_: Request, exc: AnalysisError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": {"message": exc.message, "code": exc.code, "requestId": exc.request_id}},
    )


@app.exception_handler(RequestValidationError)
async def handle_validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
    return JSONResponse(
        status_code=422,
        content={"error": {"message": "Invalid request payload.", "code": "VALIDATION_ERROR"}},
    )


@app.get("/healthz")
async def healthz(analyzer: Analyzer = Depends(get_analyzer)) -> dict[str, str | bool]:
    return {"status": "ok", "model_available": analyzer.model_available}


@app.post(
    "/v1/exceptions:analyze",
    response_model=AnalyzeResponse,
    responses={401: {"description": "Unauthorized"}, 422: {"description": "Invalid request"}, 503: {"description": "Not configured"}},
)
async def analyze_exception(
    payload: AnalyzeRequest,
    _: None = Depends(require_auth),
    analyzer: Analyzer = Depends(get_analyzer),
    settings: Settings = Depends(get_settings),
) -> AnalyzeResponse:
    if settings.demo_fail_hook and "fail" in payload.text.casefold():
        raise AnalysisError(
            f"Unavailable for {payload.shipmentId}", "DEMO_FAIL", 422, payload.requestId
        )
    return await analyzer.analyze(payload.text, payload.shipmentId, payload.requestId)
