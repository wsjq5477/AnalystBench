<script>
import { use } from "echarts/core";
import { BarChart, LineChart } from "echarts/charts";
import {
  GridComponent,
  LegendComponent,
  TooltipComponent,
} from "echarts/components";
import { CanvasRenderer } from "echarts/renderers";
import * as echarts from "echarts/core";

use([
  BarChart,
  LineChart,
  GridComponent,
  LegendComponent,
  TooltipComponent,
  CanvasRenderer,
]);

const colors = {
  Agent: "#e6b85f",
  Skill: "#8fa9ca",
  Native: "#a1a1a4",
};
const palette = [
  "#e6b85f",
  "#5eaeff",
  "#b07dd8",
  "#a4a4a7",
  "#e6765f",
  "#5ed4a7",
  "#c8a45e",
  "#7eb5d6",
];
const chartFont = '"Inter Variable", Inter, "Segoe UI", sans-serif';

export default {
  name: "ChartCanvas",
  props: {
    kind: {
      type: String,
      required: true,
      validator: (value) => ["trend", "bar", "spark"].includes(value),
    },
    labels: {
      type: Array,
      required: true,
    },
    series: {
      type: Array,
      required: true,
    },
    height: {
      type: Number,
      default: 280,
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
      return {
        animation: true,
        backgroundColor: "transparent",
        grid: isSpark
          ? { top: 2, right: 0, bottom: 2, left: 0 }
          : {
              top: isTrend ? 48 : 42,
              right: 14,
              bottom: 24,
              left: 38,
              containLabel: true,
            },
        tooltip: isSpark
          ? { show: false }
          : {
              trigger: "axis",
              backgroundColor: "#12151a",
              borderColor: "#2b3038",
              borderWidth: 1,
              textStyle: {
                color: "#f3f4f6",
                fontSize: 12,
                fontFamily: chartFont,
                fontWeight: 500,
              },
              padding: [10, 12],
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
                color: "#d5d8dd",
                fontSize: 13,
                fontFamily: chartFont,
                fontWeight: 500,
              },
            },
        xAxis: {
          type: "category",
          data: this.labels,
          boundaryGap: this.kind === "bar",
          show: !isSpark,
          axisLine: { lineStyle: { color: "#2c3037" } },
          axisTick: { show: false },
          axisLabel: {
            color: "#a3a7af",
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
            color: "#91969f",
            fontSize: 11,
            fontFamily: chartFont,
            fontWeight: 450,
          },
          axisLine: { show: false },
          axisTick: { show: false },
          splitLine: { lineStyle: { color: "#272b31", type: "dashed" } },
        },
        series: this.series.map((item, index) => {
          const color = colors[item.name] || palette[index % palette.length];
          return {
            name: item.name,
            type: this.kind === "bar" ? "bar" : "line",
            data: item.values,
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
