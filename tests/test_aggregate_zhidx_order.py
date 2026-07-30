from scrapling import Selector

from ht_lead_radar.aggregate_adapters.sites.zhidx import ZhidxAdapter


def test_clean_body_preserves_mixed_block_dom_order():
    selector = Selector(
        """
        <div class="post-content">
          <p>paragraph one with enough content</p>
          <h2>conclusion heading</h2>
          <p>paragraph two after the heading</p>
        </div>
        """
    )
    body = ZhidxAdapter._clean_body(selector.css("div.post-content")[0])

    assert body.index("paragraph one") < body.index("conclusion heading")
    assert body.index("conclusion heading") < body.index("paragraph two")
