# Dual Cognition v0.2.0

This release adds Semantic Resolution for assigning reliable foreign keys when exact joins cannot:

- `system_one_resolve` selects a bounded candidate ID or returns `ambiguous` / `no_match`.
- `system_one_resolve_stream` preserves cross-chunk batching for per-row dynamic candidate sets.
- Resolution results include confidence, probabilities, model, and cache provenance.

Platform-specific `dc` extension archives support DuckDB 1.4.5 and 1.5.5 on macOS and Linux, x86-64 and ARM64. Each archive includes the native binary, compatibility manifest, SBOM, licenses, and checksum.
