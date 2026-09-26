# DuckDB Dual Cognition

A native DuckDB extension that combines fast bounded judgments from a System One provider with generative System Two transformations. It installs as `dc`, operates directly on DuckDB vectors, and lets both systems compose in ordinary SQL.

## Features

- **Two engines, one SQL pipeline:** classify, score, generate, summarize, and extract without exporting columns to a separate process.
- **Vectorized throughput:** DuckDB chunks are deduplicated, packed into bounded requests, and dispatched through a shared native worker pool.
- **SQL composition:** chain System One into System Two, System Two into System One, or build multi-stage CTE pipelines.
- **Bounded concurrency and backpressure:** independent limits for each provider, request-byte caps, query budgets, cancellation, and transient-error retries.
- **Safe reuse:** query-local coalescing is on by default; an opt-in connection LRU adds TTL-based reuse across statements.
- **Observable:** `dc_stats()` reports provider-specific requests, items, cache hits, retries, failures, tokens, bytes, and transport latency.
- **Native distribution:** build and test matrices cover DuckDB 1.4.5 and 1.5.5 on macOS and Linux, x86-64 and arm64.

## Build

Requirements: C++17 toolchain, libcurl, Git, and [uv](https://docs.astral.sh/uv/).

```bash
git clone https://github.com/prasanthj/duckdb-dual-cognition.git
cd duckdb-dual-cognition
./build.sh
```

The artifact is `build/extension/dc/dc.duckdb_extension`.

```sql
LOAD '/absolute/path/to/build/extension/dc/dc.duckdb_extension';
```

Native DuckDB extensions are version and platform specific. Release archives are built separately for every supported combination; see [distribution](docs/distribution.md).

## Configure both systems

A single DuckDB secret configures both providers. Keys are redacted by `duckdb_secrets()`.

```sql
CREATE SECRET cognition (
  TYPE dc,
  SYSTEM_ONE_PROVIDER 'typesafe',
  SYSTEM_ONE_API_KEY '...',
  SYSTEM_ONE_MODEL 'jev-1.13.0',
  SYSTEM_TWO_PROVIDER 'openai',
  SYSTEM_TWO_API_KEY '...',
  SYSTEM_TWO_MODEL 'gpt-5.6-luna'
);
```

`SYSTEM_ONE_ENDPOINT` and `SYSTEM_TWO_ENDPOINT` are optional. The defaults are the TypeSafe System One and OpenAI Responses endpoints. HTTP is rejected except for loopback test servers.

## SQL API

| Function | Result |
|---|---|
| `system_one_noul(evidence, instructions [, criteria])` | `STRUCT(noul, model, cache_hit)` |
| `system_one_choice(evidence, instructions, criteria)` | `STRUCT(choice, confidence, probabilities, model, cache_hit)` |
| `system_one_score(evidence, instructions, levels)` | `STRUCT(score, confidence, probabilities, legend, model, cache_hit)` |
| `system_two_generate(evidence, instruction)` | `STRUCT(value, model, cache_hit)` |
| `system_two_summarize(evidence, instruction)` | `STRUCT(value, model, cache_hit)` |
| `system_two_extract(evidence, instruction, schema)` | `STRUCT(value JSON, model, cache_hit)` |
| `dc_stats()` | One metrics row per system |
| `dc_cache_clear()` | Clears both connection caches |

Evidence and instructions accept text or nested DuckDB values. Choice criteria can be a JSON object or array. Extraction schema can be a JSON array of required field names or an object whose `required` array names the required fields.

```sql
SELECT id,
       (system_one_choice(
          to_json(ticket),
          'Choose the owning team',
          '{"billing":"charges and refunds","technical":"product failures"}'::JSON
       )).choice AS route
FROM tickets;
```

```sql
SELECT id,
       (system_two_extract(
          to_json(document),
          'Extract the order facts',
          '["order_id","customer","ship_date"]'::JSON
       )).value AS facts
FROM documents;
```

## Compose both systems

System One can cheaply create a bounded decision that becomes context for System Two:

```sql
WITH routed AS MATERIALIZED (
  SELECT id, ticket,
         (system_one_choice(ticket, 'Route this ticket',
           '{"billing":"payments","technical":"product failures"}'::JSON)).choice AS route
  FROM tickets
)
SELECT id, route,
       (system_two_generate(
          {'route': route, 'ticket': ticket},
          'Draft the next action for the assigned team'
       )).value AS next_action
FROM routed;
```

The reverse direction is equally valid: summarize free text first, then make a bounded decision.

```sql
WITH summarized AS MATERIALIZED (
  SELECT id,
         (system_two_summarize(notes, 'Summarize the customer issue')).value AS summary
  FROM cases
)
SELECT id, summary,
       (system_one_choice(summary, 'Route this issue',
         '{"billing":"payments","technical":"product failures"}'::JSON)).choice AS route
FROM summarized;
```

Use `MATERIALIZED` CTEs when the stage boundary matters or a result is referenced more than once.

For selective escalation, keep the fast and reasoned paths in separate materialized stages, then merge them into one derived column. Only rows below the confidence threshold invoke System Two:

```sql
WITH params(confidence_threshold) AS (VALUES (0.80::DOUBLE)),
fast AS MATERIALIZED (
  SELECT id, evidence,
         system_one_choice(evidence, 'Classify the case',
           '{"billing":"payments","technical":"product failures",'
           '"security":"access and data risk"}'::JSON) AS result
  FROM cases
), escalated AS MATERIALIZED (
  SELECT id, evidence, result.choice AS fast_choice, result.confidence, confidence_threshold,
         CASE WHEN result.confidence < confidence_threshold THEN
           (system_two_generate(
             {'evidence':evidence,'system_one_candidate':result.choice},
             'Return exactly one label: billing, technical, or security'
           )).value
         END AS reasoned_choice
  FROM fast CROSS JOIN params
)
SELECT id,
       evidence AS input,
       CASE
         WHEN confidence >= confidence_threshold THEN fast_choice
         WHEN reasoned_choice IN ('billing','technical','security') THEN reasoned_choice
         ELSE 'manual_review'
       END AS classification,
       struct_pack(
         source := CASE
           WHEN confidence >= confidence_threshold THEN 'system_one'
           WHEN reasoned_choice IN ('billing','technical','security') THEN 'system_two'
           ELSE 'manual_review'
         END,
         system_one_choice := fast_choice,
         system_one_confidence := confidence,
         confidence_threshold := confidence_threshold,
         escalated := reasoned_choice IS NOT NULL,
         system_two_choice := reasoned_choice
       ) AS provenance
FROM escalated;
```

The result preserves the input beside the final derived column and carries an auditable DuckDB `STRUCT`:

| id | input | classification | provenance |
|---:|---|---|---|
| 0 | Duplicate invoice charge | billing | `{source: system_one, system_one_choice: billing, system_one_confidence: 0.95, confidence_threshold: 0.80, escalated: false, system_two_choice: NULL}` |
| 1 | Account access fails intermittently after login | technical | `{source: system_two, system_one_choice: security, system_one_confidence: 0.55, confidence_threshold: 0.80, escalated: true, system_two_choice: technical}` |

## Throughput controls

Both paths deduplicate equal rows inside a chunk, share in-flight work inside a query, batch independent rows, and run bounded concurrent requests. Defaults are conservative and can be changed per connection.

```sql
SET dc_system_one_batch_size = 25;
SET dc_system_one_concurrency = 10;
SET dc_system_two_batch_size = 25;
SET dc_system_two_concurrency = 5;
```

System One supports batches of 1–1000 and concurrency of 1–10. System Two supports batches of 1–500 and concurrency of 1–10. Request byte caps, timeouts, retry delays, and per-query item/request budgets are also exposed as `dc_system_one_*` and `dc_system_two_*` settings. Invalid settings fail before dispatch.

The 8 MiB query cache for each system is enabled by default and lasts for one statement. Cross-statement caching is opt-in:

```sql
SET dc_system_one_session_cache_bytes = 8388608;
SET dc_system_two_session_cache_bytes = 8388608;
SET dc_system_one_session_cache_ttl_ms = 60000;
SET dc_system_two_session_cache_ttl_ms = 60000;
SELECT dc_cache_clear();
```

Cache identity includes endpoint, model, key, operation, instructions, criteria or schema, and evidence. Provider failures are never cached. A retry can duplicate remotely accepted work if the response was lost.

## Test

```bash
uv run pytest -q
uv run ruff check tests scripts
uv run pyright tests scripts
```

Paid endpoint tests are opt-in and read credentials only from environment variables:

```bash
DC_RUN_LIVE=1 TYPESAFE_API_KEY=... OPENAI_API_KEY=... \
  uv run pytest -q tests/test_live.py
```

The deterministic suite uses local protocol stubs and exercises native request packing, concurrency, retries, budgets, caching, cancellation, malformed responses, and multi-stage SQL composition.

## Scope

This is a native extension and does not run in DuckDB-Wasm. System Two uses the OpenAI Responses API with strict structured output. System One currently targets TypeSafe. Provider errors fail the query rather than silently producing a fallback label or text.

Licensed under Apache-2.0.
