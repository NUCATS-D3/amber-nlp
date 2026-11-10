"""Hold violation counts at quality/baseline.json: none may rise (amber-7q0.4).

Legacy code may keep the violations the baseline records; new code is held to the full
standard. Metrics:

- ``ruff:<code>``: Ruff violations under ruff.toml, per rule code;
- ``mypy-strict``: ``mypy --strict`` errors in src/;
- ``suppressions:noqa``, ``suppressions:type-ignore``, ``suppressions:no-cover``: suppression
  comments in Python files, every one counted, and ``suppressions:unjustified``: those without a
  trailing justification: a ``-``, ``--``, or em dash, then text (``# noqa: E741 - the protocol's
  name``). mypy allows only a comment after an ignore, so there it is ``# type: ignore[misc]  #
  - reason``;
- ``complexity:<module>``: the highest cyclomatic complexity (Ruff's C901, mccabe) of a function
  in a src/ module. Only modules above COMPLEXITY_LIMIT are recorded; any other module may grow
  up to the limit;
- ``importlinter:ignore_imports``: ``ignore_imports`` entries in .importlinter.

Whatever the baseline says, measured against the merge base with ``--base`` (origin/main):
files added since then must have no Ruff or mypy-strict finding, no unjustified suppression, and
no function over COMPLEXITY_LIMIT, and no added line anywhere may carry an unjustified
suppression.

Exit 2 when anything rose, printing each metric old -> new with the findings likely to be new;
otherwise exit 0, printing "ratchet can be tightened" for each metric that fell. Exit 1 when a
tool or git fails. ``--write-baseline`` rewrites the baseline only with ALLOW_ORACLE_CHANGE=1 and
only when nothing rose: rebaselining tightens, and loosening is a hand edit in a human commit.

Standard library only; the tools run from this interpreter's environment (pinned by uv.lock).
"""

from __future__ import annotations

import argparse
import configparser
import io
import json
import os
import re
import subprocess
import sys
import tokenize
from collections.abc import Iterator, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASELINE = Path("quality/baseline.json")
COMPLEXITY_LIMIT = 10
REGRESSION = 2
_SHOWN = 20
_ALWAYS_RECORDED = (
    "mypy-strict",
    "suppressions:noqa",
    "suppressions:type-ignore",
    "suppressions:no-cover",
    "suppressions:unjustified",
    "importlinter:ignore_imports",
)
_SUPPRESSION = re.compile(
    r"#\s*(?P<kind>noqa|type:\s*ignore|pragma:\s*no\s+cover)"
    r"(?::\s*[A-Z]+\d+(?:\s*,\s*[A-Z]+\d+)*|\[[^\]]*\])?"
)
_KINDS = {"n": "noqa", "t": "type-ignore", "p": "no-cover"}
_JUSTIFIED = re.compile(r"\s*(?:#\s*)?(?:-|--|\u2014)\s*[^\s#]")
_COMPLEXITY = re.compile(r"\((\d+) > 0\)")
_HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")


class ToolError(RuntimeError):
    """A tool or git could not produce a measurement."""


@dataclass(frozen=True, order=True)
class Finding:
    path: str
    line: int
    code: str
    message: str = ""

    def __str__(self) -> str:
        return f"{self.path}:{self.line}:{self.code} {self.message}".rstrip()


@dataclass
class Measurement:
    findings: dict[str, list[Finding]] = field(default_factory=dict)
    complexity: dict[str, int] = field(default_factory=dict)
    over_limit: list[Finding] = field(default_factory=list)
    tools: dict[str, str] = field(default_factory=dict)

    def values(self) -> dict[str, int]:
        values = {metric: len(found) for metric, found in self.findings.items()}
        values.update({f"complexity:{path}": top for path, top in self.complexity.items()})
        return values


@dataclass(frozen=True)
class Changes:
    added_files: frozenset[str]
    renamed_from: Mapping[str, str]
    added_lines: Mapping[str, frozenset[int]]


