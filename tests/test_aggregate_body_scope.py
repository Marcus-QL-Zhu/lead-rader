from ht_lead_radar.aggregate_adapters.body_scope import (
    LONG_ARTICLE_WINDOW_CHARS,
    classify_long_article,
    clean_semantic_body_scope,
    mask_semantic_body_scope,
    scope_long_article,
)


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


def test_mask_scope_preserves_offsets_and_line_boundaries() -> None:
    body = "甲公司完成A轮融资。\n相关阅读：乙公司完成B轮融资。\n丙公司发布新品。"

    masked = mask_semantic_body_scope(body)

    assert len(masked) == len(body)
    assert masked.count("\n") == body.count("\n")
    assert masked.index("丙公司发布新品") == body.index("丙公司发布新品")
    assert "乙公司完成B轮融资" not in masked

def test_long_single_event_feature_masks_expansion_after_two_thousand_chars():
    body = (
        "九科信息针对国央企及大型企业的严苛需求，推出bit-Agent企业级智能自动化平台。"
        + "这是一段围绕行业背景、产品机制和商业意义的扩写。" * 180
    )

    scoped, decision = scope_long_article(body, title="2026企业级智能体白皮书")

    assert decision.mode == "single_event_expansion"
    assert decision.prefix_has_concrete_event is True
    assert decision.semantic_chars == LONG_ARTICLE_WINDOW_CHARS
    assert len(scoped) == len(body)
    assert scoped[:LONG_ARTICLE_WINDOW_CHARS] == body[:LONG_ARTICLE_WINDOW_CHARS]
    assert scoped[LONG_ARTICLE_WINDOW_CHARS:].strip() == ""


def test_long_digest_is_not_blindly_truncated():
    body = (
        "字节跳动发布新模型。腾讯上线新工具。蚂蚁集团完成产品升级。"
        + "行业分析与后续解读。" * 240
    )

    scoped, decision = scope_long_article(body, title="AI周报")

    assert decision.mode == "multi_event_digest"
    assert scoped == body
    assert classify_long_article(body, document_type="multi_company_bulletin").mode == (
        "multi_event_digest"
    )


def test_long_interview_without_concrete_lead_event_is_skipped():
    body = (
        "记者：您如何看待行业未来？受访者：我们认为仍需持续观察。"
        "记者：最大的挑战是什么？受访者：组织能力和执行力。"
        + "观点与背景补充。" * 280
    )

    scoped, decision = scope_long_article(body, title="专访：行业观察")

    assert decision.mode == "skip_low_value"
    assert decision.prefix_has_concrete_event is False
    assert len(scoped) == len(body)
    assert scoped.strip() == ""


def test_long_policy_commentary_without_company_operation_is_skipped():
    body = (
        "山东省人大常委会副主任、党组副书记，烟台市委书记发表讲话，强调因地制宜发展新质生产力，"
        "打造具有烟台特色的先进制造业强市，推进产业升级和政策落实。"
        * 32
    )

    scoped, decision = scope_long_article(
        body,
        title="新型工业化丨烟台市委书记：因地制宜发展新质生产力",
    )

    assert decision.mode == "skip_low_value"
    assert decision.prefix_has_concrete_event is False
    assert len(scoped) == len(body)
    assert scoped.strip() == ""
