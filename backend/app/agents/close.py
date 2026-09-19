from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select

from app.agents.base import AgentContext, BaseAgent
from app.agents.records import memory_for, open_exceptions
from app.db.models import APInvoice, BankTransaction, GoodsReceipt, LedgerEntry, Period, PurchaseOrder
from app.schemas import ChecklistItem


class CloseAgent(BaseAgent):
    name = "close"
    display_name = "Period close"

    async def run(self) -> list[ChecklistItem]:
        self.started()
        pending = open_exceptions(self.ctx)
        with self.ctx.session() as session:
            period = session.get(Period, self.ctx.period_id)
            if period is None:
                raise ValueError(f"Unknown period {self.ctx.period_id}")
            bank = session.scalars(select(BankTransaction).where(BankTransaction.period_id == self.ctx.period_id)).all()
            entries = session.scalars(select(LedgerEntry).where(LedgerEntry.period_id == self.ctx.period_id)).all()
            imbalance = round(sum(entry.debit - entry.credit for entry in entries), 2)
            missing = sum(not transaction.reconciled for transaction in bank)
            invoiced = set(session.scalars(select(APInvoice.po_id).where(APInvoice.date <= period.end_date, APInvoice.status != "void")))
            receipts = session.scalars(select(GoodsReceipt).where(GoodsReceipt.date <= period.end_date)).all()
            unbilled: dict[str, float] = {}
            for receipt in receipts:
                if receipt.po_id not in invoiced:
                    po = session.get(PurchaseOrder, receipt.po_id)
                    if po and po.period_id <= period.id:
                        unbilled[receipt.po_id] = unbilled.get(receipt.po_id, 0) + receipt.amount
            checklist = [
                ChecklistItem(name="Bank reconciliation", status="blocked" if missing else "done", count=missing, detail=f"{missing} statement entries remain unreconciled"),
                ChecklistItem(name="AP / AR controls and audit", status="blocked" if pending else "done", count=len(pending), detail=f"{len(pending)} exceptions require action"),
                ChecklistItem(name="Trial balance", status="done" if abs(imbalance) <= 0.01 else "blocked", detail=f"Debit less credit: {imbalance:.2f}"),
                ChecklistItem(name="Unbilled receipts / accruals", status="warning" if unbilled else "done", count=len(unbilled), detail=f"{sum(unbilled.values()):,.2f} in received goods without invoices; proposed accruals require controller review"),
            ]
            period.status = "pending_review" if any(item.status != "done" for item in checklist) else "closed"
            period.closed_at = datetime.now(timezone.utc) if period.status == "closed" else None
        memory_for(self.ctx).record_observation("global", self.ctx.period_id, "close_checklist", {
            "items": [item.model_dump() for item in checklist], "proposed_accruals": unbilled,
        }, self.ctx.period_id)
        self.ctx.extra["checklist"] = checklist
        self.emit("close.checklist", "Close checklist prepared", checklist=[item.model_dump() for item in checklist])
        self.finished()
        return checklist


async def run(ctx: AgentContext) -> list[ChecklistItem]:
    return await CloseAgent(ctx).run()
