# Command-line entry points

- Keep scripts thin: parse arguments, call the owning package API, emit a concise
  result, and return a meaningful exit status. Do not duplicate pricing,
  authoring, package, validation, or leakage logic here.
- Default generated outputs to explicit scratch locations. Never overwrite a
  frozen snapshot or accepted delivery without a new identity and explicit user
  scope.
- Preserve deterministic defaults and expose seeds/versions as explicit
  arguments when the owning contract requires them.
- Packaging commands must stage and validate completely before atomic publish.
  A partial directory must never look accepted.

