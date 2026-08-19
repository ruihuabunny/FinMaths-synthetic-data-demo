# BSM metric modular leaf-verifier delivery comparison

## Outcome

Phase 6 published two new deliveries from the accepted 2026-08-19 source run;
Phase 7 then removed the repository-side legacy runtime assembly. No source
market was regenerated and neither `*_rerun` baseline was modified.

| Protocol | Frozen baseline | New modular delivery |
|:---|:---|:---|
| static v2 | `20260819_metric_6x4_static_v2_rerun` | `20260819_metric_6x4_static_v2_modular` |
| DuckDB-query v3 | `20260819_metric_6x4_db_query_v3_rerun` | `20260819_metric_6x4_db_query_v3_modular` |

Both builds used source-run summary digest
`933e1eff8c1b890ddfb7b006d05e00a12494f9424b2c07cc5b41b060e399fb51`,
allocation ID `20260819_metric_6x4_v1`, and the existing 6×4 delivery profile.

## Frozen identities

| Protocol | Suite manifest SHA-256 | Complete tree SHA-256 | Files | Suite ID |
|:---|:---|:---|---:|:---|
| static v2 baseline | `1df2721f50859a42fcf43bd56ea9951dae7b512b3afa1f7369c954b8b96be406` | `d787e74c8b69cab3bdba612f62faa29863016f3e9164a869229e98ec639c9a64` | 463 | `bsm-market-metric-suite-v1-cbae54085afcc45e3592f4b4` |
| static v2 modular | `e83053ee9c8905dec742562d252fd2d32b4f7a209d32fcb447163286032ce0a7` | `c5136c39b49b4940e3e9e26da8d55a425a7ce97ddc5e0cb645a35cbcda757051` | 607 | `bsm-market-metric-suite-v1-8915d031c4c3633070f41936` |
| v3 baseline | `153a3105c4ecffda6d19eed6fe6ab093128f36a870b018fd33a4fe7d5babae38` | `0c3ac00edf1a03875122b2313e6b9740dafc3c3e437633d8f5f0f8976506bf31` | 415 | `bsm-market-metric-suite-v2-771f420aed8ba730f60e4875` |
| v3 modular | `5b38d8b67dd0a11f925991b54f9e062cb1773ccf735ae5a863bfde236ee48ace` | `025942d48f67079be4ba31306b0b71ff2a6c9e823175ddedbb996d4aea78c91d` | 559 | `bsm-market-metric-suite-v2-a87ed404711f37c633ff9feb` |

The baseline tree hashes are the pre-refactor Phase 0 identities. The new
identities are frozen in
`tests/fixtures/bsm_metric_leaf_verifier_20260819_modular_deliveries.json`.

## Unchanged contracts and data

For every one of the 24 assignments in each protocol, the old and new suite
manifests agree on every assignment field except `delivery_manifest_digest`.
In particular, these remain exact:

- source task, target, allocation rank, and round;
- derived task and snapshot identities;
- source and derived database digests;
- source market-content digest and derived logical checksum;
- derived solver-interface digest and relative task path;
- `task.duckdb`, `source_manifest.json`, the complete `evaluation_view/`, the
  protocol toolset/payloads, `oracle_config.json`, and `requirements.lock` bytes;
- independently recomputed canonical expected submissions.

The static assignment digest remains
`be8ba9929d96809ebfdba7fa41d4222f160025e6be3c6c968851cc3ab9ef0d3c`;
the v3 assignment digest remains
`f3345928c217cac55bc5079de4e4c09cd6b9378b5940f8a30b2e3ee624548fa4`.

## Changed paths

Every new leaf adds exactly these six trusted files, with no removed path:

```text
verifier/_runtime/__init__.py
verifier/_runtime/database.py
verifier/_runtime/models.py
verifier/_runtime/oracle.py
verifier/_runtime/profile.py
verifier/_runtime/submission.py
```

Every leaf changes exactly six paths already present in the baseline:

| Path | Reason |
|:---|:---|
| `delivery_manifest.json` | Binds the new nested artifacts and their `verifier_only` visibility/digests |
| `verifier/README.md` | Documents the self-contained facade/private-module architecture |
| `verifier/runtime.py` | Replaces the monolith with the stable compatibility facade |
| `verifier/test_contract.py` | Uses package-relative facade imports |
| `verifier/test_data_identity.py` | Uses package-relative facade imports |
| `verifier/test_semantics.py` | Uses package-relative facade imports |

Across either suite, 144 files are added (six modules × 24 leaves). Of common
paths, 144 leaf paths change, plus six target `batch_manifest.json` files and
the top-level `suite_manifest.json`, for 151 changed common files. No path is
removed. Batch manifests change because they bind the new leaf delivery
manifest digests and suite identity. The suite manifest changes only through
the new delivery/suite identity and those downstream bindings; planned
assignment identity remains unchanged.

## Validation evidence

`test_metric_leaf_verifier_modular_delivery.py` verifies both complete suites,
audits the exact path sets above for all 48 old/new leaf pairs, recomputes every
expected answer, checks all nested artifact and visibility bindings, and runs a
representative leaf for each protocol under `python -B -I` with cache writes
disabled. The focused migration module passes 6 tests.

Final repository validation on 2026-08-19 completed with:

```text
378 passed  # all test_metric_leaf_verifier_*.py modules
650 passed  # tests/packaging_analytic_and_implied_greeks_iv
978 passed  # complete repository pytest suite
```

Phase 7 deleted
`bsm_market_metric_verifier_runtime.py` and
`bsm_market_metric_verifier_runtime_v3.py` from the authoring source package.
Historical copies inside accepted deliveries remain immutable and are loaded
only by golden/equivalence tests.
