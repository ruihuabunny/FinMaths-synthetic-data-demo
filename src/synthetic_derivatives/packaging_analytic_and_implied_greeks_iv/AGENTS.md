# BSM package materialization

- Default deliverable is one standalone agent task unless the user explicitly
  requests a batch or suite. A standalone task does not include the repository,
  full parent dataset, generic sandbox, deployment platform or unrelated source.
- Source packages keep `public/`, `verifier/`, `reference/` and
  `authoring_private/` separate. Evaluation views contain only their exact
  allowlisted public files; trusted query/submit adapters remain host-side.
- Public prompts own the objective, conventions, units, joins, allowed resources
  and output rules. Runtime JSON owns capabilities/budgets; schemas own syntax;
  query results own public values. No public surface may reveal formulas or the
  answer.
- v2 static tools and v3 DuckDB-query tools are different interface versions.
  Preserve exact call budgets, SQL AST restrictions, response size/serialization,
  row key/order policy and submission semantics.
- Build in a sibling staging directory, run source-package/leaf/suite validation,
  leakage scans, digest/visibility checks and independent verification, then
  publish with one non-overwriting atomic rename.
- Accepted source tasks and portable deliveries are immutable. Observable prompt,
  method, schema, runtime, adapter or database changes require a new interface,
  task ID and delivery ID as specified by the owning contract.
- Maintain source-market uniqueness and unsplit-parent grouping for the 6×4 and
  100-task deliveries. Do not silently reuse a source DB across metric leaves.

