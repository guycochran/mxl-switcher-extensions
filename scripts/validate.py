#!/usr/bin/env python3
"""Dependency-free repository shape and manifest checks."""

from __future__ import annotations

import json
import pathlib
import re
import sys


ROOT = pathlib.Path(__file__).resolve().parents[1]
NAME = re.compile(r"^[a-z][a-z0-9-]{0,62}$")
VERS = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z.-]+)?$")
MATURITY = {"concept", "prototype", "candidate", "accepted"}
KINDS = {"input", "output", "recorder", "playout", "operator_surface"}


def load(path: pathlib.Path) -> dict:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def extension(path: pathlib.Path) -> None:
    item = load(path)
    require(item.get("schema_version") == 1, f"{path}: schema_version")
    require(bool(NAME.fullmatch(item.get("name", ""))), f"{path}: name")
    require(bool(VERS.fullmatch(item.get("version", ""))), f"{path}: version")
    require(item.get("maturity") in MATURITY, f"{path}: maturity")
    require(item.get("kind") in KINDS, f"{path}: kind")
    require(bool(item.get("requires_core_api")), f"{path}: requires_core_api")
    require(isinstance(item.get("capabilities"), list), f"{path}: capabilities")
    roles = item.get("flow_roles", {})
    require(isinstance(roles.get("reads"), list), f"{path}: flow_roles.reads")
    require(isinstance(roles.get("publishes"), list), f"{path}: flow_roles.publishes")
    entrypoint = item.get("entrypoint")
    if entrypoint is not None:
        require(
            (path.parent / entrypoint).is_file(),
            f"{path}: missing entrypoint {entrypoint}",
        )


def profile(path: pathlib.Path) -> None:
    item = load(path)
    require(item.get("schema_version") == 1, f"{path}: schema_version")
    require(bool(NAME.fullmatch(item.get("name", ""))), f"{path}: name")
    require(bool(item.get("audience")), f"{path}: audience")
    require(bool(item.get("core", {}).get("repository")), f"{path}: core.repository")
    require(bool(item.get("core", {}).get("ref")), f"{path}: core.ref")
    require(isinstance(item.get("extensions"), list), f"{path}: extensions")
    for selected in item["extensions"]:
        target = ROOT / selected
        require(target.is_dir(), f"{path}: missing extension directory {selected}")


def main() -> int:
    try:
        for path in ROOT.rglob("extension.json"):
            extension(path)
        for path in ROOT.glob("profiles/*/profile.json"):
            profile(path)
        for schema in ROOT.glob("schemas/*.json"):
            load(schema)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(exc, file=sys.stderr)
        return 1
    print("extension manifests, profiles, and schemas: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
