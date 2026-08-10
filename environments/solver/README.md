# Solver runtime allowlist

This directory defines the dependency boundary for solver-visible BSM tasks.

Allowed runtime:

- the pinned Python interpreter and its standard library;
- `duckdb==1.5.5` for read-only access to the published task database;
- repository code explicitly copied into the solver bundle.

Not allowed in the solver image:

- QuantLib, py_vollib, mibian, rateslib, or packaged pricing/Greek/IV APIs;
- authoring, verifier, mutation, private lineage, or hidden-oracle modules;
- dynamic package installation, network access, or undeclared filesystem reads.

QuantLib remains available only to trusted authoring and verifier code. The task
contract must freeze formulas, conventions, dtype, operation order and canonical
serialization so the solver implementation and independent verifier can be
compared exactly.
