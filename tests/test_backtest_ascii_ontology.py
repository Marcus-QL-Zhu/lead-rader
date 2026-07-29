from ht_lead_radar.backtest import effective_role_family, role_family_set


def test_director_does_not_accidentally_match_cto_substring():
    assert (
        effective_role_family(
            "Director, Customer Operations",
            "Owns customer operations and service commitments.",
        )
        != "research_development"
    )


def test_application_management_uses_application_solutions_family():
    assert (
        effective_role_family(
            "Director Application Management",
            "Own the application lifecycle and go-to-market execution.",
        )
        == "application_solutions"
    )
    assert "application_solutions" in role_family_set(
        "系统解决方案总监",
        "负责客户应用创新与系统方案落地",
    )


def test_growth_excellence_is_commercialization_not_generic_management():
    assert (
        effective_role_family(
            "Head of Growth Excellence (m/f/d)",
            "Leads pricing, commercial activation and go-to-market strategy.",
        )
        == "commercialization"
    )


def test_english_sales_and_legal_titles_use_bilingual_ontology():
    assert (
        effective_role_family(
            "VP, Sales & Business Development, China",
            "Leads sales and business development for Chinese OEM accounts.",
        )
        == "sales_accounts"
    )
    assert (
        effective_role_family(
            "China Head of Legal",
            "Leads Greater China legal and compliance.",
        )
        == "legal"
    )
