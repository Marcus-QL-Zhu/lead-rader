from pathlib import Path


FIXTURE = "evaluation/production-regression-20260818-31"


def _artifact_gate() -> str:
    workflow = (
        Path(__file__).parents[1] / ".github" / "workflows" / "ci.yml"
    ).read_text(encoding="utf-8")
    start = workflow.index("      - name: Frozen production regression artifact gate")
    end = workflow.index("\n      - ", start + 1)
    return workflow[start:end]


def test_ci_artifact_gate_safely_skips_only_the_absent_source_fixture():
    workflow = (
        Path(__file__).parents[1] / ".github" / "workflows" / "ci.yml"
    ).read_text(encoding="utf-8")
    gate = _artifact_gate()

    assert 'python-version: ["3.10", "3.11", "3.12"]' in workflow
    assert "fetch-depth: 0" in workflow
    assert workflow.count("if: matrix.python-version == '3.11'") == 2
    assert "set -euo pipefail" in gate
    assert f'fixture="{FIXTURE}"' in gate
    assert 'if [[ -e "$fixture" || -L "$fixture" ]]; then' in gate
    assert 'git rev-list --all --objects -- "$fixture"' in gate
    assert 'if [[ -n "$history_objects" ]]; then' in gate
    assert "Committed frozen artifact was deleted" in gate
    assert "explicitly not deployable" in gate
    assert "Artifact commit B is required for deployment" in gate
    assert "continue-on-error" not in gate
    assert "|| true" not in gate


def test_ci_artifact_gate_enforces_validator_and_both_documented_schemas():
    gate = _artifact_gate()

    assert gate.count("scripts/export_production_regression_set.py") == 1
    assert '--output-dir "$fixture"' in gate
    assert gate.count("--validate-only") == 1
    assert "docs/schemas/production-regression-set-v2.schema.json" in gate
    assert "docs/schemas/production-regression-day-v2.schema.json" in gate
    assert 'for daily in "$fixture"/20??-??-??.json; do' in gate
    assert "Frozen production regression artifact: validated." in gate
