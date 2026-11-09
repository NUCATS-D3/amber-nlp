# Spec: Verification Harness for Agent-Driven NLP Development

## Purpose

Set up a verification harness for this repository so that coding-agent work stays on track. Verification must be **executable**, **automatic**, and **outside the agent's control**. The harness has to catch two kinds of failure:

1. Ordinary breakage: lint, type errors, failing unit tests.
2. Silent quality regressions in NLP output: span-level precision/recall/F1 drops, changes in behavior on known phenomena such as negation and temporality.

The project is Python, possibly with a compiled core such as Rust behind Python bindings. Adapt paths to the actual repo layout, but keep the structure and guarantees below.

## Guiding principles (must hold after setup)

- "Done" is defined by a single command (`make check`), not by the agent's judgment.
- The agent cannot change the oracle. Gold data, baselines, acceptance tests and the harness config are write-protected for the agent and change only through human commits.
- Hooks and CI produce the evidence. An agent's claim that tests pass is never accepted on its own.
- Checks are tiered by speed: fast checks on every edit, medium checks before the agent stops, full checks in CI.
- There is a held-out evaluation set that the agent never sees. Only aggregate metrics from it are reported.

---

## 1. Directory layout

Create (or adapt to) this layout:

```
.
├── AGENTS.md                  # agent instructions incl. definition of done (also symlink/copy as CLAUDE.md)
├── Makefile                   # check targets
├── pyproject.toml             # ruff, pyright, pytest, hypothesis, mutmut config
├── .pre-commit-config.yaml
├── .claude/
│   ├── settings.json          # hooks + permission deny rules (PROTECTED)
│   └── hooks/
│       ├── post_edit.py       # lint/typecheck the edited file
│       └── stop_gate.py       # fast tests + dev eval gate before stopping
├── src/<package>/
├── tests/
│   ├── unit/                  # agent may add/edit
│   ├── properties/            # Hypothesis invariants (agent may add; edits reviewed)
│   ├── behavioral/            # CheckList-style suites (PROTECTED)
│   └── acceptance/            # frozen spec tests (PROTECTED)
├── eval/
│   ├── score.py               # span scorer (PROTECTED)
│   ├── gate.py                # regression gate (PROTECTED)
│   ├── diff_predictions.py    # per-doc prediction churn report
│   ├── baseline.json          # committed metric baseline (PROTECTED)
│   ├── dev_gold/              # de-identified/synthetic gold, visible to agent (PROTECTED)
│   └── predictions/           # cached system outputs per git SHA (gitignored)
└── .github/workflows/ci.yml   # or equivalent CI; runs held-out eval
```

The held-out gold set **lives outside the repo** and is reachable only from CI. Examples: a CI secret pointing at a private bucket, or a path on a CI runner. The agent's environment must not be able to read it.

---

## 2. Static checks

- **ruff** for lint and format, configured in `pyproject.toml`.
- **pyright** (or mypy) in `standard` mode, at minimum for `src/`.
- **pre-commit** hooks: ruff, ruff-format, pyright, end-of-file and trailing-whitespace fixers, a check-added-large-files guard, and a hook that **rejects commits touching protected paths unless `ALLOW_ORACLE_CHANGE=1` is set**. Humans set that variable deliberately.

---

## 3. Test suites

### 3.1 Unit tests (`tests/unit/`)
Standard pytest. Fast: the whole suite should run in under 30 s.

### 3.2 Property-based tests (`tests/properties/`)
Use Hypothesis. Implement at least these invariants for every extractor and annotator:

- Every output span satisfies `0 <= start < end <= len(text)`.
- `text[span.start:span.end] == span.text` (offset/text agreement).
- The output is deterministic: running twice on the same input gives identical results.
- The extractor doesn't crash on arbitrary Unicode, empty strings, whitespace-only text, very long lines, or mixed line endings (`\r\n`, `\n`).
- Spans are sorted and either non-overlapping or overlapping only where the schema allows it.
- If any normalization or tokenization step claims to preserve offsets, mapping offsets back to the original text round-trips correctly.

### 3.3 Behavioral tests (`tests/behavioral/`, PROTECTED)
CheckList-style suites, one file per phenomenon. Each case is a short synthetic note snippet plus the expected spans and attributes. Seed each suite with about 10 cases. Humans extend them.

- `test_negation.py` (e.g. "denies chest pain", "no evidence of", "ruled out")
- `test_uncertainty_hypothetical.py` ("rule out MI", "if symptoms worsen")
- `test_experiencer.py` (family history vs. patient)
- `test_temporality.py` (history of, current, planned)
- `test_abbreviations.py` (ambiguous clinical abbreviations)
- `test_section_context.py` (same mention in different sections, e.g. PMH vs. Assessment)

