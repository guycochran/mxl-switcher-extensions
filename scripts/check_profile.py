#!/usr/bin/env python3
"""Report whether an extension profile is pinned and promotion-ready."""

from __future__ import annotations

import argparse
import json
import pathlib


MUTABLE_REFS = {"main", "master", "head", "latest"}
PROMOTABLE = {"candidate", "accepted"}


def evaluate(root: pathlib.Path, profile_path: pathlib.Path) -> dict:
    root = root.resolve()
    profile = json.loads(profile_path.read_text())
    core_ref = str(profile["core"]["ref"])
    blockers = []
    if core_ref.lower() in MUTABLE_REFS:
        blockers.append({"scope": "core", "reason": "mutable_ref", "value": core_ref})
    packages = []
    for selected in profile["extensions"]:
        package_path = (root / selected).resolve()
        if not package_path.is_relative_to(root):
            raise ValueError(f"extension path escapes repository: {selected}")
        manifest = json.loads((package_path / "extension.json").read_text())
        package = {
            "path": selected,
            "name": manifest["name"],
            "version": manifest["version"],
            "maturity": manifest["maturity"],
            "entrypoint": manifest.get("entrypoint"),
        }
        packages.append(package)
        if manifest["maturity"] not in PROMOTABLE:
            blockers.append({
                "scope": selected,
                "reason": "maturity",
                "value": manifest["maturity"],
            })
        if not manifest.get("entrypoint"):
            blockers.append({"scope": selected, "reason": "no_entrypoint"})
    return {
        "profile": profile["name"],
        "promotion_ready": not blockers,
        "core_ref": core_ref,
        "packages": packages,
        "blockers": blockers,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", type=pathlib.Path, required=True)
    parser.add_argument(
        "--root",
        type=pathlib.Path,
        default=pathlib.Path(__file__).resolve().parents[1],
    )
    args = parser.parse_args()
    try:
        result = evaluate(args.root, args.profile)
    except (OSError, KeyError, json.JSONDecodeError) as error:
        print(json.dumps({"promotion_ready": False, "error": str(error)}, separators=(",", ":")))
        return 2
    print(json.dumps(result, separators=(",", ":")))
    return 0 if result["promotion_ready"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
