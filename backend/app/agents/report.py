from __future__ import annotations

from datetime import datetime, timezone

from app.agents.base import AgentContext, BaseAgent
from app.agents.records import memory_for, open_exceptions
from app.schemas import ChecklistItem, CloseReport, ReportSection, RunMetrics


class ReportAgent(BaseAgent):
    name = "report"
    display_name = "Controller report"

    async def run(self) -> CloseReport:
        self.started()
        memory = memory_for(self.ctx)
        period = self.call_tool("get_period", period_id=self.ctx.period_id)
        cash = self.call_tool("period_cash_summary", period_id=self.ctx.period_id)
        ar = self.call_tool("ar_aging", period_id=self.ctx.period_id)
        ap = self.call_tool("ap_open_summary", period_id=self.ctx.period_id)
        exceptions = open_exceptions(self.ctx)
        open_items = [{"id": node.id, **node.props} for node in exceptions]
        metrics = self.ctx.extra.get("metrics")
        if metrics is not None and not isinstance(metrics, RunMetrics):
            metrics = RunMetrics.model_validate(metrics)
        checklist = self.ctx.extra.get("checklist")
        if checklist is None:
            nodes = memory.store.find_nodes("Observation", key="close_checklist", scope_id=self.ctx.period_id)
            checklist = [ChecklistItem.model_validate(item) for item in nodes[0].props["value"]["items"]] if nodes else []
        sections = [
            ReportSection(title="Cash and reconciliation", body=f"{cash.get('reconciled', 0)} reconciled bank entries; ending cash {cash.get('ending_cash', 0):,.2f}.", data=cash),
            ReportSection(title="Receivables and payables", body=f"Open AR {ar.get('total', 0):,.2f}; open AP {ap.get('total', 0):,.2f}.", data={"ar": ar, "ap": ap}),
            ReportSection(title="Controller actions", body=f"{len(open_items)} unresolved exceptions remain.", evidence_ids=[node.id for node in exceptions], data={"open_count": len(open_items)}),
            ReportSection(title="Close controls", body="Unbilled receipt accruals are proposals pending controller review.", data={"checklist": [item.model_dump() for item in checklist]}),
        ]
        summary = f"{self.ctx.period_id}: {period['status']}; {len(open_items)} exceptions require review."
        if self.ctx.brain:
            summary = await self.ctx.brain.narrate_report({
                "period_id": self.ctx.period_id,
                "metrics": metrics.model_dump() if metrics else {},
                "open_items": open_items,
                "sections": [section.model_dump() for section in sections],
            })
        result = CloseReport(
            period_id=self.ctx.period_id, run_id=self.ctx.run_id, status=period["status"],
            summary=summary, sections=sections, checklist=checklist, open_items=open_items,
            metrics=metrics, generated_at=datetime.now(timezone.utc).isoformat(),
        )
        memory.record_observation("global", self.ctx.period_id, "report", result.model_dump(mode="json"), self.ctx.period_id)
        self.emit("report.ready", "Close report ready", report=result.model_dump(mode="json"))
        self.finished()
        return result


async def build_report(ctx: AgentContext) -> CloseReport:
    return await ReportAgent(ctx).run()
