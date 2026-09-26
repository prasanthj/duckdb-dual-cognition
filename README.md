# DuckDB Dual Cognition

[![Native build and tests](https://github.com/prasanthj/duckdb-dual-cognition/actions/workflows/release.yml/badge.svg)](https://github.com/prasanthj/duckdb-dual-cognition/actions/workflows/release.yml)
[![Native CI](https://github.com/prasanthj/duckdb-dual-cognition/actions/workflows/ci.yml/badge.svg)](https://github.com/prasanthj/duckdb-dual-cognition/actions/workflows/ci.yml)
[![Latest release](https://img.shields.io/github/v/release/prasanthj/duckdb-dual-cognition?display_name=tag&sort=semver)](https://github.com/prasanthj/duckdb-dual-cognition/releases/latest)
[![DuckDB 1.4.5 and 1.5.5](https://img.shields.io/badge/DuckDB-1.4.5%20%7C%201.5.5-fff000?logo=duckdb&logoColor=black)](https://duckdb.org/docs/stable/extensions/extension_distribution)
[![Targets: macOS and Linux, x86-64 and ARM64](https://img.shields.io/badge/targets-macOS%20%7C%20Linux%20%C2%B7%20x86--64%20%7C%20ARM64-blue)](docs/distribution.md)
[![License: Apache 2.0](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](LICENSE)

Compose fast, bounded System One judgments with selective System Two reasoning in one native DuckDB SQL pipeline. Classify every row cheaply, escalate only ambiguity, and keep the final value plus its decision provenance in the relation.

![Animated terminal walkthrough: confidence-gated classification, selective System Two escalation, and decision provenance](docs/images/terminal-demo.gif)

*Captured from a live run against TypeSafe Jev 1.13.0 and OpenAI gpt-5.6-luna. Eight support cases were classified in one System One batch, and rows below the 0.50 confidence threshold were resolved by System Two with complete provenance. Repeating the identical SQL produced 8/8 System One cache hits, cache hits for every escalated System Two row, and zero new provider requests. Requests run concurrently within each stage when a workload produces multiple batches; the two stages are sequential because the confidence gate determines which rows reach System Two. Reproduce it with `TYPESAFE_API_KEY=... OPENAI_API_KEY=... vhs examples/live_terminal_demo.tape` (provider charges apply).*

## Why dual cognition?

Many enrichment workloads contain two different kinds of work:

| Path | Best at | Cost profile | SQL functions |
|---|---|---|---|
| **System One** | finite choices, scores, predicates, routing | fast and highly batchable | `system_one_choice`, `system_one_score`, `system_one_noul`, `system_one_stream` |
| **System Two** | generation, summarization, extraction, resolving ambiguity | deeper and more expensive | `system_two_generate`, `system_two_summarize`, `system_two_extract` |

The useful pattern is composition. Run System One across the full relation, route only low-confidence rows to System Two, and merge both paths into a single derived column. DuckDB retains the source columns, result, confidence, fallback decision, and provenance without exporting data through pandas or a temporary JSON workflow.

For standalone System One throughput, scaling measurements, and the streaming implementation, see [Jev for DuckDB](https://github.com/prasanthj/duckdb-jev). This repository focuses on composing both systems safely in the same query.

## Features

- **Two systems, one relation:** bounded judgment and generative transformation compose through ordinary SQL and materialized CTEs.
- **Selective escalation:** confidence gates send only ambiguous rows to System Two while preserving one final output column.
- **Auditable provenance:** retain the System One candidate, confidence, threshold, escalation status, System Two answer, and final source as a DuckDB `STRUCT`.
- **Native vector execution:** evaluates DuckDB vectors directly, deduplicates equal work, batches independent rows, and dispatches bounded concurrent HTTP requests.
- **Nested evidence:** accepts text, JSON, `STRUCT`, `LIST`, and `ARRAY` inputs, so complete row context can stay structured.
- **Safe reuse:** query-local coalescing is enabled by default; optional per-connection LRU caches add TTL-based reuse across statements.
- **Production controls:** independent concurrency, byte limits, query budgets, timeouts, retries, cancellation, and strict response validation for each system.
- **Observable:** `dc_stats()` separates requests, items, cache hits, retries, errors, tokens, bytes, and latency by system.
- **Native releases:** build and test matrices cover DuckDB 1.4.5 and 1.5.5 on macOS and Linux, x86-64 and ARM64.

## Quick start

### 1. Build and load

Requirements: a C++17 toolchain, libcurl, Git, and [uv](https://docs.astral.sh/uv/).

```bash
git clone https://github.com/prasanthj/duckdb-dual-cognition.git
cd duckdb-dual-cognition
./build.sh
```

The native artifact is written to `build/extension/dc/dc.duckdb_extension`.

```sql
LOAD '/absolute/path/to/build/extension/dc/dc.duckdb_extension';
```

Until `dc` is distributed as a signed DuckDB Community Extension, clients must enable unsigned extensions when opening the connection. Native binaries must match the DuckDB version, operating system, and architecture. See [distribution](docs/distribution.md).

### 2. Configure both providers

One scoped DuckDB secret holds the provider, model, and credential for each system:

```sql
CREATE SECRET cognition (
  TYPE dc,
  SYSTEM_ONE_PROVIDER 'typesafe',
  SYSTEM_ONE_API_KEY 'ts-...',
  SYSTEM_ONE_MODEL 'jev-1.13.0',
  SYSTEM_TWO_PROVIDER 'openai',
  SYSTEM_TWO_API_KEY 'sk-...',
  SYSTEM_TWO_MODEL 'gpt-5.6-luna'
);
```

The default endpoints are TypeSafe System One and OpenAI Responses. Override them only when using a compatible service:

```sql
CREATE SECRET cognition (
  TYPE dc,
  SYSTEM_ONE_PROVIDER 'typesafe',
  SYSTEM_ONE_API_KEY '...',
  SYSTEM_ONE_ENDPOINT 'https://api.typesafe.ai/v1/systemone',
  SYSTEM_ONE_MODEL 'jev-1.13.0',
  SYSTEM_TWO_PROVIDER 'openai',
  SYSTEM_TWO_API_KEY '...',
  SYSTEM_TWO_ENDPOINT 'https://api.openai.com/v1/responses',
  SYSTEM_TWO_MODEL 'gpt-5.6-luna'
);
```

Both API keys are required by the combined secret and are redacted by `duckdb_secrets()`. Plain HTTP is rejected except for loopback test endpoints. `SET enable_external_access = false` prevents provider calls.

### 3. Classify, escalate, and preserve provenance

```sql
CREATE TABLE cases(id INTEGER, evidence VARCHAR);
INSERT INTO cases VALUES
  (0, 'Duplicate invoice charge'),
  (1, 'Account access fails intermittently after login');

WITH params(confidence_threshold) AS (VALUES (0.80::DOUBLE)),
fast AS MATERIALIZED (
  SELECT id, evidence,
         system_one_choice(
           evidence,
           'Classify the case',
           '{"billing":"payments", "technical":"product failures", "security":"access and data risk"}'::JSON
         ) AS result
  FROM cases
), escalated AS MATERIALIZED (
  SELECT id,
         evidence,
         result.choice AS system_one_choice,
         result.confidence AS system_one_confidence,
         confidence_threshold,
         CASE WHEN result.confidence < confidence_threshold THEN
           (system_two_generate(
             {
               'evidence': evidence,
               'system_one_candidate': result.choice,
               'allowed_labels': ['billing', 'technical', 'security']
             },
             'Resolve the ambiguity. Return exactly one allowed label.'
           )).value
         END AS system_two_choice
  FROM fast CROSS JOIN params
)
SELECT id,
       evidence AS input,
       CASE
         WHEN system_one_confidence >= confidence_threshold THEN system_one_choice
         WHEN system_two_choice IN ('billing', 'technical', 'security') THEN system_two_choice
         ELSE 'manual_review'
       END AS classification,
       struct_pack(
         source := CASE
           WHEN system_one_confidence >= confidence_threshold THEN 'system_one'
           WHEN system_two_choice IN ('billing', 'technical', 'security') THEN 'system_two'
           ELSE 'manual_review'
         END,
         system_one_choice := system_one_choice,
         system_one_confidence := system_one_confidence,
         confidence_threshold := confidence_threshold,
         escalated := system_two_choice IS NOT NULL,
         system_two_choice := system_two_choice
       ) AS provenance
FROM escalated
ORDER BY id;
```

The final `classification` has one stable type regardless of the path. The adjacent provenance struct explains how it was produced:

| input | classification | source | System One | confidence | threshold | System Two |
|---|---|---|---|---:|---:|---|
| Duplicate invoice charge | `billing` | `system_one` | `billing` | 0.95 | 0.80 | `NULL` |
| Account access fails intermittently after login | `technical` | `system_two` | `security` | 0.55 | 0.80 | `technical` |

The `IN (...)` guard prevents unconstrained System Two text from silently becoming a label. An invalid fallback becomes `manual_review`.

## SQL API

### System One: bounded judgments

#### Noul: probability of a proposition

`system_one_noul(evidence, instructions [, criteria])`

```sql
SELECT ticket_id,
       (system_one_noul(
          {'message': message, 'priority': priority, 'telemetry': telemetry},
          'Does this ticket require immediate human attention?'
       )).noul AS urgent_probability
FROM support_tickets;
```

Result: `STRUCT(noul DOUBLE, model VARCHAR, cache_hit BOOLEAN)`.

#### Choice: classification over a finite taxonomy

`system_one_choice(evidence, instructions, criteria)`

Criteria can be a JSON object with label descriptions or a JSON array of labels.

```sql
SELECT account_id,
       result.choice AS renewal_risk,
       result.confidence,
       result.probabilities
FROM (
  SELECT account_id,
         system_one_choice(
           {
             'usage_30d': usage_30d,
             'open_escalations': open_escalations,
             'latest_customer_message': latest_customer_message
           },
           'Classify renewal risk using only the supplied evidence.',
           '{
             "green":"healthy engagement and no material renewal threat",
             "yellow":"mixed evidence or emerging risk",
             "red":"clear and immediate renewal risk"
           }'::JSON
         ) AS result
  FROM accounts
);
```

Result: `STRUCT(choice VARCHAR, confidence DOUBLE, probabilities JSON, model VARCHAR, cache_hit BOOLEAN)`.

#### Score: position on an ordered rubric

`system_one_score(evidence, instructions, levels)`

```sql
SELECT message_id,
       result.score AS sentiment_score,
       result.confidence,
       result.legend
FROM (
  SELECT message_id,
         system_one_score(
           message,
           'Score customer sentiment.',
           '["very negative", "negative", "neutral", "positive", "very positive"]'::JSON
         ) AS result
  FROM customer_messages
);
```

Result: `STRUCT(score DOUBLE, confidence DOUBLE, probabilities JSON, legend JSON, model VARCHAR, cache_hit BOOLEAN)`.

#### Stream: cross-chunk System One batching

`system_one_stream(TABLE(...))` is the high-throughput table-in/table-out path. Its subquery must return exactly three columns in this order: correlation ID, evidence, and a question object.

```sql
WITH judged AS (
  SELECT row_id, answers, model, cache_hit
  FROM system_one_stream((
    SELECT
      ticket_id,
      {'message': message, 'account': account, 'telemetry': telemetry},
      '{
        "route": {
          "type":"choice",
          "instructions":"Choose the owning team.",
          "criteria":{"billing":"payments", "technical":"product failures"}
        },
        "urgency": {
          "type":"noul",
          "instructions":"Does this require immediate human attention?"
        }
      }'::JSON
    FROM support_tickets
  ))
)
SELECT row_id,
       answers->'route'->>'choice' AS route,
       (answers->'route'->>'confidence')::DOUBLE AS confidence,
       (answers->'urgency'->>'noul')::DOUBLE AS urgency
FROM judged;
```

Output columns are `row_id`, `answers JSON`, `model`, and `cache_hit`. Supplied IDs, including duplicate and `NULL` IDs, are preserved. Use `ORDER BY` when output order matters. See [Jev for DuckDB](https://github.com/prasanthj/duckdb-jev) for detailed System One performance and streaming measurements.

### System Two: generative transformations

#### Generate

`system_two_generate(evidence, instruction)`

```sql
SELECT ticket_id,
       (system_two_generate(
          {'route': route, 'message': message, 'customer_tier': customer_tier},
          'Draft a concise next action for the assigned team.'
       )).value AS next_action
FROM routed_tickets;
```

#### Summarize

`system_two_summarize(evidence, instruction)`

```sql
SELECT account_id,
       (system_two_summarize(
          {'support': support_history, 'usage': usage_history, 'notes': csm_notes},
          'Summarize the three strongest renewal signals in at most 80 words.'
       )).value AS renewal_summary
FROM account_evidence;
```

#### Extract

`system_two_extract(evidence, instruction, schema)` returns JSON. The schema can be an array of required field names or an object containing a `required` array.

```sql
SELECT document_id,
       result.value->>'order_id' AS order_id,
       result.value->>'destination' AS destination,
       result.value->>'ship_date' AS ship_date
FROM (
  SELECT document_id,
         system_two_extract(
           document_text,
           'Extract the order facts. Use null when a fact is absent.',
           '["order_id", "destination", "ship_date"]'::JSON
         ) AS result
  FROM shipping_documents
);
```

`system_two_generate` and `system_two_summarize` return `STRUCT(value VARCHAR, model VARCHAR, cache_hit BOOLEAN)`. `system_two_extract` returns `STRUCT(value JSON, model VARCHAR, cache_hit BOOLEAN)`.

Every row in a System Two batch is isolated in the provider instruction, and strict structured output requires exactly one result per input ID.

## Composition patterns

Use `MATERIALIZED` CTEs at inference stage boundaries. This makes evaluation order explicit and prevents a referenced result from being recomputed by query-plan rewrites.

### System One → System Two

```sql
WITH routed AS MATERIALIZED (
  SELECT ticket_id, message,
         (system_one_choice(
            message,
            'Choose the owning team.',
            '{"billing":"payments", "technical":"product failures"}'::JSON
         )).choice AS route
  FROM tickets
)
SELECT ticket_id,
       route,
       (system_two_generate(
          {'route': route, 'message': message},
          'Draft a concise investigation plan for the assigned team.'
       )).value AS investigation_plan
FROM routed;
```

### System Two → System One

```sql
WITH summarized AS MATERIALIZED (
  SELECT case_id,
         (system_two_summarize(
            conversation,
            'Summarize the customer problem and requested outcome.'
         )).value AS summary
  FROM cases
)
SELECT case_id,
       summary,
       (system_one_choice(
          summary,
          'Choose the owning team.',
          '{"billing":"payments", "technical":"product failures"}'::JSON
       )).choice AS route
FROM summarized;
```

### Multi-stage quality gate

```sql
WITH classified AS MATERIALIZED (
  SELECT ticket_id, message,
         (system_one_choice(
            message,
            'Choose the owning team.',
            '{"billing":"payments", "technical":"product failures"}'::JSON
         )).choice AS route
  FROM tickets
), drafted AS MATERIALIZED (
  SELECT ticket_id,
         route,
         (system_two_generate(
            {'route': route, 'message': message},
            'Draft a customer-safe next step. Do not promise credits or deadlines.'
         )).value AS draft
  FROM classified
)
SELECT ticket_id,
       route,
       draft,
       (system_one_noul(
          draft,
          'Is this response safe to send under the stated constraints?'
       )).noul AS safe_to_send_probability
FROM drafted;
```

### Validate fallback output

System Two generation is open text. When it resolves a bounded decision, validate its value before accepting it:

```sql
CASE
  WHEN system_two_choice IN ('billing', 'technical', 'security')
    THEN system_two_choice
  ELSE 'manual_review'
END
```

For a richer fixed shape, use `system_two_extract` and validate its JSON fields in SQL.

## Structured evidence and nulls

Evidence and instructions accept DuckDB text or nested values. Keep related columns structured instead of serializing a prompt by hand:

```sql
SELECT account_id,
       system_one_choice(
         {
           'account': {'tier': tier, 'arr': arr},
           'telemetry': {'active_users': active_users, 'usage_delta': usage_delta},
           'support': {'open_cases': open_cases, 'latest_message': latest_message}
         },
         'Classify renewal risk.',
         '{"green":"healthy", "yellow":"watch", "red":"at risk"}'::JSON
       ) AS risk
FROM account_snapshot;
```

A SQL `NULL` in a required function argument produces a SQL `NULL` result and makes no provider request. Nested null fields are preserved as JSON nulls. Empty relations and `EXPLAIN` make no calls; `EXPLAIN ANALYZE` executes the query.

## Batching and concurrency

Both systems operate directly on DuckDB vectors. Equal work is deduplicated within the query, independent rows are packed into bounded requests, and requests run on a shared native worker pool.

```sql
SET dc_system_one_batch_size = 100;
SET dc_system_one_concurrency = 10;

SET dc_system_two_batch_size = 25;
SET dc_system_two_concurrency = 5;
```

System One allows batches of 1–1,000 questions and concurrency of 1–10. System Two allows batches of 1–500 rows and concurrency of 1–10. Larger is not always faster: input size, provider limits, model latency, and the number of independent requests all matter. Measure the complete query for your workload.

For System One relations larger than a DuckDB vector, use `system_one_stream` to retain partial packs across input chunks. System Two scalar functions batch each DuckDB vector and preserve row order.

## Caching and repeated enrichment

Each system has an 8 MiB query-local cache enabled by default. It deduplicates identical calls across rows and expressions during one statement. Only successful validated results are cached; failures are not.

Cross-statement caching is opt-in and scoped to one DuckDB connection:

```sql
SET dc_system_one_session_cache_bytes = 8388608;
SET dc_system_one_session_cache_ttl_ms = 60000;
SET dc_system_two_session_cache_bytes = 8388608;
SET dc_system_two_session_cache_ttl_ms = 60000;

SELECT dc_cache_clear();
```

Cache identity includes the system, operation, endpoint, model, credential scope, instructions, criteria or schema, and evidence. Closing the connection drops the session cache. The cache is neither shared across processes nor distributed across pods.

For durable reuse, persist validated outputs to Parquet with an input fingerprint, enrichment-spec fingerprint, model, provenance, and expiration metadata. Join the current input against that artifact and send only misses to inference.

## Observability

```sql
SELECT * FROM dc_stats() ORDER BY system;
```

`dc_stats()` returns one process-wide, monotonic row per system:

| Column | Meaning |
|---|---|
| `system` | `system_one` or `system_two` |
| `requests` | logical request batches |
| `items` | System One questions or System Two rows |
| `cache_hits` | results reused instead of dispatched |
| `retries` | HTTP attempts after the first attempt |
| `errors` | failed logical requests |
| `input_tokens`, `output_tokens` | provider-reported usage |
| `request_bytes`, `response_bytes` | transport bytes, including retries |
| `total_latency_ms`, `max_latency_ms` | accumulated and maximum logical-request latency |

Measure the work added by one statement with before/after snapshots because counters are not reset by `dc_cache_clear()`.

## Configuration reference

### System One

| Setting | Default | Valid range | Purpose |
|---|---:|---:|---|
| `dc_system_one_batch_size` | 25 | 1–1,000 | questions per request |
| `dc_system_one_concurrency` | 10 | 1–10 | concurrent requests |
| `dc_system_one_max_request_bytes` | 65,536 | 256–1,048,576 | serialized request cap |
| `dc_system_one_timeout_ms` | 30,000 | 1–300,000 | timeout per attempt |
| `dc_system_one_max_retries` | 2 | 0–5 | transient retries |
| `dc_system_one_retry_base_ms` | 100 | 1–10,000 | initial exponential delay |
| `dc_system_one_retry_max_delay_ms` | 5,000 | base–60,000 | maximum delay, including `Retry-After` |
| `dc_system_one_cache_bytes` | 8 MiB | 0–64 MiB | query-local cache budget |
| `dc_system_one_session_cache_bytes` | 0 | 0–64 MiB | connection LRU budget |
| `dc_system_one_session_cache_ttl_ms` | 60,000 | 1–86,400,000 | non-sliding connection-cache TTL |
| `dc_system_one_max_questions_per_query` | 100,000 | 1–100,000,000 | billable question budget |
| `dc_system_one_max_requests_per_query` | 2,000 | 1–10,000,000 | logical request budget |

### System Two

| Setting | Default | Valid range | Purpose |
|---|---:|---:|---|
| `dc_system_two_batch_size` | 25 | 1–500 | rows per Responses request |
| `dc_system_two_concurrency` | 5 | 1–10 | concurrent requests |
| `dc_system_two_max_request_bytes` | 1 MiB | 1 KiB–4 MiB | serialized request cap |
| `dc_system_two_timeout_ms` | 90,000 | 1–300,000 | timeout per attempt |
| `dc_system_two_max_retries` | 2 | 0–5 | transient retries |
| `dc_system_two_retry_base_ms` | 250 | 1–10,000 | initial exponential delay |
| `dc_system_two_retry_max_delay_ms` | 10,000 | base–60,000 | maximum delay, including `Retry-After` |
| `dc_system_two_cache_bytes` | 8 MiB | 0–64 MiB | query-local cache budget |
| `dc_system_two_session_cache_bytes` | 0 | 0–64 MiB | connection LRU budget |
| `dc_system_two_session_cache_ttl_ms` | 60,000 | 1–86,400,000 | non-sliding connection-cache TTL |
| `dc_system_two_max_items_per_query` | 100,000 | 1–100,000,000 | uncached row budget |
| `dc_system_two_max_requests_per_query` | 2,000 | 1–10,000,000 | logical request budget |

Bound provider work before running an enrichment:

```sql
SET dc_system_one_max_questions_per_query = 10000;
SET dc_system_one_max_requests_per_query = 100;
SET dc_system_two_max_items_per_query = 500;
SET dc_system_two_max_requests_per_query = 20;
```

## Failure and safety semantics

- Provider errors fail the query; they never become a false predicate, fabricated label, empty summary, or low-confidence result.
- HTTP 429, HTTP 5xx, and transient transport failures retry with exponential backoff, bounded jitter, and `Retry-After` support.
- Other HTTP 4xx responses and malformed provider output fail immediately.
- Cancellation stops queued work and interrupts active transfers. A request already accepted remotely may still be billable.
- A retry can duplicate remotely accepted work when the response was lost.
- Request-byte caps, retained-data caps, response validation, and query budgets are checked before or during dispatch.
- Evidence is sent as untrusted data, separate from provider instructions.
- Secrets are not returned by SQL functions or stored in cache keys.

## Build, test, and release

Run the deterministic local suite:

```bash
./build.sh
uv run pytest -q
uv run ruff check tests scripts examples
uv run pyright tests scripts examples
```

The suite uses local HTTP protocol stubs and does not spend provider credits. It covers both systems, nested values, vector batching, cross-chunk streaming, concurrency, retries, budgets, caching, cancellation, malformed responses, multi-stage composition, and confidence-based escalation.

Live endpoint tests are explicitly opt-in:

```bash
DC_RUN_LIVE=1 \
TYPESAFE_API_KEY=... \
OPENAI_API_KEY=... \
uv run pytest -q tests/test_live.py
```

Release automation builds and loads the native extension against DuckDB 1.4.5 and 1.5.5 on Linux and macOS, x86-64 and ARM64. Archives include the extension, manifest, licenses, SPDX SBOM, SHA-256 checksum, and GitHub build provenance. See [distribution](docs/distribution.md) and [release notes](docs/release-notes.md).

## Current scope

- Native DuckDB only; DuckDB-Wasm is not supported.
- System One currently targets TypeSafe-compatible System One responses.
- System Two currently targets the OpenAI Responses API.
- The connection cache is local memory, not a distributed cache.
- System Two generation and summarization return text. Validate bounded fallback values in SQL or use extraction with a fixed JSON shape.
- This is distributed through GitHub Releases and is not yet a signed DuckDB Community Extension.

## Related project

[Jev for DuckDB](https://github.com/prasanthj/duckdb-jev) provides the dedicated high-throughput System One extension, including live TypeSafe performance measurements, streaming benchmarks, and detailed Jev execution semantics. Dual Cognition builds on the same native batching and safety approach, then adds System Two operations and SQL composition across both paths.

## License

Apache License 2.0. See [LICENSE](LICENSE) and [NOTICE](NOTICE).
