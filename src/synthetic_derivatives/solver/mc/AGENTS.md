# L3 Monte Carlo scaffold

This directory is not implemented. Do not add an estimator opportunistically or
advertise an API until the corresponding task contract, frozen draw bank, runtime
profile, schema, authoring records, independent verifier and package tests exist.

- First release uses the declared `Q`-measure exact terminal BSM lognormal law,
  not Euler time stepping, and consumes ordered public base-normal draws without
  resampling.
- Freeze dtype, draw/pair order, antithetic reduction, bumps/CRN semantics,
  estimator units, standard error and canonicalization before implementation.
- Solver and verifier MC numerics remain independent. Analytic BSM is QA, not the
  fixed-draw task target.
- NumPy or DuckDB access requires a new versioned Solver capability profile and
  dependency lock; this directory does not grant either.

