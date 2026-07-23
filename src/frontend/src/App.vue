<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from "vue";
import {
  IconAlertCircle,
  IconChartLine,
  IconChevronDown,
  IconChevronRight,
  IconCircleCheck,
  IconCloudUpload,
  IconDatabase,
  IconFileExport,
  IconFolder,
  IconInfoCircle,
  IconLayoutDashboard,
  IconLoader2,
  IconPlus,
  IconRefresh,
  IconSettings,
  IconSparkles,
  IconTerminal2,
  IconTrash,
  IconClipboardData,
} from "@tabler/icons-vue";
import ChartCanvas from "./components/ChartCanvas.vue";
import { analystBenchApi, ApiError, type CaseCategory, type CaseRevisionContent, type Dataset, type DatasetCase, type DirectResultListItem, type DirectResultReportSummary, type DirectResultClaim, type DirectResultComparison, type DirectResultSummary } from "./api";

type ViewName = "dashboard" | "dataset" | "analysis" | "results";
type Tone = "agent" | "skill" | "native";

const activeView = ref<ViewName>("dashboard");
const loading = ref(false);
const toast = ref("");
const connection = ref<"checking" | "connected" | "offline">("checking");
const caseListUnavailable = ref(false);
const caseListError = ref("");
const datasets = ref<Dataset[]>([]);
const datasetCases = ref<Record<string, DatasetCase[]>>({});
const datasetCategories = ref<Record<string, CaseCategory[]>>({});
const showDatasetForm = ref(false);
const showDraftForm = ref(false);
const showCategoryForm = ref(false);
const showStandaloneCategoryForm = ref(false);
const selectedDatasetId = ref("");
const selectedCaseId = ref("");
const caseContent = ref<CaseRevisionContent | null>(null);
const editableCaseJson = ref("");
const contentLoading = ref(false);
const datasetForm = reactive({ name: "", description: "", datasetKey: "" });
const caseForm = reactive({ datasetId: "", categoryKey: "", answer: "" });
const categoryForm = reactive({ key: "", name: "" });
const runForm = reactive({ datasetVersionId: "", candidateVersionId: "", scoringPolicyVersionId: "" });
const runResult = ref<Record<string, unknown> | null>(null);
const runIds = reactive({ agent: "", skill: "", native: "" });
const hasLoadedRuns = ref(false);

// ─── Direct Results view ───
const directResultList = ref<DirectResultListItem[]>([]);
const selectedResultId = ref("");
const selectedResultData = ref<Record<string, unknown> | null>(null);
const resultLoading = ref(false);

function parseSummary(data: Record<string, unknown>): DirectResultSummary | null {
  const s = data.summary;
  if (!s || typeof s !== "object") return null;
  return s as DirectResultSummary;
}

function toneFromRank(name: string): Tone {
  const idx = allCandidates.value.findIndex((c) => c.name === name);
  if (idx === 0) return "agent";
  if (idx === 1) return "skill";
  return "native";
}

function toneFromName(name: string): Tone {
  if (name.toLowerCase().includes("agent")) return "agent";
  if (name.toLowerCase().includes("skill")) return "skill";
  if (name.toLowerCase().includes("native")) return "native";
  return "agent";
}

function wrapName(name: string): string {
  // Insert newline before parenthesis for long names: "hmdiagAgent(deepseek-v4-flash)" → "hmdiagAgent\n(deepseek-v4-flash)"
  const parenIdx = name.indexOf("(");
  if (parenIdx > 0 && name.length > 14) return name.slice(0, parenIdx) + "\n" + name.slice(parenIdx);
  return name;
}

function relationTagClass(relation: string): string {
  if (relation === "match") return "tag-match";
  if (relation === "partial_match") return "tag-partial";
  return "tag-missing";
}

async function refreshDirectResults() {
  resultLoading.value = true;
  try {
    directResultList.value = await analystBenchApi.listDirectResults();
    connection.value = "connected";
  } catch {
    connection.value = "offline";
    directResultList.value = [];
  } finally {
    resultLoading.value = false;
  }
}

async function loadDirectResult(item: DirectResultListItem) {
  selectedResultId.value = item.id;
  resultLoading.value = true;
  try {
    selectedResultData.value = await analystBenchApi.getDirectResult(item.id);
  } catch (error) {
    selectedResultData.value = null;
    showToast(error instanceof Error ? error.message : "读取评测结果失败");
  } finally {
    resultLoading.value = false;
  }
}

async function deleteDirectResult(item: DirectResultListItem) {
  if (!window.confirm(`删除评测结果 ${item.id} 吗？JSON 和 Markdown 文件将一并删除。`)) return;
  loading.value = true;
  try {
    await analystBenchApi.deleteDirectResult(item.id);
    if (selectedResultId.value === item.id) {
      selectedResultId.value = "";
      selectedResultData.value = null;
    }
    await refreshDirectResults();
    dashboardLoaded.value = false;
    void loadDashboardData();
    showToast("评测结果已删除");
  } catch (error) {
    showToast(error instanceof Error ? error.message : "删除评测结果失败");
  } finally {
    loading.value = false;
  }
}

// ─── Computed data for result detail ───
const resultColors = ["#e6b85f", "#5eaeff", "#b07dd8", "#a4a4a7"];

const resultRankedReports = computed(() => {
  const summary = parseSummary(selectedResultData.value ?? {});
  if (!summary) return [];
  const byName: Record<string, DirectResultReportSummary> = {};
  for (const r of summary.reports) byName[r.candidate_name] = r;
  return summary.ranking.map((name) => byName[name]).filter(Boolean);
});

// ─── Dashboard data from direct_results API ───
const dashboardResultDetails = ref<Record<string, unknown>[]>([]);
const dashboardLoaded = ref(false);