Store cases as data files (YAML/JSONL) under `tests/behavioral/cases/`, with a thin pytest runner that parametrizes over them.

### 3.4 Acceptance tests (`tests/acceptance/`, PROTECTED)
End-to-end tests written from the feature spec **before** implementation, in a separate session, then reviewed and frozen by a human. The agent implements against them and must never edit them.

### 3.5 Differential tests (if more than one implementation exists)
If there is a compiled core with Python bindings, or a reference implementation such as medspaCy running the same rules, add `tests/differential/`. Run both implementations on the dev gold texts and on Hypothesis-generated text, and assert identical spans and attributes. Report the first N disagreements with their context.

### 3.6 Mutation testing (periodic, not on every run)
Configure `mutmut` for `src/`. Add a `make mutation` target. It isn't part of `make check`, but CI runs it weekly or nightly and reports the surviving-mutant rate.

---

## 4. Evaluation harness

### 4.1 Scorer (`eval/score.py`)
- Inputs: a gold corpus directory and system predictions. Pick one standard format and document it, e.g. JSONL with `{doc_id, spans: [{start, end, label, attrs}]}`, or BRAT standoff.
- Outputs, as JSON:
  - micro and macro P/R/F1
  - per-label P/R/F1
  - **strict** (exact boundaries + label) and **lenient** (overlap + label) matching
  - attribute accuracy (negation, certainty, experiencer, temporality) on matched spans
  - counts: TP/FP/FN per label
- Pure Python, deterministic, with its own unit tests covering hand-computed toy cases.

### 4.2 Regression gate (`eval/gate.py`)
- Runs the system on `eval/dev_gold/`, scores it, and compares against `eval/baseline.json`.
- Fails if **any** tracked metric falls below `baseline - tolerance`. Tolerance defaults to 0.005, can be overridden per metric in `baseline.json`, and labels with low support can be given larger tolerances.
- Prints a compact summary table to stdout. On failure, it lists the regressed metrics on stderr and exits with code **2**, so a Stop hook can return the result to the agent.
- Never writes to `baseline.json`. A separate `make rebaseline` target updates the baseline and is meant for humans only; it prints a warning and requires `ALLOW_ORACLE_CHANGE=1`.

### 4.3 Prediction diff (`eval/diff_predictions.py`)
- Compares predictions between two git SHAs (by default `HEAD~1` or `main` vs. the working tree) on the dev set.
- Report: the number of documents changed, then a list of added/removed/changed spans with about 60 characters of context on each side, grouped by label.
- Output as Markdown to `eval/reports/diff_<sha>.md`. In CI, post it as a PR comment or artifact.

### 4.4 Held-out evaluation (CI only)
- The CI job retrieves the held-out gold, runs the scorer, and applies the same gate logic against a separate `heldout_baseline.json`, which is stored in CI and not in the repo.
- Only aggregate metrics are reported: no document text, no per-document errors. That keeps the agent from tuning to the held-out set, and keeps any sensitive text out of logs.

### 4.5 Data hygiene
- `eval/dev_gold/` contains **only synthetic or de-identified text**. Add a check (`make phi-scan` or a pre-commit hook) that scans for obvious identifier patterns (dates of birth, MRN-like numbers, phone numbers, emails) and fails if it finds any.

---

## 5. Makefile targets

```make
lint:        ## ruff check + format check
typecheck:   ## pyright
test-fast:   ## unit + properties + behavioral (target < 60 s)
test:        ## all pytest suites incl. acceptance + differential
eval:        ## run system on dev_gold, write scores JSON
gate:        ## eval + compare to baseline (exit 2 on regression)
diff:        ## prediction diff vs main
check:       ## lint typecheck test gate   <-- the definition of done
mutation:    ## mutmut run (periodic)
rebaseline:  ## HUMAN ONLY; requires ALLOW_ORACLE_CHANGE=1
phi-scan:    ## identifier pattern scan on eval/dev_gold and tests/
```

`make check` must finish in a few minutes at most. If the dev eval is slow, add a `gate-fast` target that runs on a fixed, stratified subset, and use that one in the Stop hook.

---

## 6. Claude Code configuration (`.claude/settings.json`)

### 6.1 Permission deny rules
Block the agent from editing protected paths:

```json
{
  "permissions": {
    "deny": [
      "Edit(eval/dev_gold/**)", "Write(eval/dev_gold/**)",
      "Edit(eval/baseline.json)", "Write(eval/baseline.json)",
      "Edit(eval/score.py)", "Write(eval/score.py)",
      "Edit(eval/gate.py)", "Write(eval/gate.py)",
      "Edit(tests/acceptance/**)", "Write(tests/acceptance/**)",
      "Edit(tests/behavioral/**)", "Write(tests/behavioral/**)",
      "Edit(.claude/**)", "Write(.claude/**)"
    ]
  }
}
```

