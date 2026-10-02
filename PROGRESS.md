# AI-service progress

Working notes so a future session can recover context by reading this one file. Working style:
stop after each step for confirmation, show diffs, PowerShell-safe commands, explain root cause
before the fix.

## Contract — do not change without discussion

- Endpoint path `/v1/exceptions:analyze`
- Request shape `{text, shipmentId, requestId}`
- Response top-level keys `structuredRecord` / `actionPlan` / `customerNotification`
- Java fallback response shape `{fallback, reason, exception}`
- The keyword fallback must always stay functional with zero external dependencies
- **`structuredRecord.confidence` is `null` on the LLM path** (2026-09-28, kept: variant c's
  reduced schema was retained on its own merits -- see "Variant c vs full schema" below -- not as a
  severity fix). The model no longer emits a confidence and no substitute is
  invented; `models.py` makes the field `float | None = None`. The key is still present in the JSON
  (value `null`), so the response shape is unchanged, but it is a semantic change: the keyword
  fallback still emits its coarse 0.7 / 0.0, so `confidence` now means different things per
  `analysisSource` (and is null for LLM). **Java null-safety verified (2026-09-28):** the Java backend is at `C:\Plateforme de Suivi de Flotte de Camions\backend\shipment-service` (not under `D:\`). `confidence` is a boxed `Double` at every hop -- `AiServiceClient.java:107`, `ShipmentException.java:61`, `ShipmentExceptionResponse.java:20`, `ShipmentExceptionMapper.java:22` (getter passthrough), `ShipmentService.java:163` (builder passthrough) -- with no unboxing, so a JSON `null` is safe.

## DECISION (2026-10-01): production model is gpt-oss-120b -- ai-service is closed

- **Production LLM: `openai/gpt-oss-120b`.** `.env`: `OPENROUTER_MODEL=openai/gpt-oss-120b`,
  `OPENAI_TIMEOUT_SECONDS=12` (raised from 7.0: the 100-row eval's max was 6.99s, zero tail margin), `DEMO_FAIL_HOOK=false`
  (Step 2a now effective locally). Basis: "Final eval, gpt-oss-120b" below -- 99.0% category, 97.4% like-for-like, OTHER
  predicted 24 = expected 24, 9/10 held-out CRITICAL.
- **Documented fallback model: `allam-2-7b`** (switch back via `OPENROUTER_MODEL=allam-2-7b`, `OPENAI_TIMEOUT_SECONDS=7.0`).
  Known limits: like-for-like 56.4%, OTHER over-predicted (52 vs 24), weak on Tunisian Arabizi, 6/10 held-out CRITICAL.
  The keyword fallback is unchanged and still the zero-dependency last resort.
- **Accepted cost: false CRITICAL 5/94 (5.3%)** on the eval set with gpt-oss-120b (allam: 1/94): ids 66, 68 ("very sick,
  cannot continue"), 70, 71 ("hurt my hand a bit, cannot drive") from the model, 37 ("accident ahead") from the keyword
  rules. Accepted as alert-fatigue cost under "a false CRITICAL costs a human review, a missed one is an unattended
  emergency". Eval-set CRITICAL recall is still 3/6 (22/23/24 weather-danger rows HIGH) -- unchanged by the switch.
- **Timeout ordering: Python (12s) must stay below Java.** Java client timeout is `ai.service.timeoutMs` in
  `D:\Plateforme de Suivi de Flotte de Camions\backend\config-repo\shipment-service.yml:28` (currently `8000`; read by
  `AiServiceClient.java:30`, default `8000`, used for both connect and read timeout). Must go to ~14000 -- Java-side change,
  not done from ai-service.
- Rule-first severity stays on as the safety floor. No prompt, few-shot or keyword changes; no further evals.
- Still open (outside ai-service): native review of `data/native_review.csv`; severity work (label review of 22/23/24 and the
  sick/hurt-hand rows, fresh held-out set) if ever reopened.

## Where we are (updated 2026-09-28, both final evals done) -- historical, superseded by the decision above

- Shipped in code: Step 3 taxonomy (+ Java), variant-c reduced LLM schema with clean (non-eval) few-shot,
  rule-first severity (`escalate_severity`, escalate-only), invalid-output tolerance (unknown category -> OTHER,
  unusable severity -> rules decide, both logged). Unit tests 22/22.
- **Frozen on purpose:** the prompt, few-shot and keywords are NOT to be changed until the gpt-oss-120b run is
  reported, so that the allam and 120B runs stay comparable. The CRITICAL keyword list is **not** being extended
  (it would need a fresh held-out set; none exists and there is no time to build one).
- **Done:** `evaluate.py --label final-gptoss120b --rps 0.06` against a server restarted on
  `OPENROUTER_MODEL=openai/gpt-oss-120b` (process env override; `.env` untouched; 7.0s timeout unchanged) ->
  `eval_results/eval_20260928T142227Z_final-gptoss120b.json`. **Result: 99.0% overall, 97.4% like-for-like, OTHER
  predicted 24 times (= expected 24)** -- so the regression is not a defect of the prompt in general; it is
  model-dependent (see "Final eval, gpt-oss-120b" at the end). The server is currently running on gpt-oss-120b.
- **Regression to lead the report with (details below):** on allam-2-7b like-for-like accuracy fell 89.7% -> 56.4%; on gpt-oss-120b, same prompt, it is 97.4%.
- **gpt-oss-120b latency gate:** the formal gate FAILS -- p95 7.20s on the 15-row sample, over the 7s timeout, all of
  it one cold-start request. Warm p95 was 4.65s, max 5.14s, 25/25 succeeded in the follow-up sample, so the real
  margin is ~2s (thin). Both numbers are reported; see "gpt-oss-120b latency gate". The 100-row run agrees on p95
  (4.61s) but its **max was 6.99s** -- one row finished 0.01s under the timeout.
- Open decisions (user): switch the LLM path to gpt-oss-120b (after the 120B eval); send
  `data/native_review.csv` to a native speaker and fold corrections back; `.env` still has
  `DEMO_FAIL_HOOK=true` (user is fixing).

## ⚠ REGRESSION (measured 2026-09-28): like-for-like category accuracy FELL 89.7% -> 56.4%

Source: `eval_20260928T125439Z_final-allam.json` (allam-2-7b, 100 rows, 100% LLM path, 0 fallbacks, 0 errors) vs
the 2026-09-24 baseline re-scored on the corrected labels. The headline 35.0% -> 64.0% is **mechanical** (the
baseline could not output 4 of the 7 categories) and must not be presented as improvement on its own.

| like-for-like: the 3 categories the baseline could output (n=39) | baseline | final |
|---|---|---|
| accuracy | **89.7%** | **56.4%** |
| WEATHER recall | 13/13 | **5/13** |
| CUSTOMER_ABSENT recall | 10/13 | **6/13** |
| VEHICLE_ISSUE recall | 12/13 | 11/13 |

| OTHER usage | baseline | final |
|---|---|---|
| rows predicted OTHER | 0 | **52** |
| rows whose expected category is OTHER | 24 | 24 (23 of them recalled) |

So of the 52 OTHER predictions, at most 23 are correct; at least 29 rows that have a real category were sent to
OTHER. CRITICAL recall is unchanged at 3/6 (the hits are the rows the keyword rules also catch); severity
accuracy 68% -> 76%; the other rows of the table are in "Final eval" below.

**Hypothesis (NOT a conclusion, untested):** the "don't guess, prefer OTHER" instruction added in Steps 2-3 (the
system prompt's OTHER definition -- "none of the above fit, or the message is too vague to tell" -- and the user
prompt's "Use category OTHER ... when the message gives no concrete signal") over-corrected on a small model, which
now reaches for OTHER whenever it is unsure. Evidence that fits: OTHER went 0 -> 52; the loss is concentrated in
WEATHER/CUSTOMER_ABSENT recall; none of the 6 few-shot examples is an OTHER example, so the model never sees a
confident non-OTHER answer on an unclear sentence contrasted with an OTHER one. Evidence that does not settle it:
the 50-row paired test showed the *full* schema over-predicted OTHER even more than variant c (27 vs 19 of 43,
expected 8), so the reduced schema is not the cause, but both variants carried the same OTHER wording. Rival
explanations not ruled out: a plain 7B capability limit on Tunisian dialect; new categories legitimately
absorbing some weather rows (ROAD_TRAFFIC); label noise in the AI-drafted dataset (native review pending).

**RESULT of the discriminating experiment (2026-09-28, see "Final eval, gpt-oss-120b"): the same prompt on
gpt-oss-120b predicts OTHER exactly 24 times (expected 24) and scores 97.4% like-for-like.** So the prompt is not
broken for a capable model; the over-prediction appears with the 7B. What this does and does not show: it
refutes "the prompt is defective in general" and is consistent with the hypothesis that allam over-obeys the OTHER
instruction; it cannot separate "the 7B cannot follow this instruction well" from "the instruction is too strong for
small models" -- only a prompt ablation on allam (softer OTHER wording, an OTHER-vs-non-OTHER example pair) would,
and that has not been run.

**Deliberately NOT done: the prompt is not being changed to chase this.** Changing it now would destroy the
comparability of the allam and gpt-oss-120b runs. The gpt-oss-120b run is the discriminating experiment (same
prompt, same code, only the model changes): if the 120B model does *not* over-predict OTHER, that points to a 7B
capability limit under this prompt; if it over-predicts OTHER just as heavily, that points to the prompt. Only
then decide what to change. Ambiguity to keep in mind when reading it: a 120B model that behaves is consistent
with both "the 7B cannot follow the OTHER instruction well" and "the instruction is fine", so the result
separates "prompt" from "model" only if 120B is *also* bad.

## ⚠ METHODOLOGY CORRECTION (found 2026-09-28): eval contamination — read before trusting any Step 4 number

**All 6 few-shot examples in the Step 4 prompt were verbatim rows of `data/eval_set.csv`** (ids 3,
23, 38, 69, 72, 73). **4 of the 6 CRITICAL eval rows (23, 69, 72, 73) were literally in the prompt.**
So every post-Step-4 CRITICAL number measured against `eval_set.csv` — including the 16.7% (1/6)
in `eval_20260925T175009Z_after-step4.json` and anything from a future rerun of the *old* prompt —
is train-on-test: not held-out, and if anything *inflated*. It was found because the row-73
isolation test input turned out to be byte-identical to few-shot example #2; the model was failing
on a sentence it had been shown, which is what made the full-schema failure visible at all.

**Fix (done 2026-09-28):** the 6 examples were replaced by 6 non-eval sentences (4 CRITICAL + 2
deliberate contrast cases: routine police check -> LOW, ordinary breakdown -> HIGH), verified
programmatically disjoint from the eval CSV. A separate held-out set, `data/heldout_critical.csv`
(10 new CRITICAL + 3 non-CRITICAL overcorrection controls), is disjoint from both the eval set and
the few-shot list. Rule going forward: **no eval or held-out sentence may appear in the prompt**; if
an example is ever added, check it against both CSVs first (the check is a 6-line script, see the
2026-09-28 session). Baseline numbers (2026-09-24) are unaffected — that run predates the few-shot
block.

## Baseline (2026-09-24)

`eval_results/eval_20260924T150807Z_baseline.json` — 100-row eval against `data/eval_set.csv`,
label `baseline`. 100% valid JSON, 95% LLM / 5% keyword fallback, latency mean 2.53s / p95 4.58s.

- Overall category accuracy: 35.0% — **mechanical, not model failure**: the dataset was labeled
  with 8 categories (7 final + ADDRESS_ISSUE) but `app/models.py`'s `Category` enum only
  implements 3 (`VEHICLE_ISSUE`, `CUSTOMER_ABSENT`, `WEATHER`), so ~60 rows could never score
  correctly regardless of the model's actual judgment.
- **Restricted accuracy** (only rows whose expected_category is one of the 3 implemented
  categories): **89.7%** (35/39 rows). This is the number that reflects real model quality today;
  report both numbers going forward, not just overall.
- CRITICAL severity recall: **50%**. Not a taxonomy artifact — `Severity` already has `CRITICAL`
  in code, so the model is genuinely missing half the emergencies (injury/accident/danger/
  ambulance/police cases). Needs its own definitions, boundary rules and few-shot examples in
  Step 4, not just category rules.

## Step 1: dataset relabel + re-score (done)

**What changed:** `data/eval_set.csv` — the 12 rows previously labeled `ADDRESS_ISSUE`
(ids 77-88) are now `expected_category=OTHER`, `ambiguous=false`. They're kept distinguishable
from the 12 genuinely-ambiguous `OTHER` rows (ids 89-100, `ambiguous=true`) via a note prefix
(`was ADDRESS_ISSUE; out of scope -> OTHER`). `ADDRESS_ISSUE`/`CUSTOMER_REFUSED` are out of scope
per the final 7-category taxonomy: `VEHICLE_ISSUE`, `WEATHER`, `ROAD_TRAFFIC`, `CUSTOMER_ABSENT`,
`CARGO_ISSUE`, `DRIVER_ISSUE`, `OTHER`.

**Why OTHER and not CUSTOMER_ABSENT:** a wrong/incomplete address must never be classified as
`CUSTOMER_ABSENT` — that would tell a customer who was home the entire time to wait for a
reschedule. The pre-relabel baseline already does this: see regression check below.

**Re-scoring:** wrote `rescore.py`, which reuses `evaluate.py`'s scoring functions against the
*saved* per-row predictions in the baseline JSON (no HTTP/LLM calls, no rerun) and swaps in the
corrected `expected_category`/`ambiguous` from the CSV. It also computes a restricted-accuracy
summary generically from whatever `app/models.py`'s `Category` enum currently implements, so it
stays correct as categories are added in Step 3.

Ran: `python rescore.py --baseline eval_results\eval_20260924T150807Z_baseline.json --label baseline-rescored`
→ `eval_results/eval_20260924T150807Z_baseline_baseline-rescored.json`

**Result:** overall accuracy unchanged at 35.0% — none of the 12 relabeled rows were predicted
`OTHER` under the old labels either (support was 12, precision/recall 0.0 in the original baseline
for `ADDRESS_ISSUE`), so relabeling didn't change what was already wrong; it just changes *what
"correct" means* for the future Step 3/4 runs. Restricted accuracy (89.7%) and CRITICAL recall
(50%) are unaffected, as expected, since neither touches these rows.

**Named regression check** — ids 78, 81, 84, 85, 86, 87. The pre-relabel baseline predicted
`CUSTOMER_ABSENT` for all six of these (originally-ADDRESS_ISSUE) rows:

| id | text | baseline predicted | expected (corrected) |
|----|------|---------------------|------------------------|
| 78 | ما لقيتش العنوان الجي بي اس غالط | CUSTOMER_ABSENT | OTHER |
| 81 | adresse fausse ma3andich el number exact | CUSTOMER_ABSENT | OTHER |
| 84 | wrong address given by customer wasted an hour searching | CUSTOMER_ABSENT | OTHER |
| 85 | l adresse n existe pas sur la carte | CUSTOMER_ABSENT | OTHER |
| 86 | ma3andich raqm el 3imara el adresse na9sa | CUSTOMER_ABSENT | OTHER |
| 87 | ما عنديش رقم العمارة العنوان ناقص | CUSTOMER_ABSENT | OTHER |

**Not fixed yet** — this was expected, per the taxonomy/code gap above: `rescore.py`'s
regression-check output still shows all six as `STILL MISROUTED` because nothing in the running
code changed. This is the before/after evidence for the report: these six must flip to
`OTHER` + `needsReview=true` after Steps 3-4 land, and that's the check to re-run then.

No eval rerun is needed before Step 3 — any rerun now would still score low mechanically since
the service can't return `ROAD_TRAFFIC`/`CARGO_ISSUE`/`DRIVER_ISSUE` yet. The re-scored baseline
above is the reference point until then.

## Step 2: quick fixes (2b/2c/2d done; 2a code default is `False`, but `.env` overrides it)

Status: 2b, 2c, 2d are done (see "Step 2 (continued)" and Step 3 below). 2a: `app/config.py:38`
reads `_boolean("DEMO_FAIL_HOOK", False)`, so the **code default is `False`**. I can't show a diff
proving *when* that changed: `git` isn't on PATH in this shell, so the history wasn't checked from
here — the claim is only about the file's current contents. **`.env` still has `DEMO_FAIL_HOOK=true`,
which overrides the code default locally**, so 2a is not effective in this environment until
`.env` is changed. The user is fixing `.env` themselves; do not touch it.

a. `app/config.py` — `DEMO_FAIL_HOOK` must default to `False`, not `True`.
b. `app/keywords.py` + `app/analyzer.py` — replace substring matching with word-boundary regex +
   text normalization (Arabizi 9→q, 7→h, 5→kh, 3→a; strip accents; casefold; collapse whitespace).
c. `classify_category()` must stop defaulting to `VEHICLE_ISSUE`; return `OTHER` +
   `needsReview=true` instead.
d. Surface `analysisSource` end to end. Already implemented Python-side
   (`app/models.py` `AnalysisSource` enum, present in every `AnalyzeResponse`) — the remaining
   work is adding the matching field(s) to shipment-service's Java DTO/entity; exact fields to be
   confirmed once that code is reviewed.

## Step 2 (continued): `OTHER` does not work end to end yet

Python now returns `Category.OTHER` + `needsReview=true` correctly when no keyword matches
(verified: `python -m unittest discover -s tests -v`, 11/11 pass). **But** shipment-service's Java
`Category` enum (`entity/Category.java`) still only has `VEHICLE_ISSUE`/`CUSTOMER_ABSENT`/`WEATHER`.
`ShipmentService.analyzeAndCreateException` (line 146) does `Category.valueOf(...)`, catches the
resulting `IllegalArgumentException`, and degrades to
`ExceptionAnalysisResult.fallbackMode("AI returned an unrecognized severity/category")` — so it
won't crash, but every message Python now correctly routes to OTHER silently becomes an opaque
"unrecognized taxonomy" fallback on the Java side, losing `actionPlan`/`customerNotification`/
`needsReview` entirely. Not a regression (the LLM could already return unseen values) but it means
OTHER has no real effect until Java's `Category` enum gets it too. **Batched into Step 3** rather
than fixed standalone, so the Java enum, DTOs and migration only need one pass.

Also added to the test suite per your request: `RealNumberDigitTests` in `tests/test_analyzer.py`
checks the Arabizi digit substitution (9→q, 7→h, 5→kh, 3→a) against 5 real-number phrases drivers
also write ("5 km", "3 tonnes", "9h30", "wait 7 hours", "20 colis") — none match any category or
severity keyword after normalization, so the digit mapping was left unrestricted (no need to
require the digit be letter-adjacent).

## Step 3: taxonomy + Java pass (done)

### Python (`D:\ai-service`)

- `app/models.py` — `Category` gained `ROAD_TRAFFIC`, `CARGO_ISSUE`, `DRIVER_ISSUE` (final 7 with
  the existing 3 + `OTHER`). `StructuredRecord` gained `confidence: float = Field(default=1.0,
  ge=0.0, le=1.0)`. `AnalyzeResponse` reordered so `reasoning` is the first field (also reflected
  in the LLM prompt's requested JSON shape) so the model reasons before committing to a category.
- `app/keywords.py` — added keyword lists (English/French/Arabizi/Arabic-script) for the 3 new
  categories, mined from the eval dataset's actual vocabulary for those rows plus reasonable
  synonyms. Deliberately did **not** add a bare "road blocked" phrase to `ROAD_TRAFFIC` — that
  phrase is already a `WEATHER` keyword (roads get blocked by rain too) and the two are only
  distinguishable by cause, not the word "blocked" itself.
- `app/analyzer.py` — `_action_plan`/`_customer_notification` got entries for the 3 new
  categories; `analyze_with_keywords` now sets `confidence` (0.7 if a keyword matched, 0.0 if it
  fell through to `OTHER`+`needsReview` — keyword matching has no real probability model, so this
  is a deliberately coarse signal, not a calibrated score). LLM prompt/system-prompt updated with
  the 7-category definitions and the wrong-address-is-never-CUSTOMER_ABSENT rule; the deeper
  CRITICAL-severity few-shot rewrite is Step 4, not done here.

**Keyword-fallback sanity check** (offline, no HTTP/LLM calls, run against corrected
`data/eval_set.csv`, restricted to the 6 non-OTHER categories now implemented, 76 rows):
**86.8% (66/76)**. This is the fallback path only — the LLM handles 95% of real traffic — so this
number is diligence, not the headline metric. One real collision found and fixed: `"chaufeur jri7
in accident blesse"` tied `DRIVER_ISSUE` (1 match: `"chaufeur jri7"`) against the pre-existing
bare `VEHICLE_ISSUE` keyword `"accident"` (1 match) and lost the dict-order tie-break; added
standalone `"jri7"`/`"blesse"`/`"injured"` as `DRIVER_ISSUE` keywords, which is a real
generalization (injury words legitimately signal DRIVER_ISSUE anywhere), not test-set overfitting.
Verified with `python -m unittest discover -s tests`: 11/11 still pass.

**Remaining 10 misses, left as-is (not today's scope, or intentionally adversarial):**
- ids 4, 11, 12 (`VEHICLE_ISSUE`→`OTHER`), 42, 47, 50 (`CUSTOMER_ABSENT`→`OTHER`), 23/24
  (`WEATHER`→`VEHICLE_ISSUE`): pre-existing keyword-coverage gaps in `VEHICLE_ISSUE`/
  `CUSTOMER_ABSENT` that predate Step 2/3 (e.g. "broke down" isn't "breakdown"; "no one home"
  isn't "not home"/"nobody home") — the old substring matcher wouldn't have caught these either.
  Not touched; out of scope for the taxonomy work.
- id 37 (`ROAD_TRAFFIC`→`WEATHER`): the same generic "road blocked" ambiguity noted above — the
  Arabizi row (36) happens to resolve correctly because it has a second, distinct accident phrase
  that tips the score; the Arabic-script row (37) only has the one shared phrase. Not fixed by
  adding a near-duplicate keyword — that would be memorizing this one sentence, not generalizing.
- id 54 (`CARGO_ISSUE`→`CUSTOMER_ABSENT`): the dataset's own "confusable pair" (rows 39 vs 53/54,
  "couldn't find the **customer**" vs "couldn't find the **goods**") — flagged as intentionally
  hard in the CSV's own notes column, not a bug to chase with keyword tie-breaking.

### Java (`shipment-service`, batched from Step 2 item d + this step)

1. `entity/Category.java` — now all 7: `VEHICLE_ISSUE, CUSTOMER_ABSENT, WEATHER, ROAD_TRAFFIC,
   CARGO_ISSUE, DRIVER_ISSUE, OTHER`. This closes the Step 2 gap where Python could return `OTHER`
   but Java's `Category.valueOf()` would throw and silently degrade to `fallbackMode`.
2. `client/AiServiceClient.java` — `AnalyzeResponse` gained `analysisSource`; `StructuredRecord`
   gained `needsReview` and `confidence`. **Correction applied:** these three use
   `@JsonProperty("analysisSource"/"needsReview"/"confidence")` as the *primary* name with
   snake_case only as a defensive `@JsonAlias` — Python emits camelCase directly (plain Pydantic
   field names, no alias generator), so camelCase has to be primary, not the alias. (The existing
   3 fields keep their pre-existing snake_case-primary pattern; not touched, out of scope.)
3. `entity/ShipmentException.java` — added `analysisSource` (String), `needsReview` (Boolean),
   `confidence` (Double), all **nullable with no `@Column(nullable = false)`** — deliberately, and
   permanently, not just as a migration phase. Unlike `customer_auth_user_id` (where a real value
   exists and can eventually be backfilled), there is no correct historical value for these three
   on exceptions created before this feature existed, so nullable is the correct end state, not a
   temporary step toward not-null.
4. `dto/ShipmentExceptionResponse.java` + `mapper/ShipmentExceptionMapper.java` — the 3 fields
   threaded through to the API response.
5. `service/ShipmentService.java` (`analyzeAndCreateException`) — builder now sets
   `.analysisSource(...)`, `.needsReview(...)`, `.confidence(...)` from the AI response.

**No CHECK constraint exists to update.** Verified by grep across the whole `backend/` tree: no
Flyway, no Liquibase, no `@Check` annotation, no `columnDefinition` with a CHECK clause anywhere.
`config-repo/shipment-service.yml` has `hibernate.ddl-auto: update`; `category` is a plain
`@Enumerated(EnumType.STRING)` column (generic VARCHAR, Hibernate doesn't restrict its values).
So: expanding the enum needs no schema change at all (existing rows' values are still valid), and
the 3 new nullable columns will be auto-added by Hibernate on next boot — consistent with how
every other schema change in this project has happened so far (no migration tool is set up). If
you actually want a DB-level CHECK constraint (defense in depth against a future Java/Python enum
drift), that would be a new addition, not an update to something existing, and would need a manual
`ALTER TABLE exceptions ADD CONSTRAINT ... CHECK (category IN (...))` run once against `fleet_db`
since there's no migration tool to codify it — didn't do this since it wasn't confirmed as wanted.

**Rule B / needsReview decision — agreed with your view, implemented:** `ShipmentService.java`'s
Rule B now reads `if ((severity == HIGH || severity == CRITICAL) && !needsReview)`. Reasoning:
`needsReview=true` means the classifier had no real signal for what happened (severity keywords
came from the same ambiguous text as the category ones) — auto-HALTing a shipment on top of an
admitted guess compounds uncertainty into a disruptive action a human hasn't reviewed. The
`ADDRESS_ISSUE`→`CUSTOMER_ABSENT` bug is exactly this pattern in miniature: acting confidently on
an unreliable signal caused real harm (telling a present customer to expect a reschedule). Rule B
otherwise fires uniformly by severity regardless of category (unchanged, category-agnostic) — a
HIGH/CRITICAL `DRIVER_ISSUE` or `CARGO_ISSUE` halts exactly like `VEHICLE_ISSUE`/`WEATHER` always
did, no code change needed there since the condition was never category-specific. One nuance not
implemented: a CRITICAL-severity message that also has `needsReview=true` (ambiguous category, but
urgent-sounding language) doesn't get any special escalation beyond the normal audit-log entry —
worth a follow-up (e.g. a distinct "needs urgent review" flag) if that combination turns out to
matter in practice, but not built now since it's speculative.

**Build verified:** `mvn -pl shipment-service -am compile` → `BUILD SUCCESS`.

**Not done — `_eta_impact` in `app/analyzer.py`:** left keyed by `Severity` only, unchanged, no
per-category entries added. It already covers LOW/HIGH/CRITICAL for any category with no crash
risk (verified: the keyword-fallback sanity check exercised all 7 categories across 100 rows with
no `KeyError`). If category-specific ETA phrasing was actually intended by "eta-impact entries" in
the Step 3 instructions, flag it and I'll add it — read it as covered by the existing design.

## Step 4: severity few-shot rewrite (done, not yet evaluated against the live model)

Baseline CRITICAL recall was 50% (3/6): correctly CRITICAL on ids 22, 24, 69; wrongly downgraded
on ids 23 (flood, predicted LOW), 72 ("minor fall needs medical help", predicted HIGH), 73
("blesse besoin d'une ambulance", predicted HIGH). 95% of the baseline ran on the LLM path, so
this is real model error, not a keyword-fallback issue — id 73 explicitly contains "ambulance" and
"blesse" and was still under-rated, and the same flood sentence (23 arabizi vs 24 arabic-script)
got inconsistent severity for identical meaning. That inconsistency-across-script pattern is why
this needed explicit boundary rules and concrete examples, not just a taxonomy fix.

**`app/analyzer.py`** — `system_prompt` now has an explicit severity section, independent from the
category section: CRITICAL = immediate risk to life/safety (injury, accident with injury, active
danger e.g. stuck in rising floodwater, ambulance genuinely needed) with an explicit rule not to
downgrade an injury to HIGH just because the driver calls it "minor"; HIGH = real operational
problem, no danger to anyone; LOW = minor impact. Also states a routine police checkpoint is not
CRITICAL by itself.

Added `_SEVERITY_FEW_SHOT_EXAMPLES` — 6 real assistant-turn examples (not prose description) sent
as `user`/`assistant` message pairs before the real request, since concrete precedent in the exact
output shape is more reliable than instructions alone for a free-tier/small model:
1. "driver injured in a minor fall needs medical help" → CRITICAL (don't downgrade "minor")
2. "chauffeur blesse besoin d une ambulance" → CRITICAL (explicit ambulance)
3. "fayadan fi tri9 el karhba ma temchich" → CRITICAL (active danger, no explicit injury)
4. "chaufeur jri7 in accident blesse" → CRITICAL (accident + injury)
5. "police blocked the road for an inspection" → LOW, ROAD_TRAFFIC (contrast: routine police ≠ CRITICAL)
6. "le camion est en panne" → HIGH, VEHICLE_ISSUE (contrast: ordinary breakdown ≠ CRITICAL)

**`app/keywords.py`** — found and fixed a real precision bug while writing the above: bare
`"police"`/`"police"`(FR)/`"بوليس"` were in `CRITICAL_SEVERITY_KEYWORDS`, which would have scored
CRITICAL for *any* mention of police, including the dataset's own LOW-severity row 38 ("police
blocked the road for an inspection") if that message ever hit the keyword fallback path. Replaced
with more specific escalation phrases (`"called the police"`, `"police called"`, `"appelé la
police"`, `"طلب البوليس"`) plus the pre-existing `"taleb police"` (Arabizi) — bare `"ambulance"` was
left as-is since it has no equivalent routine/non-emergency use in this domain.

**Tests** — 3 new cases in `tests/test_analyzer.py` (`CriticalSeverityPrecisionTests`): routine
police checkpoint is not CRITICAL, explicit ambulance request is still CRITICAL, an explicit
"called the police for an emergency" phrase is still CRITICAL. Full suite: **14/14 pass**
(`python -m unittest discover -s tests -v`).

## Step 4 evaluation attempt (2026-09-25): quota-blocked, methodology decision to not report

Tried to get real after-step4 numbers and hit a genuine Groq free-tier quota wall for
`allam-2-7b`, not a bug in our code. Documenting the investigation itself, since "the run was
fallback-dominated so the accuracy would have been meaningless" is a methodology decision, not
just a failure to record and move past.

**Bug found and fixed along the way (real, keep this):** `Analyzer._analyze_with_llm`
(`app/analyzer.py`) constructed `AsyncOpenAI(...)` without `max_retries=0`. The SDK's default
(`max_retries=2`) retries silently *inside our server* on a 429, sleeping 10-20s+ per attempt,
invisible to our own `OPENAI_TIMEOUT_SECONDS` and to callers. This stacked with `evaluate.py`'s
own client-side timeout (15s default) expiring mid-retry and resending the same row while the
first attempt was still in flight -- up to ~3x real request volume per logical row. Fixed by
adding `max_retries=0` so one bounded attempt (7s) fails fast into our own keyword fallback
instead of hanging. Verified: a run that was projected to take over an hour (only ~20/100 rows
done after 14 minutes, pre-fix) completed the same 100 rows in under 2 minutes post-fix. This is a
real production fix too -- a live driver message hitting a rate-limited moment would have hung the
same way before this change, not just eval runs.

**Evidence chain that this is a genuine Groq-side quota, not our pacing:** three full/partial runs
after the fix, at decreasing request rates, got *worse*, not better:
| run | pacing | fallback rate |
|---|---|---|
| 1 | `--rps 0.5` (2s spacing) | 90% |
| 2 | `--rps 0.1` (10s spacing) | 73% |
| 3 | 10-row targeted subset, `--rps 0.2`, 6 retries, 3s backoff base | 100% |

Directly contradicts "just pace it slower": a monotonic worsening despite slower pacing and more
retry margin. Confirmed with isolated diagnostic calls (`RateLimitError` headers/body captured via
a standalone script bypassing `evaluate.py` entirely): 8 rapid tiny-prompt calls succeeded with
zero 429s; 4 rapid calls using the real ~1300-token few-shot prompt also succeeded with zero 429s,
run immediately *after* a heavily-rate-limited full run. Isolated small bursts succeed instantly;
sustained runs over many minutes get hammered regardless of how slowly they're paced. That pattern
points to a cumulative per-hour/day budget for this specific niche/free model (`allam-2-7b` is a
narrow Arabic-specialized preview model on Groq, plausibly given a much lower quota than mainstream
models), consumed by the session's own volume today (~230+ real requests across the broken run's
duplicate traffic, two full 100-row runs, and diagnostic bursts) -- not something fixable by
tuning `--rps` further within the same window.

Raw evidence: `httpx: HTTP Request: POST https://api.groq.com/openai/v1/chat/completions "HTTP/1.1
429 Too Many Requests"` in `uvicorn_stderr.log` (133 occurrences in that file alone), each one also
independently logged by our own code as `ai_service.analyzer: LLM analysis FAILED ... code=RATE_LIMITED`.

**Decision: do not report any of the three post-fix runs' accuracy numbers.** All three were
90-100% `KEYWORD_FALLBACK`, meaning they measured the deterministic keyword matcher, not the Step 4
few-shot prompt rewrite the user actually asked about. Reporting them as "after-step4" numbers
would misattribute keyword-fallback behavior to the LLM change under test.

**What the fallback-path data DID show for the specific rows in the targeted subset check** (real,
verified, but explicitly NOT the LLM path -- kept as informational, not as the answer to the
original question):
| id | text | fallback predicted | expected | note |
|---|---|---|---|---|
| 73 | chauffeur blesse besoin d'une ambulance | CRITICAL ✓ | CRITICAL | via `"ambulance"` keyword, already CRITICAL pre-Step-4 -- doesn't test the few-shot fix |
| 23 | fayadan... (flood, Arabizi) | VEHICLE_ISSUE / LOW ✗ | WEATHER / CRITICAL | wrong category and severity |
| 24 | same flood, Arabic script | VEHICLE_ISSUE / LOW ✗ | WEATHER / CRITICAL | same as 23 -- at least now consistent with each other, both wrong |
| 52 | couldnt find the products | CARGO_ISSUE ✓ | CARGO_ISSUE | Step 3 keyword, not Step 4 |
| 78 | wrong address (Arabic script) | CUSTOMER_ABSENT ✗ | OTHER | still misrouted -- see limitation below |
| 81, 84, 85, 86, 87 | wrong address (other 5) | OTHER ✓ | OTHER | all correct |

**Documented limitation: keyword matching cannot fully resolve the "confusable pair" cases.** id
78 fails specifically because `"ما لقيتش"` ("couldn't find") is a `CUSTOMER_ABSENT` keyword and
*does* match -- the Step 2 OTHER-default fix only helps when *no* keyword matches at all; it does
nothing when the *wrong* keyword matches. This is the same structural issue as the CARGO_ISSUE vs
CUSTOMER_ABSENT "confusable pair" documented in Step 3 (`"couldn't find the customer"` vs
`"couldn't find the goods"` vs `"couldn't find the address"` all share the generic "couldn't find
X" phrase). A keyword matcher has no way to know what X refers to -- that requires actually
understanding the sentence. This is structurally *why the LLM path exists*: the LLM system prompt
has the explicit "a wrong address is OTHER, never CUSTOMER_ABSENT" rule precisely for this case,
but that rule is Python-side reasoning the deterministic keyword matcher cannot replicate. Treat
this as a permanent property of the degraded/fallback path, not a bug to keep chasing with more
keywords -- the fallback's job is to be *safe and available*, not to match LLM-level accuracy.

**Decision: do not switch models to get unblocked today.** The baseline was run on `allam-2-7b`;
changing model and prompt in the same comparison would make before/after uninterpretable (can't
tell whether a change in numbers comes from the prompt rewrite or from a different model). A
model comparison is a separate experiment for later, run against a stable prompt.

**Correction (2026-09-25, later same session):** the "wait an hour or two for the quota to reset"
framing above was my own unverified guess, not a confirmed fact -- called out and corrected after
the user directly challenged it. What's actually confirmed: Groq's own docs
(`console.groq.com/docs/rate-limits`) don't list `allam-2-7b` in the current rate-limit table at
all. Third-party sources (not Groq itself, so treat as indicative not authoritative) consistently
report Groq's free-tier "high-quota" model group -- said to include Allam 2 7B -- at roughly 30
RPM / **6,000 TPM** / ~1,000 RPD (daily figure disputed between sources, 1,000 vs 14,400).

The 6,000 TPM number fits the observed failure pattern much better than a hypothetical
hourly/daily budget: our few-shot prompt measured at `prompt_tokens=1296` per request (confirmed
via a direct diagnostic call). At a 6,000 TPM **rolling** window, that's only ~4 requests/minute
before tripping 429 regardless of spacing -- which is why slowing `--rps` from 0.5 to 0.1 (still
~6 req/min, over a ~4/min ceiling) barely helped, and why isolated 4-request bursts succeeded
cleanly while sustained runs got hammered. If this is right, there is nothing to "reset" on an
hours timescale -- it's a rolling per-minute window, so pacing at ~1 request per 15-20s (`--rps
0.06`) should work *right now*, not after a wait.

**Confirmed against the real dashboard** (`console.groq.com/settings/limits`, checked by the user):
`allam-2-7b` = 30 RPM / 7,000 RPD / **6,000 TPM** / 500,000 TPD. TPM is the binding constraint --
RPM/RPD/TPD all have large headroom (today's session used ~230 requests against a 7,000/day
budget). Matches the third-party estimate almost exactly. Our few-shot prompt measures ~1,296
prompt tokens + ~150-230 completion tokens ≈ ~1,470 tokens/request, so 6,000 TPM caps us at ~4
req/min with zero margin. Pacing at 1 request per 20s (3/min, ~4,400 tokens/min) leaves ~25%
margin. This is a rolling per-minute window, not an hourly/daily budget -- no need to wait, just
pace correctly. `--rps 0.05` is the value used for the actual after-step4 run below.

### Real run at `--rps 0.05` (2026-09-25, after confirming actual Groq limits)

`eval_results/eval_20260925T175009Z_after-step4.json`. Fallback rate down to 39% (from 90%/73%/100%
in the mis-paced attempts) -- confirms the TPM-pacing diagnosis was right -- but still above the
script's own 15% trust threshold and the baseline's 5%, so **the aggregate numbers below are still
not being reported as final**, per standing instruction. Recorded here as a data point, not a
conclusion:
overall accuracy 71.0%, macro-F1 0.718, CRITICAL recall 16.7% (1/6), latency mean 0.82s / p95 2.52s.

**But: a real, non-fallback finding surfaced from this run, independent of the fallback-rate
caveat.** All 4 of the 6 CRITICAL-severity rows that went through the LLM path (ids 22, 24, 69, 73)
got the wrong severity -- 0/4, worse than baseline's 3/6 (which was also mostly LLM). This is not
rate-limit noise: reproduced id 73 live via a direct call to the running server (not through
evaluate.py) and captured the full response:
```
text: "chauffeur blesse besoin d une ambulance"
reasoning: "The driver is injured and needs an ambulance, but the message is too vague
            for a specific category -- this falls under OTHER for now..."
severity: LOW, category: OTHER, needsReview: true, confidence: 0.7
```
The model's own reasoning correctly identifies the injury and the ambulance request, then
contradicts that reasoning in the structured fields (LOW/OTHER instead of CRITICAL/DRIVER_ISSUE).
This exact sentence is also a near-verbatim few-shot example in the prompt showing the correct
CRITICAL/DRIVER_ISSUE answer -- the model saw the right answer earlier in its own context and
still didn't reproduce it.

**Working hypothesis, not confirmed:** `allam-2-7b` (a small, 7B, regional/niche model) may not
reliably use a long multi-turn few-shot context (14 messages: system + 6 example pairs + real
request) combined with a 6-field structured JSON schema (reasoning, severity, category, etaImpact,
needsReview, confidence + actionPlan/customerNotification) -- reasoning-then-structured-output
consistency is a known weak point for smaller models, and the added context length/schema
complexity from Step 3+4 may be overloading it rather than helping. Not yet isolated which factor
(context length vs. schema complexity vs. something else) is the actual cause, and not yet decided
how to respond -- flagging for discussion rather than acting unilaterally.

**To rerun (single command, server already running with the fix loaded). Pace at `--rps 0.05`
(1 request / 20s) at the very most -- but see the 2026-09-28 correction below: the real request is
~2,230 tokens, not ~1,470, so even 0.05 is over the 6,000 TPM cap with the current prompt:**
```powershell
cd D:\ai-service
.\.venv\Scripts\python.exe evaluate.py --base-url http://127.0.0.1:8000 --label after-step4 --rps 0.05
.\.venv\Scripts\python.exe rescore.py --baseline eval_results\<the new after-step4 file>.json --label after-step4-rescored
```
(Superseded advice removed: an earlier version of this block said to use the default `--rps 1.0`.
That was written before the TPM limit was confirmed and is wrong -- at ~1,470 tokens/request,
1 req/s blows the 6,000 TPM cap immediately. Even at 0.05 the 2026-09-25 run still had 39%
fallback -- explained by the token correction below.) If the server isn't still running (check
with `Invoke-RestMethod http://127.0.0.1:8000/healthz`), restart it first:
```powershell
cd D:\ai-service
Start-Process .\.venv\Scripts\python.exe -ArgumentList "-m","uvicorn","app.main:app","--host","127.0.0.1","--port","8000" -WindowStyle Hidden -RedirectStandardOutput uvicorn_stdout.log -RedirectStandardError uvicorn_stderr.log
```
Once a genuinely LLM-dominated run lands (fallback rate back near the baseline's 5%), build the
before/after table: overall accuracy, macro-F1, 3-category (now N-category, see Step 3) restricted
accuracy, CRITICAL recall, per-language accuracy, valid JSON rate, fallback rate, latency
mean/p95 -- and re-check the specific rows: 73 (must be CRITICAL), 23 vs 24 (must agree with each
other), 52 (must be CARGO_ISSUE), 78/81/84/85/86/87 (must be OTHER+needsReview, not
CUSTOMER_ABSENT).

**Also noted, not yet acted on:** `.env` has `DEMO_FAIL_HOOK=true`, which overrides the Step 2 code
default (`False`) for this local environment -- the env var wins over the code default whenever
it's explicitly set. Doesn't affect the eval (no dataset row contains "fail"), but means the Step
2a fix isn't actually active locally until `.env` is also updated; flagging, not changing `.env`
without being asked.

## Latency check on the after-step4 run (2026-09-28)

Suspicion: 0.82s mean / 2.52s p95 is *faster* than baseline (2.53s / 4.58s) despite a longer
prompt, so the 39% keyword-fallback rows must be dragging the mean down. **Checked; the hypothesis
is not supported.** Recomputed from per-row `latency_seconds`, split by `analysis_source`:

| run | path | n | mean | median | p95 |
|---|---|---|---|---|---|
| baseline 2026-09-24 | all (as reported) | 100 | 2.53s | -- | 4.58s |
| baseline | LLM only | 95 | 2.56s | 2.50s | 4.58s |
| baseline | KEYWORD_FALLBACK | 5 | 2.02s | 2.31s | 4.59s |
| after-step4 2026-09-25 | all (as reported) | 100 | 0.82s | -- | 2.52s |
| after-step4 | **LLM only** | 61 | **0.66s** | 0.50s | **1.38s** |
| after-step4 | KEYWORD_FALLBACK | 39 | 1.06s | 0.52s | 4.10s |

Fallback rows are *slower* (they include a failed/429 round trip first, max 8.9s), so they pull the
blended mean *up*. LLM-path-only latency is even lower (0.66s), so the speedup is real for the rows
that did reach the model. Cause of the speedup vs baseline is **not established**: not prompt size
(the prompt is larger now). Candidates, untested: Groq-side load differed between 09-24 and 09-25;
the 61 LLM rows are survivors (slow requests that timed out/429'd became fallback rows, so the LLM
sample is biased fast); cold-vs-warm variance (in the 2026-09-28 diagnostic, the same control call
took 5.72s cold and 0.39s on the immediate repeat). Do not cite 0.82s/2.52s as a latency
*improvement*; latency is not comparable between these runs.

## Row-73 severity isolation (2026-09-28, allam-2-7b, direct calls, not evaluate.py)

Script (scratchpad, not in repo): 5 variants x 2 repeats, 22s apart, temperature 0, same
`response_format=json_object`. Variant (a) messages were captured from the real
`Analyzer._analyze_with_llm` via a stub client, so the control is byte-identical to production.
Input: row 73 verbatim, `chauffeur blesse besoin d une ambulance` (expected DRIVER_ISSUE / CRITICAL).

| variant | what changed vs control | severity | category | prompt tok | completion tok | 2 repeats agree |
|---|---|---|---|---|---|---|
| a control | -- (full prompt, 6 few-shot, full schema) | **LOW** | OTHER | 2038 | 190 | yes |
| b | few-shot removed, rules kept (+ dropped the sentence pointing at "the examples that follow") | **HIGH** | DRIVER_ISSUE | 761 | 223 | yes |
| c | full few-shot, output = reasoning + severity + category only | **CRITICAL** | DRIVER_ISSUE | 1317 | 62 | yes |
| d1 | severity alone, few-shot kept (severity-only examples) | **CRITICAL** | -- | 872 | 12 | yes |
| d2 | severity alone, no few-shot | **CRITICAL** | -- | 601 | 11 | yes |

Reading: **output-schema size is the variable that flips the answer.** Full schema fails with
examples (a: LOW) and without (b: HIGH); reduced schema succeeds with examples (c, d1) and without
(d2). Context length alone is not the cause -- b has a short 761-token context and is still wrong.
The examples don't help on their own either: under the full schema they made it worse (a LOW/OTHER
vs b HIGH/DRIVER_ISSUE). Caveat: c also shrinks the examples' own assistant turns, so "schema" and
"length of the assistant turns" are not separable in c; b is what rules out prompt length.

**Caveats that limit this result:**
1. One row, two repeats. Nothing here says the fix generalises to ids 22/23/24/69/72.
2. **Eval contamination.** All 6 few-shot examples are verbatim eval rows (ids 3, 23, 38, 69, 72,
   73); 4 of the 6 CRITICAL eval rows (23, 69, 72, 73) are literally in the prompt. Row 73 is
   few-shot example #2. So c/d1 passing on 73 may be memorisation. d2 (no examples) is the only
   uncontaminated CRITICAL evidence, and the control failing on a sentence it was *shown verbatim*
   is what makes the full-schema failure so damning. Any post-step4 CRITICAL recall on this eval set
   is not a held-out measurement until the examples are replaced with non-eval sentences.
3. **Token-budget correction:** the control request is **2,038 prompt + ~190 completion = ~2,230
   tokens**, not the ~1,296/~1,470 recorded in the 2026-09-25 notes (that was measured before the
   full few-shot block/prompt; I did not re-derive why). At 6,000 TPM that is ~2.7 req/min, so
   `--rps 0.05` (3/min = ~6,700 TPM) was still over the cap -- this explains the residual 39%
   fallback. Variant c is ~1,380 tokens (~4.3 req/min); d2 is ~610 (~9.8 req/min).

## Held-out validation of variant c (2026-09-28): DOES NOT FIX SEVERITY (c is kept for other reasons, see "Variant c vs full schema")

Ran the **shipped code path** (`Analyzer._analyze_with_llm`, new non-eval few-shot, reduced
reasoning+severity+category schema, derived actionPlan/customerNotification/etaImpact,
needsReview = category==OTHER) on `data/heldout_critical.csv`, allam-2-7b, 18s apart, 13 calls.
Gate the user set: "if c holds up, go with it". **It did not hold up as a severity fix.** (It was later kept anyway, for token cost and category accuracy -- see below; severity is handled by rule-first.)

| id | lang | kind | expected | c severity | c category | ok |
|---|---|---|---|---|---|---|
| H1 | arabizi | injury + ambulance | CRITICAL | **LOW** | DRIVER_ISSUE | ✗ |
| H2 | arabizi | armed men threaten driver | CRITICAL | **LOW** | ROAD_TRAFFIC | ✗ |
| H3 | arabic-script | injury + ambulance | CRITICAL | CRITICAL | DRIVER_ISSUE | ✓ |
| H4 | arabic-script | ground collapsing under truck | CRITICAL | **LOW** | VEHICLE_ISSUE | ✗ |
| H5 | french | accident, several injured | CRITICAL | CRITICAL | ROAD_TRAFFIC | ✓ |
| H6 | french | cardiac episode, calling SAMU | CRITICAL | CRITICAL | DRIVER_ISSUE | ✓ |
| H7 | english | driver collapsed, ambulance | CRITICAL | CRITICAL | DRIVER_ISSUE | ✓ |
| H8 | english | rollover, 2 trapped | CRITICAL | CRITICAL | VEHICLE_ISSUE | ✓ |
| H9 | english | brakes failed on descent | CRITICAL | **HIGH** | VEHICLE_ISSUE | ✗ |
| H10 | french | sandstorm, truck stopped on motorway (borderline label) | CRITICAL | **HIGH** | ROAD_TRAFFIC | ✗ |
| N1 | english | customer not home (control) | not CRITICAL | LOW | **OTHER** | ✓ sev / ✗ cat (expected CUSTOMER_ABSENT) |
| N2 | arabic-script | road closed, works (control) | not CRITICAL | HIGH | ROAD_TRAFFIC | ✓ |
| N3 | french | driver tired, 2h break (control) | not CRITICAL | LOW | DRIVER_ISSUE | ✓ |

**CRITICAL recall on held-out: 5/10 (50%)** — same as the contaminated baseline's 50%. No
overcorrection (3/3 controls not CRITICAL). By kind: injury/ambulance/casualty 5/6 (only the
**arabizi** one failed), active-danger-without-injury **0/4**. By language: french 2/3, english 2/3,
arabic-script 1/2, **arabizi 0/2**. All 13 calls returned valid output (0 errors, 0 fallbacks).

**The failures are comprehension/calibration errors, not schema errors** — read the model's own
`reasoning` on the misses: H1 "driver is feeling uneasy due to a previous accident (hit-and-run)"
(misread `tjar7 ... ambulance` entirely); H2 "stuck in traffic due to congestion" (misread armed
men); H4 "potential safety issue... but not immediately life-threatening, so LOW" (it *sees* the
danger and still calls it LOW); H9 "poses a safety risk" -> HIGH. N1's reasoning ignores the
CUSTOMER_ABSENT definition it was given. This is a 7B model that under-reads Tunisian Arabizi and
under-weights danger that isn't an explicit injury.

**Severity-only split (d), same 13 sentences, direct calls** — to check whether splitting would
have rescued it:
| variant | CRITICAL recall | notes |
|---|---|---|
| c (single call, reduced schema) | 5/10 | ~1,500 tok/call (1,430 prompt + ~69 completion, measured) |
| d1 severity-only + severity-only few-shot | **6/10** (H3-H8) | ~945 tok/call |
| d2 severity-only, no few-shot | 5/9 (H4 returned `BadRequestError`, not scored) | ~605 tok/call |
| keyword `classify_severity` (offline, 0 tokens) | 4/10 (H1,H3,H5,H7), 0 false positives | |
All fail on the same rows (H2, H9, H10 by every LLM variant; H1 by c, d1, d2). **d1's 6/10 vs c's 5/10
is one sentence — noise, not a reason to pay for a second call.** The split is therefore *not*
recommended: a second call adds ~945 tokens (~+63% over c) for no measured gain. The earlier row-73
"schema fixes it" finding was real for that one row but **did not generalise** — row 73 is French
with an explicit "blesse"/"ambulance", the easy end of the distribution.

**Caveats on this test:** n=10 CRITICAL, 1 run each (temperature 0, not repeated). The two arabizi
sentences and the arabic-script ones were **written by an LLM, not a native Tunisian speaker**, so
part of the arabizi failure could be my dictionary ("tjar7", "3tarrdou", "msal7in" may not be
natural) rather than the model — H1/H2 need native-speaker review before being read as a
pure model failure. H4/H5/H8 category and H10's label are debatable (ambiguous acceptable
categories are recorded in the CSV; H10 is marked borderline). No held-out sentence overlaps eval or few-shot.

**State of the code (RESOLVED 2026-09-28: c kept, see "Variant c vs full schema"; original note follows):** `app/analyzer.py` and `app/models.py` currently
contain the variant-c implementation + the new non-eval few-shot (14/14 unit tests pass). It was
written in order to test the *shipped* path, and is **not adopted** since the gate failed. There is
no git on PATH in this shell, so the pre-change files are only in the session scratchpad
(`analyzer.py.bak`, `models.py.bak`); the running uvicorn (started without `--reload`) still
serves the old code if it is up. Decision needed: keep c's structure (cheaper, ~33% fewer tokens
than the old ~2,230 control) with the new clean examples -- note the full-schema prompt with the new
examples was *not* run on the held-out set, so "no worse than the full schema" is untested, or revert to the
full schema with the new clean examples. Nothing here is an accuracy win either way.

**Options to discuss (not done):**
1. **Keyword safety floor**: final severity = max(LLM, keyword CRITICAL). Deterministic, 0 tokens,
   0 false positives here; adds H1 to c's hits (6/10). Asymmetric-cost argument: a false CRITICAL
   costs a human review, a missed one is an unattended emergency. Does not fix H2/H4/H9/H10 —
   extend `CRITICAL_SEVERITY_KEYWORDS` for danger phrases (brake failure, collapse, armed/threat)
   only as generalisations, not by memorising these sentences.
2. **Bigger model** for the LLM path as its own experiment (already decided earlier: not in the same
   comparison as a prompt change). allam-2-7b is the likely ceiling here.
3. Native-speaker review of the arabizi / arabic-script rows in both CSVs (already flagged in the
   eval CSV header).

## Rerun pacing, recomputed (2026-09-28)

Groq `allam-2-7b`: **6,000 TPM** is the binding limit (prompt + completion). Measured request sizes:
| prompt | tokens/request | max req/min @100% | `--rps` that stays <=75% of cap |
|---|---|---|---|
| old full-schema control (contaminated examples) | ~2,230 (2,038 + ~190) | 2.7 | 0.03 (1.8/min, 4,000 TPM) |
| current code, variant c + new examples | ~1,500 (1,430 + ~69) | 4.0 | **0.05** (3/min, 4,500 TPM = 75%) |
| severity-only d1 (if ever used, per call) | ~945 | 6.3 | 0.08 |
The old guidance (0.05 with the ~2,230-token control = 6,690 TPM, 111% of cap) was over the limit,
which is why that run still had 39% fallback. **For the code as it stands now (variant c), use
`--rps 0.05`; if the code is reverted to the full-schema prompt, use `--rps 0.03`.** The full
100-row eval is still on hold until the CRITICAL question is settled; when it runs, also run
`heldout_critical.csv` and report CRITICAL recall on the held-out set, not on `eval_set.csv`.

## FINDING (2026-09-28): allam-2-7b is not reliable on emergency severity in dialect, whatever the schema

Recorded as the conclusion of the row-73 isolation + held-out validation above. **The earlier CRITICAL
"successes" were memorisation**: the few-shot examples were verbatim eval rows, so passes on ids
23/69/72/73 (variants c and d1 on row 73, the after-step4 eval CRITICAL rows) cannot be credited to
the prompt design. On sentences the model has never seen, allam-2-7b gets CRITICAL right on **5/10**
whatever the schema shape tried on the held-out set (c, d1, d2 all land at 5-6/10; the full schema was only tested on row 73, where it failed), and the misses cluster on
Tunisian arabizi and on danger-without-injury. One precision on the record: variant d2 (severity
alone, *no* few-shot) also got row 73 right -- that is not memorisation, but row 73 is French with
an explicit "blesse"/"ambulance", the easy end, and it did not generalise to the held-out set.
Consequence: severity cannot be left to this model alone -> rule-first below.

## Rule-first severity (2026-09-28, implemented)

`app/analyzer.py`: `escalate_severity(model_severity, text)` = the higher of the model's severity and
`classify_severity(text)` (the CRITICAL/HIGH keyword rules), run on **every LLM-path message**. Rules can
raise a model answer, never lower it. When it fires, the response `reasoning` gets
`[Severity raised from X to Y by the keyword safety rules.]` appended and a WARNING is logged, so an
escalation is auditable. **Interpretation to confirm:** the brief said "the model's severity only applies
when the rules find nothing"; taken literally, a rule-HIGH would *downgrade* a model-CRITICAL, which
contradicts the rationale (never miss an emergency). Implemented escalate-only, i.e. `max(model, rules)`.
The keyword-fallback path is unchanged (it is already rules-only). Tests: 18/18 pass
(`SeverityEscalationTests`: rules raise LOW/HIGH -> CRITICAL, rules never lower, no rule hit -> model kept,
end-to-end via a stub client incl. the reasoning marker).

**Measured (no new LLM calls; escalation is post-hoc, applied to saved model outputs):**

| set | metric | model-only | rule-first |
|---|---|---|---|
| held-out, allam-2-7b (c), n=10 CRITICAL | CRITICAL recall | 5/10 | **6/10** (+H1, the arabizi ambulance) |
| held-out controls, n=3 | false CRITICAL | 0/3 | 0/3 |
| baseline eval preds (2026-09-24, old prompt, uncontaminated), n=100 | CRITICAL recall | 3/6 | 5/6 |
| same | false CRITICAL (of 94 non-CRITICAL) | 2 | 3 (+1: id 37, "accident ahead, road blocked" -- bare *accident* is a CRITICAL keyword) |
| same | LOW rows over-flagged HIGH (of 59) | 4 | 7 (+3: ids 25 "light rain slight delay", 26 "just a small delay", 38 routine police -- generic HIGH words *delay*/*blocked*) |
| same | 3-class severity accuracy | 68.0% | 70.0% |

Rules alone on the eval set: CRITICAL on 3/6 true CRITICAL, on 1/94 non-CRITICAL. **False-positive cost is
low and visible:** one extra false CRITICAL and three extra LOW->HIGH over-flags across 100 rows; accuracy
did not drop. **Caveats:** (1) the eval-set gain (3/6 -> 5/6) is optimistic -- the CRITICAL keywords
("jri7", "blesse", "injured", the police phrases) were partly written after seeing rows 69/72/73 -- so the
clean number is the held-out **+1 (5 -> 6 of 10)**; (2) the rules only catch 4/10 held-out CRITICAL on their
own and miss all four danger-without-injury sentences (H2 armed men, H4 ground collapse, H9 brake failure,
H10 sandstorm on motorway). Extending `CRITICAL_SEVERITY_KEYWORDS` for those phrase families would be
tuning on the held-out set, so it needs a **fresh held-out set** to be validated -- not done.

## Model comparison on the held-out set (2026-09-28): allam-2-7b vs openai/gpt-oss-120b

Only the model id changed (`OPENROUTER_MODEL`); same code path (`Analyzer._analyze_with_llm`), same
prompt, same few-shot, same reduced schema, temperature 0, rule-first applied on top. allam column is the
earlier run of the same code (identical prompt; escalation is post-hoc so the model outputs are unchanged).
`openai/gpt-oss-120b` was the largest model on this Groq key (also listed: gpt-oss-20b, qwen/qwen3.8-27b;
llama-3.3-70b is not available). Its limit: 8,000 TPM / 1,000 RPD.

| id | kind | allam model | allam rule-first | gpt-oss-120b model | gpt-oss rule-first |
|---|---|---|---|---|---|
| H1 arabizi | injury+ambulance | LOW | **CRITICAL** | CRITICAL | CRITICAL |
| H2 arabizi | armed men | LOW | LOW | LOW | LOW |
| H3 arabic | injury+ambulance | CRITICAL | CRITICAL | CRITICAL | CRITICAL |
| H4 arabic | ground collapsing | LOW | LOW | CRITICAL | CRITICAL |
| H5 french | accident, injured | CRITICAL | CRITICAL | CRITICAL | CRITICAL |
| H6 french | cardiac / SAMU | CRITICAL | CRITICAL | CRITICAL | CRITICAL |
| H7 english | collapsed, ambulance | CRITICAL | CRITICAL | CRITICAL | CRITICAL |
| H8 english | rollover, trapped | CRITICAL | CRITICAL | CRITICAL | CRITICAL |
| H9 english | brakes failed | HIGH | HIGH | CRITICAL | CRITICAL |
| H10 french | sandstorm (borderline) | HIGH | HIGH | CRITICAL | CRITICAL |
| N1 / N2 / N3 | controls | LOW / HIGH / LOW | same | LOW / HIGH / HIGH | same |

| | allam-2-7b | gpt-oss-120b |
|---|---|---|
| CRITICAL recall, model-only | 5/10 | **9/10** |
| CRITICAL recall, rule-first | 6/10 | **9/10** (rules add nothing on top) |
| false CRITICAL on controls | 0/3 | 0/3 |
| category in acceptable set (13 sentences) | 10/13 (H2, H10, N1 wrong) | 13/13 |
| tokens per request (prompt + completion) | ~1,500 (1,430 + ~69) | ~1,580 (1,315 + ~265, completion includes reasoning tokens) |

**Reading:** the misses are a capability limit of the 7B model, not of the prompt or schema: same
prompt, same sentences, the 120B model gets 9/10 including 3 of the 4 danger-without-injury cases, and
its category is right on all 13 (allam: `N1 customer not home -> OTHER`). **Not isolated:** "larger" is
confounded with "reasoning model" (gpt-oss spends ~4x the completion tokens thinking) and with training
mix; a 27B (qwen/qwen3.8-27b) or gpt-oss-20b run would separate size from reasoning. n=10, one run each,
temperature 0; H2 (armed men) is missed by both models, so it stays a rule/keyword gap. Operational cost
of switching is not measured: gpt-oss-120b's latency was not recorded, and its 8,000 TPM at ~1,580
tokens/request is ~5 req/min at 100% (~3 at 60%), similar to allam's budget.

## Variant c vs full schema (2026-09-28): c is kept, on its own merits

Paired test, allam-2-7b, **same 50 eval rows** (every other row of `eval_set.csv`), same system prompt,
same clean few-shot, temperature 0, calls interleaved (c via the shipped `_analyze_with_llm`; "full" = the
old 7-field schema and prompt, with the same clean examples rewritten in the full shape). Few-shot are
non-eval, so this is a clean measurement of the eval set (unlike the pre-2026-09-28 prompt).

| | c (reduced) | full schema |
|---|---|---|
| Category accuracy, rows where both returned valid output (n=43) | **30/43 = 69.8%** | 20/43 = 46.5% |
| same, non-ambiguous rows only (n=40) | 27/40 = 67.5% | 17/40 = 42.5% |
| Category accuracy, all 50 rows, errors counted wrong (intention-to-treat) | **31/50 = 62%** | 25/50 = 50% |
| Discordant pairs (right in one, wrong in the other) | c only 11 | full only 1 (two-sided sign test p = 0.006) |
| Rows predicted OTHER (expected OTHER: 8 of 43) | 19 | 27 |
| Severity, model-only, 3-class (n=43) | 34/43 | 26/43 |
| Invalid/unusable output rows | **5/50 (10%)** | 2/50 (4%) |
| Tokens per request (measured mean) | **1,486** (1,423 + 63) | 2,351 (2,136 + 215) |

**Verdict: category accuracy does not drop -- it is higher, and the token saving is real (-37%).** The
reduction is not just neutral, so it stays. Category gain is concentrated where the full schema collapses
to OTHER (CUSTOMER_ABSENT 4/7 vs 0/7, ROAD_TRAFFIC 5/6 vs 3/6, CARGO 1/5 vs 0/5). Power caveat: 43 pairs
detect only large differences; 11-vs-1 is large, but the per-category counts are tiny. **Absolute quality is
still poor for this model**: 62-70% category accuracy and both variants over-predict OTHER (19 and 27 vs 8
expected) -- that is the 7B ceiling again (gpt-oss-120b was 13/13 on the held-out sentences), and the
reason `needsReview` will fire often on allam.

**c's real cost: 10% invalid output, reproducible, with a clear cause.** Re-running the 5 failing rows:
4 reproduced (row 1 succeeded the second time). On vague messages the model puts the word **"OTHER" in the
`severity` field** (ids 91, 93, 99: `{"severity":"OTHER","category":"OTHER"}`), and once invented the retired
label `ADDRESS_ISSUE` (id 83, Arabic wrong-address -- which by the taxonomy *should* be OTHER). Both fail
enum validation -> `INVALID_OUTPUT` -> keyword fallback in production (safe and available, but it throws
away a mostly-right answer: the expected category was OTHER for 4 of the 5 rows, and the model said OTHER in its category or reasoning for all four). The full schema had the same
failure class at a lower rate (2 `ValueError`s). **Not fixed -- proposal for your decision:** tolerate the
two known failure shapes instead of falling back: unknown/invalid `category` -> `OTHER` (+ needsReview,
which is what the taxonomy wants for a wrong address), and unusable `severity` (e.g. "OTHER") -> treat as
"no model signal" and take the rules' severity (`classify_severity`, default LOW). That is a change to
what the service accepts, so it is not done unilaterally.

Token arithmetic used for the rerun block is the measured c figure (~1,486, i.e. the ~1,500 already there).

## Native-review file (2026-09-28)

`data/native_review.csv`: **54 rows**, UTF-8 with BOM (opens correctly in Excel), one instruction line at the top.
Every arabizi and Arabic-script sentence in use: 47 from `eval_set.csv` (24 arabizi + 23 arabic-script), 5
from `heldout_critical.csv` (H1, H2 arabizi; H3, H4, N2 arabic-script), 2 few-shot examples in `analyzer.py`
(FS1 arabizi, FS5 arabic-script). Columns: source, id, language, text, expected_category, expected_severity,
note, and five blank columns for the reviewer (native_ok, correct_text, correct_category, correct_severity,
comment). `mixed`-language rows are not included (they were not classed as arabizi/arabic-script). Once
returned, fold the corrections back into the source CSVs / few-shot list -- and re-check the few-shot list
against both CSVs for overlap afterwards.

## gpt-oss-120b latency gate (2026-09-28): does p95 fit under the 7s Python timeout?

Server restarted on `openai/gpt-oss-120b` (env override), 15 eval rows (every 7th), end-to-end over HTTP, 20s
apart, new code. Groq limit for the model: 8,000 TPM / 1,000 RPD.

| | value |
|---|---|
| n / source | 15 / all 15 `LLM`, 0 fallbacks |
| latency mean / median | 1.65s / 1.13s |
| **latency p95 (n=15, = max)** | **7.20s** (the very first request after server start) |
| the other 14 (warm) | mean 1.26s, median 1.13s, p95/max 3.45s |
| tokens / request (measured earlier, 13 calls) | ~1,580 (1,315 prompt + ~265 completion incl. reasoning tokens) |
| resulting `--rps` | 8,000 TPM / 1,580 = 5.1 req/min at 100% -> **`--rps 0.06`** (3.6/min = ~5,700 TPM, 71% of cap) |

**Verdict under the rule you set (p95 must fit under 7s with margin): NO -- measured p95 is 7.20s, over the
timeout, so gpt-oss-120b stays a documented comparison and the final eval ran on allam.** Honest reading of
the number: the single slow request was the first one after a cold start, and the 14 warm requests all finished
in <=3.45s, so warm p95 would fit with ~2x margin -- but n=15 cannot certify a p95, and a cold request after an
idle period is a real production case, not a test artefact. Note also that the `openai` client's 7.0s is an
httpx *per-phase* timeout, not a total deadline: the 7.20s request still returned `LLM` rather than timing out.
A follow-up sample (25 requests, real 7s timeout, incl. a cold-after-idle probe) is recorded below.

## Invalid-output tolerance (2026-09-28, done)

Implemented in `app/analyzer.py` (`_coerce_category`, `_coerce_severity`) + `app/models.py`
(`LlmStructuredRecord` fields are now plain strings, so strict enum validation no longer throws the answer away):

- **Unknown / missing category** (e.g. the retired `ADDRESS_ISSUE`) -> `OTHER`, hence `needsReview=true`.
  Logs `WARNING Unknown model category '<x>' -> OTHER for shipment=<id>`.
- **Unusable severity** (e.g. `"OTHER"`, missing) -> *no model signal*: `classify_severity(text)` (the keyword
  rules, default LOW) decides. The reasoning gets `[Model gave no usable severity; keyword rules decided: X.]`.
  Logs `WARNING Unusable model severity '<x>' -> keyword rules decide, shipment=<id>`.
- Case/whitespace is accepted (` critical ` / `driver_issue` are valid), since that is the same answer.
- Still `INVALID_OUTPUT` -> keyword fallback: non-JSON, or missing/non-string `reasoning` / `structuredRecord`.
- Unchanged: rule-first escalation on a *valid* model severity.

Tests (22/22 pass): `severity:"OTHER"` (rules find nothing -> LOW + OTHER + needsReview; rules find an
ambulance -> CRITICAL, so an unusable severity cannot hide an emergency), an invented category name
(`ADDRESS_ISSUE` -> OTHER, valid severity kept), both invalid at once (2 log lines, one per field), casing
tolerated, missing reasoning still `INVALID_OUTPUT`.

**Known limitation, not changed:** `needsReview` is tied to category==OTHER only (as specified). Java's Rule B
does not auto-HALT a HIGH/CRITICAL when `needsReview=true`, so a rules-derived CRITICAL on an OTHER-category
message will not auto-halt (it is still CRITICAL and audit-logged). That predates this change (Step 3 "nuance")
but is now reachable more often; worth a follow-up ("needs urgent review" flag).

**Follow-up sample (2026-09-28, gpt-oss-120b, direct through `Analyzer._analyze_with_llm` with the real 7.0s
timeout, 25 requests 15s apart, incl. a request after a 120s idle gap):** 25/25 `LLM`, **0 timeouts, 0 errors**;
mean 1.42s, median 1.06s, **p95 4.65s**, max 5.14s (one request >5s, one at 4.65s); the cold-after-idle request
took 1.36s, so idleness is not what made the first request slow -- that was a cold *process* start (first call
on a fresh server). Pooling both samples (n=40): p95 ~5.1s, max 7.20s (the first-request outlier). **Updated
reading: marginal, not clearly failing** -- warm p95 sits ~2s under the 7s timeout (~27% margin), which is thin
for a per-phase timeout on a shared free tier, and the per-request tail (4.6-5.1s for ~2 of 25) is 4-5x the
median. The formal gate result above (NO) is unchanged and the eval was launched on allam accordingly; whether
that margin is acceptable, and whether to raise `OPENAI_TIMEOUT_SECONDS` if switching, is the user's call.
Switching would use `OPENROUTER_MODEL=openai/gpt-oss-120b` and `--rps 0.06`.

## Final eval: `eval_20260928T125439Z_final-allam.json` (2026-09-28) -- before/after

allam-2-7b, the shipped code (variant-c schema, clean non-eval few-shot, rule-first severity, invalid-output
tolerance), served by uvicorn restarted at ~13:20 with `OPENROUTER_MODEL=allam-2-7b`, `evaluate.py --rps 0.05`
(~1,490 tok/request = ~4,500 TPM, 74% of the 6,000 TPM cap). **100% LLM path, 0 keyword fallbacks, 0 errors, 0
rate-limit 429s** -- the first genuinely LLM-dominated run since the baseline, so these numbers are trustworthy
as LLM-path numbers. Few-shot is disjoint from `eval_set.csv`, so the category numbers are held-out. Baseline =
2026-09-24 predictions re-scored against the corrected labels (`ADDRESS_ISSUE` rows -> OTHER).

| metric | baseline | final |
|---|---|---|
| overall category accuracy (7 categories) | 35.0% | **64.0%** |
| **accuracy on the old 3 categories (n=39)** | **89.7%** | **56.4%  <- REGRESSION** |
| category macro-F1 | 0.221 | 0.627 |
| CRITICAL recall | 3/6 | 3/6 (unchanged) |
| false CRITICAL (of 94 non-CRITICAL) | 2 | 1 (id 37) |
| 3-class severity accuracy | 68.0% | 76.0% |
| accuracy: arabizi / arabic-script / french / english / mixed | 29 / 35 / 38 / 32 / 50% | 42 / 65 / 81 / 80 / 50% |
| valid JSON / LLM path / fallback | 100% / 95% / 5% | 100% / 100% / 0% |
| latency mean / p95 | 2.53s / 4.58s | 0.40s / 0.57s |

Per-category recall (baseline -> final): CARGO 0/13 -> 2/13, CUSTOMER_ABSENT 10/13 -> **6/13**, DRIVER 0/12 -> 8/12,
OTHER 0/24 -> 23/24, ROAD_TRAFFIC 0/12 -> 9/12, VEHICLE 12/13 -> 11/13, WEATHER 13/13 -> **5/13**.

**How to read it (this is the honest version, not the headline):**
1. **The 35% -> 64% jump is mostly mechanical**: the baseline could not output 4 of the 7 categories. It is not
   evidence the model got smarter. The like-for-like number is the old-3-category accuracy, which **fell from
   89.7% to 56.4%**: WEATHER 13/13 -> 5/13 and CUSTOMER_ABSENT 10/13 -> 6/13. The cause is visible in the
   totals: **the model now predicts OTHER on 52 rows against 24 expected** (baseline: 0), i.e. allam-2-7b uses
   OTHER as a catch-all under the new schema (also seen in the 50-row paired test: 19 predicted OTHER vs 8
   expected). Some of the loss is legitimate competition from the new categories (weather rows going to
   ROAD_TRAFFIC), but most is OTHER over-use. `needsReview` will therefore fire on roughly half of allam's
   output -- correct per the taxonomy, noisy operationally.
2. **CRITICAL recall did not improve (3/6)** and the 3 hits are the rows the keyword rules also catch (69, 72,
   73 -- the rows those keywords were partly tuned on). The three weather-danger rows (22 snow-storm stuck, 23/24
   flood) are all still HIGH: the held-out finding (allam misses danger-without-injury) reproduces on the eval set.
   Row 24 got *worse* than baseline (was CRITICAL). Rule-first added no measurable recall on this set.
3. **Wrong-address rows (named regression check): 5 of 6 fixed, 1 still harmful.** 78, 81, 85, 86, 87 -> OTHER (was
   CUSTOMER_ABSENT); **84 "wrong address given by customer wasted an hour searching" is still CUSTOMER_ABSENT** (and
   LOW vs expected HIGH) -- the exact failure the taxonomy fix was for. 73 -> DRIVER_ISSUE/CRITICAL (fixed; but row 73 is
   also one the rules catch). 52 "i couldnt find the products" -> OTHER (expected CARGO_ISSUE, still wrong;
   was CUSTOMER_ABSENT). 23 vs 24: severities now agree (both HIGH, both wrong), categories still differ
   (VEHICLE_ISSUE vs ROAD_TRAFFIC; expected WEATHER for both).
4. **Tolerance paid off**: 6 responses with an unusable severity (`"OTHER"`) and 2 with a bad category
   (`'ADDRESS_ISSUE'` on id 83, and the pipe-list `'ROAD_TRAFFIC|OTHER'` on id 19, copied from the prompt's option
   list) were handled and logged. Under strict validation those 8 rows (8%) would have gone to the keyword
   fallback; instead the run is 100% LLM. Rule-first fired: 5 LOW->HIGH escalations (generic "delay"/"blocked"
   words, e.g. ids 6, 25, 26) and 3 ->CRITICAL (incl. the one false CRITICAL, id 37 "accident ahead").
5. **Latency 0.40s / 0.57s is not comparable to the baseline's 2.53s / 4.58s and must not be reported as an
   improvement.** Plausible contributors (untested): completions are ~63 tokens now vs ~190-230 (fewer output
   tokens), Groq-side load on the day, paced 20s apart so no queuing. Same caveat as the 2026-09-25 run.

**Bottom line for the report:** the taxonomy and wrong-address handling work, the pipeline is now robust (0
fallbacks, 0 errors), and the contamination was found and removed; **but allam-2-7b's category quality on
the original three categories regressed and its severity on physical-danger cases is unfixed.** The one measured
lever that fixes the severity side is a larger model (gpt-oss-120b: 9/10 held-out CRITICAL, 13/13 category on the
held-out sentences), which passed 25/25 requests under the 7s timeout with warm p95 ~4.7s -- switching is the user's
decision (see the latency gate above). Not yet run: the full 100-row eval on gpt-oss-120b, which is the direct
answer to "does the regression go away with a bigger model" (`--rps 0.06`, ~30 min).

## Final eval, gpt-oss-120b: `eval_20260928T142227Z_final-gptoss120b.json` (2026-09-28)

Identical code and prompt as the allam final eval (variant-c schema, clean few-shot, rule-first severity, invalid-output
tolerance); the only change is the model (`OPENROUTER_MODEL=openai/gpt-oss-120b`, env override) and `--rps 0.06`
(~1,580 tok/request, ~5,700 TPM = 71% of the 8,000 TPM cap). **100/100 LLM path, 0 fallbacks, 0 errors, 0 timeouts.**

| metric | baseline | allam-2-7b final | gpt-oss-120b final |
|---|---|---|---|
| overall category accuracy (7 categories) | 35.0% | 64.0% | **99.0%** |
| **like-for-like accuracy, old 3 categories (n=39)** | 89.7% | 56.4% | **97.4%** |
| category macro-F1 | 0.221 | 0.627 | 0.989 |
| rows predicted OTHER (expected 24) | 0 | 52 | **24** |
| CRITICAL recall | 3/6 | 3/6 | 3/6 |
| false CRITICAL (of 94 non-CRITICAL) | 2 | 1 | **5** |
| 3-class severity accuracy | 68.0% | 76.0% | **64.0%** |
| accuracy arabizi / arabic-script / french / english / mixed | 29 / 35 / 38 / 32 / 50% | 42 / 65 / 81 / 80 / 50% | 96 / 100 / 100 / 100 / 100% |
| valid JSON / LLM-path rate / fallback rows | 100% / 95% / 5 | 100% / 100% / 0 | 100% / 100% / 0 |
| latency mean / p95 / max | 2.53 / 4.58 / 5.91s | 0.40 / 0.57 / 0.98s | 1.70 / 4.61 / **6.99s** |

Per-category recall (baseline -> allam -> gpt-oss-120b): CARGO 0/13 -> 2/13 -> 13/13; CUSTOMER_ABSENT 10/13 -> 6/13 ->
13/13; DRIVER 0/12 -> 8/12 -> 12/12; OTHER 0/24 -> 23/24 -> 24/24; ROAD_TRAFFIC 0/12 -> 9/12 -> 12/12; VEHICLE 12/13 ->
11/13 -> 13/13; WEATHER 13/13 -> 5/13 -> 12/13.

**Category: the regression is model-dependent.** With the same prompt, the 120B model recovers everything the 7B lost and
beats the baseline on the 3 old categories (97.4% vs 89.7%), with OTHER predicted exactly as often as expected. All the
named wrong-address rows are right: 78, 81, 84, 85, 86, 87 -> OTHER (**84, the one allam still sent to CUSTOMER_ABSENT, is
now OTHER/HIGH**); 52 -> CARGO_ISSUE (right category, severity HIGH vs LOW). Caveats: single run, n=100, temperature 0;
the dataset labels are AI-drafted and unreviewed by a native speaker, so 99% may partly reflect an easy/clean dataset
rather than robustness; and the few-shot is disjoint from the eval set, so this is not memorisation.

**Severity is NOT fixed by the bigger model on this eval set -- and is worse on false alarms.**
- CRITICAL recall is still 3/6 (69, 72, 73 right; **22, 23, 24 -- snow-storm stuck, flood x2 -- all HIGH**). This contrasts with
  9/10 on the held-out sentences, so held-out recall does not carry over to weather-danger rows. Note those three labels
  are AI-drafted and arguably debatable (is "snow storm truck stuck" CRITICAL?), so the disagreement may be partly a
  label question -- unreviewed.
- **5 false CRITICALs** (vs 1 for allam): ids 66, 68 ("i feel very sick cannot continue" family), 70, 71 ("hurt my hand a bit,
  cannot drive" family) -- all model-originated (the dataset labels all four HIGH, DRIVER_ISSUE) -- plus id 37 (rule-originated). 70/71 ("slightly hurt my hand, cannot drive") are a borderline label-vs-policy call rather than a clear model error: the prompt's own rule says any injury needing medical attention is CRITICAL, and row 72 ("minor fall needs medical help") is labelled CRITICAL. Severity
  accuracy is 64% (allam 76%). Under the "over-flagging is cheap" stance these are defensible, but 5/94 = 5.3% is a
  measurable alert-fatigue cost, and it cuts against "the bigger model fixes severity".
- **Rule-first on this model bought nothing here:** 4 escalations in the run (`EVAL-25/26/38` LOW->HIGH, `EVAL-37`
  HIGH->CRITICAL): +0 recall, +1 false CRITICAL (37, bare "accident"), +3 LOW->HIGH over-flags. It remains the safety net
  for the weaker model (allam: +1 held-out CRITICAL), but on gpt-oss-120b its measured value on this set is negative.
  0 unusable-severity / unknown-category events (the tolerance code did not fire; it was for allam).

**Latency (100 rows, real 7.0s timeout): mean 1.70s, p95 4.61s, max 6.99s.** No fallbacks and no timeouts, but 5 rows took
5.8-7.0s (ids 32, 33, 34 consecutively -- a slow window -- then 60 at 6.68s and 76 at 6.99s). The `openai` client's 7.0s is
an httpx per-phase timeout, not a total deadline, so row 76 (0.01s under) succeeded; a stricter total deadline would
have sent it to the keyword fallback. Combined with the earlier samples (15-row gate: p95 7.20s from a cold start;
follow-up 25 requests: warm p95 4.65s, max 5.14s) the numbers to report are:

| sample | n | p95 | max | timeouts |
|---|---|---|---|---|
| 15-row gate (first request cold) | 15 | **7.20s** (formal gate: FAIL) | 7.20s | 0 |
| follow-up, warm, real timeout | 25 | 4.65s | 5.14s | 0 (25/25 OK) |
| full eval | 100 | 4.61s | **6.99s** | 0 |

Real p95 margin ~2.4s; **tail margin ~0.0s** (max 6.99s). If switching, raise `OPENAI_TIMEOUT_SECONDS` (e.g. 12s) rather
than rely on the tail; that is a config change for the user to decide, not made here.

**What the two runs together say (for the report):** (1) the taxonomy, wrong-address handling, contamination fix and robust
parsing are sound -- proven by the 120B run's 99% on the same code; (2) allam-2-7b is the limiting factor for category
quality *and* for danger-severity; (3) the larger model fixes category almost completely but **does not fix eval-set CRITICAL
recall (3/6) and adds false CRITICALs**, so severity needs its own work (label review of 22/23/24, the sick/hurt-hand rows,
and -- with a fresh held-out set -- keyword coverage), which was explicitly deferred. Recommendation, for the user to decide:
switch the LLM path to gpt-oss-120b for category quality, with a larger timeout, keeping rule-first as the safety floor.
Not done / open: native review of `data/native_review.csv`; prompt ablation on allam (untested hypothesis about the OTHER
wording); fresh held-out set; `.env` `DEMO_FAIL_HOOK=true` (user is fixing).