async function loadDashboardData() {
  if (dashboardLoaded.value) return;
  loading.value = true;
  try {
    const list = await analystBenchApi.listDirectResults();
    const details = await Promise.all(
      list.map(async (item) => analystBenchApi.getDirectResult(item.id))
    );
    dashboardResultDetails.value = details;
    dashboardLoaded.value = true;
    connection.value = "connected";
  } catch {
    connection.value = "offline";
    dashboardResultDetails.value = [];
  } finally {
    loading.value = false;
  }
}

// Collect all unique candidate names across all results (sorted by average score descending)
const allCandidates = computed(() => {
  const scoreMap: Record<string, number[]> = {};
  for (const detail of dashboardResultDetails.value) {
    const summary = parseSummary(detail);
    if (!summary) continue;
    for (const r of summary.reports) {
      if (!scoreMap[r.candidate_name]) scoreMap[r.candidate_name] = [];
      scoreMap[r.candidate_name].push(parseFloat(r.score));
    }
  }
  const entries = Object.entries(scoreMap).map(([name, scores]) => ({
    name,
    avg: scores.reduce((s, v) => s + v, 0) / scores.length,
  }));
  entries.sort((a, b) => b.avg - a.avg);
  return entries;
});

// Dashboard score cards: top candidates by average score
const dashboardScoreCards = computed(() =>
  allCandidates.value.map((c, idx) => ({
    label: wrapName(c.name),
    score: c.avg,
    tone: toneFromRank(c.name),
    color: resultColors[idx % resultColors.length],
    change: "",
  }))
);

// Dashboard metric cards: 3 fixed cards (coverage, pass rate, missing chains)
// averaged across all candidates
const dashboardMetricCards = computed(() => {
  const coverages: number[] = [];
  const passRates: number[] = [];
  const chainMisses: number[] = [];
  for (const detail of dashboardResultDetails.value) {
    const summary = parseSummary(detail);
    if (!summary) continue;
    for (const r of summary.reports) {
      coverages.push(Number(r.metrics.claim_coverage) * 100);
      passRates.push(r.passed ? 100 : 0);
      chainMisses.push(Number(r.metrics.missing_chain_count));
    }
  }
  const avgCov = coverages.length ? coverages.reduce((s, v) => s + v, 0) / coverages.length : 0;
  const avgPass = passRates.length ? passRates.reduce((s, v) => s + v, 0) / passRates.length : 0;
  const avgMiss = chainMisses.length ? chainMisses.reduce((s, v) => s + v, 0) / chainMisses.length : 0;
  return [
    { title: "覆盖率", suffix: "（越高越好）", value: `${avgCov.toFixed(1)}%`, change: "", tone: "agent" as Tone, values: coverages },
    { title: "通过率", value: `${avgPass.toFixed(1)}%`, change: "", tone: "skill" as Tone, values: passRates },
    { title: "缺失链数", suffix: "（越低越好）", value: avgMiss.toFixed(1), change: "", tone: "native" as Tone, values: chainMisses },
  ];
});

// Score matrix: columns = result IDs, rows = candidates
const dashboardMatrixCases = computed(() =>
  dashboardResultDetails.value.map((d) => String(d.id ?? "").replace("direct-", ""))
);

const dashboardMatrixRows = computed(() =>
  allCandidates.value.map((c, idx) => ({
    name: wrapName(c.name),
    tone: toneFromRank(c.name),
    color: resultColors[idx % resultColors.length],
    scores: dashboardResultDetails.value.map((detail) => {
      const summary = parseSummary(detail);
      if (!summary) return 0;
      const report = summary.reports.find((r) => r.candidate_name === c.name);
      return report ? parseFloat(report.score) : 0;
    }),
  }))
);

const dashboardAverages = computed(() =>
  dashboardMatrixRows.value.map((row) => ({
    ...row,
    average: row.scores.length ? row.scores.reduce((sum, score) => sum + score, 0) / row.scores.length : 0,
  }))
);

// Category bar chart: claim-level scores from first result
const dashboardCategoryLabels = computed(() => {
  const first = dashboardResultDetails.value[0];
  const summary = parseSummary(first ?? {});
  if (!summary) return [];
  return summary.reports[0]?.claims.map((c) => `${c.id}\n${c.statement.slice(0, 12)}`) ?? [];
});

const dashboardCategorySeries = computed(() =>
  allCandidates.value.map((c) => ({
    name: wrapName(c.name),
    values: dashboardResultDetails.value.length
      ? (() => {
          // Use first result's claims as labels; map each candidate's claim scores
          const firstSummary = parseSummary(dashboardResultDetails.value[0] ?? {});
          if (!firstSummary) return [];
          const claimIds = firstSummary.reports[0]?.claims.map((cl) => cl.id) ?? [];
          // Average across all results for this candidate
          const avgByClaim: Record<string, number[]> = {};
          for (const detail of dashboardResultDetails.value) {
            const s = parseSummary(detail);
            if (!s) continue;
            const report = s.reports.find((r) => r.candidate_name === c.name);
            if (!report) continue;
            for (const cl of report.claims) {
              if (!avgByClaim[cl.id]) avgByClaim[cl.id] = [];
              avgByClaim[cl.id].push(parseFloat(cl.score));
            }
          }
          return claimIds.map((id) => {
            const arr = avgByClaim[id] ?? [];
            return arr.length ? arr.reduce((s, v) => s + v, 0) / arr.length : 0;
          });
        })()
      : [],
  }))
);

const selectedDataset = computed(() => datasets.value.find((item) => item.id === selectedDatasetId.value));
const selectedCase = computed(() => datasetCases.value[selectedDatasetId.value]?.find((item) => item.id === selectedCaseId.value));
const selectedCaseCategory = computed(() =>
  datasetCategories.value[selectedDatasetId.value]?.find((item) => item.id === selectedCase.value?.category_id),
);
const draftCategories = computed(() => datasetCategories.value[caseForm.datasetId] ?? []);

watch(selectedCase, (item) => {
  const revisionId = item?.revisions.at(-1)?.id;
  if (revisionId) {
    void loadCaseContent(revisionId);
  } else {
    caseContent.value = null;
    editableCaseJson.value = "";
  }
});

