"use client";

import { useLocale, useTranslations } from "next-intl";
import { Bar, BarChart, Cell, LabelList, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

import { useStatusLabel } from "@/components/common/status-label";
import type { StatBucket } from "@/lib/api";
import { formatShare, statusesFor, type ShareRow } from "@/lib/audit";
import { formatNumber } from "@/lib/format";
import { htmlLang } from "@/i18n/config";
import { cn } from "@/lib/utils";

const ROW_HEIGHT = 30;
const BAR_SIZE = 14; // DESIGN dataviz: thin marks, capped well under the slot height

/** Approximate width of a monospace label at caption size, for the category axis. */
function labelWidth(rows: readonly ShareRow[]): number {
  const longest = rows.reduce((max, row) => Math.max(max, row.key.length), 0);
  return Math.min(150, Math.max(64, Math.round(longest * 7.6) + 16));
}

interface TickProps {
  x?: number | string;
  y?: number | string;
  payload?: { value?: unknown };
}

function SourceTick({ x = 0, y = 0, payload }: TickProps) {
  return (
    <text
      x={Number(x) - 8}
      y={Number(y)}
      dy="0.35em"
      textAnchor="end"
      className="fill-muted-foreground font-mono text-xs"
    >
      {String(payload?.value ?? "")}
    </text>
  );
}

interface TipLabelProps {
  x?: number | string;
  y?: number | string;
  width?: number | string;
  height?: number | string;
  index?: number;
}

/** Count and share at the bar's tip, in text tokens (never the series colour). */
function renderTip(props: TipLabelProps, rows: readonly ShareRow[], locale: string) {
  const { x = 0, y = 0, width = 0, height = 0, index = 0 } = props;
  const row = rows[index];
  if (!row) return null;
  return (
    <text
      x={Number(x) + Number(width) + 8}
      y={Number(y) + Number(height) / 2}
      dy="0.35em"
      className="fill-muted-foreground text-xs tabular-nums"
    >
      {formatNumber(row.count, locale)}
      <tspan className="fill-faint"> · {formatShare(row.share, locale)}</tspan>
    </text>
  );
}

/** The hover card of one bar: decisions, share, the source verbatim with its meaning, outcomes. */
export function TooltipBody({
  active,
  payload,
  buckets,
  meaningOf,
}: {
  active?: boolean;
  payload?: readonly { payload?: ShareRow }[];
  buckets: readonly StatBucket[];
  meaningOf: (source: string) => string | null;
}) {
  const t = useTranslations("audit.stats");
  const locale = htmlLang[useLocale()];
  const statusLabel = useStatusLabel();
  const row = payload?.[0]?.payload;
  if (!active || !row) return null;
  const meaning = meaningOf(row.key);
  const outcomes = statusesFor(buckets, row.key);
  return (
    <div className="max-w-xs rounded-md bg-foreground px-3 py-2 text-xs text-background">
      <p className="font-semibold tabular-nums">
        {t("decisions", { count: row.count })} · {formatShare(row.share, locale)}
      </p>
      <p className="mt-0.5 font-mono opacity-80">
        {row.key}
        {meaning ? <span className="font-sans"> · {meaning}</span> : null}
      </p>
      {outcomes.length > 0 ? (
        <ul className="mt-1.5 space-y-0.5 opacity-90">
          {outcomes.map((o) => (
            <li key={o.key} className="flex justify-between gap-4 tabular-nums">
              <span>{statusLabel(o.key)}</span>
              <span>{formatNumber(o.count, locale)}</span>
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}

/**
 * Decisions per route source as one horizontal bar each (one series, so one hue: chart-1),
 * labelled at the tip with count and share. Selecting a source keeps it in the accent and
 * greys the rest (emphasis). The SVG is decorative for assistive tech: the same numbers are in
 * the table below it.
 */
export function RouteShareChart({
  rows,
  buckets,
  selected,
  onSelect,
  meaningOf,
}: {
  rows: readonly ShareRow[];
  buckets: readonly StatBucket[];
  selected: string | null;
  onSelect: (source: string) => void;
  meaningOf: (source: string) => string | null;
}) {
  const locale = htmlLang[useLocale()];
  const height = rows.length * ROW_HEIGHT + 4;
  const width = labelWidth(rows);

  return (
    <div aria-hidden="true" className="w-full" style={{ height }} data-testid="route-share-chart">
      <ResponsiveContainer width="100%" height="100%">
        <BarChart
          data={rows as ShareRow[]}
          layout="vertical"
          margin={{ top: 2, right: 84, bottom: 2, left: 0 }}
          accessibilityLayer={false}
        >
          <XAxis type="number" hide domain={[0, "dataMax"]} />
          <YAxis
            type="category"
            dataKey="key"
            width={width}
            tickLine={false}
            axisLine={false}
            interval={0}
            tick={<SourceTick />}
          />
          <Tooltip
            cursor={{ className: "fill-muted/60" }}
            isAnimationActive={false}
            content={<TooltipBody buckets={buckets} meaningOf={meaningOf} />}
          />
          <Bar
            dataKey="count"
            barSize={BAR_SIZE}
            radius={[0, 4, 4, 0]}
            isAnimationActive={false}
            className="cursor-pointer"
            onClick={(entry) => {
              const key = (entry as { payload?: ShareRow } | undefined)?.payload?.key;
              if (key) onSelect(key);
            }}
          >
            {rows.map((row) => (
              <Cell
                key={row.key}
                className={cn(
                  "transition-[fill] duration-150",
                  selected && selected !== row.key ? "fill-chart-5" : "fill-chart-1",
                )}
              />
            ))}
            <LabelList dataKey="count" content={(props) => renderTip(props as TipLabelProps, rows, locale)} />
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}
