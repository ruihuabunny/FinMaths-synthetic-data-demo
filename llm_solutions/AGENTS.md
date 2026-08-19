# External-model runners

- Keep provider/API orchestration isolated from authoring, Solver and verifier
  numerics. A runner may consume only the public task surface selected for the
  evaluation.
- Never pass the repository, verifier, authoring-private files, reference answer
  or hidden task database directly to the model.
- Preserve tool-call budgets, transcript order, raw model response and submission
  identity needed for audit. Keep credentials in environment variables and out
  of logs, fixtures and committed files.
- Runner behavior must not relax the task's runtime or submission contract.