function showToast(message: string) {
  toast.value = message;
  window.setTimeout(() => {
    if (toast.value === message) toast.value = "";
  }, 4000);
}

function extractScore(value: Record<string, unknown>, fallback: number) {
  const candidate = value.score ?? value.total_score ?? (value.summary as Record<string, unknown> | undefined)?.score;
  return typeof candidate === "number" && Number.isFinite(candidate) ? Math.max(0, Math.min(100, candidate)) : fallback;
}

async function refreshCatalog() {
  loading.value = true;
  try {
    const nextDatasets = await analystBenchApi.listDatasets();
    datasets.value = nextDatasets;
    if (!selectedDatasetId.value && nextDatasets[0]) selectedDatasetId.value = nextDatasets[0].id;
    caseListUnavailable.value = false;
    caseListError.value = "";
    const catalogEntries = await Promise.all(nextDatasets.map(async (dataset) => {
      try {
        const [cases, categories] = await Promise.all([
          analystBenchApi.listDatasetCases(dataset.id),
          analystBenchApi.listDatasetCategories(dataset.id),
        ]);
        return [dataset.id, cases, categories] as const;
      } catch (error) {
        if (error instanceof ApiError && error.status === 404) caseListUnavailable.value = true;
        else caseListError.value = `Case 列表接口出错 (${error instanceof ApiError ? error.status : "未知"})，请重启后端后刷新。`;
        return [dataset.id, [], []] as const;
      }
    }));
    datasetCases.value = Object.fromEntries(catalogEntries.map(([id, cases]) => [id, cases]));
    datasetCategories.value = Object.fromEntries(catalogEntries.map(([id, , categories]) => [id, categories]));
    if (!caseForm.datasetId && nextDatasets[0]) caseForm.datasetId = selectedDatasetId.value || nextDatasets[0].id;
    connection.value = "connected";
  } catch {
    connection.value = "offline";
  } finally {
    loading.value = false;
  }
}

async function createDataset() {
  if (!datasetForm.name.trim()) return showToast("请填写数据集名称");
  loading.value = true;
  try {
    await analystBenchApi.createDataset({
      name: datasetForm.name.trim(),
      description: datasetForm.description.trim(),
      dataset_key: datasetForm.datasetKey.trim() || undefined,
    });
    Object.assign(datasetForm, { name: "", description: "", datasetKey: "" });
    showDatasetForm.value = false;
    await refreshCatalog();
    showToast("数据集已创建");
  } catch (error) {
    showToast(error instanceof Error ? error.message : "创建数据集失败");
  } finally {
    loading.value = false;
  }
}

function openCaseForm() {
  showDraftForm.value = !showDraftForm.value;
  showCategoryForm.value = false;
  if (!caseForm.datasetId) caseForm.datasetId = selectedDatasetId.value || datasets.value[0]?.id || "";
  if (!caseForm.categoryKey) caseForm.categoryKey = draftCategories.value[0]?.category_key || "";
}

function openStandaloneCategoryForm() {
  showStandaloneCategoryForm.value = !showStandaloneCategoryForm.value;
  showCategoryForm.value = false;
  if (!caseForm.datasetId) caseForm.datasetId = selectedDatasetId.value || datasets.value[0]?.id || "";
}

function updateDraftDataset() {
  caseForm.categoryKey = draftCategories.value[0]?.category_key || "";
  showCategoryForm.value = false;
}

async function createCategory() {
  if (!caseForm.datasetId) return showToast("请选择测评集");
  if (!categoryForm.key.trim()) return showToast("请填写类别标识");
  loading.value = true;
  try {
    const category = await analystBenchApi.createDatasetCategory(caseForm.datasetId, {
      category_key: categoryForm.key.trim(),
      name: categoryForm.name.trim() || categoryForm.key.trim(),
    });
    const current = datasetCategories.value[caseForm.datasetId] ?? [];
    datasetCategories.value = {
      ...datasetCategories.value,
      [caseForm.datasetId]: current.some((item) => item.id === category.id) ? current : [...current, category],
    };
    caseForm.categoryKey = category.category_key;
    Object.assign(categoryForm, { key: "", name: "" });
    showCategoryForm.value = false;
    showStandaloneCategoryForm.value = false;
    showToast("类别已创建并选中");
  } catch (error) {
    showToast(error instanceof Error ? error.message : "创建类别失败");
  } finally {
    loading.value = false;
  }
}

async function deleteDataset(dataset: Dataset) {
  if (!window.confirm(`删除测评集“${dataset.name}”吗？其中的类别与 Case 会一并归档。`)) return;
  loading.value = true;
  try {
    await analystBenchApi.deleteDataset(dataset.id);
    if (selectedDatasetId.value === dataset.id) {
      selectedDatasetId.value = "";
      selectedCaseId.value = "";
    }
    if (caseForm.datasetId === dataset.id) {
      caseForm.datasetId = "";
      caseForm.categoryKey = "";
    }
    await refreshCatalog();
    showToast("测评集已归档");
  } catch (error) {
    showToast(error instanceof Error ? error.message : "删除测评集失败");
  } finally {
    loading.value = false;
  }
}

async function deleteCategory(datasetId: string, category: CaseCategory) {
  if (!window.confirm(`删除类别“${category.name}”吗？该类别下的 Case 会一并归档。`)) return;
  loading.value = true;
  try {
    await analystBenchApi.deleteDatasetCategory(datasetId, category.id);
    if (selectedCase.value?.category_id === category.id) selectedCaseId.value = "";
    if (caseForm.datasetId === datasetId && caseForm.categoryKey === category.category_key) {
      caseForm.categoryKey = "";
    }
    await refreshCatalog();
    showToast("类别已归档");
  } catch (error) {
    showToast(error instanceof Error ? error.message : "删除类别失败");
  } finally {
    loading.value = false;
  }
}

