# Distribution

`dc` is a native C++ DuckDB extension. A binary must match both the DuckDB version and target platform.

Supported release targets:

- DuckDB 1.4.5 and 1.5.6
- Linux x86-64 and arm64
- macOS x86-64 and arm64

Every release job builds the native binary, loads it into the matching DuckDB runtime, runs the deterministic suite, and packages the verified artifact with a manifest, SBOM, licenses, and SHA-256 checksum. The publish job runs only after all eight targets pass.

Install a release by extracting the archive for the exact DuckDB version and platform, then loading the absolute path:

```sql
LOAD '/path/to/dc.duckdb_extension';
```

Until the project is accepted as a signed DuckDB Community Extension, connect with unsigned extensions enabled. Do not substitute an artifact built for another DuckDB version or architecture.
