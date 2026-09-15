# Authoring inputs

`authoring/` holds internal generator inputs that sit above the Python authoring
implementation:

- `configs/` is reserved for authoring-job-specific configuration;
- `templates/` contains reviewed starting points for new generator configurations.

Runtime code lives in
[`src/synthetic_derivatives/authoring/`](../src/synthetic_derivatives/authoring/README.md),
while publishable versioned configurations live under [`configs/`](../configs/README.md).
Templates are not solver-visible contracts and should never contain canonical
answers.

