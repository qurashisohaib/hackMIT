"use client";

import { Bar, CartesianGrid, ComposedChart, Legend, Line, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import type { ForecastWeek } from "@/lib/types";
import { CHART_CHROME, formatDateShort, formatMoney } from "@/lib/utils";

export function CashChart({ weeks }: { weeks: ForecastWeek[] }) {
  const points = weeks.map((week) => ({
    ...week,
    outflow_bar: -Math.abs(week.outflows),
    label: formatDateShort(`${week.week_start}T12:00:00`),
  }));
  return (
    <div role="figure" aria-label="Weekly cash inflows and outflows with ending cash balance. Exact amounts are in the weekly cash schedule below.">
      <div className="mb-2 flex flex-wrap justify-between gap-2 font-mono text-[10px] text-muted uppercase">
        <span>Cash movements · USD · left axis</span>
        <span>Ending cash · USD · right axis</span>
      </div>
      <div className="h-[340px] min-w-0 sm:h-[390px]">
        <ResponsiveContainer width="100%" height="100%" minWidth={0}>
          <ComposedChart data={points} stackOffset="sign" margin={{ top: 15, right: 6, bottom: 6, left: 0 }} accessibilityLayer>
            <CartesianGrid vertical={false} stroke={CHART_CHROME.grid} />
            <XAxis dataKey="label" tick={{ fill: CHART_CHROME.muted, fontSize: 10 }} axisLine={false} tickLine={false} minTickGap={22} />
            <YAxis yAxisId="movements" tickFormatter={(value: number) => formatMoney(value, { compact: true })} tick={{ fill: CHART_CHROME.muted, fontSize: 10 }} axisLine={false} tickLine={false} width={62} />
            <YAxis yAxisId="balance" orientation="right" tickFormatter={(value: number) => formatMoney(value, { compact: true })} tick={{ fill: "#67e8f9", fontSize: 10 }} axisLine={false} tickLine={false} width={66} />
            <Tooltip
              formatter={(value, name) => [typeof value === "number" ? formatMoney(value) : "—", name ?? ""]}
              contentStyle={{ background: CHART_CHROME.surface, border: `1px solid ${CHART_CHROME.axis}`, borderRadius: 8, fontSize: 12 }}
              labelStyle={{ color: CHART_CHROME.ink, marginBottom: 6 }}
              cursor={{ fill: "rgba(255,255,255,0.03)" }}
              isAnimationActive={false}
            />
            <Legend wrapperStyle={{ fontSize: 11, paddingTop: 16 }} />
            <ReferenceLine yAxisId="movements" y={0} stroke={CHART_CHROME.muted} />
            <Bar yAxisId="movements" dataKey="inflows" name="Cash inflows" stackId="cash" fill="#34d399" maxBarSize={30} isAnimationActive={false} />
            <Bar yAxisId="movements" dataKey="outflow_bar" name="Cash outflows" stackId="cash" fill="#a78bfa" maxBarSize={30} isAnimationActive={false} />
            <Line yAxisId="balance" dataKey="ending_cash" name="Ending cash" type="linear" stroke="#67e8f9" strokeWidth={2.5} dot={{ r: 3, fill: "#67e8f9", stroke: CHART_CHROME.surface }} isAnimationActive={false} />
          </ComposedChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}
