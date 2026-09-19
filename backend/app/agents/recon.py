from __future__ import annotations

from app.agents.base import AgentContext, BaseAgent
from app.agents.hypotheses import investigate_bank_txn
from app.agents.records import remember


class ReconAgent(BaseAgent):
    name = "recon"
    display_name = "Bank reconciliation"

    async def run(self) -> None:
        self.started("Investigate statement evidence and learned precedents")
        for transaction in self.call_tool("list_unreconciled_bank_txns", period_id=self.ctx.period_id):
            result = await investigate_bank_txn(self.ctx, self, transaction["id"])
            remember(self.ctx, result)
        self.finished()


async def run(ctx: AgentContext) -> None:
    await ReconAgent(ctx).run()
