from dataclasses import dataclass

from ht_lead_radar.relationships import DeepResearchEngine, RelationshipStore


@dataclass
class Result:
    title: str
    snippet: str
    url: str


class FakeProvider:
    def __init__(self):
        self.calls = 0

    def search(self, query, limit=8):
        self.calls += 1
        if query.startswith('红杉中国'):
            return [Result(
                '红杉中国灵巧手赛道投资团队',
                '红杉中国董事总经理赵敏负责灵巧手与机器人投资',
                'https://example.com/investor-team',
            )]
        if '融资' in query:
            return [Result(
                '曦诺未来完成融资，红杉中国领投',
                '红杉中国合伙人张伟表示看好灵巧手赛道',
                'https://example.com/funding',
            )]
        if '创始人' in query:
            return [Result('曦诺未来创始人李明介绍团队', '联合创始人李明曾任某公司CTO', 'https://example.com/founder')]
        if 'HRD' in query:
            return [Result('团队介绍', '人力资源总监王芳负责组织建设', 'https://example.com/hr')]
        return []


def test_deep_research_caches_people_institutions_and_graph(tmp_path):
    provider = FakeProvider()
    store = RelationshipStore(tmp_path / 'relations.sqlite')
    engine = DeepResearchEngine(provider, store)
    report = engine.research('曦诺未来', '灵巧手')

    assert any(item.name == '红杉中国' for item in report.institutions)
    assert any(item.name == '张伟' for item in report.investors)
    assert any(item.name == '李明' for item in report.founders)
    assert any(item.name == '王芳' for item in report.hr_people)
    calls = provider.calls
    cached = engine.research('曦诺未来', '灵巧手')
    assert cached.cached is True
    assert provider.calls == calls
    assert any(item.name == '赵敏' for item in report.investors)
    graph = store.graph('曦诺未来')
    assert graph['edges']
    assert graph['nodes']
    assert any(
        edge['relation'] == 'WORKS_AT'
        for edge in graph['edges']
    )
