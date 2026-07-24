<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from "vue";
import {
  IconAlertCircle,
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
import { analystBenchApi, ApiError, type AppSettings, type CaseCategory, type CaseRevisionContent, type Dataset, type DatasetCase, type DirectResultListItem, type DirectResultReportSummary, type DirectResultClaim, type DirectResultComparison, type DirectResultSummary, type DirectResultStats, type StatsTestSet, type StatsCandidate, type LocalCaseTree } from "./api";

type ViewName = "dashboard" | "dataset" | "results" | "settings";
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
const hasLoadedRuns = ref(false);

// ─── Local case tree ───
const localCaseTree = ref<LocalCaseTree[]>([]);
const selectedLocalCasePath = ref("");
const selectedLocalCaseData = ref<Record<string, unknown> | null>(null);

async function loadLocalCaseTree() {
  try {
    localCaseTree.value = await analystBenchApi.getLocalCaseTree();
    connection.value = "connected";
  } catch {
    connection.value = "offline";
    localCaseTree.value = [];
  }
}

async function selectLocalCase(tsKey: string, catKey: string, csKey: string, csNode: LocalCaseTree) {
  const path = `${tsKey}/${catKey}/${csKey}`;
  selectedLocalCasePath.value = path;
  try {
    selectedLocalCaseData.value = await analystBenchApi.getLocalCase(path);
    connection.value = "connected";
  } catch (error) {
    selectedLocalCaseData.value = null;
    showToast(error instanceof Error ? error.message : "读取 Case 失败");
  }
}

// ─── Direct Results view ───
type ResultSource = "tmp" | "formal";
const resultSource = ref<ResultSource>("tmp");
const allDirectResults = ref<DirectResultListItem[]>([]);
const directResultList = ref<DirectResultListItem[]>([]);
const selectedResultId = ref("");
const selectedResultData = ref<Record<string, unknown> | null>(null);
const resultLoading = ref(false);

// ─── Promote/move dialog ───
const showMoveDialog = ref(false);
const moveDialogItem = ref<DirectResultListItem | null>(null);
const moveDialogMode = ref<"promote" | "move">("promote");
const moveForm = reactive({ test_set: "", category: "", case_dir: "" });

// Cascade select options derived from localCaseTree
const moveTestSetOptions = computed(() => localCaseTree.value.map((ts) => ({ key: ts.key, name: ts.name })));
const moveCategoryOptions = computed(() => {
  const ts = localCaseTree.value.find((t) => t.key === moveForm.test_set);
  return ts?.children?.map((cat) => ({ key: cat.key, name: cat.name })) ?? [];
});
const moveCaseDirOptions = computed(() => {
  const ts = localCaseTree.value.find((t) => t.key === moveForm.test_set);
  const cat = ts?.children?.find((c) => c.key === moveForm.category);
  return cat?.children?.map((cs) => ({ key: cs.key, name: cs.name })) ?? [];
});

function onMoveTestSetChange() {
  moveForm.category = "";
  moveForm.case_dir = "";
}
function onMoveCategoryChange() {
  moveForm.case_dir = "";
}

// ─── Settings view ───
const appSettings = ref<AppSettings>({ results_tmp_path: "data/results/tmp", results_formal_path: "data/results" });

function parseSummary(data: Record<string, unknown>): DirectResultSummary | null {
  const s = data.summary;
  if (!s || typeof s !== "object") return null;
  return s as DirectResultSummary;
}

function toneFromRank(name: string): Tone {
  const idx = activeCandidates.value.findIndex((c) => c.name === name);
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

function formatResultId(item: DirectResultListItem): string {
  if (item.source === "tmp") {
    return `${item.case_dir} › ${item.timestamp}`;
  }
  if (item.timestamp) {
    return `${item.test_set} › ${item.category} › ${item.case_dir} › ${item.timestamp}`;
  }
  return item.id;
}

// Build tree for formal results: test_set > category > case_dir > timestamps
const formalTree = computed(() => {
  const items = allDirectResults.value.filter((item) => item.source === "formal");
  const tree: Record<string, Record<string, Record<string, DirectResultListItem[]>>> = {};
  for (const item of items) {
    const ts = item.test_set || "default";
    const cat = item.category || "uncategorized";
    const cd = item.case_dir || "case";
    if (!tree[ts]) tree[ts] = {};
    if (!tree[ts][cat]) tree[ts][cat] = {};
    if (!tree[ts][cat][cd]) tree[ts][cat][cd] = [];
    tree[ts][cat][cd].push(item);
  }
  return tree;
});

function openMoveDialog(item: DirectResultListItem, mode: "promote" | "move") {
  moveDialogItem.value = item;
  moveDialogMode.value = mode;
  // Try to match existing values to localCaseTree keys; fall back to empty
  const tsMatch = localCaseTree.value.find((t) => t.key === item.test_set);
  moveForm.test_set = tsMatch ? item.test_set : "";
  const catMatch = tsMatch?.children?.find((c) => c.key === item.category);
  moveForm.category = catMatch ? item.category : "";
  const csMatch = catMatch?.children?.find((cs) => cs.key === item.case_dir);
  moveForm.case_dir = csMatch ? item.case_dir : "";
  showMoveDialog.value = true;
}

async function confirmMoveDialog() {
  if (!moveForm.test_set || !moveForm.category || !moveForm.case_dir) return showToast("请填写测试集、问题分类和 Case 目录");
  const item = moveDialogItem.value;
  if (!item) return;
  loading.value = true;
  try {
    const dest = { test_set: moveForm.test_set, category: moveForm.category, case_dir: moveForm.case_dir };
    if (moveDialogMode.value === "promote") {
      await analystBenchApi.promoteDirectResult(item.id, dest);
      showToast("已归档到正式结果集");
    } else {
      await analystBenchApi.moveDirectResult(item.id, dest);
      showToast("已移动结果");
    }
    showMoveDialog.value = false;
    await refreshDirectResults();
    dashboardLoaded.value = false;
    void loadDashboardData();
  } catch (error) {
    showToast(error instanceof Error ? error.message : (moveDialogMode.value === "promote" ? "归档失败" : "移动失败"));
  } finally {
    loading.value = false;
  }
}

async function loadAppSettings() {
  try {
    appSettings.value = await analystBenchApi.getAppSettings();
    connection.value = "connected";
  } catch {
    connection.value = "offline";
  }
}

async function saveAppSettings() {
  loading.value = true;
  try {
    appSettings.value = await analystBenchApi.updateAppSettings(appSettings.value);
    connection.value = "connected";
    showToast("设置已保存");
  } catch (error) {
    showToast(error instanceof Error ? error.message : "保存设置失败");
  } finally {
    loading.value = false;
  }
}

async function refreshDirectResults() {
  resultLoading.value = true;
  try {
    allDirectResults.value = await analystBenchApi.listDirectResults();
    directResultList.value = allDirectResults.value.filter((item) => item.source === resultSource.value);
    connection.value = "connected";
  } catch {
    connection.value = "offline";
    allDirectResults.value = [];
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

// ─── Dashboard data from direct_results stats API ───
const dashboardStats = ref<DirectResultStats | null>(null);
const dashboardLoaded = ref(false);
const selectedTestSet = ref("");  // "" means "全部测试集"

async function loadDashboardData() {
  loading.value = true;
  try {
    dashboardStats.value = await analystBenchApi.getDirectResultStats();
    dashboardLoaded.value = true;
    connection.value = "connected";
    // Default to first test set if available
    if (!selectedTestSet.value && dashboardStats.value.test_sets.length) {
      selectedTestSet.value = dashboardStats.value.test_sets[0].key;
    }
  } catch {
    connection.value = "offline";
    dashboardStats.value = null;
    dashboardLoaded.value = false;
  } finally {
    loading.value = false;
  }
}

// Candidates for the selected test set (or all)
const activeCandidates = computed(() => {
  if (!dashboardStats.value) return [];
  if (selectedTestSet.value) {
    const ts = dashboardStats.value.test_sets.find((t) => t.key === selectedTestSet.value);
    return ts?.candidates ?? [];
  }
  return dashboardStats.value.candidates;
});

// Categories for the selected test set
const activeCategories = computed(() => {
  if (!dashboardStats.value) return [];
  if (selectedTestSet.value) {
    const ts = dashboardStats.value.test_sets.find((t) => t.key === selectedTestSet.value);
    return ts?.categories ?? [];
  }
  // Aggregate all categories across test sets
  const allCats: StatsTestSet["categories"] = [];
  for (const ts of dashboardStats.value.test_sets) {
    for (const cat of ts.categories) {
      const existing = allCats.find((c) => c.key === cat.key);
      if (existing) {
        // Merge candidates
        for (const cand of cat.candidates) {
          const ec = existing.candidates.find((c) => c.name === cand.name);
          if (ec) {
            ec.avg_score = (ec.avg_score + cand.avg_score) / 2;
          } else {
            existing.candidates.push(cand);
          }
        }
        existing.case_count += cat.case_count;
      } else {
        allCats.push({ ...cat });
      }
    }
  }
  return allCats;
});

// Dashboard score cards: candidates by average score
const dashboardScoreCards = computed(() =>
  activeCandidates.value.map((c, idx) => ({
    label: wrapName(c.name),
    score: c.avg_score,
    tone: toneFromRank(c.name),
    color: resultColors[idx % resultColors.length],
    change: "",
  }))
);

// Category comparison table data
const categoryComparisonRows = computed(() =>
  activeCandidates.value.map((cand, idx) => ({
    name: wrapName(cand.name),
    color: resultColors[idx % resultColors.length],
    categoryScores: activeCategories.value.map((cat) => {
      const catCand = cat.candidates.find((c) => c.name === cand.name);
      return catCand?.avg_score ?? 0;
    }),
    average: cand.avg_score,
  }))
);

// Bar chart: categories as labels, candidates as series
const categoryBarLabels = computed(() =>
  activeCategories.value.map((cat) => cat.name)
);

const categoryBarSeries = computed(() =>
  activeCandidates.value.map((c, idx) => ({
    name: wrapName(c.name),
    values: activeCategories.value.map((cat) => {
      const catCand = cat.candidates.find((cc) => cc.name === c.name);
      return catCand?.avg_score ?? 0;
    }),
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

function navigate(view: ViewName) {
  activeView.value = view;
  if (view === "dataset") void loadLocalCaseTree();
  if (view === "results") void refreshDirectResults();
  if (view === "settings") void loadAppSettings();
}

onMounted(() => { void loadLocalCaseTree(); void loadDashboardData(); });
</script>

<template>
  <div class="app-shell">
    <aside class="sidebar">
      <div class="brand"><span>AnalystBench</span></div>
      <nav class="nav-list" aria-label="主导航">
        <button :class="['nav-item', { active: activeView === 'dashboard' }]" @click="navigate('dashboard')"><IconLayoutDashboard :size="19" />总览</button>
        <button :class="['nav-item', { active: activeView === 'dataset' }]" @click="navigate('dataset')"><IconDatabase :size="19" />测试集<IconChevronRight class="nav-arrow" :size="16" /></button>
        <button :class="['nav-item', { active: activeView === 'results' }]" @click="navigate('results')"><IconClipboardData :size="19" />评测结果<IconChevronRight class="nav-arrow" :size="16" /></button>
        <button :class="['nav-item muted', { active: activeView === 'settings' }]" @click="navigate('settings')"><IconSettings :size="19" />设置<IconChevronRight class="nav-arrow" :size="16" /></button>
      </nav>
      <div class="sidebar-foot"><span>© 2026 AnalystBench</span><span>v1.0.0</span><small :class="connection">{{ connection === 'connected' ? 'API 已连接' : connection === 'offline' ? 'API 未连接' : '正在检查 API' }}</small></div>
    </aside>

    <main class="content-area">
      <header class="app-header">
        <div class="breadcrumb"><span>{{ activeView === 'dashboard' ? '总览' : activeView === 'dataset' ? '测试集' : activeView === 'results' ? '评测结果' : '设置' }}</span></div>
        <div class="header-actions">
          <label class="date-input"><IconTerminal2 :size="15" /><input type="text" value="2025-06-22 ~ 2025-07-22" aria-label="时间范围" /></label>
          <button class="ghost-button"><IconFileExport :size="16" />导出报告</button>
          <button class="avatar" aria-label="用户菜单">A</button>
        </div>
      </header>

      <section v-if="activeView === 'dashboard'" class="dashboard-page">
        <div v-if="(!dashboardLoaded || !dashboardStats?.candidates?.length) && !loading" class="empty-state surface" style="min-height:200px"><IconLayoutDashboard :size="30" /><p>暂无评测数据</p><span>使用 CLI evaluate 生成评测结果后刷新即可查看。</span><button class="ghost-button" @click="loadDashboardData"><IconRefresh :size="16" />加载数据</button></div>
        <template v-else-if="dashboardStats?.candidates?.length">
        <div class="test-set-selector">
          <label><IconDatabase :size="16" />测试集</label>
          <select v-model="selectedTestSet">
            <option value="">全部测试集</option>
            <option v-for="ts in dashboardStats?.test_sets ?? []" :key="ts.key" :value="ts.key">{{ ts.name }}</option>
          </select>
          <button class="ghost-button" @click="void loadDashboardData()"><IconRefresh :size="16" />刷新</button>
        </div>
        <section class="surface score-panel">
          <div class="panel-title">综合得分 <span>（越高越好）</span><IconInfoCircle :size="15" /></div>
          <div class="score-list">
            <article v-for="card in dashboardScoreCards" :key="card.label" class="score-item" :style="{ '--row-color': card.color }">
              <span>{{ card.label }}</span><strong>{{ card.score.toFixed(1) }}</strong><i><b :style="{ width: `${card.score}%` }" /></i>
            </article>
          </div>
        </section>

        <div class="chart-grid">
          <section class="surface chart-panel"><div class="panel-heading"><h2>按问题种类得分对比</h2><span v-if="dashboardLoaded" class="api-badge"><IconCircleCheck :size="14" />真实运行数据</span></div><ChartCanvas kind="bar" :labels="categoryBarLabels" :series="categoryBarSeries" :height="276" /></section>
          <section class="surface chart-panel"><div class="panel-heading"><h2>综合得分对比</h2></div><ChartCanvas kind="bar" :labels="dashboardScoreCards.map(c => c.label)" :series="[{name: '得分', values: dashboardScoreCards.map(c => c.score)}]" :height="276" /></section>
        </div>

        <section class="surface matrix-panel">
          <div class="panel-heading"><h2>按问题种类对比 <span>（各分类平均得分）</span></h2><button class="text-button" @click="navigate('results')">查看全部 <IconChevronRight :size="15" /></button></div>
          <div class="matrix-scroll"><table class="score-matrix"><thead><tr><th>方案</th><th v-for="cat in activeCategories" :key="cat.key">{{ cat.name }}<small v-if="cat.case_count > 1">（{{ cat.case_count }} case）</small></th><th>总平均</th></tr></thead><tbody><tr v-for="row in categoryComparisonRows" :key="row.name" :style="{ '--row-color': row.color }"><th class="row-color-dot"><i></i>{{ row.name }}</th><td v-for="(score, index) in row.categoryScores" :key="`${row.name}-${index}`" class="row-color-text">{{ score.toFixed(1) }}</td><td class="average row-color-text">{{ row.average.toFixed(1) }}</td></tr></tbody></table></div>
        </section>
        </template>
      </section>

      <section v-else-if="activeView === 'dataset'" class="work-page">
        <div class="work-heading"><div><p class="eyebrow">CASE LIBRARY</p><h1>测试集</h1><p>浏览本地 case.json 文件，按测试集和问题分类组织。</p></div><button class="ghost-button" @click="loadLocalCaseTree"><IconRefresh :size="16" />刷新</button></div>
        <div v-if="localCaseTree.length === 0 && !loading" class="empty-state surface" style="min-height:200px"><IconFolder :size="30" /><p>暂无本地 Case</p><span>将 case.json 放入 data/results/{test_set}/{category}/{case_dir}/ 目录后刷新即可查看。</span></div>
        <div v-else class="dataset-layout">
          <section class="surface tree-panel">
            <div class="panel-heading"><h2><IconFolder :size="17" />Case 目录</h2></div>
            <div class="result-tree">
              <div v-for="ts in localCaseTree" :key="ts.key" class="result-tree-group">
                <span class="result-tree-heading">{{ ts.name }}</span>
                <div v-for="cat in ts.children" :key="cat.key" class="result-tree-group">
                  <span class="result-tree-subheading">{{ cat.name }}</span>
                  <div v-for="cs in cat.children" :key="cs.key">
                    <div :class="['result-tree-leaf', { selected: selectedLocalCasePath === `${ts.key}/${cat.key}/${cs.key}` }]" @click="selectLocalCase(ts.key, cat.key, cs.key, cs)">
                      <span class="result-tree-leaf-name">{{ cs.case_data?.case_key || cs.name }}</span>
                      <span class="result-tree-leaf-meta">{{ cs.case_data?.claims_count ?? 0 }} 评分项 · {{ cs.case_data?.result_count ?? 0 }} 次评测</span>
                    </div>
                  </div>
                </div>
              </div>
            </div>
          </section>
          <section class="surface detail-panel">
            <div class="panel-heading"><h2>Case 详情</h2><span>{{ selectedLocalCasePath || '未选择' }}</span></div>
            <template v-if="selectedLocalCaseData">
              <dl>
                <div><dt>Case Key</dt><dd>{{ (selectedLocalCaseData.case as Record<string,unknown>)?.case_key ?? '—' }}</dd></div>
                <div><dt>问题分类</dt><dd>{{ ((selectedLocalCaseData.case as Record<string,unknown>)?.category as Record<string,unknown>)?.name ?? '—' }}</dd></div>
                <div><dt>测试集</dt><dd>{{ ((selectedLocalCaseData.case as Record<string,unknown>)?.test_set as Record<string,unknown>)?.name ?? '—' }}</dd></div>
                <div><dt>评分项数</dt><dd>{{ ((selectedLocalCaseData.eval_spec_draft as Record<string,unknown>)?.claims as unknown[])?.length ?? 0 }}</dd></div>
              </dl>
              <div class="code-block">{{ JSON.stringify(selectedLocalCaseData, null, 2) }}</div>
            </template>
            <div v-else class="empty-state"><IconInfoCircle :size="30" /><p>选择左侧 Case 查看详情</p></div>
          </section>
        </div>
      </section>

      <section v-if="activeView === 'results'" class="work-page results-page">
        <div class="work-heading"><div><p class="eyebrow">EVALUATION RESULTS</p><h1>评测结果</h1><p>查看评测结果，临时结果可归档到正式结果集。</p></div><button class="ghost-button" @click="resultSource === 'tmp' ? refreshDirectResults() : refreshDirectResults()"><IconRefresh :size="16" />刷新</button></div>
        <div class="source-tabs">
          <button :class="['source-tab', { active: resultSource === 'tmp' }]" @click="resultSource = 'tmp'; void refreshDirectResults()"><IconFolder :size="16" />临时结果</button>
          <button :class="['source-tab', { active: resultSource === 'formal' }]" @click="resultSource = 'formal'; void refreshDirectResults()"><IconClipboardData :size="16" />正式结果</button>
        </div>
        <!-- Tmp results -->
        <template v-if="resultSource === 'tmp'">
          <div v-if="directResultList.length === 0 && !resultLoading" class="empty-state surface" style="min-height:200px"><IconClipboardData :size="30" /><p>暂无临时评测结果</p><span>使用 CLI <code>ab evaluate</code> 生成结果后刷新即可查看。</span></div>
          <div v-else class="results-layout">
            <section class="surface result-list-panel">
              <div class="panel-heading"><h2><IconFolder :size="17" />临时结果</h2><span>{{ directResultList.length }} 条</span></div>
              <div v-if="directResultList.length" class="result-list">
                <div v-for="item in directResultList" :key="item.id" class="result-tree-leaf-row">
                  <div :class="['result-tree-leaf', { selected: selectedResultId === item.id }]" @click="loadDirectResult(item)">
                    <span class="result-tree-leaf-name">{{ formatResultId(item) }}</span>
                    <span class="result-tree-leaf-meta"><span :class="item.status === 'completed' ? 'tag-match' : 'tag-missing'">{{ item.status }}</span></span>
                  </div>
                  <button class="ghost-button" title="归档到正式结果集" @click.stop="openMoveDialog(item, 'promote')"><IconCloudUpload :size="14" />归档</button>
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
        </template>
        <!-- Formal results -->
        <template v-if="resultSource === 'formal'">
          <div v-if="directResultList.length === 0 && !resultLoading" class="empty-state surface" style="min-height:200px"><IconClipboardData :size="30" /><p>暂无正式评测结果</p><span>归档临时结果后即可在此查看。</span></div>
          <div v-else class="results-layout">
            <section class="surface result-list-panel">
              <div class="panel-heading"><h2><IconClipboardData :size="17" />正式结果</h2><span>{{ directResultList.length }} 条</span></div>
              <div v-if="Object.keys(formalTree).length" class="result-tree">
                <div v-for="(catTree, tsKey) in formalTree" :key="tsKey" class="result-tree-group">
                  <span class="result-tree-heading">{{ tsKey }}</span>
                  <div v-for="(caseTree, catKey) in catTree" :key="catKey" class="result-tree-group">
                    <span class="result-tree-subheading">{{ catKey }}</span>
                    <div v-for="(items, cdKey) in caseTree" :key="cdKey">
                      <span class="result-tree-leaf-label">{{ cdKey }}</span>
                      <div v-for="item in items" :key="item.id" class="result-tree-leaf-row">
                        <div :class="['result-tree-leaf', { selected: selectedResultId === item.id }]" @click="loadDirectResult(item)">
                          <span class="result-tree-leaf-name">{{ item.timestamp }}</span>
                          <span class="result-tree-leaf-meta"><span :class="item.status === 'completed' ? 'tag-match' : 'tag-missing'">{{ item.status }}</span></span>
                        </div>
                        <button class="tree-move" title="Move" @click.stop="openMoveDialog(item, 'move')"><IconChevronRight :size="14" /></button>
                        <button class="tree-delete" title="删除评测结果" @click.stop="deleteDirectResult(item)"><IconTrash :size="14" /></button>
                      </div>
                    </div>
                  </div>
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
                <section v-for="report in parseSummary(selectedResultData)!.reports" :key="'f-' + report.candidate_name" class="surface claim-detail-section">
                  <div class="panel-heading"><h3 :class="toneFromName(report.candidate_name)"><i></i>{{ report.candidate_name }}</h3><span>得分 {{ parseFloat(report.score).toFixed(1) }} / 100</span></div>
                  <table class="claim-detail-table">
                    <thead><tr><th>评分项</th><th>类型</th><th>判定</th><th>得分</th><th>关键字匹配</th><th>结论语义</th></tr></thead>
                    <tbody>
                      <tr v-for="claim in report.claims" :key="'f-' + claim.id">
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
                    <div v-for="claim in report.claims.filter((c) => c.closest_keyword_line)" :key="'fcl-' + claim.id" class="closest-line-item">
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
                      <tr v-for="report in parseSummary(selectedResultData)!.reports" :key="'fm-' + report.candidate_name">
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
        </template>
      </section>

      <!-- Settings view -->
      <section v-if="activeView === 'settings'" class="work-page settings-page">
        <div class="work-heading"><div><p class="eyebrow">SETTINGS</p><h1>设置</h1><p>配置评测结果的存储路径。</p></div><button class="ghost-button" @click="loadAppSettings"><IconRefresh :size="16" />刷新</button></div>
        <section class="surface form-card">
          <div class="panel-heading"><h2>结果路径配置</h2></div>
          <label>临时结果目录<span>单次评测默认输出到此目录</span><input v-model="appSettings.results_tmp_path" placeholder="data/results/tmp" /></label>
          <label>正式结果集目录<span>归档后的结果存入此目录（按 测试集/分类/case/时间戳 组织）</span><input v-model="appSettings.results_formal_path" placeholder="data/results" /></label>
          <button class="primary-button wide" @click="saveAppSettings"><IconSettings :size="16" />保存设置</button>
        </section>
      </section>
    </main>

    <div v-if="loading" class="busy-layer"><IconLoader2 :size="25" class="spin" />正在与 AnalystBench API 通信…</div>
    <div v-if="toast" class="toast"><IconAlertCircle :size="17" />{{ toast }}</div>

    <!-- Move/Promote dialog -->
    <div v-if="showMoveDialog" class="dialog-overlay" @click.self="showMoveDialog = false">
      <section class="surface dialog-card">
        <div class="panel-heading"><h2>{{ moveDialogMode === 'promote' ? '归档到正式结果集' : '移动结果' }}</h2><span>{{ moveDialogItem?.id }}</span></div>
        <p class="form-note">选择目标 Case（只移动结果，不移动 case.json）</p>
        <label>测试集
          <select v-model="moveForm.test_set" @change="onMoveTestSetChange">
            <option value="" disabled>请选择测试集</option>
            <option v-for="ts in moveTestSetOptions" :key="ts.key" :value="ts.key">{{ ts.name }}</option>
          </select>
        </label>
        <label>问题分类
          <select v-model="moveForm.category" @change="onMoveCategoryChange" :disabled="!moveForm.test_set">
            <option value="" disabled>请选择分类</option>
            <option v-for="cat in moveCategoryOptions" :key="cat.key" :value="cat.key">{{ cat.name }}</option>
          </select>
        </label>
        <label>Case 目录
          <select v-model="moveForm.case_dir" :disabled="!moveForm.category">
            <option value="" disabled>请选择 Case</option>
            <option v-for="cs in moveCaseDirOptions" :key="cs.key" :value="cs.key">{{ cs.name }}</option>
          </select>
        </label>
        <div class="dialog-actions">
          <button class="ghost-button" @click="showMoveDialog = false">取消</button>
          <button class="primary-button" @click="confirmMoveDialog" :disabled="!moveForm.test_set || !moveForm.category || !moveForm.case_dir"><IconCloudUpload v-if="moveDialogMode === 'promote'" :size="16" /><IconChevronRight v-else :size="16" />{{ moveDialogMode === 'promote' ? '归档' : '移动' }}</button>
        </div>
      </section>
    </div>
  </div>
</template>