def _run(root: Path, command: Sequence[str], ok: Sequence[int] = (0,)) -> str:
    done = subprocess.run(command, cwd=root, capture_output=True, text=True, check=False)
    if done.returncode not in ok:
        raise ToolError(f"{' '.join(command[:4])} failed ({done.returncode}): {done.stderr[-500:]}")
    return done.stdout


def _tool(root: Path, *args: str, ok: Sequence[int] = (0,)) -> str:
    return _run(root, [sys.executable, "-m", *args], ok)


def _git(root: Path, *args: str) -> str:
    return _run(root, ["git", "-c", "core.quotePath=false", *args])


def _relative(path: str, root: Path) -> str:
    resolved = Path(path) if Path(path).is_absolute() else root / path
    return resolved.resolve().relative_to(root.resolve()).as_posix()


def ruff_findings(root: Path, *options: str, target: str = ".") -> list[Finding]:
    report = _tool(root, "ruff", "check", "--exit-zero", "--output-format=json", *options, target)
    return [
        Finding(
            _relative(item["filename"], root),
            item["location"]["row"],
            item["code"] or "syntax-error",
            item["message"],
        )
        for item in json.loads(report)
    ]


def mypy_findings(root: Path) -> list[Finding]:
    report = _tool(root, "mypy", "--strict", "-O", "json", "src", ok=(0, 1))
    found = []
    for line in report.splitlines():
        if not line.startswith("{"):
            continue
        item = json.loads(line)
        if item["severity"] == "error":
            path = _relative(item["file"], root)
            found.append(Finding(path, item["line"], item["code"] or "error", item["message"]))
    return found


def function_complexity(root: Path) -> list[tuple[Finding, int]]:
    """Every function in src/ with its cyclomatic complexity, from Ruff's C901 at limit 0."""
    options = ("--select", "C901", "--config", "lint.mccabe.max-complexity = 0")
    scored = []
    for finding in ruff_findings(root, *options, target="src"):
        match = _COMPLEXITY.search(finding.message)
        if match is None:
            raise ToolError(f"unexpected C901 message: {finding}")
        scored.append((finding, int(match.group(1))))
    return scored


def python_files(root: Path) -> list[str]:
    listed = _git(root, "ls-files", "--cached", "--others", "--exclude-standard", "--", "*.py")
    return sorted(path for path in listed.splitlines() if (root / path).is_file())


def suppressions(path: str, text: str) -> Iterator[tuple[str, Finding]]:
    """Each suppression comment as (kind, finding); the code is ``unjustified`` when it has no
    trailing justification."""
    try:
        tokens = list(tokenize.generate_tokens(io.StringIO(text).readline))
    except (tokenize.TokenError, SyntaxError) as error:
        raise ToolError(f"{path}: cannot tokenize: {error}") from error
    for token in tokens:
        if token.type != tokenize.COMMENT:
            continue
        for match in _SUPPRESSION.finditer(token.string):
            justified = _JUSTIFIED.match(token.string, match.end()) is not None
            code = "justified" if justified else "unjustified"
            yield _KINDS[match["kind"][0]], Finding(path, token.start[0], code, match.group())


def import_ignores(root: Path) -> list[Finding]:
    config = configparser.ConfigParser()
    config.read(root / ".importlinter")
    return [
        Finding(".importlinter", 0, section, entry.strip())
        for section in config.sections()
        for entry in config[section].get("ignore_imports", "").splitlines()
        if entry.strip()
    ]


def _version(root: Path, tool: str) -> str:
    return _tool(root, tool, "--version").split()[1]


