# Schema boundaries

This directory holds versioned structures shared across permission boundaries:

- market snapshot and manifest records;
- task coordinates, variants and package manifests;
- public submission and trajectory records;
- verification and dataset-export records.

The schema answers “what fields and shapes are legal.” Numerical methods, units,
runtime capabilities and hidden verification logic remain in their owning
contracts. Accepted packages bind exact schema identities/digests, so breaking
changes require a new version rather than an in-place edit.

