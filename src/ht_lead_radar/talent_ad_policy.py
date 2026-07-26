"""Prompt policy for turning inferred company demand into specific public ads."""


def advertisement_specificity_policy() -> str:
    return """
额外的具体性硬要求：
- 必须使用前置公司需求分析里的具体业务任务、技术词和能力要求。
- 标题必须带赛道、技术、产品环节或商业任务；禁止只写“研发总监”
  “产品总监”“供应链总监”“硬科技研发总监”等宽泛标题。
- 每条 public_payload 至少自然包含两个 specificity_terms。
- responsibilities 与 must_have 必须能区分具体人才，不能只写团队管理、
  跨部门协同、结果负责等所有总监都适用的表述。
- cities 必须是只含一个城市的数组，例如 ["上海"]，绝不能填多个城市。
""".strip()


__all__ = ["advertisement_specificity_policy"]
