from __future__ import annotations

from datetime import date, timedelta
from statistics import mean

from sqlalchemy import select

from app.agents.base import AgentContext, BaseAgent
from app.agents.records import memory_for
from app.db.models import APInvoice, ARInvoice, BankTransaction, Customer, PayrollRun, Period, Vendor
from app.schemas import ForecastView, ForecastWeek


class ForecastAgent(BaseAgent):
    name = "forecast"
    display_name = "13-week cash forecast"

    async def run(self) -> ForecastView:
        self.started()
        memory = memory_for(self.ctx)
        observations = memory.store.find_nodes("Observation", key="avg_days_to_pay")
        latest = {}
        for observation in sorted(observations, key=lambda node: node.props["period_id"]):
            if observation.props["period_id"] <= self.ctx.period_id:
                latest[observation.props["scope_id"]] = observation
        rules = [
            rule for rule in memory.active_rules()
            if (rule.props.get("learned_in_period") or "") <= self.ctx.period_id
        ]
        assumptions: list[dict] = []
        used: set[str] = set()
        adjustments = 0.0
        weekly = [{"ar_collections": 0.0, "ap_payments": 0.0, "payroll": 0.0, "fees": 0.0} for _ in range(13)]

        with self.ctx.session() as session:
            period = session.get(Period, self.ctx.period_id)
            if period is None:
                raise ValueError(f"Unknown period {self.ctx.period_id}")
            start = period.end_date + timedelta(days=1)
            bank = session.scalars(select(BankTransaction).where(BankTransaction.period_id == period.id)).all()
            opening = round(period.opening_cash + sum(item.amount for item in bank), 2)
            processors = list(session.scalars(select(Vendor).where(Vendor.is_payment_processor.is_(True))))
            customers = {customer.id: customer for customer in session.scalars(select(Customer))}

            def week_index(when: date) -> int:
                return max(0, (when - start).days // 7)

            def note(node_id: str, description: str, value: object, source: str) -> None:
                if node_id not in used:
                    assumptions.append({"source": source, "node_id": node_id, "description": description, "value": value})
                    used.add(node_id)

            for invoice in session.scalars(select(ARInvoice).where(ARInvoice.date <= period.end_date, ARInvoice.status.in_(["open", "partial", "exception"]))):
                amount = round(max(0, invoice.amount - invoice.paid_amount), 2)
                expected = invoice.due_date
                customer_observation = latest.get(invoice.customer_id)
                if customer_observation:
                    expected = invoice.date + timedelta(days=round(float(customer_observation.props["value"])))
                    note(customer_observation.id, f"Observed collection delay for {invoice.customer_id}", customer_observation.props["value"], "observation")
                index = week_index(expected)
                if index >= 13:
                    continue
                weekly[index]["ar_collections"] += amount
                customer = customers[invoice.customer_id]
                processor_ids = {vendor.id for vendor in processors if customer.channel and customer.channel.lower() in (vendor.name + " " + vendor.id).lower()}
                for rule in rules:
                    if rule.props["scope_type"] != "global" and rule.props["scope_id"] not in {invoice.customer_id, *processor_ids}:
                        continue
                    params = rule.props["params"]
                    if params.get("bank_type") or params.get("cash_direction", "in") != "in":
                        continue
                    if rule.props["pattern_type"] in {"percentage_fee", "early_pay_discount"} and params.get("direction", "deduct") == "deduct":
                        fee = round(amount * float(params.get("rate", 0)), 2)
                        weekly[index]["fees"] += fee
                        adjustments -= fee
                        note(rule.id, rule.props["description"], params, "rule")
                        break
            for ap_invoice in session.scalars(select(APInvoice).where(APInvoice.date <= period.end_date, APInvoice.status.in_(["open", "approved"]))):
                amount = max(0, ap_invoice.amount - ap_invoice.paid_amount) * (ap_invoice.fx_rate or 1)
                index = week_index(ap_invoice.due_date)
                if index >= 13:
                    continue
                weekly[index]["ap_payments"] += amount
                for rule in rules:
                    if rule.props["scope_type"] != "global" and (rule.props["scope_type"] != "vendor" or rule.props["scope_id"] != ap_invoice.vendor_id):
                        continue
                    params = rule.props["params"]
                    if params.get("bank_type") or params.get("cash_direction", "out") != "out":
                        continue
                    if rule.props["pattern_type"] == "percentage_fee" and params.get("direction") == "add":
                        fee = round(amount * float(params.get("rate", 0)), 2)
                        weekly[index]["fees"] += fee
                        adjustments -= fee
                        note(rule.id, rule.props["description"], params, "rule")
                        break
            payroll = session.scalars(select(PayrollRun).where(PayrollRun.period_id == period.id).order_by(PayrollRun.pay_date)).all()
            if payroll:
                interval = max(1, round(mean([(b.pay_date - a.pay_date).days for a, b in zip(payroll, payroll[1:])]))) if len(payroll) > 1 else 14
                amount = mean([pay.net + pay.employer_taxes for pay in payroll])
                payday = payroll[-1].pay_date + timedelta(days=interval)
                while payday < start:
                    payday += timedelta(days=interval)
                while week_index(payday) < 13:
                    weekly[week_index(payday)]["payroll"] += amount
                    payday += timedelta(days=interval)
                assumptions.append({"source": "ledger", "description": f"Repeat observed payroll every {interval} days", "value": round(amount, 2)})
        assumptions.append({"source": "default", "description": "Existing open invoices only; due dates unless observed collection delays exist. Held invoices excluded. No uncontracted revenue or expenses.", "value": None})
        weeks = []
        cash = opening
        for index, detail in enumerate(weekly):
            detail = {key: round(value, 2) for key, value in detail.items()}
            inflow = detail["ar_collections"]
            outflow = round(detail["ap_payments"] + detail["payroll"] + detail["fees"], 2)
            net = round(inflow - outflow, 2)
            cash = round(cash + net, 2)
            weeks.append(ForecastWeek(week_start=(start + timedelta(days=index * 7)).isoformat(), inflows=inflow, outflows=outflow, net=net, ending_cash=cash, detail=detail))
        view = ForecastView(period_id=self.ctx.period_id, as_of=(start - timedelta(days=1)).isoformat(), opening_cash=opening, weeks=weeks, assumptions=assumptions, memory_adjustment_total=round(adjustments, 2))
        memory.record_observation("global", self.ctx.period_id, "forecast", view.model_dump(mode="json"), self.ctx.period_id)
        self.emit("forecast.updated", "13-week cash forecast updated", forecast=view.model_dump(mode="json"))
        self.finished()
        return view


async def build_forecast(ctx: AgentContext) -> ForecastView:
    return await ForecastAgent(ctx).run()