async function deleteCase(item: DatasetCase) {
  if (!window.confirm(`删除 Case ${item.case_key} 吗？其历史 Revision 会保留。`)) return;
  loading.value = true;
  try {
    await analystBenchApi.deleteCase(item.id);
    if (selectedCaseId.value === item.id) selectedCaseId.value = "";
    await refreshCatalog();
    showToast(`Case ${item.case_key} 已归档`);
  } catch (error) {
    showToast(error instanceof Error ? error.message : "删除 Case 失败");
  } finally {
    loading.value = false;
  }
}

async function createCase() {
  if (!caseForm.datasetId) return showToast("请选择测评集");
  if (!caseForm.categoryKey || !caseForm.answer.trim()) return showToast("请选择类别，并填写标准文档");
  loading.value = true;
  try {
    const created = await analystBenchApi.createCase(caseForm.datasetId, {
      reference_answer: caseForm.answer.trim(),
      category_key: caseForm.categoryKey,
    });
    selectedDatasetId.value = caseForm.datasetId;
    await refreshCatalog();
    const createdCase = datasetCases.value[caseForm.datasetId]?.find((item) =>
      item.revisions.some((revision) => revision.id === created.id),
    );
    selectedCaseId.value = createdCase?.id || "";
    Object.assign(caseForm, { answer: "" });
    showDraftForm.value = false;
    showToast(`Case ${createdCase?.case_key || ""} 已自动编号并创建`);
  } catch (error) {
    showToast(error instanceof Error ? error.message : "创建 Case 失败");
  } finally {
    loading.value = false;
  }
}

async function loadCaseContent(revisionId: string) {
  contentLoading.value = true;
  try {
    const content = await analystBenchApi.getCaseRevisionContent(revisionId);
    caseContent.value = content;
    editableCaseJson.value = content.reference_answer;
  } catch (error) {
    caseContent.value = null;
    editableCaseJson.value = "";
    showToast(error instanceof Error ? error.message : "读取 Case 内容失败");
  } finally {
    contentLoading.value = false;
  }
}

async function selectCase(item: DatasetCase) {
  selectedDatasetId.value = item.dataset_id;
  selectedCaseId.value = item.id;
  const revisionId = item.revisions.at(-1)?.id;
  if (revisionId) await loadCaseContent(revisionId);
}

async function saveCaseJson() {
  if (!selectedCase.value) return;
  if (!editableCaseJson.value.trim()) {
    return showToast("标准文档不能为空");
  }
  loading.value = true;
  try {
    const revision = await analystBenchApi.createCaseRevision(selectedCase.value.id, {
      case_key: selectedCase.value.case_key,
      reference_answer: editableCaseJson.value.trim(),
    });
    const datasetId = selectedCase.value.dataset_id;
    datasetCases.value = {
      ...datasetCases.value,
      [datasetId]: (datasetCases.value[datasetId] ?? []).map((item) =>
        item.id === selectedCase.value?.id ? { ...item, revisions: [...item.revisions, revision] } : item,
      ),
    };
    await loadCaseContent(revision.id);
    showToast(`已保存为 Revision v${revision.revision_number}`);
  } catch (error) {
    showToast(error instanceof Error ? error.message : "保存失败");
  } finally {
    loading.value = false;
  }
}

async function createBenchmarkRun() {
  if (!runForm.datasetVersionId || !runForm.candidateVersionId || !runForm.scoringPolicyVersionId) return showToast("请填写 Dataset、Candidate 与 Scoring Policy Version ID");
  loading.value = true;
  try {
    runResult.value = await analystBenchApi.createBenchmarkRun({ dataset_version_id: runForm.datasetVersionId, candidate_version_id: runForm.candidateVersionId, scoring_policy_version_id: runForm.scoringPolicyVersionId });
    showToast("Benchmark Run 已创建；复制 Run ID 后可回填总览");
  } catch (error) {
    showToast(error instanceof Error ? error.message : "创建 Benchmark Run 失败");
  } finally { loading.value = false; }
}

async function loadRunComparison() {
  const configured = Object.entries(runIds).filter(([, id]) => id.trim());
  if (!configured.length) return showToast("请先填写至少一个 Benchmark Run ID");
  loading.value = true;
  try {
    const results = await Promise.all(
      configured.map(async ([name, id]) => {
        const run = await analystBenchApi.getBenchmarkRun(id.trim());
        const caseRuns = await analystBenchApi.getBenchmarkCaseRuns(id.trim());
        const details = await Promise.all(caseRuns.slice(0, 10).map((item) => analystBenchApi.getBenchmarkCaseResult(item.id)));
        return { name, run, caseRuns, details };
      }),
    );
    const fallback = { agent: 88.4, skill: 79.2, native: 63.8 };
    for (const item of results) {
      const score = extractScore(item.run.summary, fallback[item.name as keyof typeof fallback]);
      const card = scoreCards.value.find((entry) => entry.label.toLowerCase() === item.name);
      if (card) card.score = score;
      const row = matrixRows.value.find((entry) => entry.name.toLowerCase() === item.name);
      if (row && item.details.length) {
        row.scores = matrixCases.value.map((_, index) => extractScore(item.details[index] ?? {}, score));
      }
    }
    hasLoadedRuns.value = true;
    activeView.value = "dashboard";
    showToast("已读取 Benchmark Run，仪表盘已更新");
  } catch (error) {
    showToast(error instanceof Error ? error.message : "读取 Benchmark Run 失败");
  } finally {
    loading.value = false;
  }
}

function navigate(view: ViewName) {
  activeView.value = view;
  if (view === "dataset") void refreshCatalog();
  if (view === "results") void refreshDirectResults();
}

onMounted(() => { void refreshCatalog(); void loadDashboardData(); });
</script>

