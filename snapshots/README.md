# Market snapshots

`snapshots/public/` contains the small frozen DuckDB snapshot and manifest used
for public tests and examples. Large, private or regenerable authoring databases
do not belong in Git.

See the [public snapshot contract](public/README.md) for identity and visibility,
and the [SQL catalog](public/sql_query/README.md) for safe read-only inspection.
IV, Greeks and verifier answers are derived task outputs and are not separate
truth tables in the market snapshot.

