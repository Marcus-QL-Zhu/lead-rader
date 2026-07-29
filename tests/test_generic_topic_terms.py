from ht_lead_radar.source_pack_collector import _topic_terms
from ht_lead_radar.source_packs import load_source_packs


def test_compound_arbitrary_topic_is_split_into_reusable_discovery_terms():
    terms = _topic_terms(load_source_packs(), "动力电池与储能")

    assert "动力电池" in terms
    assert "储能" in terms
