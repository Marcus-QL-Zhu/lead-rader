"""Registry for dedicated aggregate-source adapters."""

from __future__ import annotations

from typing import Iterable

from .base import AggregateAdapter, AggregateAdapterError


class DedicatedAdapterRegistry:
    def __init__(self, adapters: Iterable[AggregateAdapter] = ()) -> None:
        self._adapters: dict[str, AggregateAdapter] = {}
        self._sources: dict[str, AggregateAdapter] = {}
        for adapter in adapters:
            self.register(adapter)

    @classmethod
    def defaults(cls) -> "DedicatedAdapterRegistry":
        from .sites.beijing_etown import BeijingEtownMajorProjectsAdapter
        from .sites.caict_market import CaictMarketAnalysisAdapter
        from .sites.ccid_report import CcidReportCommentaryAdapter
        from .sites.cena import CenaAdapter
        from .sites.cls import ClsAdapter
        from .sites.cnstock import CnstockCompanyChannelAdapter
        from .sites.cyzone import CyzoneAdapter
        from .sites.fusion import FusionIndustryMediaAdapter, IterChinaAdapter
        from .sites.jazzyear import JazzyearAdapter
        from .sites.jazzyear_research import JazzyearResearchAdapter
        from .sites.jiqizhixin import JiqizhixinAdapter
        from .sites.kr36 import Kr36Adapter
        from .sites.lieyun import LieyunAdapter
        from .sites.miit import MiitAdapter
        from .sites.nbd_vcpe import NbdVcpeWeeklyAdapter
        from .sites.pedaily import PedailyAdapter
        from .sites.shenzhen_semiconductor import (
            ShenzhenSemiconductorAssociationAdapter,
        )
        from .sites.shenzhen_sasac import ShenzhenSasacAppointmentsAdapter
        from .sites.shanghai_fgw_annual_plan import ShanghaiFgwAnnualPlanAdapter
        from .sites.stcn import StcnAdapter
        from .sites.suzhou_robot_association import SuzhouRobotAssociationAdapter
        from .sites.vbdata import VbdataAdapter
        from .sites.zhidx import ZhidxAdapter

        return cls(
            (
                Kr36Adapter(),
                PedailyAdapter(),
                CyzoneAdapter(),
                LieyunAdapter(),
                VbdataAdapter(),
                JazzyearAdapter(),
                ZhidxAdapter(),
                ClsAdapter(),
                CnstockCompanyChannelAdapter(),
                StcnAdapter(),
                MiitAdapter(),
                IterChinaAdapter(),
                FusionIndustryMediaAdapter(),
                SuzhouRobotAssociationAdapter(),
                BeijingEtownMajorProjectsAdapter(),
                ShenzhenSemiconductorAssociationAdapter(),
                ShenzhenSasacAppointmentsAdapter(),
                CaictMarketAnalysisAdapter(),
                CcidReportCommentaryAdapter(),
                CenaAdapter(),
                JiqizhixinAdapter(),
                JazzyearResearchAdapter(),
                ShanghaiFgwAnnualPlanAdapter(),
                NbdVcpeWeeklyAdapter(),
            )
        )

    def register(self, adapter: AggregateAdapter) -> None:
        if not adapter.adapter_id:
            raise AggregateAdapterError("adapter_id cannot be empty")
        if adapter.adapter_id in self._adapters:
            raise AggregateAdapterError(f"duplicate adapter: {adapter.adapter_id}")
        for channel in adapter.channels:
            if channel.source_id in self._sources:
                raise AggregateAdapterError(
                    f"duplicate dedicated source: {channel.source_id}"
                )
        self._adapters[adapter.adapter_id] = adapter
        for channel in adapter.channels:
            self._sources[channel.source_id] = adapter

    def for_source(self, source_id: str) -> AggregateAdapter | None:
        return self._sources.get(source_id)

    @property
    def source_ids(self) -> frozenset[str]:
        return frozenset(self._sources)

    @property
    def adapter_ids(self) -> tuple[str, ...]:
        return tuple(sorted(self._adapters))
