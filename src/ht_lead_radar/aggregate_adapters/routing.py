"""Broad deterministic routing cues for high-frequency aggregate streams.

These cues decide which already-indexed items merit detail and semantic
processing. They are never used as evidence and never remove the raw index.
"""

from __future__ import annotations

import re


HARDTECH_CUE = re.compile(
    r"\u79d1\u6280|\u7535\u5b50|\u82af\u7247|\u534a\u5bfc\u4f53|"
    r"\u673a\u5668\u4eba|\u667a\u80fd|\u7b97\u529b|\u6570\u636e|"
    r"\u8f6f\u4ef6|\u7b97\u6cd5|\u4eba\u5de5\u667a\u80fd|AI\b|"
    r"\u822a\u7a7a|\u822a\u5929|\u536b\u661f|\u706b\u7bad|"
    r"\u65e0\u4eba\u673a|\u65b0\u80fd\u6e90|\u7535\u6c60|"
    r"\u7535\u673a|\u50a8\u80fd|\u6c22\u80fd|\u5149\u4f0f|"
    r"\u98ce\u7535|\u6838\u7535|\u80fd\u6e90|\u6750\u6599|"
    r"\u88c5\u5907|\u4f20\u611f\u5668|\u5149\u7535|\u6fc0\u5149|"
    r"\u81ea\u52a8\u5316|\u5de5\u4e1a|\u6c7d\u8f66|\u673a\u68b0|"
    r"\u673a\u7535|\u7535\u6c14|\u7535\u529b|\u5236\u9020|"
    r"\u5316\u5de5|\u4eea\u5668|\u901a\u4fe1|\u7f51\u7edc|"
    r"\u4e91\u8ba1\u7b97|\u8ba1\u7b97\u673a|\u6570\u63a7|"
    r"\u751f\u7269|\u533b\u836f|\u533b\u7597|\u57fa\u56e0|"
    r"\u8111\u673a|\u6838\u805a\u53d8|\u91cf\u5b50",
    re.I,
)


__all__ = ["HARDTECH_CUE"]
