import json

from verifier.runtime import bsm_greeks_logical_checksum, digest_file


def test_public_data_identity(package_root):
    manifest = json.loads((package_root / "manifest.json").read_text(encoding="utf-8"))
    database = package_root / "public/task.duckdb"
    assert bsm_greeks_logical_checksum(database) == manifest["public_child_snapshot"]["logical_checksum"]
    assert digest_file(database) == manifest["artifacts"]["public/task.duckdb"]
