# AI Service

FastAPI service that classifies shipment exceptions (Tunisian Arabic/Arabizi, French, or English driver
messages) into `severity` / `category` / action plan / customer notification. Tries an LLM first, and
always has a deterministic keyword-based fallback so the endpoint never hard-fails.

Consumed by `shipment-service` (`POST /v1/exceptions:analyze`) in the main
[Fleet Platform](https://github.com/imensahli12345/fleet-platform) backend.

## Run locally

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
uvicorn app.main:app --reload
```

`GET /healthz` reports whether an LLM is configured. Send a request to `POST /v1/exceptions:analyze`:

```powershell
Invoke-RestMethod -Method Post http://127.0.0.1:8000/v1/exceptions:analyze -ContentType 'application/json' -Body '{"text":"ta9s khayeb barsha, manjmsh nwasel e sel3a fil wa9t","shipmentId":"SHP-123","requestId":"req-1"}'
```

## Configuration (`.env`, gitignored — never commit real keys)

| Variable | Purpose |
|---|---|
| `AI_SERVICE_API_KEY` | If set, requires `Authorization: Bearer <key>` on requests. Empty = open (local dev only). |
| `OPENROUTER_API_KEY` | API key for the LLM provider. Despite the name, this project points it at **Groq's OpenAI-compatible endpoint**, not OpenRouter. |
| `OPENROUTER_BASE_URL` | `https://api.groq.com/openai/v1` |
| `OPENROUTER_MODEL` | `allam-2-7b` — the only model (of the ones this Groq key can access) that reliably classifies Tunisian Arabizi/Darija text. Verify access with `GET /v1/models` on the Groq API before changing it. |
| `ENABLE_KEYWORD_FALLBACK` | If `true` (default), any LLM failure (timeout, rate limit, bad output, misconfiguration) transparently falls back to the deterministic keyword matcher in `app/keywords.py`. |
| `DEMO_FAIL_HOOK` | Dev-only: forces a failure when the input text contains "fail". |

## LLM vs. keyword-fallback visibility

Every request logs which path served it (`app/analyzer.py`), at `INFO` for success/fallback and
`WARNING` when the LLM call failed and why:

```
INFO  ai_service.analyzer: LLM analysis SUCCEEDED for shipment=... model=allam-2-7b request=...
WARNING ai_service.analyzer: LLM analysis FAILED for shipment=... model=... code=RATE_LIMITED message=... -> falling back to keyword matching
```

The response body also carries `analysisSource: "LLM" | "KEYWORD_FALLBACK"` so callers can tell which
one produced a given result.

## Category taxonomy

Only three categories exist end-to-end (enforced by a Postgres `CHECK` constraint in shipment-service's
DB too): `VEHICLE_ISSUE`, `CUSTOMER_ABSENT`, `WEATHER`. There is currently no category for
missing/wrong/damaged cargo — see open items below.

## Known limitations / open items

- `allam-2-7b` is a small (7B) model on a free-tier key; it's the best of the 5 models this key can
  access for Tunisian dialect, but isn't perfect. The prompt in `_analyze_with_llm` includes a
  Tunisian-Arabizi glossary and runs at `temperature=0` for determinism — both were required to get
  reliable weather-vs-vehicle classification.
- The 3-category taxonomy has no bucket for missing/wrong cargo (e.g. "I couldn't find the products"),
  which currently risks being misclassified as `CUSTOMER_ABSENT`. Adding a 4th category (e.g.
  `CARGO_ISSUE`) would need changes here plus shipment-service's `Category` enum and DB constraint.