<template>
  <div class="app-shell">
    <aside class="sidebar">
      <div class="brand"><span>AnalystBench</span></div>
      <nav class="nav-list" aria-label="主导航">
        <button :class="['nav-item', { active: activeView === 'dashboard' }]" @click="navigate('dashboard')"><IconLayoutDashboard :size="19" />总览</button>
        <button :class="['nav-item', { active: activeView === 'dataset' }]" @click="navigate('dataset')"><IconDatabase :size="19" />测试集<IconChevronRight class="nav-arrow" :size="16" /></button>
        <button :class="['nav-item', { active: activeView === 'results' }]" @click="navigate('results')"><IconClipboardData :size="19" />评测结果<IconChevronRight class="nav-arrow" :size="16" /></button>
        <button :class="['nav-item', { active: activeView === 'analysis' }]" @click="navigate('analysis')"><IconChartLine :size="19" />评测分析<IconChevronRight class="nav-arrow" :size="16" /></button>
        <button class="nav-item muted"><IconSettings :size="19" />设置</button>
      </nav>
      <div class="sidebar-foot"><span>© 2026 AnalystBench</span><span>v1.0.0</span><small :class="connection">{{ connection === 'connected' ? 'API 已连接' : connection === 'offline' ? 'API 未连接' : '正在检查 API' }}</small></div>
    </aside>

    <main class="content-area">
      <header class="app-header">
        <div class="breadcrumb"><span>{{ activeView === 'dashboard' ? '总览' : activeView === 'dataset' ? '测试集' : activeView === 'results' ? '评测结果' : '评测分析' }}</span><span>/</span><strong>Kernel日志分析基准集</strong><button class="version-select">v1.3 <IconChevronDown :size="14" /></button></div>
        <div class="header-actions">
          <label class="date-input"><IconTerminal2 :size="15" /><input type="text" value="2025-06-22 ~ 2025-07-22" aria-label="时间范围" /></label>
          <button class="ghost-button"><IconFileExport :size="16" />导出报告</button>
          <button class="primary-button" @click="navigate('analysis')"><IconCloudUpload :size="16" />上传结果</button>
          <button class="avatar" aria-label="用户菜单">A</button>
        </div>
      </header>

      <section v-if="activeView === 'dashboard'" class="dashboard-page">
        <div v-if="!dashboardLoaded && !loading" class="empty-state surface" style="min-height:200px"><IconLayoutDashboard :size="30" /><p>暂无评测数据</p><span>使用 CLI evaluate 生成评测结果后刷新即可查看。</span><button class="ghost-button" @click="loadDashboardData"><IconRefresh :size="16" />加载数据</button></div>
        <template v-else>
        <section class="surface score-panel">
          <div class="panel-title">综合得分 <span>（越高越好）</span><IconInfoCircle :size="15" /></div>
          <div class="score-list">
            <article v-for="card in dashboardScoreCards" :key="card.label" class="score-item" :style="{ '--row-color': card.color }">
              <span>{{ card.label }}</span><strong>{{ card.score.toFixed(1) }}</strong><i><b :style="{ width: `${card.score}%` }" /></i>
            </article>
          </div>
        </section>

        <div class="chart-grid">
          <section class="surface chart-panel"><div class="panel-heading"><h2>按评分项得分对比</h2><span v-if="dashboardLoaded" class="api-badge"><IconCircleCheck :size="14" />真实运行数据</span></div><ChartCanvas kind="bar" :labels="dashboardCategoryLabels" :series="dashboardCategorySeries" :height="276" /></section>
          <section class="surface chart-panel"><div class="panel-heading"><h2>综合得分对比</h2></div><ChartCanvas kind="bar" :labels="dashboardMatrixCases" :series="dashboardMatrixRows.map(r => ({name: r.name, values: r.scores}))" :height="276" /></section>
        </div>

        <section class="surface matrix-panel">
          <div class="panel-heading"><h2>Case 详情对比 <span>（得分矩阵）</span></h2><button class="text-button" @click="navigate('results')">查看全部 <IconChevronRight :size="15" /></button></div>
          <div class="matrix-scroll"><table class="score-matrix"><thead><tr><th>方案</th><th v-for="caseName in dashboardMatrixCases" :key="caseName">{{ caseName }}</th><th>平均分</th></tr></thead><tbody><tr v-for="row in dashboardAverages" :key="row.name" :style="{ '--row-color': row.color }"><th class="row-color-dot"><i></i>{{ row.name }}</th><td v-for="(score, index) in row.scores" :key="`${row.name}-${index}`" class="row-color-text">{{ score.toFixed(1) }}</td><td class="average row-color-text">{{ row.average.toFixed(1) }}</td></tr></tbody></table></div>
        </section>
        </template>
      </section>

      <section v-else-if="activeView === 'dataset'" class="work-page">
        <div class="work-heading"><div><p class="eyebrow">CASE LIBRARY</p><h1>测试集管理</h1><p>直接连接当前后端的 Dataset、Case 和不可变 Revision 资源。</p></div><div class="button-row"><button class="ghost-button" @click="refreshCatalog"><IconRefresh :size="16" />刷新</button><button class="ghost-button" @click="openCaseForm"><IconPlus :size="16" />新建 Case</button><button class="ghost-button" @click="openStandaloneCategoryForm"><IconPlus :size="16" />新建问题类别</button><button class="primary-button" @click="showDatasetForm = !showDatasetForm"><IconPlus :size="16" />新建数据集</button></div></div>
        <div v-if="showDatasetForm" class="form-surface"><input v-model="datasetForm.name" placeholder="数据集名称" /><input v-model="datasetForm.datasetKey" placeholder="dataset_key（可选）" /><input v-model="datasetForm.description" placeholder="说明（可选）" /><button class="primary-button" @click="createDataset">创建</button></div>
        <div v-if="showStandaloneCategoryForm" class="form-surface category-standalone"><select v-model="caseForm.datasetId" @change="updateDraftDataset"><option value="" disabled>选择所属测评集</option><option v-for="dataset in datasets" :key="dataset.id" :value="dataset.id">{{ dataset.name }}</option></select><input v-model="categoryForm.key" placeholder="类别标识，例如 HM_PANIC_SYSMGR" /><input v-model="categoryForm.name" placeholder="类别名称（可选）" /><button class="primary-button" @click="createCategory">创建类别</button></div>
        <section v-if="showDraftForm" class="draft-surface case-form">
          <div>
            <p>选择测评集和类别后创建 Case；粘贴标准文档原文即可，Case Key 由后端按类别自动编号。</p>
            <div class="case-select-grid">
              <label>测评集<select v-model="caseForm.datasetId" @change="updateDraftDataset"><option value="" disabled>请选择测评集</option><option v-for="dataset in datasets" :key="dataset.id" :value="dataset.id">{{ dataset.name }}</option></select></label>
              <label>类别<select v-model="caseForm.categoryKey" :disabled="!caseForm.datasetId"><option value="" disabled>请选择类别</option><option v-for="category in draftCategories" :key="category.id" :value="category.category_key">{{ category.name }}（{{ category.category_key }}）</option></select></label>
              <button type="button" class="ghost-button category-create-toggle" @click="showCategoryForm = !showCategoryForm"><IconPlus :size="15" />新建类别</button>
            </div>
            <div v-if="showCategoryForm" class="category-create-row"><input v-model="categoryForm.key" placeholder="类别标识，例如 HM_PANIC_SYSMGR" /><input v-model="categoryForm.name" placeholder="类别名称（可选）" /><button type="button" class="ghost-button" @click="createCategory">创建类别</button></div>
            <textarea v-model="caseForm.answer" placeholder="粘贴标准文档（纯文本，无需 JSON）" />
          </div>
          <button class="primary-button" :disabled="!caseForm.datasetId || !caseForm.categoryKey" @click="createCase">创建 Case</button>
        </section>
        <div v-if="caseListUnavailable" class="api-notice"><IconInfoCircle :size="16" /><span>当前运行的后端尚未加载 Case 列表接口；重启后端以启用 <code>GET /api/v1/datasets/:id/cases</code> 后，Case 会自动显示。</span></div>
        <div v-if="caseListError" class="api-notice"><IconAlertCircle :size="16" /><span>{{ caseListError }}</span></div>
        <div class="dataset-layout">
          <section class="surface tree-panel">
            <div class="panel-heading"><h2><IconFolder :size="17" />数据结构</h2><span>{{ datasets.length }} Datasets</span></div>
            <div v-if="datasets.length" class="tree-list">
              <div v-for="item in datasets" :key="item.id" class="tree-group">
                <div class="tree-node">
                  <button :class="{ selected: selectedDatasetId === item.id }" @click="selectedDatasetId = item.id; selectedCaseId = ''"><IconDatabase :size="15" />{{ item.name }}<small>{{ datasetCases[item.id]?.length ?? 0 }} Cases</small></button>
                  <button class="tree-delete" title="删除测评集" @click.stop="deleteDataset(item)"><IconTrash :size="14" /></button>
                </div>
                <div v-for="category in datasetCategories[item.id]" :key="category.id" class="category-tree-group">
                  <div class="category-tree-heading"><span>{{ category.name }}</span><button class="tree-delete" title="删除类别" @click.stop="deleteCategory(item.id, category)"><IconTrash :size="13" /></button></div>
                  <div v-for="caseItem in (datasetCases[item.id] ?? []).filter((entry) => entry.category_id === category.id)" :key="caseItem.id" class="case-tree-row"><button :class="{ selected: selectedCaseId === caseItem.id }" @click="selectedDatasetId = item.id; selectedCaseId = caseItem.id">{{ caseItem.case_key }}</button><button class="tree-delete" title="删除 Case" @click.stop="deleteCase(caseItem)"><IconTrash :size="13" /></button></div>
                </div>
                <div v-for="caseItem in (datasetCases[item.id] ?? []).filter((entry) => !entry.category_id)" :key="caseItem.id" class="case-tree-row"><button :class="{ selected: selectedCaseId === caseItem.id }" @click="selectedDatasetId = item.id; selectedCaseId = caseItem.id">{{ caseItem.case_key }}</button><button class="tree-delete" title="删除 Case" @click.stop="deleteCase(caseItem)"><IconTrash :size="13" /></button></div>
              </div>
            </div>
            <div v-else class="empty-state"><IconDatabase :size="30" /><p>暂无数据集</p><span>创建数据集后会显示在这里。</span></div>
          </section>
          <section class="surface detail-panel">
            <div class="panel-heading"><h2>{{ selectedCaseId ? 'Case 详情' : '数据集详情' }}</h2><span>{{ selectedCase?.case_key || selectedDataset?.dataset_key || '未选择' }}</span></div>
            <template v-if="selectedCase">
              <dl><div><dt>Case Key</dt><dd>{{ selectedCase.case_key }}</dd></div><div><dt>类别</dt><dd>{{ selectedCaseCategory?.name || '未分类' }}</dd></div><div><dt>最新 Revision</dt><dd><span class="status-good">v{{ selectedCase.revisions.at(-1)?.revision_number ?? 0 }}</span></dd></div></dl>
              <div class="code-block">{{ JSON.stringify({ case_key: selectedCase.case_key, category: selectedCaseCategory?.category_key || null, latest_revision: selectedCase.revisions.at(-1)?.revision_number ?? 0 }, null, 2) }}</div>
            </template>
            <template v-else-if="selectedDataset"><dl><div><dt>Dataset Key</dt><dd>{{ selectedDataset.dataset_key }}</dd></div><div><dt>名称</dt><dd>{{ selectedDataset.name }}</dd></div><div><dt>说明</dt><dd>{{ selectedDataset.description || '—' }}</dd></div></dl><div class="code-block">{{ JSON.stringify(selectedDataset, null, 2) }}</div></template>
            <div v-else class="empty-state"><IconInfoCircle :size="30" /><p>选择左侧数据集查看详情</p></div>
          </section>
        </div>
        <section v-if="selectedCase" class="surface json-edit-panel">
          <div class="json-editor-head">
            <div><strong>标准文档编辑器</strong><span>保存会生成新 Revision，不会覆盖历史内容。</span></div>
            <button class="primary-button" :disabled="contentLoading" @click="saveCaseJson">{{ contentLoading ? '读取中…' : '保存为新 Revision' }}</button>
          </div>
          <textarea v-model="editableCaseJson" class="json-editor" :disabled="contentLoading" />
          <p class="json-hint">直接编辑标准文档原文；保存后生成不可变 Revision。</p>
        </section>
      </section>

      <section v-if="activeView === 'results'" class="work-page results-page">
        <div class="work-heading"><div><p class="eyebrow">DIRECT RESULTS</p><h1>评测结果</h1><p>查看 CLI evaluate 命令生成的 direct_file 评测结果，包含评分明细、claim 命中对比与排序。</p></div><button class="ghost-button" @click="refreshDirectResults"><IconRefresh :size="16" />刷新</button></div>
        <div v-if="directResultList.length === 0 && !resultLoading" class="empty-state surface" style="min-height:200px"><IconClipboardData :size="30" /><p>暂无评测结果</p><span>使用 CLI <code>ab evaluate</code> 生成结果后刷新即可查看。</span></div>
        <div v-else class="results-layout">
          <section class="surface result-list-panel">
            <div class="panel-heading"><h2><IconClipboardData :size="17" />结果列表</h2><span>{{ directResultList.length }} 条</span></div>
            <div v-if="directResultList.length" class="result-list">
              <div v-for="item in directResultList" :key="item.id" class="result-list-item-row">
                <div :class="['result-list-item', { selected: selectedResultId === item.id }]" @click="loadDirectResult(item)">
                  <div class="result-list-header"><strong>{{ item.case_key }}</strong><small>{{ item.id }}</small><span class="result-list-status"><span :class="item.status === 'completed' ? 'tag-match' : 'tag-missing'">{{ item.status }}</span></span></div>
                </div>
                <button class="tree-delete" title="删除评测结果" @click.stop="deleteDirectResult(item)"><IconTrash :size="14" /></button>
              </div>
            </div>
            <div v-else class="empty-state"><IconClipboardData :size="22" /><p>暂无结果</p></div>
          </section>
          <section v-if="selectedResultData" class="surface result-detail-panel">
            <template v-if="parseSummary(selectedResultData)">
              <div class="panel-heading"><h2>评测结果详情</h2><span>{{ String(selectedResultData.case_key ?? '') }} · {{ String(selectedResultData.id ?? '') }}</span></div>
              <p class="engine-note">{{ parseSummary(selectedResultData)!.engine_note }}</p>
              <section class="surface result-score-panel">
                <div class="panel-title">综合得分 <span>（越高越好）</span></div>
                <div class="result-score-list">
                  <div v-for="(report, idx) in resultRankedReports" :key="'sc-' + report.candidate_name" class="result-score-row" :style="{ '--row-color': resultColors[idx] }">
                    <span class="result-score-name"><i></i>{{ report.candidate_name }}</span>
                    <strong>{{ parseFloat(report.score).toFixed(1) }}</strong>
                    <i class="result-score-bar"><b :style="{ width: `${parseFloat(report.score)}%` }" /></i>
                    <span :class="report.passed ? 'tag-match' : 'tag-missing'">{{ report.passed ? '通过' : '未通过' }}</span>
                  </div>
                </div>
              </section>
              <section class="surface result-overview-section">
                <h3>总览</h3>
                <table class="result-overview-table">
                  <thead><tr><th>排名</th><th>报告</th><th>得分</th><th>结果</th><th>命中</th><th>缺失链</th></tr></thead>
                  <tbody>
                    <tr v-for="(report, idx) in parseSummary(selectedResultData)!.reports" :key="report.candidate_name">
                      <td>{{ idx + 1 }}</td>
                      <td :class="toneFromName(report.candidate_name)"><i></i>{{ report.candidate_name }}</td>
                      <td>{{ parseFloat(report.score).toFixed(1) }}</td>
                      <td><span :class="report.passed ? 'tag-match' : 'tag-missing'">{{ report.passed ? '通过' : '未通过' }}</span></td>
                      <td>{{ report.hit_count }}/{{ report.claim_count }}</td>
                      <td>{{ report.missing_chains.length ? report.missing_chains.join('；') : '—' }}</td>
                    </tr>
                  </tbody>
                </table>
              </section>
              <section v-for="report in parseSummary(selectedResultData)!.reports" :key="report.candidate_name" class="surface claim-detail-section">
                <div class="panel-heading"><h3 :class="toneFromName(report.candidate_name)"><i></i>{{ report.candidate_name }}</h3><span>得分 {{ parseFloat(report.score).toFixed(1) }} / 100</span></div>
                <table class="claim-detail-table">
                  <thead><tr><th>评分项</th><th>类型</th><th>判定</th><th>得分</th><th>关键字匹配</th><th>结论语义</th></tr></thead>
                  <tbody>
                    <tr v-for="claim in report.claims" :key="claim.id">
                      <td class="claim-statement">{{ claim.statement }}</td>
                      <td class="claim-type">{{ claim.type === 'root_cause' ? '根因' : claim.type === 'classification' ? '分类' : '分析链' }}</td>
                      <td><span :class="relationTagClass(claim.overall_relation)">{{ claim.relation_label }}</span></td>
                      <td class="claim-score">{{ parseFloat(claim.score).toFixed(1) }}</td>
                      <td>
                        <template v-if="claim.keyword_match !== null">
                          <span :class="claim.keyword_match ? 'tag-match' : 'tag-missing'">{{ claim.keyword_match ? '命中' : '未命中' }}</span>
                          <small v-if="claim.evidence_keyword">（{{ claim.keyword_score }}）</small>
                        </template>
                        <template v-else>—</template>
                      </td>
                      <td>
                        <template v-if="claim.conclusion_similarity !== null">
                          {{ (claim.conclusion_similarity * 100).toFixed(0) }}%
                          <small v-if="claim.conclusion_score">（{{ claim.conclusion_score }}）</small>
                        </template>
                        <template v-else>—</template>
                      </td>
                    </tr>
                  </tbody>
                </table>
                <div v-if="report.claims.some((c) => c.closest_keyword_line)" class="closest-lines">
                  <strong>最接近行</strong>
                  <div v-for="claim in report.claims.filter((c) => c.closest_keyword_line)" :key="'cl-' + claim.id" class="closest-line-item">
                    <span>{{ claim.id }}（第 {{ claim.closest_keyword_line!.line_number }} 行）：</span>
                    <code>{{ claim.closest_keyword_line!.quote }}</code>
                    <small>相似度 {{ (claim.closest_keyword_line!.diagnostic_similarity * 100).toFixed(1) }}%</small>
                  </div>
                </div>
              </section>
              <section v-if="parseSummary(selectedResultData)!.comparisons.length" class="surface comparison-section">
                <div class="panel-heading"><h3>报告对比</h3></div>
                <table class="comparison-table">
                  <thead><tr><th>基线</th><th>候选</th><th>差值</th><th>判定</th></tr></thead>
                  <tbody>
                    <tr v-for="comp in parseSummary(selectedResultData)!.comparisons" :key="`${comp.baseline}-${comp.candidate}`">
                      <td>{{ comp.baseline }}</td>
                      <td>{{ comp.candidate }}</td>
                      <td>{{ parseFloat(comp.delta).toFixed(1) }}</td>
                      <td><span :class="comp.classification === 'improved' ? 'tag-match' : comp.classification === 'degraded' ? 'tag-missing' : 'tag-partial'">{{ comp.classification_label }}</span></td>
                    </tr>
                  </tbody>
                </table>
              </section>
              <section class="surface metrics-section">
                <div class="panel-heading"><h3>指标概要</h3></div>
                <table class="metrics-table">
                  <thead><tr><th>报告</th><th>覆盖率</th><th>根因精确</th><th>矛盾数</th><th>禁止命中</th><th>缺失链数</th></tr></thead>
                  <tbody>
                    <tr v-for="report in parseSummary(selectedResultData)!.reports" :key="'m-' + report.candidate_name">
                      <td :class="toneFromName(report.candidate_name)"><i></i>{{ report.candidate_name }}</td>
                      <td>{{ (Number(report.metrics.claim_coverage) * 100).toFixed(1) }}%</td>
                      <td><span :class="Boolean(report.metrics.root_cause_exact) ? 'tag-match' : 'tag-missing'">{{ Boolean(report.metrics.root_cause_exact) ? '是' : '否' }}</span></td>
                      <td>{{ report.metrics.contradiction_count }}</td>
                      <td>{{ report.metrics.forbidden_hit_count }}</td>
                      <td>{{ report.metrics.missing_chain_count }}</td>
                    </tr>
                  </tbody>
                </table>
              </section>
            </template>
            <div v-else class="empty-state"><IconInfoCircle :size="30" /><p>选择左侧结果查看详情</p></div>
          </section>
          <section v-else class="surface result-detail-panel">
            <div class="empty-state"><IconInfoCircle :size="30" /><p>选择左侧结果查看详情</p></div>
          </section>
        </div>
      </section>

      <section v-else class="work-page analysis-page">
        <div class="work-heading"><div><p class="eyebrow">EVALUATION WORKBENCH</p><h1>评测分析</h1><p>创建 Benchmark Run，或输入已有 Run ID 回填总览。</p></div><button class="ghost-button" @click="refreshCatalog"><IconRefresh :size="16" />刷新后端状态</button></div>
        <div class="analysis-grid"><section class="surface form-card"><div class="panel-heading"><h2>创建 Benchmark Run</h2><span>Current API</span></div><p class="form-note">此表单直接调用当前后端的 <code>POST /api/v1/benchmark-runs</code>，需要三个已冻结的版本 ID。</p><label>Dataset Version ID<input v-model="runForm.datasetVersionId" placeholder="Dataset Version UUID" /></label><label>Candidate Version ID<input v-model="runForm.candidateVersionId" placeholder="Candidate Version UUID" /></label><label>Scoring Policy Version ID<input v-model="runForm.scoringPolicyVersionId" placeholder="Scoring Policy UUID" /></label><button class="primary-button wide" @click="createBenchmarkRun"><IconCloudUpload :size="16" />发起评测运行</button><pre v-if="runResult" class="result-block">{{ JSON.stringify(runResult, null, 2) }}</pre></section><section class="surface form-card"><div class="panel-heading"><h2>关联 Benchmark Run</h2><span>Dashboard Adapter</span></div><p class="form-note">当前后端按 Run 查询且没有 Run 列表接口。填写已有 ID 后，前端将读取 Run、Case Runs 与每条结果，并更新总览矩阵。</p><label>Agent Run ID<input v-model="runIds.agent" placeholder="Benchmark Run UUID" /></label><label>Skill Run ID<input v-model="runIds.skill" placeholder="Benchmark Run UUID" /></label><label>Native Run ID<input v-model="runIds.native" placeholder="Benchmark Run UUID" /></label><button class="primary-button wide" @click="loadRunComparison"><IconChartLine :size="16" />应用到仪表盘</button><div class="api-map"><strong>已接入接口</strong><code>GET /benchmark-runs/:id</code><code>GET /benchmark-runs/:id/case-runs</code><code>GET /benchmark-case-runs/:id/result</code></div></section></div>
      </section>
    </main>

    <div v-if="loading" class="busy-layer"><IconLoader2 :size="25" class="spin" />正在与 AnalystBench API 通信…</div>
    <div v-if="toast" class="toast"><IconAlertCircle :size="17" />{{ toast }}</div>
  </div>
</template>
