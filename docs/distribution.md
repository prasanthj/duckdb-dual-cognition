# Distribution

`dc` is a native C++ DuckDB extension. A binary must match both the DuckDB version and target platform.

Supported release targets:

- DuckDB 1.4.5, 1.5.6 and 2.0.0
- Linux x86-64 and arm64
- macOS x86-64 and arm64

Every release job builds the native binary, loads it into the matching DuckDB runtime, runs the deterministic suite, and packages the verified artifact with a manifest, SBOM, licenses, and SHA-256 checksum. The publish job runs only after all twelve targets pass.

Install a release by extracting the archive for the exact DuckDB version and platform, then loading the absolute path:

```sql
LOAD '/path/to/dc.duckdb_extension';
```

GitHub release archives are unsigned; enable unsigned extensions when loading those files. Do not substitute an artifact built for another DuckDB version or architecture.

For versions published to DuckDB's signed community repository:

```sql
INSTALL dc FROM community;
LOAD dc;
```

## Community release checklist

A GitHub release does not update the community repository. For every release intended for community users:

1. Build and test all supported DuckDB versions and platforms.
2. Update `extensions/dc/description.yml` in `duckdb/community-extensions` through a separate PR. Pin the tested source commit in `ref` (2.0), `variegata` (1.5), and `andium` (1.4).
3. Request explicit builds for older DuckDB versions when the upstream automatic jobs are disabled. Descriptor pins alone do not publish historical binaries.
4. After merge and deployment, verify a fresh `INSTALL dc FROM community; LOAD dc;` with each target runtime and platform, then check the public compatibility matrix.

The CMake build selects version-specific APIs directly from DuckDB's version, so the community workflow does not need private build-script flags.
