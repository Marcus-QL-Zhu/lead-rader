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
    assert "发布全部" not in text
    assert "飞书入站审批与一键发布尚未接通" in text
    assert "明确要求 Codex/OpenClaw" in text
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
    assert "职位草稿生成存在失败：退出码 71" in text
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
    assert "--generator direct-llm" in script
    assert "/home/admin/.openclaw/openclaw.json" in script
    assert "/home/admin/.openclaw/agents/main/agent/models.json" in script
    assert 'exit "$talent_draft_status"' in script
    assert "flock -n 9" in script

def test_feishu_company_list_prefers_evidence_bound_minimax_roles(tmp_path):
    report = sample_report(leads=1)
    text = build_summary(
        run_date="2026-07-26",
        direction="具身智能",
        task_exit_code=0,
        report_path=tmp_path / "report.json",
        report=report,
        company_demands=[
            {
                "lead_index": 1,
                "company": "星火机器人",
                "hypotheses": [
                    {"specific_title": "运动控制算法工程化总监"}
                ],
            }
        ],
    )

    assert "MiniMax 已分析：1 家" in text
    assert "运动控制算法工程化总监" in text
    assert "机器人研发总监" not in text
