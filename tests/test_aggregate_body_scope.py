from ht_lead_radar.aggregate_adapters.body_scope import clean_semantic_body_scope


def test_related_reading_spans_are_removed_without_truncating_later_prose():
    body = (
        "第一段是正文。"
        "（延展阅读：估值90亿英镑，体育版LVMH启动融资）"
        "第二段仍是正文并宣布完成1亿元A轮融资。"
        "（相关阅读：旧公司完成天使轮融资）\n"
        "推荐阅读：另一家公司启动Pre-IPO轮融资\n"
        "第三段也是正文。"
    )

    scoped = clean_semantic_body_scope(body)

    assert "体育版LVMH" not in scoped
    assert "旧公司" not in scoped
    assert "另一家公司" not in scoped
    assert "第二段仍是正文并宣布完成1亿元A轮融资" in scoped
    assert scoped.endswith("第三段也是正文。")


def test_unbounded_single_line_heading_is_left_intact_conservatively():
    body = "推荐阅读：标题没有边界 随后可能仍是正文"

    assert clean_semantic_body_scope(body) == body


def test_flattened_reference_title_is_removed_but_following_prose_survives():
    body = (
        "正文第一句。"
        "相关阅读：旧公司完成天使轮融资。"
        "正文第二句宣布新公司完成A轮融资。"
    )

    scoped = clean_semantic_body_scope(body)

    assert "旧公司完成天使轮融资" not in scoped
    assert "正文第一句" in scoped
    assert "正文第二句宣布新公司完成A轮融资" in scoped
