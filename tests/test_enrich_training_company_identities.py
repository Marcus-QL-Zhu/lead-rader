from scripts.enrich_training_company_identities import enrich_pool


def test_enrichment_is_stable_and_harmonizes_corporate_family_split() -> None:
    pool = {
        "schema_version": 1,
        "companies": [
            {
                "company": "\u897f\u95e8\u5b50\uff08\u4e2d\u56fd\uff09",
                "split": "train",
            },
            {
                "company": "\u897f\u95e8\u5b50\u533b\u7597\uff08\u4e2d\u56fd\uff09",
                "split": "test",
            },
            {"company": "\u7532\u8fb0\u79d1\u6280", "split": "calibration"},
        ],
    }

    first = enrich_pool(pool)
    second = enrich_pool(pool)

    assert first == second
    siemens = first["companies"][:2]
    assert {row["split"] for row in siemens} == {"test"}
    assert len({row["corporate_family_id"] for row in siemens}) == 1
    assert len({row["canonical_company_id"] for row in siemens}) == 2
    assert first["companies"][2]["split"] == "calibration"
