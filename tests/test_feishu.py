from datetime import date

from ht_lead_radar.collectors import load_demo_fixture
from ht_lead_radar.feishu import ProjectionState, stable_company_id, sync_leads
from ht_lead_radar.pipeline import build_leads


def test_projection_is_incremental_and_emits_deactivation(tmp_path):
    evidence, metadata = load_demo_fixture('灵巧手')
    leads = build_leads('灵巧手', evidence, metadata, as_of=date(2026, 7, 24))
    state = ProjectionState(tmp_path / 'projection.sqlite')

    first = sync_leads(leads, state, dry_run_path=tmp_path / 'first.json')
    assert len(first) == len(leads)
    assert {item.operation for item in first} == {'create'}
    for item in first:
        state.commit(item, f'rec-{item.company_id}')

    assert sync_leads(leads, state) == []
    reduced = leads[:1]
    changes = sync_leads(reduced, state)
    assert sum(item.operation == 'deactivate' for item in changes) == len(leads) - 1


def test_company_id_is_whitespace_and_case_stable():
    assert stable_company_id(' Example Tech ') == stable_company_id('exampletech')