def measure(root: Path) -> Measurement:
    with ThreadPoolExecutor() as pool:
        ruff = pool.submit(ruff_findings, root)
        mypy = pool.submit(mypy_findings, root)
        scored = pool.submit(function_complexity, root)
        versions = {tool: pool.submit(_version, root, tool) for tool in ("ruff", "mypy")}
        files = pool.submit(python_files, root)
    measurement = Measurement(findings={metric: [] for metric in _ALWAYS_RECORDED})
    for finding in ruff.result():
        measurement.findings.setdefault(f"ruff:{finding.code}", []).append(finding)
    measurement.findings["mypy-strict"] = mypy.result()
    for finding, score in scored.result():
        top = measurement.complexity.get(finding.path, 0)
        measurement.complexity[finding.path] = max(top, score)
        if score > COMPLEXITY_LIMIT:
            measurement.over_limit.append(finding)
    for path in files.result():
        for kind, finding in suppressions(path, (root / path).read_text(encoding="utf-8")):
            measurement.findings[f"suppressions:{kind}"].append(finding)
            if finding.code == "unjustified":
                measurement.findings["suppressions:unjustified"].append(finding)
    measurement.findings["importlinter:ignore_imports"] = import_ignores(root)
    measurement.tools = {tool: version.result() for tool, version in versions.items()}
    return measurement


def _added_lines(diff: str) -> dict[str, set[int]]:
    lines: dict[str, set[int]] = {}
    path = None
    for line in diff.splitlines():
        if line.startswith("+++ "):
            path = line[6:] if line.startswith("+++ b/") else None
        elif path is not None and (hunk := _HUNK.match(line)):
            start, count = int(hunk.group(1)), int(hunk.group(2) or 1)
            lines.setdefault(path, set()).update(range(start, start + count))
    return lines


def changed_since(root: Path, base: str) -> Changes:
    """Files and lines added since the merge base of ``base`` and HEAD, including uncommitted
    and untracked work."""
    try:
        merge_base = _git(root, "merge-base", base, "HEAD").strip()
    except ToolError as error:
        raise ToolError(f"no merge base with {base}; fetch it or pass --base") from error
    added, renamed = set(), {}
    status = _git(root, "diff", "--name-status", "-M", "-z", merge_base).split("\0")
    fields = iter(status)
    for code in fields:
        if code.startswith("R"):
            old, new = next(fields), next(fields)
            renamed[new] = old
        elif code.startswith("C"):
            next(fields)
            added.add(next(fields))
        elif code:
            path = next(fields)
            if code == "A":
                added.add(path)
    lines = _added_lines(_git(root, "diff", "-U0", "--no-color", "--no-ext-diff", merge_base))
    for path in _git(root, "ls-files", "--others", "--exclude-standard").splitlines():
        added.add(path)
        text = (root / path).read_text(encoding="utf-8", errors="replace")
        lines[path] = set(range(1, text.count("\n") + 2))
    return Changes(
        frozenset(added),
        renamed,
        {path: frozenset(numbers) for path, numbers in lines.items()},
    )


def _ceiling(metric: str, baseline: Mapping[str, int], renamed: Mapping[str, str]) -> int:
    if metric in baseline:
        return baseline[metric]
    if not metric.startswith("complexity:"):
        return 0
    old = renamed.get(metric.removeprefix("complexity:"))
    return baseline.get(f"complexity:{old}", COMPLEXITY_LIMIT)


def compare(
    baseline: Mapping[str, int], current: Mapping[str, int], renamed: Mapping[str, str]
) -> tuple[list[tuple[str, int, int]], list[tuple[str, int, int]]]:
    """Metrics that rose and metrics that fell, each as (metric, old, new)."""
    rose, fell = [], []
    for metric in sorted(set(baseline) | set(current)):
        old, new = _ceiling(metric, baseline, renamed), current.get(metric, 0)
        if new > old:
            rose.append((metric, old, new))
        elif metric in baseline and new < old:
            fell.append((metric, old, new))
    return rose, fell


def unclean_new_code(measurement: Measurement, changes: Changes) -> list[Finding]:
    """Findings that new code may not have, whatever the baseline says. (A new module's
    complexity needs no check here: with no baseline entry its ceiling is the limit.)"""
    strict = [
        finding
        for metric, found in measurement.findings.items()
        if metric.startswith("ruff:") or metric == "mypy-strict"
        for finding in found
    ]
    unclean = {finding for finding in strict if finding.path in changes.added_files}
    for finding in measurement.findings["suppressions:unjustified"]:
        added = changes.added_lines.get(finding.path, frozenset())
        if finding.path in changes.added_files or finding.line in added:
            unclean.add(finding)
    return sorted(unclean)


