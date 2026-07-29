from ht_lead_radar.taxonomy import classify_seniority


def test_seniority_is_title_first_and_excludes_associate_director():
    assert classify_seniority("Associate Director", "Owns a team")[1] is False
    assert classify_seniority("Site Quality Head", "Owns site quality")[1] is True
    assert classify_seniority("Head, TCO", "Owns the function")[1] is True
    assert classify_seniority("算法经理", "向总经理汇报并跨部门协作")[1] is False
    assert classify_seniority("Assistant Head of Quality", "Owns quality")[1] is False
    assert classify_seniority("MVP Product Manager", "Owns the roadmap")[1] is False
    assert classify_seniority("Head of Quality", "Advises the team")[1] is False
    assert classify_seniority("Assistant Vice President", "Owns sales")[1] is False
    assert classify_seniority("Deputy VP", "Owns sales")[1] is False
    assert classify_seniority("VP Assistant", "Supports sales")[1] is False
    assert classify_seniority("SVP, Operations", "Owns operations")[1] is True
    assert classify_seniority("EVP Technology", "Owns technology")[1] is True
    assert classify_seniority("发射技术中心主任", "全面负责中心团队")[1] is True
    assert classify_seniority("发射技术中心副主任", "协助管理中心团队")[1] is False
    assert classify_seniority("发射技术中心主任助理", "支持中心主任")[1] is False