Note: the deny rules for `.claude/**` should be added **last**, after the human has reviewed the hooks. Check the current Claude Code docs for the exact permission-rule syntax, and confirm the rules block Bash-based writes as well (e.g. `sed -i`, `cp`). If they don't, rely on the pre-commit guard plus CI as the backstop.

### 6.2 Hooks

```json
{
  "hooks": {
    "PostToolUse": [
      {
        "matcher": "Edit|Write|MultiEdit",
        "hooks": [{ "type": "command", "command": "python .claude/hooks/post_edit.py" }]
      }
    ],
    "Stop": [
      {
        "hooks": [{ "type": "command", "command": "python .claude/hooks/stop_gate.py" }]
      }
    ]
  }
}
```

**`post_edit.py`**
- Reads the hook JSON from stdin and gets `tool_input.file_path`.
- If the file is a `.py` file under `src/` or `tests/`: runs `ruff check --fix`, `ruff format`, and `pyright <file>`.
- On errors, prints them to stderr and exits 2, so the agent sees them right away. Otherwise exits 0 quietly.
- Must run in under a few seconds.

**`stop_gate.py`**
- Reads the hook JSON from stdin. **If `stop_hook_active` is true, exits 0** to avoid an infinite loop.
- Skips (exits 0) if `git diff --name-only HEAD` shows no changes under `src/` or `tests/`.
- Runs `make test-fast`, then `make gate-fast` (or `make gate`).
- On failure: prints a concise summary (failing test names, regressed metrics) to stderr and exits 2.
- On success: prints a one-line summary such as `check ok: N tests, dev micro-F1 0.xxx (baseline 0.xxx)`.

---

## 7. Agent instructions (`AGENTS.md` / `CLAUDE.md`)

Write a concise file with these sections:

- **Definition of done:** `make check` passes. Include its summary line in your final message. Don't claim success without that output.
- **Protected paths:** list them, and say they must not be modified, worked around, or duplicated. If a protected test seems wrong, stop and explain why instead of changing it.
- **Forbidden shortcuts:** adding `skip`/`xfail`, loosening assertions, special-casing specific test inputs or doc IDs, catching and suppressing exceptions to make tests pass, editing baselines.
- **Workflow:** work from `PLAN.md` (checklist + acceptance criteria), keep tasks small, update `PLAN.md` as you go, and commit at each point where `make check` is green.
- **When improving extraction quality:** run `make diff` and summarize the prediction churn. Report per-label changes as well as micro-F1.
- **Data:** never paste real clinical text into code, tests, or commit messages.

---

## 8. Review step

Add `.claude/agents/reviewer.md` (a subagent definition), or a documented prompt in `docs/review_prompt.md`, for an independent reviewer that runs in a fresh context. Its instructions:

- Read the diff (`git diff main...HEAD`), `PLAN.md`, and the relevant spec.
- Flag: weakened or deleted tests, special-cased inputs, swallowed exceptions, scope creep, changes to protected paths, metric gains that could come from overfitting dev_gold (e.g. rules keyed to rare literal strings that appear in only one gold doc).
- Output a pass/fail verdict plus a list of findings with file and line.
- Read-only: no edit tools.

---

## 9. CI (`.github/workflows/ci.yml` or equivalent)

On every PR:
1. `make lint typecheck test`
2. `make gate` (dev set)
3. Held-out eval + gate (aggregate metrics only)
4. `make diff` against `main`; upload the report as an artifact or PR comment
5. Fail the PR if it touches protected paths without a `oracle-change` label applied by a human

Nightly or weekly: `make mutation`, full differential tests, and the full dev eval if PRs use a subset.

---

## 10. Acceptance criteria for this setup task

- [ ] `make check` runs end to end on a clean checkout and passes on the current code.
- [ ] `eval/baseline.json` has been generated once from the current code (human-approved commit).
- [ ] Deliberately breaking one extraction rule makes `make gate` exit 2 with a readable regression message.
- [ ] Deliberately introducing an off-by-one offset bug makes a property test fail.
- [ ] Editing a file under `tests/acceptance/` is blocked by the agent permission rules and by the pre-commit guard.
- [ ] The Stop hook blocks the agent from finishing when a fast test fails, and doesn't loop when `stop_hook_active` is set.
- [ ] The PostToolUse hook surfaces a pyright error introduced in an edited file.
- [ ] `make diff` produces a readable Markdown churn report.
- [ ] The CI held-out job is wired up, possibly with a placeholder dataset until real held-out data is connected, and reports only aggregates.
- [ ] `AGENTS.md` exists and states the definition of done, protected paths, and forbidden shortcuts.

## Out of scope

- Creating real gold annotations (humans supply these; seed with a few synthetic examples only).
- Choosing tolerance values per label beyond the defaults (humans tune these after the first baseline).
