<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";
import * as echarts from "echarts";

type ScoreSeries = { name: string; values: number[] };

const props = withDefaults(
  defineProps<{
    kind: "trend" | "bar" | "spark";
    labels: string[];
    series: ScoreSeries[];
    height?: number;
  }>(),
  { height: 280 },
);

const target = ref<HTMLDivElement>();
let chart: echarts.ECharts | undefined;
let observer: ResizeObserver | undefined;

const colors: Record<string, string> = {
  Agent: "#e6b85f",
  Skill: "#8fa9ca",
  Native: "#a1a1a4",
};
const palette = ["#e6b85f", "#5eaeff", "#b07dd8", "#a4a4a7", "#e6765f", "#5ed4a7", "#c8a45e", "#7eb5d6"];
const chartFont = '"Inter Variable", Inter, "Segoe UI", sans-serif';

const option = computed<echarts.EChartsOption>(() => {
  const isSpark = props.kind === "spark";
  const isTrend = props.kind === "trend";
  return {
    animation: true,
    backgroundColor: "transparent",
    grid: isSpark
      ? { top: 2, right: 0, bottom: 2, left: 0 }
      : { top: isTrend ? 48 : 42, right: 14, bottom: 24, left: 38, containLabel: true },
    tooltip: isSpark
      ? { show: false }
      : {
          trigger: "axis",
          backgroundColor: "#12151a",
          borderColor: "#2b3038",
          borderWidth: 1,
          textStyle: { color: "#f3f4f6", fontSize: 12, fontFamily: chartFont, fontWeight: 500 },
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
          textStyle: { color: "#d5d8dd", fontSize: 13, fontFamily: chartFont, fontWeight: 500 },
        },
    xAxis: {
      type: "category",
      data: props.labels,
      boundaryGap: props.kind === "bar",
      show: !isSpark,
      axisLine: { lineStyle: { color: "#2c3037" } },
      axisTick: { show: false },
      axisLabel: { color: "#a3a7af", fontSize: 11, margin: 12, fontFamily: chartFont, fontWeight: 450, interval: 0, width: 80, overflow: "break" },
    },
    yAxis: {
      type: "value",
      min: isSpark ? undefined : 0,
      max: isSpark ? undefined : 100,
      show: !isSpark,
      splitNumber: 4,
      axisLabel: { color: "#91969f", fontSize: 11, fontFamily: chartFont, fontWeight: 450 },
      axisLine: { show: false },
      axisTick: { show: false },
      splitLine: { lineStyle: { color: "#272b31", type: "dashed" } },
    },
    series: props.series.map((item, idx) => ({
      name: item.name,
      type: props.kind === "bar" ? "bar" : "line",
      data: item.values,
      smooth: props.kind !== "bar",
      showSymbol: !isSpark && props.kind === "trend",
      symbolSize: 6,
      barMaxWidth: 22,
      barGap: "20%",
      itemStyle: { color: colors[item.name] ?? palette[idx % palette.length], borderRadius: [3, 3, 0, 0] },
      lineStyle: { width: isSpark ? 1.25 : 2, color: colors[item.name] ?? palette[idx % palette.length] },
      areaStyle: isSpark ? { color: "transparent" } : undefined,
      emphasis: { focus: "series" },
    })),
  };
});

function render() {
  if (!target.value) return;
  chart ??= echarts.init(target.value, undefined, { renderer: "canvas" });
  chart.setOption(option.value, { notMerge: true });
}

onMounted(() => {
  render();
  observer = new ResizeObserver(() => chart?.resize());
  if (target.value) observer.observe(target.value);
});

watch(option, render, { deep: true });
onBeforeUnmount(() => {
  observer?.disconnect();
  chart?.dispose();
});
</script>

<template>
  <div ref="target" class="chart-canvas" :style="{ height: `${height}px` }" />
</template>
