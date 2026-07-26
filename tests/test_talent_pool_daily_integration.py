from pathlib import Path

from ht_lead_radar.feishu_notify import build_summary, load_talent_drafts
from ht_lead_radar.talent_pool import generate_draft_bundle
from ht_lead_radar.talent_pool_store import TalentPoolStore
from test_talent_pool import sample_report


def test_feishu_summary_includes_drafts_and_exact_commands(tmp_path):
    report = sample_report()
    bundle = generate_draft_bundle(report)
    database = tmp_path / "talent.sqlite"
    TalentPoolStore(database).save_bundle(bundle.to_dict())
    drafts = load_talent_drafts(
        database, run_date=bundle.run_date, direction=bundle.direction
    )
    text = build_summary(
        run_date=bundle.run_date,
        direction=bundle.direction,
        task_exit_code=0,
        report_path=tmp_path / "report.json",
        report=report,
        talent_drafts=drafts,
    )
    assert "今日建议发布的人才蓄水职位（共 5 个）" in text
    assert bundle.drafts[0].draft_id in text
    assert "发布全部" in text
    assert "发布 1,3,5" in text
    assert "跳过全部" in text
    assert "查看 2 的完整广告 JSON" in text
    assert "星火机器人" in text  # market lead section remains internal to user


def test_feishu_summary_surfaces_generation_failure_without_stale_drafts(tmp_path):
    text = build_summary(
        run_date="2026-07-26",
        direction="具身智能",
        task_exit_code=0,
        report_path=tmp_path / "report.json",
        report=sample_report(),
        talent_drafts=[],
        talent_generation_error="退出码 71",
    )
    assert "人才蓄水草稿生成失败：退出码 71" in text
    assert "今日建议发布的人才蓄水职位" not in text


def test_daily_launcher_generates_before_the_single_feishu_notification():
    script = (
        Path(__file__).parents[1] / "scripts" / "run_daily_fixed_sources.sh"
    ).read_text(encoding="utf-8")
    generator = script.index("scripts/generate_talent_pool_drafts.py")
    notifier = script.index("scripts/send_daily_feishu_summary.py")
    assert generator < notifier
    assert script.count("scripts/send_daily_feishu_summary.py") == 1
    assert "--talent-state-db data/talent-pool.sqlite" in script
    assert '--talent-draft-exit-code "$talent_draft_status"' in script
    assert "--generator openclaw" in script
    assert "/home/admin/.local/share/pnpm/openclaw" in script
    assert 'exit "$talent_draft_status"' in script
