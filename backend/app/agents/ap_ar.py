from __future__ import annotations

from collections import defaultdict
from statistics import mean

from sqlalchemy import select

from app.agents.base import AgentContext, BaseAgent
from app.agents.hypotheses import investigate_ap_invoice
from app.agents.records import memory_for, remember
from app.db.models import ARInvoice, BankTransaction


class APARAgent(BaseAgent):
    name = "ap_ar"
    display_name = "AP / AR"

    async def run(self) -> None:
        self.started("Verify purchase orders, receipts, invoices and payment timing")
        for invoice in self.call_tool("list_ap_invoices", period_id=self.ctx.period_id):
            result = await investigate_ap_invoice(self.ctx, self, invoice["id"])
            remember(self.ctx, result)
        self.finished()


def observe_collections(ctx: AgentContext) -> None:
    memory = memory_for(ctx)
    days: dict[str, list[int]] = defaultdict(list)
    with ctx.session() as session:
        transactions = {
            item.id: item for item in session.scalars(
                select(BankTransaction).where(BankTransaction.period_id == ctx.period_id, BankTransaction.reconciled.is_(True))
            )
        }
        for invoice in session.scalars(select(ARInvoice).where(ARInvoice.period_id <= ctx.period_id)):
            dates = [transactions[tid].date for tid in invoice.matched_bank_txn_ids if tid in transactions]
            if dates and invoice.paid_amount >= invoice.amount - 0.01:
                elapsed = (max(dates) - invoice.date).days
                if elapsed >= 0:
                    days[invoice.customer_id].append(elapsed)
    for customer, samples in days.items():
        memory.record_observation("customer", customer, "avg_days_to_pay", round(mean(samples), 2), ctx.period_id)
        ctx.emit("ap_ar", "memory.write", f"Observed collections for {customer}", samples=len(samples), avg_days_to_pay=mean(samples))


async def run(ctx: AgentContext) -> None:
    await APARAgent(ctx).run()
