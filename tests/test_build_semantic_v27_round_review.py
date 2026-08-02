from __future__ import annotations

from scripts.build_semantic_v27_round_review import build_packet


def _prediction(variant: str, *, failed: int = 0) -> dict:
    return {
        "variant": variant,
        "model": "minimax/MiniMax-M3",
        "code_contract_sha256": "same",
        "selected_keys": ["source:1"],
        "prompt_config": {"prompt_version": f"prompt-{variant}", "secret": "hidden"},
        "prompt_config_sha256": f"hash-{variant}",
        "summary": {"failed_claim_count": failed},
        "results": [
            {
                "key": "source:1",
                "events": [],
                "audit": {
                    "infrastructure_errors": (
                        ["offline"] if variant == "c" else []
                    )
                },
            }
        ],
    }


def _evaluation() -> dict:
    return {
        "host_contract": {
            "uncited_event_count": 0,
            "ungrounded_evidence_event_count": 0,
        },
        "overall": {"unsupported_predicted_event_count": 0},
    }


def test_review_packet_is_blind_and_disqualifies_failed_candidates() -> None:
    packet, mapping = build_packet(
        gold={"cases": [{"key": "source:1", "clean_body": "原文"}]},
        predictions=[
            _prediction("a"),
            _prediction("b", failed=1),
            _prediction("c"),
        ],
        evaluations=[_evaluation(), _evaluation(), _evaluation()],
        seed="round-1",
    )

    assert len(packet["candidates"]) == 3
    assert sum(row["selection_eligible"] for row in packet["candidates"]) == 1
    rendered = str(packet)
    assert "prompt-a" not in rendered
    assert "hidden" not in rendered
    assert {row["variant"] for row in mapping["mapping"].values()} == {
        "a",
        "b",
        "c",
    }
