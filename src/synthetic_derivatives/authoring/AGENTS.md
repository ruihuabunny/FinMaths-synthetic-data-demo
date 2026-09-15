# Authoring implementation

- This is the trusted/private boundary. Pinned QuantLib and DuckDB are allowed;
  latent model state, seeds, audits and oracle material must remain private.
- Underlying history is a declared `P`-measure process. For deterministic
  piecewise-linear drift/volatility, integrate drift and variance across every
  calendar interval and node; do not replace integrated variance with endpoint or
  arithmetic-average volatility.
- `initial_spot` is the state at `start_date`. Preserve the configured published
  rounded-state restart law and deterministic RNG partition/order used by the
  current snapshot contract; changing it requires a new generator version.
- Dependence couples underlying risk drivers through a measure-qualified PSD
  specification. Keep P/Q specs and shared common-`Q` pricing context explicit.
- An endpoint transition does not create valid OHLC. Volume, spreads, dividends
  and corporate actions require their own declared model or explicit rule.
- Option generation uses the declared `Q`, numeraire, curves, calendar, day count,
  exercise and settlement. Physical drift never enters option pricing.
- Generate canonical market quotes, not IV/Greek truth tables. Quote noise changes
  visible quotes; canonical IV is recovered later from those visible values.
- All writes go through the transactional DRAFT/freeze/revision pipeline and its
  quality gates. Extend config/schema/tests together.
- `config.py`, `schema.py`, and `pipeline.py` are stable façades. Internal
  modules must import the owning focused module instead of importing a façade
  back into its implementation and creating a cycle.
- Generation modules (`underlying_path`, `underlying_observations`,
  `pricing_metadata`, and product generators) must not import DuckDB. Storage,
  schema, persistence, and migration modules must not import QuantLib or model
  numerics.
- `AuthoringPipeline` alone owns complete `BEGIN / COMMIT / ROLLBACK` ordering.
  Materialization and validation components reuse its connection and never
  publish a manifest or open an independent writer.
- Immutable preflight and post-write validation are distinct security/correctness
  phases. Preserve both even when they invoke the same contract comparison.
- Typed rows in `row_contracts.py` and column order in `table_specs.py` are an
  explicit two-sided contract; keep their field tuples exactly equal and do not
  generate either one by reflection from the other.
