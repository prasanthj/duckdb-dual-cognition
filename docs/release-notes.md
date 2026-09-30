# Dual Cognition v0.2.1

This release adds DuckDB 1.5.6 support while retaining the Semantic Resolution introduced in v0.2.0:

- `system_one_resolve` selects a bounded candidate ID or returns `ambiguous` / `no_match`.
- `system_one_resolve_stream` preserves cross-chunk batching for per-row dynamic candidate sets.
- Resolution results include confidence, probabilities, model, and cache provenance.

Platform-specific `dc` extension archives support DuckDB 1.4.5 and 1.5.6 on macOS and Linux, x86-64 and ARM64. Each archive includes the native binary, compatibility manifest, SBOM, licenses, and checksum.
