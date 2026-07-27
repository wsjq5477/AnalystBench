import ChartCanvas from "./components/ChartCanvas.vue";
import { analystBenchApi } from "./api";
import { analystBenchIcons } from "./icons";

const RESULT_COLORS = [
  "#e6b85f",
  "#5eaeff",
  "#b07dd8",
  "#a4a4a7",
  "#e6765f",
  "#5ed4a7",
  "#c8a45e",
  "#7eb5d6",
];

const VIEW_PATHS = {
  dashboard: "/dashboard",
  dataset: "/datasets",
  results: "/results",
  settings: "/settings",
};

export default {
  name: "AnalystBenchApp",
  components: {
    ChartCanvas,
    ...analystBenchIcons,
  },
  data() {
    return {
      loading: false,
      toast: "",
      connection: "checking",

      localCaseTree: [],
      selectedLocalCasePath: "",
      selectedLocalCaseData: null,

      showEvaluateDialog: false,
      evaluateForm: { judge: "lexical" },
      evaluateFiles: [],
      evaluateRunning: false,

      showCreateCaseDialog: false,
      showCaseReviewDialog: false,
      caseCreateForm: {
        reference_answer: "",
        problem_statement: "",
        case_key: "",
        test_set: "",
        category: "",
      },
      caseDraftId: null,
      caseDraftView: null,
      caseCreateRunning: false,
      caseReviewStep: 0,

      resultSource: "tmp",
      allDirectResults: [],
      directResultList: [],
      selectedResultId: "",
      selectedResultData: null,
      resultLoading: false,

      showMoveDialog: false,
      moveDialogItem: null,
      moveDialogMode: "promote",
      moveForm: { test_set: "", category: "", case_dir: "" },

      appSettings: {
        results_tmp_path: "data/results/tmp",
        results_formal_path: "data/results",
      },

      dashboardStats: null,
      dashboardLoaded: false,
      selectedTestSet: "",
      resultColors: RESULT_COLORS,
    };
  },
  computed: {
    activeView: {
      get() {
        return this.$store.state.analystbench.activeView;
      },
      set(view) {
        this.$store.commit("analystbench/setActiveView", view);
      },
    },
    moveTestSetOptions() {
      return this.localCaseTree.map((item) => ({
        key: item.key,
        name: item.name,
      }));
    },
    moveCategoryOptions() {
      const testSet = this.localCaseTree.find(
        (item) => item.key === this.moveForm.test_set,
      );
      return (testSet && testSet.children
        ? testSet.children.map((item) => ({ key: item.key, name: item.name }))
        : []);
    },
    moveCaseDirOptions() {
      const testSet = this.localCaseTree.find(
        (item) => item.key === this.moveForm.test_set,
      );
      const category =
        testSet &&
        testSet.children &&
        testSet.children.find((item) => item.key === this.moveForm.category);
      return category && category.children
        ? category.children.map((item) => ({ key: item.key, name: item.name }))
        : [];
    },
    formalTree() {
      const tree = {};
      this.allDirectResults
        .filter((item) => item.source === "formal")
        .forEach((item) => {
          const testSet = item.test_set || "default";
          const category = item.category || "uncategorized";
          const caseDir = item.case_dir || "case";
          if (!tree[testSet]) tree[testSet] = {};
          if (!tree[testSet][category]) tree[testSet][category] = {};
          if (!tree[testSet][category][caseDir]) {
            tree[testSet][category][caseDir] = [];
          }
          tree[testSet][category][caseDir].push(item);
        });
      return tree;
    },
    resultRankedReports() {
      const summary = this.parseSummary(this.selectedResultData || {});
      if (!summary) return [];
      const byName = {};
      summary.reports.forEach((report) => {
        byName[report.candidate_name] = report;
      });
      return summary.ranking.map((name) => byName[name]).filter(Boolean);
    },
    activeCandidates() {
      if (!this.dashboardStats) return [];
      if (this.selectedTestSet) {
        const testSet = this.dashboardStats.test_sets.find(
          (item) => item.key === this.selectedTestSet,
        );
        return testSet ? testSet.candidates : [];
      }
      return this.dashboardStats.candidates;
    },
    activeCategories() {
      if (!this.dashboardStats) return [];
      if (this.selectedTestSet) {
        const testSet = this.dashboardStats.test_sets.find(
          (item) => item.key === this.selectedTestSet,
        );
        return testSet ? testSet.categories : [];
      }

      const categories = [];
      this.dashboardStats.test_sets.forEach((testSet) => {
        testSet.categories.forEach((category) => {
          const existing = categories.find((item) => item.key === category.key);
          if (!existing) {
            categories.push({
              ...category,
              candidates: category.candidates.map((item) => ({ ...item })),
            });
            return;
          }
          category.candidates.forEach((candidate) => {
            const current = existing.candidates.find(
              (item) => item.name === candidate.name,
            );
            if (current) {
              current.avg_score = (current.avg_score + candidate.avg_score) / 2;
            } else {
              existing.candidates.push({ ...candidate });
            }
          });
          existing.case_count += category.case_count;
        });
      });
      return categories;
    },
    dashboardScoreCards() {
      return this.activeCandidates.map((candidate, index) => ({
        label: this.wrapName(candidate.name),
        score: candidate.avg_score,
        tone: this.toneFromRank(candidate.name),
        color: RESULT_COLORS[index % RESULT_COLORS.length],
        change: "",
      }));
    },
    categoryComparisonRows() {
      return this.activeCandidates.map((candidate, index) => ({
        name: this.wrapName(candidate.name),
        color: RESULT_COLORS[index % RESULT_COLORS.length],
        categoryScores: this.activeCategories.map((category) => {
          const value = category.candidates.find(
            (item) => item.name === candidate.name,
          );
          return value ? value.avg_score : 0;
        }),
        average: candidate.avg_score,
      }));
    },
    categoryBarLabels() {
      return this.activeCategories.map((category) => category.name);
    },
    categoryBarSeries() {
      return this.activeCandidates.map((candidate) => ({
        name: this.wrapName(candidate.name),
        values: this.activeCategories.map((category) => {
          const value = category.candidates.find(
            (item) => item.name === candidate.name,
          );
          return value ? value.avg_score : 0;
        }),
      }));
    },
  },
  watch: {
    $route: {
      immediate: true,
      handler(route) {
        const view = route && route.meta && route.meta.view;
        if (view) this.activeView = view;
      },
    },
  },
  mounted() {
    this.loadLocalCaseTree();
    if (this.activeView === "dashboard") this.loadDashboardData();
    if (this.activeView === "results") this.refreshDirectResults();
    if (this.activeView === "settings") this.loadAppSettings();
  },
  methods: {
    showToast(message) {
      this.toast = message;
      window.setTimeout(() => {
        if (this.toast === message) this.toast = "";
      }, 4000);
    },
    navigate(view) {
      this.activeView = view;
      const path = VIEW_PATHS[view] || "/";
      if (this.$route.path !== path) {
        this.$router.push(path).catch(() => {});
      }
      if (view === "dataset") this.loadLocalCaseTree();
      if (view === "results") this.refreshDirectResults();
      if (view === "settings") this.loadAppSettings();
      if (view === "dashboard" && !this.dashboardLoaded) {
        this.loadDashboardData();
      }
    },
    async loadLocalCaseTree() {
      try {
        this.localCaseTree = await analystBenchApi.getLocalCaseTree();
        this.connection = "connected";
      } catch {
        this.connection = "offline";
        this.localCaseTree = [];
      }
    },
    async selectLocalCase(testSetKey, categoryKey, caseKey) {
      const path = `${testSetKey}/${categoryKey}/${caseKey}`;
      this.selectedLocalCasePath = path;
      try {
        this.selectedLocalCaseData = await analystBenchApi.getLocalCase(path);
        this.connection = "connected";
      } catch (error) {
        this.selectedLocalCaseData = null;
        this.showToast(error instanceof Error ? error.message : "读取 Case 失败");
      }
    },
    openEvaluateDialog() {
      if (!this.selectedLocalCasePath) {
        this.showToast("请先选择一个 Case");
        return;
      }
      this.showEvaluateDialog = true;
      this.evaluateForm.judge = "lexical";
      this.evaluateFiles = [];
    },
    onEvaluateFileChange(event) {
      this.evaluateFiles = Array.from(event.target.files || []);
    },
    async runEvaluate() {
      if (!this.selectedLocalCasePath) return;
      if (!this.evaluateFiles.length) {
        this.showToast("请选择至少一份日志文件");
        return;
      }
      this.evaluateRunning = true;
      try {
        const result = await analystBenchApi.evaluateLocalCase(
          this.selectedLocalCasePath,
          this.evaluateForm.judge,
          this.evaluateFiles,
        );
        this.showEvaluateDialog = false;
        this.evaluateFiles = [];
        this.resultSource = "tmp";
        this.navigate("results");
        await this.refreshDirectResults();
        this.pollResultUntilDone(result.result_id);
      } catch (error) {
        this.showToast(error instanceof Error ? error.message : "评分失败");
      } finally {
        this.evaluateRunning = false;
      }
    },
    async pollResultUntilDone(resultId) {
      for (let index = 0; index < 120; index += 1) {
        await new Promise((resolve) => window.setTimeout(resolve, 5000));
        try {
          const data = await analystBenchApi.getDirectResult(resultId);
          const status = String(data.status || "");
          if (status === "completed") {
            this.showToast(`评分完成：${resultId}`);
            await this.refreshDirectResults();
            this.selectedResultId = resultId;
            this.selectedResultData = data;
            return;
          }
          if (status === "failed") {
            const errorMessage =
              data.error && data.error.message ? data.error.message : "评分失败";
            this.showToast(`评分失败：${String(errorMessage)}`);
            await this.refreshDirectResults();
            return;
          }
          if (index % 3 === 0) await this.refreshDirectResults();
        } catch {
          // A transient polling failure should not cancel the evaluation.
        }
      }
      this.showToast("评分超时");
    },
    async submitCreateCase() {
      const form = this.caseCreateForm;
      if (!form.reference_answer.trim()) return this.showToast("请输入参考答案文本");
      if (!form.case_key.trim()) return this.showToast("请输入 Case Key");
      if (!form.test_set.trim()) return this.showToast("请输入测试集");
      if (!form.category.trim()) return this.showToast("请输入问题分类");
      this.caseCreateRunning = true;
      try {
        const draft = await analystBenchApi.generateCaseDraft({
          reference_answer: form.reference_answer,
          problem_statement: form.problem_statement || undefined,
          case_key: form.case_key,
          test_set: form.test_set,
          category: form.category,
        });
        this.caseDraftId = draft.id;
        this.caseDraftView = draft;
        this.showCreateCaseDialog = false;
        if (draft.status === "generating") {
          this.showCaseReviewDialog = true;
          await this.pollCaseDraftUntilReady(draft.id);
        } else if (draft.status === "needs_confirmation") {
          this.showCaseReviewDialog = true;
        } else {
          this.showToast(`创建失败：${draft.status}`);
        }
      } catch (error) {
        this.showToast(error instanceof Error ? error.message : "创建 Case 失败");
      } finally {
        this.caseCreateRunning = false;
      }
    },
    async pollCaseDraftUntilReady(draftId) {
      for (let index = 0; index < 60; index += 1) {
        await new Promise((resolve) => window.setTimeout(resolve, 5000));
        try {
          const draft = await analystBenchApi.getCaseDraft(draftId);
          this.caseDraftView = draft;
          if (draft.status === "needs_confirmation" || draft.status === "ready") {
            return;
          }
          if (draft.status === "failed") {
            const message =
              draft.error && draft.error.message
                ? draft.error.message
                : "未知错误";
            this.showToast(`生成失败：${message}`);
            return;
          }
        } catch {
          // Continue polling while the generator is running.
        }
      }
      this.showToast("生成超时");
    },
    async approveCaseDraft() {
      if (!this.caseDraftView) return;
      try {
        const answers = this.caseDraftView.questions.map((question) => ({
          question_id: question.id,
          value:
            question.suggested_value == null
              ? "approved"
              : question.suggested_value,
        }));
        const updated = await analystBenchApi.submitCaseDraftAnswers(
          this.caseDraftView.id,
          answers,
        );
        this.caseDraftView = updated;
        if (updated.status === "ready") {
          try {
            const published = await analystBenchApi.publishCaseDraft(updated.id);
            this.caseDraftView = published;
            this.showToast(`Case 发布成功：${published.case_key}`);
            this.showCaseReviewDialog = false;
            await this.loadLocalCaseTree();
          } catch (publishError) {
            this.caseDraftView = await analystBenchApi.getCaseDraft(updated.id);
            this.showToast(
              publishError instanceof Error ? publishError.message : "发布失败",
            );
          }
        } else if (updated.status === "needs_confirmation") {
          this.caseReviewStep = 0;
        }
      } catch (error) {
        try {
          this.caseDraftView = await analystBenchApi.getCaseDraft(
            this.caseDraftView.id,
          );
        } catch {
          // Preserve the last known draft when refreshing it also fails.
        }
        this.showToast(error instanceof Error ? error.message : "审核失败");
      }
    },
    rejectCaseDraft() {
      this.showCaseReviewDialog = false;
      this.caseDraftView = null;
      this.caseDraftId = null;
    },
    parseSummary(data) {
      const summary = data && data.summary;
      return summary && typeof summary === "object" ? summary : null;
    },
    toneFromRank(name) {
      const index = this.activeCandidates.findIndex(
        (candidate) => candidate.name === name,
      );
      if (index === 0) return "agent";
      if (index === 1) return "skill";
      return "native";
    },
    toneFromName(name) {
      const value = String(name || "").toLowerCase();
      if (value.includes("agent")) return "agent";
      if (value.includes("skill")) return "skill";
      if (value.includes("native")) return "native";
      return "agent";
    },
    wrapName(name) {
      const match = String(name).match(/^([a-zA-Z0-9_.]+)(.*)/);
      if (!match) return name;
      const rest = match[2].trim();
      return rest ? `${match[1]}\n${rest}` : name;
    },
    relationTagClass(relation) {
      if (relation === "match") return "tag-match";
      if (relation === "partial_match") return "tag-partial";
      return "tag-missing";
    },
    formatResultId(item) {
      if (item.source === "tmp") return `${item.case_dir} › ${item.timestamp}`;
      if (item.timestamp) {
        return `${item.test_set} › ${item.category} › ${item.case_dir} › ${item.timestamp}`;
      }
      return item.id;
    },
    onMoveTestSetChange() {
      this.moveForm.category = "";
      this.moveForm.case_dir = "";
    },
    onMoveCategoryChange() {
      this.moveForm.case_dir = "";
    },
    openMoveDialog(item, mode) {
      this.moveDialogItem = item;
      this.moveDialogMode = mode;
      const testSet = this.localCaseTree.find(
        (value) => value.key === item.test_set,
      );
      this.moveForm.test_set = testSet ? item.test_set : "";
      const category =
        testSet &&
        testSet.children &&
        testSet.children.find((value) => value.key === item.category);
      this.moveForm.category = category ? item.category : "";
      const caseItem =
        category &&
        category.children &&
        category.children.find((value) => value.key === item.case_dir);
      this.moveForm.case_dir = caseItem ? item.case_dir : "";
      this.showMoveDialog = true;
    },
    async confirmMoveDialog() {
      const form = this.moveForm;
      if (!form.test_set || !form.category || !form.case_dir) {
        this.showToast("请填写测试集、问题分类和 Case 目录");
        return;
      }
      if (!this.moveDialogItem) return;
      this.loading = true;
      try {
        const destination = {
          test_set: form.test_set,
          category: form.category,
          case_dir: form.case_dir,
        };
        if (this.moveDialogMode === "promote") {
          await analystBenchApi.promoteDirectResult(
            this.moveDialogItem.id,
            destination,
          );
          this.showToast("已归档到正式结果集");
        } else {
          await analystBenchApi.moveDirectResult(
            this.moveDialogItem.id,
            destination,
          );
          this.showToast("已移动结果");
        }
        this.showMoveDialog = false;
        await this.refreshDirectResults();
        this.dashboardLoaded = false;
        this.loadDashboardData();
      } catch (error) {
        this.showToast(
          error instanceof Error
            ? error.message
            : this.moveDialogMode === "promote"
              ? "归档失败"
              : "移动失败",
        );
      } finally {
        this.loading = false;
      }
    },
    async loadAppSettings() {
      try {
        this.appSettings = await analystBenchApi.getAppSettings();
        this.connection = "connected";
      } catch {
        this.connection = "offline";
      }
    },
    async saveAppSettings() {
      this.loading = true;
      try {
        this.appSettings = await analystBenchApi.updateAppSettings(
          this.appSettings,
        );
        this.connection = "connected";
        this.showToast("设置已保存");
      } catch (error) {
        this.showToast(error instanceof Error ? error.message : "保存设置失败");
      } finally {
        this.loading = false;
      }
    },
    async refreshDirectResults() {
      this.resultLoading = true;
      try {
        this.allDirectResults = await analystBenchApi.listDirectResults();
        this.directResultList = this.allDirectResults.filter(
          (item) => item.source === this.resultSource,
        );
        this.connection = "connected";
      } catch {
        this.connection = "offline";
        this.allDirectResults = [];
        this.directResultList = [];
      } finally {
        this.resultLoading = false;
      }
    },
    async loadDirectResult(item) {
      this.selectedResultId = item.id;
      this.resultLoading = true;
      try {
        this.selectedResultData = await analystBenchApi.getDirectResult(item.id);
      } catch (error) {
        this.selectedResultData = null;
        this.showToast(
          error instanceof Error ? error.message : "读取评测结果失败",
        );
      } finally {
        this.resultLoading = false;
      }
    },
    async deleteDirectResult(item) {
      if (
        !window.confirm(
          `删除评测结果 ${item.id} 吗？JSON 和 Markdown 文件将一并删除。`,
        )
      ) {
        return;
      }
      this.loading = true;
      try {
        await analystBenchApi.deleteDirectResult(item.id);
        if (this.selectedResultId === item.id) {
          this.selectedResultId = "";
          this.selectedResultData = null;
        }
        await this.refreshDirectResults();
        this.dashboardLoaded = false;
        this.loadDashboardData();
        this.showToast("评测结果已删除");
      } catch (error) {
        this.showToast(error instanceof Error ? error.message : "删除评测结果失败");
      } finally {
        this.loading = false;
      }
    },
    async loadDashboardData() {
      this.loading = true;
      try {
        this.dashboardStats = await analystBenchApi.getDirectResultStats();
        this.dashboardLoaded = true;
        this.connection = "connected";
        if (
          !this.selectedTestSet &&
          this.dashboardStats.test_sets &&
          this.dashboardStats.test_sets.length
        ) {
          this.selectedTestSet = this.dashboardStats.test_sets[0].key;
        }
      } catch {
        this.connection = "offline";
        this.dashboardStats = null;
        this.dashboardLoaded = false;
      } finally {
        this.loading = false;
      }
    },
  },
};
