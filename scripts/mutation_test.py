"""Mutation testing of amber's validation core (amber-zun): weekly in CI and on demand.

Copies the working tree (tracked and unignored files) to a scratch directory, configures mutmut
there, and runs it with ``uv run --with mutmut``, so the repository, ``pyproject.toml``, and
``uv.lock`` are untouched. Prints, and writes as JSON, the killed/survived counts per module
and the surviving mutant names; inspect one with ``mutmut show <name>`` in the scratch
directory. File a bead for each survivor in invariant code, or note why it is equivalent.

    uv run python scripts/mutation_test.py                 # the whole validation core
    uv run python scripts/mutation_test.py --module grounding --max-children 8
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# The validation core. mutmut maps each mutant to the tests that cover it and runs only those.
CORE = ("annotations", "cases", "examples", "_graph_validation", "grounding")


def _toml_list(values: list[str]) -> str:
    return "[" + ", ".join(json.dumps(value) for value in values) + "]"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--module", action="append", choices=sorted(CORE), help="repeatable")
    parser.add_argument("--max-children", type=int, default=4)
    parser.add_argument("--output", type=Path, default=ROOT / "mutation-report.json")
    parser.add_argument("--keep", action="store_true", help="keep the scratch directory")
    args = parser.parse_args()
    modules = args.module or list(CORE)

    scratch = Path(tempfile.mkdtemp(prefix="amber-mutation-"))
    # The working tree (tracked and unignored files), so uncommitted fixes are tested too.
    files = subprocess.run(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
        cwd=ROOT, check=True, capture_output=True,
    ).stdout  # fmt: skip
    archive = subprocess.run(
        ["tar", "-c", "--null", "-T", "-"], cwd=ROOT, input=files, check=True, capture_output=True
    ).stdout
    subprocess.run(["tar", "-x", "-C", str(scratch)], input=archive, check=True)
    # Tests read docs, scripts, and fixtures, so mutmut's copy gets every top-level path.
    copied = sorted(path.name for path in scratch.iterdir() if path.name != "pyproject.toml")
    with (scratch / "pyproject.toml").open("a", encoding="utf-8") as config:
        config.write(
            "\n[tool.mutmut]\n"
            f"source_paths = {_toml_list([f'src/amber/{m}.py' for m in modules])}\n"
            'pytest_add_cli_args_test_selection = ["tests/", "-m", "not slow"]\n'
            f"also_copy = {_toml_list(copied)}\n"
        )
    # The derandomized profile, so a mutant's outcome does not depend on Hypothesis's draw.
    env = {"MLFLOW_DISABLE_AGENT_HINT": "1", "HYPOTHESIS_PROFILE": "ci"}
    # The extras CI tests with, so mutmut's baseline run is the suite CI runs.
    extras = ["--extra", "dev", "--extra", "tracking", "--extra", "bundles"]
    run = ["uv", "run", "--quiet", *extras, "--with", "mutmut", "mutmut"]
    subprocess.run(
        [*run, "run", "--max-children", str(args.max_children)],
        cwd=scratch,
        env={**_environment(), **env},
        check=False,
    )
    subprocess.run([*run, "export-cicd-stats"], cwd=scratch, env=_environment(), check=True)
    stats = json.loads((scratch / "mutants" / "mutmut-cicd-stats.json").read_text())
    listed = subprocess.run(
        [*run, "results"], cwd=scratch, env=_environment(), check=True, capture_output=True,
        text=True,
    ).stdout  # fmt: skip
    survivors = sorted(
        line.split(":")[0].strip()
        for line in listed.splitlines()
        if line.strip().endswith("survived")
    )
    by_module: dict[str, int] = {}
    for name in survivors:
        module = name.split(".")[1] if name.startswith("amber.") else name.split(".")[0]
        by_module[module] = by_module.get(module, 0) + 1
    tested = stats.get("killed", 0) + stats.get("survived", 0) + stats.get("timeout", 0)
    if not tested:
        print("mutmut tested no mutants; its baseline test run failed (see above)")
        return 1
    report = {
        "modules": modules,
        "stats": stats,
        "kill_rate": None if not tested else (tested - stats.get("survived", 0)) / tested,
        "survivors_by_module": by_module,
        "survivors": survivors,
        "scratch": str(scratch) if args.keep else None,
    }
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: report[key] for key in ("stats", "kill_rate", "survivors_by_module")}))
    print(f"report: {args.output}")
    if not args.keep:
        subprocess.run(["rm", "-rf", str(scratch)], check=False)
    return 0


def _environment() -> dict[str, str]:
    import os

    return {key: value for key, value in os.environ.items() if key != "VIRTUAL_ENV"}


if __name__ == "__main__":
    sys.exit(main())
