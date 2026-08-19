# Task-space registry

- This package models coordinates and compatibility; it does not generate tasks,
  choose curriculum weights, implement numerical methods, or claim capability.
- Keep the seven axes and rank/order deterministic. Legacy six-axis migration is
  explicit and unique; do not add silent compatibility guesses.
- A compatibility rule should explain the product/model/method constraint and be
  covered by positive and negative tests.
- New model families require stable separate identity fields and versioned config;
  do not overload `task_family_id` or reuse a BSM method label.

