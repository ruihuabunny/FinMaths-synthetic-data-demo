# Shared task contracts

- Only Solver/verifier-neutral definitions belong here: public inputs, economic
  units, method identity, row/key semantics, status values and canonical output.
- Never put pricing formulas, root-finding implementation, QuantLib calls, hidden
  expected answers, verifier tolerance, or authoring lineage in this package.
- Freeze quote conversion, binary64 cast points, output scaling, Decimal
  canonicalization and row ordering/key-alignment rules in the versioned contract.
- Current unit Greeks are unit-option values: Delta per 1 spot unit, Gamma per 1
  squared spot unit, Vega per 1 volatility percentage point, Theta per one
  calendar day with fixed expiry, and Rho per 1 percentage-point continuous rate.
- Breaking field, unit, method, status, order or canonicalization changes require
  new config/schema/interface identity and tests on both sides.

