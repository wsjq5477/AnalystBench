import ChartCanvas from "./components/ChartCanvas.vue";
import { analystBenchApi } from "./api";
import { analystBenchIcons } from "./icons";
import {
  applyThemeToDocument,
  getInitialTheme,
  persistTheme,
  THEME_PALETTES,
  THEME_STORAGE_KEY,
} from "./theme";

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
      theme: getInitialTheme(),
      loading: false,
      toast: "",
      connection: "checking",

      localCaseTree: [],
      selectedLocalCasePath: "",
      selectedLocalCaseData: null,
      selectedCaseLogs: null,
      caseLogFiles: [],
      caseLogsUploading: false,

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
      caseCreateLogFiles: [],

      evaluationMethods: [],
      evaluationSubmissions: [],
      selectedSubmissionId: "",
      submissionCaseRuns: [],
      showSubmitEvaluationDialog: false,
      submissionStep: 1,
      submissionForm: {
        dataset_key: "",
        case_paths: [],
        method_ids: [],
        judge_runner: "claude-code",
      },
      submissionRunning: false,
      showArtifactDialog: false,
      methodArtifactView: null,
      showMethodDialog: false,
      methodSaving: false,
      methodForm: {
        key: "",
        name: "",
        tool_dir: "",
        command_template: "",
        timeout_seconds: 1800,
        max_output_bytes: 10485760,
        concurrency_limit: 1,
      },

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
    };
  },
  computed: {
    resultColors() {
      return THEME_PALETTES[this.theme];
    },
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
    frozenEvaluationMethods() {
      return this.evaluationMethods.filter((item) => item.status === "frozen");
    },
    selectedSubmission() {
      return this.evaluationSubmissions.find(
        (item) => item.id === this.selectedSubmissionId,
      );
    },
    selectedSubmissionCases() {
      const testSet = this.localCaseTree.find(
        (item) => item.key === this.submissionForm.dataset_key,
      );
      if (!testSet) return [];
      return (testSet.children || []).flatMap((category) =>
        (category.children || []).map((caseItem) => ({
          ...caseItem,
          category: category.key,
        })),
      );
    },
    selectableSubmissionCases() {
      return this.selectedSubmissionCases.filter(
        (caseItem) => caseItem.case_data?.submission_ready,
      );
    },
    unavailableSubmissionCases() {
      return this.selectedSubmissionCases.filter(
        (caseItem) => !caseItem.case_data?.submission_ready,
      );
    },
    selectedCaseParts() {
      const parts = this.selectedLocalCasePath.split("/");
      return parts.length === 3
        ? { testSet: parts[0], category: parts[1], caseKey: parts[2] }
        : null;
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
        color: this.resultColors[index % this.resultColors.length],
        change: "",
      }));
    },
    activeDailyScores() {
      if (!this.dashboardStats) return [];
      if (this.selectedTestSet) {
        const testSet = this.dashboardStats.test_sets.find(
          (item) => item.key === this.selectedTestSet,
        );
        return testSet ? testSet.daily_scores || [] : [];
      }
      return this.dashboardStats.daily_scores || [];
    },
    dailyScoreLabels() {
      return this.activeDailyScores.map((item) => item.date);
    },
    dailyScoreSeries() {
      return this.activeCandidates.map((candidate) => ({
        name: this.wrapName(candidate.name),
        values: this.activeDailyScores.map((item) => {
          const value = item.candidates.find(
            (entry) => entry.name === candidate.name,
          );
          return value ? value.avg_score : null;
        }),
      }));
    },
    categoryComparisonRows() {
      return this.activeCandidates.map((candidate, index) => ({
        name: this.wrapName(candidate.name),
        color: this.resultColors[index % this.resultColors.length],
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
    this.applyTheme(this.theme);
    if (window.matchMedia) {
      this._themeMediaQuery = window.matchMedia(
        "(prefers-color-scheme: light)",
      );
      this._themeMediaListener = (event) => {
        let hasSavedPreference = false;
        try {
          hasSavedPreference = window.localStorage.getItem(THEME_STORAGE_KEY) != null;
        } catch {
          // A blocked storage API means system preference remains authoritative.
        }
        if (!hasSavedPreference) {
          this.theme = event.matches ? "light" : "dark";
          this.applyTheme(this.theme);
        }
      };
      if (this._themeMediaQuery.addEventListener) {
        this._themeMediaQuery.addEventListener(
          "change",
          this._themeMediaListener,
        );
      } else {
        this._themeMediaQuery.addListener(this._themeMediaListener);
      }
    }
    this.loadLocalCaseTree();
    if (this.activeView === "dashboard") this.loadDashboardData();
    if (this.activeView === "results") {
      this.refreshDirectResults();
      this.loadEvaluationSubmissions();
    }
    if (this.activeView === "settings") {
      this.loadAppSettings();
      this.loadEvaluationMethods();
    }
  },
  beforeDestroy() {
    if (!this._themeMediaQuery || !this._themeMediaListener) return;
    if (this._themeMediaQuery.removeEventListener) {
      this._themeMediaQuery.removeEventListener(
        "change",
        this._themeMediaListener,
      );
    } else {
      this._themeMediaQuery.removeListener(this._themeMediaListener);
    }
  },
  methods: {
    applyTheme(theme) {
      applyThemeToDocument(theme);
    },
    toggleTheme() {
      this.theme = this.theme === "dark" ? "light" : "dark";
      this.applyTheme(this.theme);
      persistTheme(this.theme);
    },
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
      if (view === "results") {
        this.refreshDirectResults();
        this.loadEvaluationSubmissions();
      }
      if (view === "settings") {
        this.loadAppSettings();
        this.loadEvaluationMethods();
      }
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
        this.selectedCaseLogs = await analystBenchApi.getLocalCaseLogs(
          testSetKey,
          categoryKey,
          caseKey,
        );
        this.connection = "connected";
      } catch (error) {
        this.selectedLocalCaseData = null;
        this.showToast(error instanceof Error ? error.message : "读取 Case 失败");
      }
    },
    async onCaseLogFileChange(event) {
      this.caseLogFiles = Array.from(event.target.files || []);
      if (!this.caseLogFiles.length) {
        return;
      }
      try {
        await this.uploadSelectedCaseLogs();
      } finally {
        this.caseLogFiles = [];
        event.target.value = "";
      }
    },
    onCaseCreateLogFileChange(event) {
      this.caseCreateLogFiles = Array.from(event.target.files || []);
    },
    async uploadSelectedCaseLogs() {
      if (!this.selectedCaseParts || !this.caseLogFiles.length) {
        this.showToast("请选择至少一个日志文件");
        return;
      }
      this.caseLogsUploading = true;
      try {
        const parts = this.selectedCaseParts;
        this.selectedCaseLogs = await analystBenchApi.uploadLocalCaseLogs(
          parts.testSet,
          parts.category,
          parts.caseKey,
          this.caseLogFiles,
        );
        this.caseLogFiles = [];
        await this.loadLocalCaseTree();
        this.showToast("日志已上传");
      } catch (error) {
        this.showToast(error instanceof Error ? error.message : "上传日志失败");
      } finally {
        this.caseLogsUploading = false;
      }
    },
    async setSelectedCasePrimary(filename) {
      if (!this.selectedCaseParts) return;
      try {
        const parts = this.selectedCaseParts;
        this.selectedCaseLogs = await analystBenchApi.setLocalCasePrimaryLog(
          parts.testSet,
          parts.category,
          parts.caseKey,
          filename,
        );
        await this.loadLocalCaseTree();
        this.showToast(`主日志已设为 ${filename}`);
      } catch (error) {
        this.showToast(error instanceof Error ? error.message : "设置主日志失败");
      }
    },
    async deleteSelectedCaseLog(filename) {
      if (!this.selectedCaseParts || !window.confirm(`删除日志 ${filename} 吗？`)) {
        return;
      }
      try {
        const parts = this.selectedCaseParts;
        this.selectedCaseLogs = await analystBenchApi.deleteLocalCaseLog(
          parts.testSet,
          parts.category,
          parts.caseKey,
          filename,
        );
        await this.loadLocalCaseTree();
        this.showToast("日志已删除");
      } catch (error) {
        this.showToast(error instanceof Error ? error.message : "删除日志失败");
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
            if (this.caseCreateLogFiles.length) {
              await analystBenchApi.uploadLocalCaseLogs(
                published.test_set,
                published.category,
                published.case_key,
                this.caseCreateLogFiles,
              );
              this.caseCreateLogFiles = [];
            }
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
    async loadEvaluationMethods() {
      try {
        this.evaluationMethods = await analystBenchApi.listEvaluationMethods();
        this.connection = "connected";
      } catch {
        this.evaluationMethods = [];
        this.connection = "offline";
      }
    },
    openMethodDialog() {
      this.methodForm = {
        key: "",
        name: "",
        tool_dir: "",
        command_template: "",
        timeout_seconds: 1800,
        max_output_bytes: 10485760,
        concurrency_limit: 1,
      };
      this.showMethodDialog = true;
    },
    async createEvaluationMethod() {
      const form = this.methodForm;
      if (!form.key.trim() || !form.name.trim() || !form.command_template.trim()) {
        this.showToast("请填写 Key、名称和命令模板");
        return;
      }
      this.methodSaving = true;
      try {
        const created = await analystBenchApi.createEvaluationMethod({
          ...form,
          key: form.key.trim(),
          name: form.name.trim(),
          command_template: form.command_template.trim(),
          tool_dir: form.tool_dir.trim() || null,
        });
        await analystBenchApi.probeEvaluationMethod(created.id);
        await this.loadEvaluationMethods();
        this.showMethodDialog = false;
        this.showToast("测评方式已创建并完成命令检测，请确认后冻结");
      } catch (error) {
        this.showToast(error instanceof Error ? error.message : "创建测评方式失败");
      } finally {
        this.methodSaving = false;
      }
    },
    async probeEvaluationMethod(method) {
      try {
        const result = await analystBenchApi.probeEvaluationMethod(method.id);
        await this.loadEvaluationMethods();
        this.showToast(
          result.probe && result.probe.available
            ? "命令检测成功"
            : "命令不可用，请检查配置",
        );
      } catch (error) {
        this.showToast(error instanceof Error ? error.message : "命令检测失败");
      }
    },
    async freezeEvaluationMethod(method) {
      try {
        await analystBenchApi.freezeEvaluationMethod(method.id);
        await this.loadEvaluationMethods();
        this.showToast("测评方式已冻结，可用于提交测评");
      } catch (error) {
        this.showToast(error instanceof Error ? error.message : "冻结失败");
      }
    },
    async archiveEvaluationMethod(method) {
      if (!window.confirm(`归档测评方式 ${method.name} v${method.version} 吗？`)) return;
      try {
        await analystBenchApi.archiveEvaluationMethod(method.id);
        await this.loadEvaluationMethods();
        this.showToast("测评方式已归档");
      } catch (error) {
        this.showToast(error instanceof Error ? error.message : "归档失败");
      }
    },
    async openSubmitEvaluationDialog() {
      await Promise.all([this.loadLocalCaseTree(), this.loadEvaluationMethods()]);
      const frozenMethods = this.frozenEvaluationMethods;
      this.submissionForm = {
        dataset_key: this.localCaseTree.length ? this.localCaseTree[0].key : "",
        case_paths: [],
        method_ids: frozenMethods.length === 1 ? [frozenMethods[0].id] : [],
        judge_runner: "claude-code",
      };
      this.selectAllReadySubmissionCases();
      this.submissionStep = 1;
      this.showSubmitEvaluationDialog = true;
    },
    submissionCasePath(caseItem) {
      return `${this.submissionForm.dataset_key}/${caseItem.category}/${caseItem.key}`;
    },
    selectAllReadySubmissionCases() {
      this.submissionForm.case_paths = this.selectableSubmissionCases.map(
        (caseItem) => this.submissionCasePath(caseItem),
      );
    },
    clearSubmissionCaseSelection() {
      this.submissionForm.case_paths = [];
    },
    resetSubmissionCaseSelection() {
      this.selectAllReadySubmissionCases();
    },
    advanceSubmissionStep() {
      if (this.submissionStep === 1 && !this.submissionForm.dataset_key) {
        this.showToast("请选择测试集");
        return;
      }
      if (this.submissionStep === 1 && !this.validateSubmissionCaseSelection()) {
        return;
      }
      if (this.submissionStep === 2 && !this.submissionForm.method_ids.length) {
        this.showToast(
          this.frozenEvaluationMethods.length
            ? "请选择至少一种测评方式"
            : "没有可用的测评方式，请先检测并冻结",
        );
        return;
      }
      this.submissionStep = Math.min(3, this.submissionStep + 1);
    },
    validateSubmissionCaseSelection() {
      if (this.submissionForm.case_paths.length) {
        return true;
      }
      this.showToast(
        this.selectableSubmissionCases.length
          ? "请至少选择一个本次要测评的 Case"
          : "当前测试集没有日志就绪的 Case",
      );
      return false;
    },
    async createEvaluationSubmission() {
      if (!this.submissionForm.dataset_key) {
        this.showToast("请选择测试集");
        return;
      }
      if (!this.validateSubmissionCaseSelection()) {
        return;
      }
      if (!this.submissionForm.method_ids.length) {
        this.showToast("请选择至少一种测评方式");
        return;
      }
      this.submissionRunning = true;
      try {
        const submission = await analystBenchApi.createEvaluationSubmission(
          this.submissionForm,
        );
        this.showSubmitEvaluationDialog = false;
        this.selectedSubmissionId = submission.id;
        await this.loadEvaluationSubmissions();
        await this.loadEvaluationSubmissionCaseRuns(submission.id);
        this.showToast(`测评已提交，共 ${submission.case_count} 个 Case`);
        this.pollEvaluationSubmission(submission.id);
      } catch (error) {
        this.showToast(error instanceof Error ? error.message : "提交测评失败");
      } finally {
        this.submissionRunning = false;
      }
    },
    async loadEvaluationSubmissions() {
      try {
        this.evaluationSubmissions =
          await analystBenchApi.listEvaluationSubmissions();
        if (!this.selectedSubmissionId && this.evaluationSubmissions.length) {
          this.selectedSubmissionId = this.evaluationSubmissions[0].id;
        }
        if (this.selectedSubmissionId) {
          await this.loadEvaluationSubmissionCaseRuns(this.selectedSubmissionId);
        }
      } catch {
        this.evaluationSubmissions = [];
        this.submissionCaseRuns = [];
      }
    },
    async selectEvaluationSubmission(submissionId) {
      this.selectedSubmissionId = submissionId;
      await this.loadEvaluationSubmissionCaseRuns(submissionId);
    },
    async loadEvaluationSubmissionCaseRuns(submissionId) {
      try {
        this.submissionCaseRuns =
          await analystBenchApi.getEvaluationSubmissionCaseRuns(submissionId);
      } catch (error) {
        this.submissionCaseRuns = [];
        this.showToast(error instanceof Error ? error.message : "读取批次进度失败");
      }
    },
    async pollEvaluationSubmission(submissionId) {
      const terminal = ["completed", "completed_with_errors", "failed", "cancelled"];
      for (let index = 0; index < 720; index += 1) {
        await new Promise((resolve) => window.setTimeout(resolve, 5000));
        try {
          const submission =
            await analystBenchApi.getEvaluationSubmission(submissionId);
          const position = this.evaluationSubmissions.findIndex(
            (item) => item.id === submissionId,
          );
          if (position >= 0) this.$set(this.evaluationSubmissions, position, submission);
          else this.evaluationSubmissions.unshift(submission);
          if (this.selectedSubmissionId === submissionId) {
            await this.loadEvaluationSubmissionCaseRuns(submissionId);
          }
          if (terminal.includes(submission.status)) {
            if (submission.status === "cancelled") {
              await this.refreshDirectResults();
              this.showToast(`测评批次已结束：${submission.status}`);
            } else {
              await this.showEvaluationSubmissionResults(
                submission,
                `测评批次已结束：${submission.status}，已切换到正式结果`,
              );
            }
            this.dashboardLoaded = false;
            return;
          }
        } catch {
          // A transient polling failure must not change the server-side batch.
        }
      }
    },
    async showEvaluationSubmissionResults(
      submission,
      successMessage = "已打开该批次的正式结果",
    ) {
      this.resultSource = "formal";
      await this.refreshDirectResults();
      const matchingResults = this.directResultList.filter(
        (item) =>
          item.test_set === submission.dataset_key &&
          item.timestamp === submission.timestamp,
      );
      if (!matchingResults.length) {
        this.selectedResultId = "";
        this.selectedResultData = null;
        this.showToast("该批次尚未生成可显示的正式结果");
        return false;
      }
      await this.loadDirectResult(matchingResults[0]);
      this.showToast(successMessage);
      return true;
    },
    async cancelEvaluationSubmission(submission) {
      if (!window.confirm(`取消测评批次 ${submission.timestamp} 吗？`)) return;
      try {
        await analystBenchApi.cancelEvaluationSubmission(submission.id);
        await this.loadEvaluationSubmissions();
        this.showToast("已取消尚未开始的任务");
      } catch (error) {
        this.showToast(error instanceof Error ? error.message : "取消失败");
      }
    },
    async retryEvaluationCaseRun(caseRun) {
      try {
        await analystBenchApi.retryEvaluationCaseRun(caseRun.id);
        await this.loadEvaluationSubmissionCaseRuns(caseRun.submission_id);
        this.pollEvaluationSubmission(caseRun.submission_id);
        this.showToast("失败项已重新入队");
      } catch (error) {
        this.showToast(error instanceof Error ? error.message : "重试失败");
      }
    },
    async openMethodArtifacts(methodRun) {
      try {
        this.methodArtifactView =
          await analystBenchApi.getEvaluationMethodRunArtifacts(methodRun.id);
        this.showArtifactDialog = true;
      } catch (error) {
        this.showToast(error instanceof Error ? error.message : "读取审计产物失败");
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