def _suspects(metric: str, measurement: Measurement, changes: Changes) -> list[Finding]:
    """A rising metric's findings on added lines, or all of them when none is."""
    if metric.startswith("complexity:"):
        path = metric.removeprefix("complexity:")
        found = [f for f in measurement.over_limit if f.path == path]
    else:
        found = measurement.findings.get(metric, [])
    added = [f for f in found if f.line in changes.added_lines.get(f.path, frozenset())]
    return added or found


def recorded(measurement: Measurement) -> dict[str, int]:
    """The metrics a baseline keeps: every nonzero count, the fixed metrics, and the modules
    over the complexity limit."""
    return {
        metric: value
        for metric, value in sorted(measurement.values().items())
        if metric in _ALWAYS_RECORDED
        or (value > COMPLEXITY_LIMIT if metric.startswith("complexity:") else value > 0)
    }


def _print_findings(findings: Sequence[Finding]) -> None:
    for finding in findings[:_SHOWN]:
        print(f"  {finding}")
    if len(findings) > _SHOWN:
        print(f"  ... and {len(findings) - _SHOWN} more")


@dataclass(frozen=True)
class Baseline:
    metrics: dict[str, int]
    tools: dict[str, str]


def _load(path: Path) -> Baseline | None:
    if not path.exists():
        return None
    stored = json.loads(path.read_text())
    return Baseline(
        {metric: int(value) for metric, value in stored["metrics"].items()},
        {tool: str(version) for tool, version in stored["tools"].items()},
    )


def _write_baseline(
    path: Path, measurement: Measurement, rose: Sequence[tuple[str, int, int]], allowed: bool
) -> int:
    if not allowed:
        print("ratchet: rewriting the baseline is for humans: set ALLOW_ORACLE_CHANGE=1")
        return 1
    if rose:
        print("ratchet: refusing to rebaseline; it may only tighten. Rose:")
        for metric, old, new in rose:
            print(f"  {metric} {old} -> {new}")
        return REGRESSION
    metrics = recorded(measurement)
    path.write_text(json.dumps({"tools": measurement.tools, "metrics": metrics}, indent=2) + "\n")
    print(f"ratchet: wrote {path} ({len(metrics)} metrics)")
    return 0


def run(root: Path, base: str, write_baseline: bool, env: Mapping[str, str]) -> int:
    measurement = measure(root)
    changes = changed_since(root, base)
    stored = _load(root / BASELINE)
    metrics = stored.metrics if stored else {}
    rose, fell = compare(metrics, measurement.values(), changes.renamed_from)
    if write_baseline:
        allowed = env.get("ALLOW_ORACLE_CHANGE") == "1"
        return _write_baseline(root / BASELINE, measurement, rose if stored else [], allowed)
    if stored is None:
        print(f"ratchet: no {BASELINE}; a human runs ALLOW_ORACLE_CHANGE=1 make quality-rebaseline")
        return 1
    if stored.tools != measurement.tools:
        print(f"ratchet: warning: tools {measurement.tools} differ from the baseline's")
    for metric, old, new in rose:
        print(f"ratchet: {metric} rose {old} -> {new}")
        _print_findings(_suspects(metric, measurement, changes))
    unclean = unclean_new_code(measurement, changes)
    if unclean:
        print("ratchet: new code must be clean (added files and added lines):")
        _print_findings(unclean)
    for metric, old, new in fell:
        print(f"ratchet can be tightened: {metric} {old} -> {new}")
    if rose or unclean:
        return REGRESSION
    print(f"ratchet ok: {len(metrics)} metrics at or under the baseline")
    return 0


def main(argv: Sequence[str] | None = None, env: Mapping[str, str] = os.environ) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--base", default="origin/main", help="compare new code against this")
    parser.add_argument("--root", type=Path, default=ROOT, help=argparse.SUPPRESS)
    parser.add_argument(
        "--write-baseline", action="store_true", help="human only: rewrite the baseline"
    )
    args = parser.parse_args(argv)
    try:
        return run(args.root, args.base, args.write_baseline, env)
    except ToolError as error:
        print(f"ratchet: {error}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
