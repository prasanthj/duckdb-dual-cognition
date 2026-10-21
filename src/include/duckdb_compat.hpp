#pragma once

#include "duckdb/planner/expression/bound_function_expression.hpp"

#ifdef DC_DUCKDB_2
#include "duckdb/common/identifier.hpp"
#endif

namespace duckdb {
#ifdef DC_DUCKDB_2
using SqlIdentifier = Identifier;
#else
using SqlIdentifier = string;
#endif

static string FunctionName(const BoundFunctionExpression &expression) {
#ifdef DC_DUCKDB_2
  return expression.Function().GetName().GetIdentifierName();
#else
  return expression.function.name;
#endif
}

static void ConfigureVolatileFunction(ScalarFunction &function) {
#ifdef DC_DUCKDB_2
  function.SetStability(FunctionStability::VOLATILE);
  function.SetNullHandling(FunctionNullHandling::SPECIAL_HANDLING);
  function.SetFallible();
#else
  function.stability = FunctionStability::VOLATILE;
  function.null_handling = FunctionNullHandling::SPECIAL_HANDLING;
#endif
}
} // namespace duckdb
