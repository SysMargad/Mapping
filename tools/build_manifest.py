"""Build a small hash manifest for static GitHub Pages assets."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1] / "dist"


def main(version: str, updated: str) -> None:
    registry_path = ROOT / "data" / "datasets.json"
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    assets = {"registry": "./data/datasets.json"}
    for dataset in registry.get("datasets", []):
        asset = dataset.get("webAsset") or dataset.get("metadataAsset")
        if asset:
            assets[dataset["id"]] = asset
    hashes = {}
    for key, asset in assets.items():
        path = ROOT / asset.removeprefix("./")
        if path.exists():
            hashes[key] = hashlib.sha256(path.read_bytes()).hexdigest()[:16]
    payload = {"version": version, "updated": updated, "datasets": assets, "hashes": hashes}
    output = ROOT / "data" / "manifest.json"
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {output} with {len(hashes)} asset hashes")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--version", required=True)
    parser.add_argument("--updated", required=True)
    args = parser.parse_args()
    main(args.version, args.updated)
