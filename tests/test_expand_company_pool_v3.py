from scripts.expand_company_pool_v3 import expand_pool


def test_expand_pool_preserves_base_splits_and_adds_balanced_unseen_companies():
    base = {
        "companies": [
            {
                "company": "已有公司",
                "company_type": "listed",
                "sector": "semiconductor",
                "split": "test",
            }
        ]
    }
    result = expand_pool(base, seed="fixed")
    assert result["companies"][0]["split"] == "test"
    additions = result["companies"][1:]
    assert len(additions) == 60
    assert len({row["company"] for row in additions}) == 60
    assert sum(row["split"] == "train" for row in additions) == 36
    assert sum(row["split"] == "calibration" for row in additions) == 6
    assert sum(row["split"] == "test" for row in additions) == 18
