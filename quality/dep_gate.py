"""Report dependency changes since origin/main; CI refuses them without a human label
(amber-7q0.7).

Compares the merge base of ``--base`` and HEAD with the working tree: in pyproject.toml, each
requirement in [project] dependencies, every optional extra, every [dependency-groups] group,
and [build-system] requires, plus the [tool.uv] and [tool.hatch] tables whole (sources,
indexes, overrides, build-hook dependencies); in uv.lock, every package's version, source, and
hashes (the ratchet's counts depend on the locked tool versions). Names compare normalized
(PEP 503) and whitespace is ignored; an entry the gate cannot parse, such as an include-group
table, is compared whole, so nothing is skipped. Prints each added (+), removed (-), or changed
(~) entry.

Exit 0 when nothing changed. Otherwise, with ``--enforce`` (CI), exit 2 unless ``--labels``
includes ``dependency-change``, which only a human adds; without it (make quality) the diff is a
warning and the exit is 0. Exit 1 when git or a file cannot be read. Standard library only.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
import tomllib
from collections.abc import Callable, Iterator, Mapping, Sequence
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
LABEL = "dependency-change"
REGRESSION = 2
_NAME = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)")


class GateError(RuntimeError):
    """git or a dependency file could not be read."""


def _normalize(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _compact(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _requirements(section: str, entries: Sequence[Any]) -> Iterator[tuple[str, str]]:
    """Each requirement as (``section: name``, the requirement with its name normalized and
    whitespace removed), so spelling alone is not a change; anything else is compared whole."""
    for entry in entries:
        if isinstance(entry, str) and (match := _NAME.match(entry)):
            name = _normalize(match[1])
            yield f"{section}: {name}", name + re.sub(r"\s+", "", entry[match.end() :])
        else:
            yield f"{section}: {_compact(entry)}", _compact(entry)


def declared(pyproject: Mapping[str, Any]) -> dict[str, str]:
    """Each declared requirement, keyed by ``section: name``; several entries for one name join
    in order, so adding one is a change."""
    project = pyproject.get("project", {})
    sections = [("project.dependencies", project.get("dependencies", []))]
    for extra, entries in sorted(project.get("optional-dependencies", {}).items()):
        sections.append((f"optional-dependencies.{extra}", entries))
    for group, entries in sorted(pyproject.get("dependency-groups", {}).items()):
        sections.append((f"dependency-groups.{group}", entries))
    build = pyproject.get("build-system", {}).get("requires", [])
    sections.append(("build-system.requires", build))
    found: dict[str, list[str]] = {}
    for section, entries in sections:
        for key, requirement in _requirements(section, entries):
            found.setdefault(key, []).append(requirement)
    for tool in ("uv", "hatch"):
        if table := pyproject.get("tool", {}).get(tool):
            found[f"tool.{tool}: table"] = [_compact(table)]
    return {key: " | ".join(requirements) for key, requirements in found.items()}


def locked(lock: Mapping[str, Any]) -> dict[str, str]:
    """Each locked package's versions, keyed by ``uv.lock: name``, with a fingerprint of its
    source and hashes, so a new source or artifact at the same version is a change."""
    versions: dict[str, set[str]] = {}
    for package in lock.get("package", []):
        key = f"uv.lock: {_normalize(package['name'])}"
        origin = {part: package.get(part) for part in ("source", "sdist", "wheels")}
        fingerprint = hashlib.sha256(_compact(origin).encode()).hexdigest()[:12]
        versions.setdefault(key, set()).add(f"{package.get('version', '?')} ({fingerprint})")
    return {key: ", ".join(sorted(found)) for key, found in versions.items()}


def _shown(key: str, value: str) -> str:
    section, name = key.split(": ", 1)
    return f"{name} {value}" if section == "uv.lock" else value


def differences(old: Mapping[str, str], new: Mapping[str, str]) -> list[str]:
    """Added (+), removed (-), and changed (~) entries, one line each."""
    lines = []
    for key in sorted(set(old) | set(new)):
        section = key.split(": ", 1)[0]
        if key not in old:
            lines.append(f"+ {section}: {_shown(key, new[key])}")
        elif key not in new:
            lines.append(f"- {section}: {_shown(key, old[key])}")
        elif old[key] != new[key]:
            lines.append(f"~ {section}: {_shown(key, old[key])} -> {new[key]}")
    return lines


def _git(root: Path, *args: str) -> str:
    done = subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, check=False)
    if done.returncode != 0:
        raise GateError(f"git {' '.join(args)}: {done.stderr.strip()[:300]}")
    return done.stdout


def _toml(text: str, what: str) -> dict[str, Any]:
    try:
        return tomllib.loads(text)
    except tomllib.TOMLDecodeError as error:
        raise GateError(f"{what}: {error}") from error


def _dependencies(read: Callable[[str], str]) -> dict[str, str]:
    pyproject = _toml(read("pyproject.toml"), "pyproject.toml")
    return declared(pyproject) | locked(_toml(read("uv.lock"), "uv.lock"))


def changes(root: Path, base: str) -> list[str]:
    """The dependency diff between the merge base of ``base`` and HEAD and the working tree."""
    merge_base = _git(root, "merge-base", base, "HEAD").strip()

    def at_base(name: str) -> str:
        return _git(root, "show", f"{merge_base}:{name}")

    def now(name: str) -> str:
        return (root / name).read_text(encoding="utf-8")

    return differences(_dependencies(at_base), _dependencies(now))


def main(argv: Sequence[str] | None = None) -> int:
    """Print the dependency diff; with ``--enforce``, fail on it unless a human approved."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--base", default="origin/main", help="compare against this")
    parser.add_argument("--labels", default="", help="pull request labels, comma-separated")
    parser.add_argument("--enforce", action="store_true", help="fail without the label (CI)")
    parser.add_argument("--root", type=Path, default=ROOT, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    try:
        found = changes(args.root, args.base)
    except (GateError, OSError) as error:
        print(f"dep_gate: {error}")
        return 1
    if not found:
        print("dep_gate: no dependency changes")
        return 0
    print("dep_gate: dependency changes:")
    for line in found:
        print(f"  {line}")
    if LABEL in {label.strip() for label in args.labels.split(",")}:
        print(f"dep_gate: approved by the {LABEL} label")
        return 0
    if args.enforce:
        print(f"dep_gate: a human approves dependency changes with the {LABEL} label")
        return REGRESSION
    print(f"dep_gate: warning: CI fails this change until a human adds the {LABEL} label")
    return 0


if __name__ == "__main__":
    sys.exit(main())
