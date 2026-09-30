PROJ_DIR := $(dir $(abspath $(lastword $(MAKEFILE_LIST))))

# Configuration of extension
EXT_NAME=dc
EXT_CONFIG=${PROJ_DIR}extension_config.cmake

# Standard DuckDB Community Extensions build targets.
include extension-ci-tools/makefiles/duckdb_extension.Makefile
