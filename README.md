# AI Service

FastAPI service that classifies shipment exceptions using deterministic keywords, with an optional OpenRouter Llama path and safe fallback.

## Run locally

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
uvicorn app.main:app --reload
```

`GET /healthz` reports readiness. Send a request to `POST /v1/exceptions:analyze`:

```powershell
Invoke-RestMethod -Method Post http://127.0.0.1:8000/v1/exceptions:analyze -ContentType 'application/json' -Body '{"text":"Truck engine breakdown, delivery delayed","shipmentId":"SHP-123","requestId":"req-1"}'
```

Set `AI_SERVICE_API_KEY` in `.env` to require `Authorization: Bearer <key>`. Add an `OPENROUTER_API_KEY` to enable `meta-llama/llama-3.3-70b-instruct:free`; keyword analysis remains available whenever the model call fails.
