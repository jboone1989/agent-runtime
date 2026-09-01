"""Evidence persistence-boundary hardening beyond single-event bounds."""
from __future__ import annotations

import json

import pytest

from agent_runtime.execution.evidence import (
    MAX_DEPTH,
    MAX_DICT_ITEMS,
    MAX_LIST_ITEMS,
    DEPTH_MARK,
    REDACTION_MARK,
    bound_execution_evidence,
)


def _redacted(text: str) -> str:
    return bound_execution_evidence({"verification_summary": text})["verification_summary"]


@pytest.mark.parametrize(
    "text, secret, shape",
    [
        (
            'export GITHUB_TOKEN=ghp_1234567890abcdef && gh repo list',
            "ghp_1234567890abcdef",
            "GITHUB_TOKEN",
        ),
        (
            "AWS_SESSION_TOKEN=FwoGZXIvYXdzEBB12345 terraform plan",
            "FwoGZXIvYXdzEBB12345",
            "AWS_SESSION_TOKEN",
        ),
        ("cli deploy --token abc12345 prod", "abc12345", "--token"),
        ("cli deploy --password hunter2hunter2", "hunter2hunter2", "--password"),
        ('curl -H "Authorization: Bearer sk-live-abcd" https://api.example.com', "sk-live-abcd", "Authorization: Bearer"),
        ("https://ci.example.com/hook?token=tok_12345&run=7", "tok_12345", "?token="),
    ],
)
def test_common_credential_shapes_are_masked_but_stay_auditable(
    text, secret, shape
) -> None:
    sanitized = _redacted(text)
    assert secret not in sanitized
    assert REDACTION_MARK in sanitized
    assert shape in sanitized, "credential shape stays auditable"


def test_nested_list_items_are_capped_with_sentinel() -> None:
    changes = [{"path": f"f{index}.py"} for index in range(MAX_LIST_ITEMS + 50)]
    out = bound_execution_evidence(
        {
            "backend_events": [
                {"kind": "backend.files_changed", "payload": {"changes": changes}}
            ]
        }
    )
    recorded = out["backend_events"][0]["payload"]["changes"]
    assert len(recorded) == MAX_LIST_ITEMS + 1
    assert recorded[-1] == f"...[50 more items truncated]"
    assert recorded[MAX_LIST_ITEMS - 1]["path"] == f"f{MAX_LIST_ITEMS - 1}.py"
    assert f"f{MAX_LIST_ITEMS}.py" not in json.dumps(out)


def test_wide_dicts_and_deep_nesting_are_bounded() -> None:
    wide = {f"k{index}": index for index in range(MAX_DICT_ITEMS + 10)}
    deep: dict = {}
    cursor = deep
    for _level in range(MAX_DEPTH + 5):
        cursor["child"] = {}
        cursor = cursor["child"]
    cursor["leaf"] = "bottom-secret-value"

    out = bound_execution_evidence({"wide": wide, "deep": deep})
    assert len(out["wide"]) == MAX_DICT_ITEMS + 1
    assert out["wide"]["_truncated_items"] == 10
    serialized = json.dumps(out)
    assert "depth limit reached" in serialized
    assert "bottom-secret-value" not in serialized


def test_total_node_budget_bounds_deep_branching() -> None:
    # 64 x 64 x 64 nested lists: every single collection stays under the
    # per-list cap, yet the traversal far exceeds the total node budget.
    def _matrix(depth: int) -> list:
        if depth == 0:
            return list(range(64))
        return [_matrix(depth - 1) for _ in range(64)]

    out = bound_execution_evidence(
        {
            "backend_events": [
                {"kind": "backend.files_changed", "payload": {"grid": _matrix(2)}}
            ]
        }
    )
    serialized = json.dumps(out)
    assert "evidence budget exhausted" in serialized
    assert len(serialized) < 100_000
