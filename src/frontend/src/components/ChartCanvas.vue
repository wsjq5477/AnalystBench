<script>
import { use } from "echarts/core";
import { BarChart, LineChart, ScatterChart } from "echarts/charts";
import {
  GridComponent,
  LegendComponent,
  MarkAreaComponent,
  MarkLineComponent,
  TooltipComponent,
} from "echarts/components";
import { LabelLayout } from "echarts/features";
import { CanvasRenderer } from "echarts/renderers";
import * as echarts from "echarts/core";
import { CHART_THEMES, THEME_PALETTES } from "../theme";
import { formatDurationMs } from "../timing-display";
import { paretoFrontier } from "../pareto-frontier";

use([
  BarChart,
  LineChart,
  ScatterChart,
  GridComponent,
  LegendComponent,
  MarkAreaComponent,
  MarkLineComponent,
  TooltipComponent,
  LabelLayout,
  CanvasRenderer,
]);

const chartFont = '"Inter Variable", Inter, "Segoe UI", sans-serif';

function escapeHtml(value) {
  return String(value ?? "").replace(
    /[&<>"']/g,
    (character) =>
      ({
        "&": "&amp;",
        "<": "&lt;",
        ">": "&gt;",
        '"': "&quot;",
        "'": "&#39;",
      })[character],
  );
}

export default {
  name: "ChartCanvas",
  props: {
    kind: {
      type: String,
      required: true,
      validator: (value) => ["trend", "bar", "scatter", "spark"].includes(value),
    },
    labels: {
      type: Array,
      required: true,
    },
    series: {
      type: Array,
      required: true,
    },
    referenceLine: {
      type: Object,
      default: null,
    },
    height: {
      type: Number,
      default: 280,
    },
    theme: {
      type: String,
      default: "dark",
      validator: (value) => ["dark", "light"].includes(value),
    },
  },
  data() {
    return {
      chart: null,
      observer: null,
      resizeFrame: null,
    };
  },
  computed: {
    chartOption() {
      const isSpark = this.kind === "spark";
      const isTrend = this.kind === "trend";
      const isScatter = this.kind === "scatter";
      const chartTheme = CHART_THEMES[this.theme];
      const palette = THEME_PALETTES[this.theme];
      if (isScatter) {
        const points = this.series.flatMap((item) => item.values || []);
        const frontier = paretoFrontier(points);
        const durations = points
          .map((point) => Number(point.value?.[0]))
          .filter((value) => Number.isFinite(value) && value > 0);
        const scores = points
          .map((point) => Number(point.value?.[1]))
          .filter(Number.isFinite);
        const referenceScore = Number(this.referenceLine?.value);
        if (Number.isFinite(referenceScore)) scores.push(referenceScore);
        const durationMin = durations.length ? Math.min(...durations) : 1000;
        const durationMax = durations.length ? Math.max(...durations) : 10000;
        const xAxisMin = Math.max(1, durationMin / 1.45);
        const xAxisMax = Math.max(xAxisMin * 1.1, durationMax * 1.45);
        const xAxisMid = Math.sqrt(xAxisMin * xAxisMax);
        const observedScoreMin = scores.length ? Math.min(...scores) : 0;
        const observedScoreMax = scores.length ? Math.max(...scores) : 100;
        const yAxisMin = Math.max(0, Math.floor((observedScoreMin - 6) / 5) * 5);
        const yAxisMax = Math.min(
          100,
          Math.max(yAxisMin + 10, Math.ceil((observedScoreMax + 6) / 5) * 5),
        );
        const yAxisMid = (yAxisMin + yAxisMax) / 2;
        return {
          animation: true,
          backgroundColor: "transparent",
          grid: {
            top: 58,
            right: 34,
            bottom: 30,
            left: 48,
            containLabel: true,
          },
          tooltip: {
            trigger: "item",
            backgroundColor: chartTheme.tooltipBg,
            borderColor: chartTheme.tooltipBorder,
            borderWidth: 1,
            textStyle: {
              color: chartTheme.tooltipText,
              fontSize: 12,
              fontFamily: chartFont,
              fontWeight: 500,
            },
            padding: [10, 12],
            formatter: (parameter) => {
              const point = parameter.data || {};
              const modelRow =
                point.model && point.model !== "-"
                  ? `<br><span style="opacity:.72">MODEL ${escapeHtml(point.model)}</span>`
                  : "";
              return `${parameter.marker || ""}<strong>${escapeHtml(point.fullLabel || point.targetLabel || parameter.seriesName)}</strong><br><span style="opacity:.72">HARNESS ${escapeHtml(point.harness || parameter.seriesName)}</span>${modelRow}<br><span style="opacity:.72">AVG DURATION ${escapeHtml(formatDurationMs(point.duration_ms))}</span><br>SCORE ${Number(point.score).toFixed(1)}`;
            },
          },
          legend: {
            top: 4,
            left: "center",
            itemWidth: 9,
            itemHeight: 9,
            itemGap: 18,
            icon: "circle",
            textStyle: {
              color: chartTheme.legend,
              fontSize: 12,
              fontFamily: chartFont,
              fontWeight: 500,
            },
          },
          xAxis: {
            type: "log",
            logBase: 10,
            min: xAxisMin,
            max: xAxisMax,
            name: "AVERAGE DURATION (LOG SCALE)",
            nameLocation: "middle",
            nameGap: 34,
            nameTextStyle: {
              color: chartTheme.axis,
              fontSize: 10,
              fontFamily: chartFont,
              fontWeight: 550,
            },
            axisLine: { lineStyle: { color: chartTheme.line } },
            axisTick: { show: false },
            minorTick: { show: false },
            axisLabel: {
              color: chartTheme.axis,
              fontSize: 10,
              fontFamily: chartFont,
              fontWeight: 450,
              formatter: (value) => formatDurationMs(value),
            },
            splitLine: { lineStyle: { color: chartTheme.split, type: "dashed" } },
            minorSplitLine: { show: false },
          },
          yAxis: {
            type: "value",
            min: yAxisMin,
            max: yAxisMax,
            splitNumber: 4,
            name: "AVERAGE SCORE",
            nameLocation: "middle",
            nameGap: 42,
            nameTextStyle: {
              color: chartTheme.axis,
              fontSize: 10,
              fontFamily: chartFont,
              fontWeight: 550,
            },
            axisLabel: {
              color: chartTheme.axis,
              fontSize: 10,
              fontFamily: chartFont,
              fontWeight: 450,
            },
            axisLine: { show: false },
            axisTick: { show: false },
            splitLine: { lineStyle: { color: chartTheme.split, type: "dashed" } },
          },
          series: [
            ...this.series.map((item, index) => {
              const color = palette[index % palette.length];
              return {
              name: item.name,
              type: "scatter",
              data: item.values,
              symbolSize: 14,
              z: 3,
              label: {
                show: true,
                position: "top",
                distance: 7,
                color: chartTheme.legend,
                fontSize: 10,
                fontFamily: chartFont,
                fontWeight: 520,
                formatter: (parameter) => parameter.data?.targetLabel || "",
              },
              labelLayout: { hideOverlap: false, moveOverlap: "shiftY" },
              itemStyle: {
                color,
                borderColor: this.theme === "dark" ? "#08101c" : "#ffffff",
                borderWidth: 2,
                shadowBlur: 8,
                shadowColor: `${color}55`,
              },
              emphasis: {
                focus: "series",
                scale: 1.35,
                label: { fontWeight: 700 },
              },
              markArea:
                index === 0
                  ? {
                      silent: true,
                      itemStyle: {
                        color:
                          this.theme === "dark"
                            ? "rgba(187, 247, 208, .11)"
                            : "rgba(187, 247, 208, .44)",
                      },
                      label: {
                        show: true,
                        position: "insideTopLeft",
                        color:
                          this.theme === "dark"
                            ? "rgba(187, 247, 208, .72)"
                            : "rgba(22, 101, 52, .72)",
                        fontSize: 10,
                        fontFamily: chartFont,
                        fontWeight: 650,
                        formatter: "FAST + HIGH SCORE",
                        padding: [7, 5],
                      },
                      data: [
                        [
                          { xAxis: xAxisMin, yAxis: yAxisMid },
                          { xAxis: xAxisMid, yAxis: yAxisMax },
                        ],
                      ],
                    }
                  : undefined,
              markLine:
                index === 0 && Number.isFinite(referenceScore)
                  ? {
                      silent: true,
                      symbol: ["none", "none"],
                      lineStyle: {
                        color:
                          this.theme === "dark"
                            ? "rgba(255, 255, 255, .56)"
                            : "rgba(52, 64, 84, .55)",
                        type: "dashed",
                        width: 1.5,
                      },
                      label: {
                        show: true,
                        position: "insideEndTop",
                        color: chartTheme.legend,
                        fontSize: 10,
                        fontFamily: chartFont,
                        fontWeight: 650,
                        formatter: `${this.referenceLine?.label || "script"} · ${referenceScore.toFixed(1)}`,
                        padding: [3, 5],
                        backgroundColor: chartTheme.tooltipBg,
                        borderRadius: 3,
                      },
                      data: [{ yAxis: referenceScore }],
                    }
                  : undefined,
              };
            }),
            ...(frontier.length > 1
              ? [
                  {
                    name: "Pareto line",
                    type: "line",
                    data: frontier,
                    showSymbol: false,
                    silent: true,
                    z: 2,
                    tooltip: { show: false },
                    lineStyle: {
                      color:
                        this.theme === "dark"
                          ? "rgba(255, 255, 255, .62)"
                          : "rgba(52, 64, 84, .68)",
                      type: "dotted",
                      width: 2,
                    },
                    emphasis: { disabled: true },
                  },
                ]
              : []),
          ],
        };
      }
      return {
        animation: true,
        backgroundColor: "transparent",
        grid: isSpark
          ? { top: 2, right: 0, bottom: 2, left: 0 }
          : {
              top: isTrend ? 48 : 42,
              right: isTrend ? 40 : 14,
              bottom: 24,
              left: isTrend ? 54 : 38,
              containLabel: true,
            },
        tooltip: isSpark
          ? { show: false }
          : {
              trigger: "axis",
              backgroundColor: chartTheme.tooltipBg,
              borderColor: chartTheme.tooltipBorder,
              borderWidth: 1,
              textStyle: {
                color: chartTheme.tooltipText,
                fontSize: 12,
                fontFamily: chartFont,
                fontWeight: 500,
              },
              padding: [10, 12],
              formatter: (parameters) => {
                const items = Array.isArray(parameters) ? parameters : [parameters];
                if (!items.length) return "";
                const title = items[0].axisValueLabel || items[0].name || "";
                const rows = items.map((item) => {
                  const point =
                    item.data && typeof item.data === "object"
                      ? item.data
                      : { value: item.value, duration_ms: null };
                  const hasScore = point.value !== null && point.value !== undefined;
                  const numericScore = Number(point.value);
                  const score = hasScore && Number.isFinite(numericScore)
                    ? numericScore.toFixed(1)
                    : "—";
                  return `${item.marker || ""}${escapeHtml(item.seriesName)}：${score}<br><span style="opacity:.72">DURATION ${escapeHtml(formatDurationMs(point.duration_ms))}</span>`;
                });
                return `<strong>${escapeHtml(title)}</strong><br>${rows.join("<br>")}`;
              },
            },
        legend: isSpark
          ? { show: false }
          : {
              top: 4,
              left: "center",
              itemWidth: 18,
              itemHeight: 2,
              itemGap: 20,
              textStyle: {
                color: chartTheme.legend,
                fontSize: 13,
                fontFamily: chartFont,
                fontWeight: 500,
              },
            },
        xAxis: {
          type: "category",
          data: this.labels,
          boundaryGap: !isSpark,
          show: !isSpark,
          axisLine: { lineStyle: { color: chartTheme.line } },
          axisTick: { show: false },
          axisLabel: {
            color: chartTheme.axis,
            fontSize: 11,
            margin: 12,
            fontFamily: chartFont,
            fontWeight: 450,
            interval: 0,
            width: 80,
            overflow: "break",
          },
        },
        yAxis: {
          type: "value",
          min: isSpark ? undefined : 0,
          max: isSpark ? undefined : 100,
          show: !isSpark,
          splitNumber: 4,
          axisLabel: {
            color: chartTheme.axis,
            fontSize: 11,
            fontFamily: chartFont,
            fontWeight: 450,
          },
          axisLine: { show: false },
          axisTick: { show: false },
          splitLine: { lineStyle: { color: chartTheme.split, type: "dashed" } },
        },
        series: this.series.map((item, index) => {
          const color = palette[index % palette.length];
          return {
            name: item.name,
            type: this.kind === "bar" ? "bar" : "line",
            data: item.values.map((value, valueIndex) => ({
              value,
              duration_ms: item.durations?.[valueIndex] ?? null,
            })),
            smooth: this.kind !== "bar",
            showSymbol: !isSpark && this.kind === "trend",
            symbolSize: 6,
            barMaxWidth: 22,
            barGap: "20%",
            itemStyle: {
              color,
              borderRadius: [3, 3, 0, 0],
            },
            lineStyle: {
              width: isSpark ? 1.25 : 2,
              color,
            },
            areaStyle: isSpark ? { color: "transparent" } : undefined,
            emphasis: { focus: "series" },
          };
        }),
      };
    },
  },
  watch: {
    chartOption: {
      deep: true,
      handler() {
        this.renderChart();
      },
    },
  },
  mounted() {
    this.renderChart();
    if (typeof ResizeObserver !== "undefined") {
      this.observer = new ResizeObserver(() => {
        if (this.resizeFrame) cancelAnimationFrame(this.resizeFrame);
        this.resizeFrame = requestAnimationFrame(() => {
          this.resizeFrame = null;
          if (this.chart) this.chart.resize();
        });
      });
      this.observer.observe(this.$refs.target);
    } else {
      window.addEventListener("resize", this.resizeChart);
    }
  },
  beforeDestroy() {
    if (this.observer) this.observer.disconnect();
    if (this.resizeFrame) cancelAnimationFrame(this.resizeFrame);
    window.removeEventListener("resize", this.resizeChart);
    if (this.chart) this.chart.dispose();
  },
  methods: {
    resizeChart() {
      if (this.chart) this.chart.resize();
    },
    renderChart() {
      if (!this.$refs.target) return;
      if (!this.chart) {
        this.chart = echarts.init(this.$refs.target, undefined, {
          renderer: "canvas",
        });
      }
      this.chart.setOption(this.chartOption, { notMerge: true });
    },
  },
};
</script>

<template>
  <div
    ref="target"
    class="chart-canvas"
    :style="{ height: `${height}px` }"
  />
</template>
