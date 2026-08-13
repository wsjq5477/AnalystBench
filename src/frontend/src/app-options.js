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
import { elapsedDurationMs, formatDurationMs } from "./timing-display";

const VIEW_PATHS = {
  dashboard: "/dashboard",
  dataset: "/datasets",
  results: "/results",
  optimization: "/skill-optimization",
  settings: "/settings",
};

function averageScores(values, fallback = 0) {
  return values.length
    ? values.reduce((sum, value) => sum + Number(value), 0) / values.length
    : fallback;
}

function averageAvailable(values) {
  const available = values.filter(
    (value) => value !== null && value !== undefined && Number.isFinite(Number(value)),
  );
  return available.length ? averageScores(available) : null;
}

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
      evaluationHarnesses: [],
      evaluationModels: [],
      evaluationTargets: [],
      configuredSkills: [],
      skillScanResults: [],
      showSkillDialog: false,
      skillScanning: false,
      skillSaving: false,
      skillForm: { harness_id: "", key: "" },
      evaluationSubmissions: [],
      evaluationSchedules: [],
      selectedSubmissionId: "",
      submissionCaseRuns: [],
      showSubmitEvaluationDialog: false,
      submissionStep: 1,
      submissionForm: {
        dataset_key: "",
        case_paths: [],
        method_ids: [],
        target_selection_keys: [],
        judge_runner: "claude",
      },
      submissionRunning: false,
      methodTimingNow: Date.now(),
      showArtifactDialog: false,
      methodArtifactView: null,
      showTargetComparisonDialog: false,
      targetComparison: null,
      showMethodDialog: false,
      methodSaving: false,
      editingMethodId: "",
      methodForm: {
        key: "",
        tool_dir: "",
        command_template: "",
        timeout_seconds: 1800,
        max_output_bytes: 10485760,
        concurrency_limit: 1,
      },
      showHarnessDialog: false,
      harnessSaving: false,
      harnessActionId: "",
      harnessAction: "",
      editingHarnessId: "",
      harnessForm: {
        key: "",
        skill_base_dir: "",
        command_template: "",
        timeout_seconds: 1800,
        concurrency_limit: 1,
      },
      showModelDialog: false,
      modelSaving: false,
      modelForm: { key: "" },
      catalogActionId: "",
      showScheduleDialog: false,
      scheduleSaving: false,
      editingScheduleId: "",
      scheduleForm: {
        name: "",
        dataset_key: "",
        case_mode: "all_ready",
        case_paths: [],
        method_ids: [],
        target_selection_keys: [],
        judge_runner: "claude",
        timezone: "Asia/Shanghai",
        local_time: "23:00",
        enabled: true,
      },
      showScheduleRunsDialog: false,
      scheduleRunsTitle: "",
      scheduleRuns: [],

      resultSource: "formal",
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
      dashboardComparisonDimension: "harness",
      dashboardModelFilter: "Average",
      dashboardHarnessFilter: "Average",

      skills: [],
      optimizationExperiments: [],
      selectedOptimizationExperimentId: "",
      selectedOptimizationDetail: null,
      selectedOptimizationEvents: [],
      selectedOptimizationVersions: [],
      selectedOptimizationBinding: null,
      selectedOptimizationBindingHistory: [],
      optimizationPreflight: null,
      optimizationPreflightRunning: false,
      optimizationExportFormat: "",
      optimizationVersionActionId: "",
      optimizationEpochOffset: 0,
      optimizationEpochLimit: 3,
      optimizationLoading: false,
      showOptimizationDialog: false,
      optimizationSaving: false,
      optimizationDialogStep: 1,
      optimizationCandidateDetail: null,
      optimizationForm: {
        name: "",
        combination_key: "",
        evaluation_target_id: "",
        data_mode: "development_regression",
        case_paths: [],
        train_case_paths: [],
        validation_case_paths: [],
        hidden_test_case_paths: [],
        prospective_holdout_case_paths: [],
        optimizer_runner: "claude",
        optimizer_executable: "claude",
        optimizer_instruction:
          "根据失败证据对当前 Skill 做小步、通用、可迁移的优化。不要写入具体 Case 答案。",
        judge_runner: "claude",
        min_overall_delta: 1,
        max_latency_growth: 0.2,
        max_token_growth: 0.2,
        max_epochs: 1,
      },
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
    frozenEvaluationHarnesses() {
      return this.evaluationHarnesses.filter((item) => item.status === "frozen");
    },
    frozenEvaluationModels() {
      return this.evaluationModels.filter((item) => item.status === "frozen");
    },
    skillConfigurationHarnesses() {
      return this.frozenEvaluationHarnesses.filter(
        (item) => item.skill_base_dir,
      );
    },
    visibleEvaluationHarnesses() {
      return this.evaluationHarnesses.filter((item) => item.status !== "archived");
    },
    visibleEvaluationModels() {
      return this.evaluationModels.filter((item) => item.status !== "archived");
    },
    evaluationSelectionGroups() {
      return this.frozenEvaluationHarnesses
        .map((harness) => {
          const models =
            harness.model_policy === "none"
              ? [null]
              : this.frozenEvaluationModels;
          const skills = [
            null,
            ...this.configuredSkills.filter(
              (skill) =>
                skill.harness_id === harness.id && skill.selectable,
            ),
          ];
          return {
            harness,
            options: models.flatMap((model) =>
              skills.map((skill) => ({
                key: this.targetSelectionKey(
                  harness.id,
                  model?.id || null,
                  skill?.key || null,
                ),
                harness_id: harness.id,
                model_id: model?.id || null,
                skill_key: skill?.key || null,
                model,
                skill,
              })),
            ),
          };
        })
        .filter((group) => group.options.length);
    },
    allEvaluationSelectionKeys() {
      return this.evaluationSelectionGroups.flatMap((group) =>
        group.options.map((option) => option.key),
      );
    },
    visibleEvaluationMethods() {
      return this.evaluationMethods.filter((item) => item.status !== "archived");
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
    selectedScheduleCases() {
      const testSet = this.localCaseTree.find(
        (item) => item.key === this.scheduleForm.dataset_key,
      );
      if (!testSet) return [];
      return (testSet.children || []).flatMap((category) =>
        (category.children || []).map((caseItem) => ({
          ...caseItem,
          category: category.key,
        })),
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
              current.avg_duration_ms = averageAvailable([
                current.avg_duration_ms,
                candidate.avg_duration_ms,
              ]);
            } else {
              existing.candidates.push({ ...candidate });
            }
          });
          existing.case_count += category.case_count;
        });
      });
      return categories;
    },
    dashboardComparisonLabel() {
      return this.dashboardComparisonDimension === "model" ? "Model" : "Harness";
    },
    dashboardModelOptions() {
      return [...new Set(
        this.activeCandidates
          .map((candidate) => this.splitEvaluationTargetName(candidate.name).model)
          .filter((model) => model !== "-"),
      )].sort((left, right) => left.localeCompare(right));
    },
    dashboardHarnessOptions() {
      return [...new Set(
        this.activeCandidates
          .map((candidate) => this.splitEvaluationTargetName(candidate.name))
          .filter((target) => target.model !== "-")
          .map((target) => target.harness),
      )].sort((left, right) => left.localeCompare(right));
    },
    activeDashboardModelFilter() {
      return this.dashboardModelFilter === "Average" ||
        this.dashboardModelOptions.includes(this.dashboardModelFilter)
        ? this.dashboardModelFilter
        : "Average";
    },
    activeDashboardHarnessFilter() {
      return this.dashboardHarnessFilter === "Average" ||
        this.dashboardHarnessOptions.includes(this.dashboardHarnessFilter)
        ? this.dashboardHarnessFilter
        : "Average";
    },
    dashboardComparisonContext() {
      const filter =
        this.dashboardComparisonDimension === "harness"
          ? this.activeDashboardModelFilter
          : this.activeDashboardHarnessFilter;
      return filter ? `${this.dashboardComparisonLabel} · ${filter}` : this.dashboardComparisonLabel;
    },
    dashboardComparisonGroups() {
      const groups = new Map();
      this.activeCandidates.forEach((candidate) => {
        const target = this.splitEvaluationTargetName(candidate.name);
        const isBaseline = target.model === "-";
        const selectedFilter =
          this.dashboardComparisonDimension === "harness"
            ? this.activeDashboardModelFilter
            : this.activeDashboardHarnessFilter;
        const matchesFilter =
          isBaseline ||
          selectedFilter === "Average" ||
          (this.dashboardComparisonDimension === "harness"
            ? target.model === selectedFilter
            : target.harness === selectedFilter);
        if (!matchesFilter) return;
        const label =
          this.dashboardComparisonDimension === "model" && !isBaseline
            ? target.model
            : target.harness;
        const key = isBaseline
          ? `baseline:${target.harness}`
          : `${this.dashboardComparisonDimension}:${label}`;
        if (!groups.has(key)) {
          groups.set(key, { key, label, members: [], scores: [], durations: [] });
        }
        const group = groups.get(key);
        group.members.push(candidate.name);
        group.scores.push(candidate.avg_score);
        group.durations.push(candidate.avg_duration_ms);
      });
      return [...groups.values()]
        .map((group) => ({
          key: group.key,
          label: group.label,
          members: group.members,
          score: averageScores(group.scores),
          duration_ms: averageAvailable(group.durations),
        }))
        .sort(
          (left, right) =>
            right.score - left.score || left.label.localeCompare(right.label),
        );
    },
    dashboardScoreCards() {
      return this.dashboardComparisonGroups.map((group, index) => ({
        label: group.label,
        score: group.score,
        duration_ms: group.duration_ms,
        color: this.resultColors[index % this.resultColors.length],
      }));
    },
    performanceScatterSeries() {
      const groups = new Map();
      this.activeCandidates.forEach((candidate) => {
        const target = this.splitEvaluationTargetName(candidate.name);
        if (target.model === "-") return;
        const duration = Number(candidate.avg_duration_ms);
        const score = Number(candidate.avg_score);
        if (!Number.isFinite(duration) || duration <= 0 || !Number.isFinite(score)) {
          return;
        }
        const fullLabel = `${target.harness} × ${target.model}`;
        if (!groups.has(target.harness)) {
          groups.set(target.harness, {
            name: target.harness,
            values: [],
          });
        }
        groups.get(target.harness).values.push({
          value: [duration, score],
          targetLabel: target.model,
          fullLabel,
          harness: target.harness,
          model: target.model,
          duration_ms: duration,
          score,
        });
      });
      return [...groups.values()].sort((left, right) =>
        left.name.localeCompare(right.name),
      );
    },
    performanceScatterBaseline() {
      const baseline = this.activeCandidates.find((candidate) => {
        const target = this.splitEvaluationTargetName(candidate.name);
        return (
          target.model === "-" &&
          target.harness.toLowerCase() === "script" &&
          Number.isFinite(Number(candidate.avg_score))
        );
      });
      if (!baseline) return null;
      return {
        label: this.splitEvaluationTargetName(baseline.name).harness,
        value: Number(baseline.avg_score),
      };
    },
    performanceScatterPointCount() {
      return this.performanceScatterSeries.reduce(
        (count, series) => count + series.values.length,
        0,
      );
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
      return this.dashboardComparisonGroups.map((group) => ({
        name: group.label,
        values: this.activeDailyScores.map((item) => {
          const scores = item.candidates
            .filter((entry) => group.members.includes(entry.name))
            .map((entry) => entry.avg_score);
          return averageScores(scores, null);
        }),
        durations: this.activeDailyScores.map((item) =>
          averageAvailable(
            item.candidates
              .filter((entry) => group.members.includes(entry.name))
              .map((entry) => entry.avg_duration_ms),
          ),
        ),
      }));
    },
    categoryComparisonRows() {
      return [...this.activeCandidates]
        .sort(
          (left, right) =>
            Number(right.avg_score) - Number(left.avg_score) ||
            String(left.name).localeCompare(String(right.name)),
        )
        .map((candidate, index) => {
          const target = this.splitEvaluationTargetName(candidate.name);
          return {
            name: candidate.name,
            rank: index + 1,
            harness: target.harness,
            model: target.model,
            duration_ms: candidate.avg_duration_ms,
            categoryScores: this.activeCategories.map((category) => {
              const value = category.candidates.find(
                (item) => item.name === candidate.name,
              );
              return value ? value.avg_score : 0;
            }),
            score: candidate.avg_score,
          };
        });
    },
    categoryBarLabels() {
      return this.activeCategories.map((category) => category.name);
    },
    categoryBarSeries() {
      return this.dashboardComparisonGroups.map((group) => ({
        name: group.label,
        values: this.activeCategories.map((category) => {
          const scores = category.candidates
            .filter((candidate) => group.members.includes(candidate.name))
            .map((candidate) => candidate.avg_score);
          return averageScores(scores);
        }),
        durations: this.activeCategories.map((category) =>
          averageAvailable(
            category.candidates
              .filter((candidate) => group.members.includes(candidate.name))
              .map((candidate) => candidate.avg_duration_ms),
          ),
        ),
      }));
    },
    optimizationCaseOptions() {
      return this.localCaseTree.flatMap((testSet) =>
        (testSet.children || []).flatMap((category) =>
          (category.children || []).map((caseItem) => ({
            path: `${testSet.key}/${category.key}/${caseItem.key}`,
            label: `${testSet.name} / ${category.name} / ${
              caseItem.case_data?.case_key || caseItem.name
            }`,
            ready: Boolean(caseItem.case_data?.submission_ready),
          })),
        ),
      );
    },
    optimizationSelectedCasePaths() {
      if (this.optimizationForm.data_mode === "development_regression") {
        return [...this.optimizationForm.case_paths];
      }
      return [
        ...this.optimizationForm.train_case_paths,
        ...this.optimizationForm.validation_case_paths,
        ...this.optimizationForm.hidden_test_case_paths,
        ...this.optimizationForm.prospective_holdout_case_paths,
      ];
    },
    optimizationSplitCounts() {
      return {
        train: this.optimizationForm.train_case_paths.length,
        validation: this.optimizationForm.validation_case_paths.length,
        hidden: this.optimizationForm.hidden_test_case_paths.length,
        prospective: this.optimizationForm.prospective_holdout_case_paths.length,
      };
    },
    optimizationCombinationOptions() {
      return this.evaluationSelectionGroups.flatMap((group) =>
        group.options
          .filter((option) => option.skill)
          .map((option) => ({
            ...option,
            key: `${option.harness_id}|${option.model_id || ""}|${option.skill.key}`,
            harness: group.harness,
            label: `${group.harness.key} · ${
              option.model?.name || "无模型基线"
            } · /${option.skill.key}`,
          })),
      );
    },
    selectedOptimizationCombination() {
      return this.optimizationCombinationOptions.find(
        (item) => item.key === this.optimizationForm.combination_key,
      );
    },
    optimizationInvokeAs() {
      const skillKey = this.selectedOptimizationCombination?.skill?.key;
      return skillKey ? `/${skillKey}` : "/skill-name";
    },
    optimizationSourcePath() {
      return this.selectedOptimizationCombination?.skill?.source_path || "";
    },
    selectedOptimizationExperiment() {
      return this.optimizationExperiments.find(
        (item) => item.id === this.selectedOptimizationExperimentId,
      );
    },
    optimizationSummary() {
      return {
        total: this.optimizationExperiments.length,
        running: this.optimizationExperiments.filter(
          (item) => item.status === "running",
        ).length,
        completed: this.optimizationExperiments.filter(
          (item) => item.status === "completed",
        ).length,
        promoted: this.selectedOptimizationEvents.filter(
          (item) => item.type === "skill_version_promoted",
        ).length,
      };
    },
    optimizationRollbackVersionIds() {
      return new Set(
        this.selectedOptimizationBindingHistory
          .filter(
            (item) =>
              item.evaluation_target_id ===
              this.selectedOptimizationDetail?.experiment?.evaluation_target_id,
          )
          .map((item) => item.active_version_id)
          .filter(Boolean),
      );
    },
  },
  watch: {
    selectedTestSet() {
      this.syncDashboardComparisonFilters();
    },
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
    this._methodTimingTimer = window.setInterval(() => {
      const hasRunningMethod = this.submissionCaseRuns.some((caseRun) =>
        (caseRun.methods || []).some(
          (methodRun) => methodRun.status === "running" && methodRun.started_at,
        ),
      );
      if (hasRunningMethod) this.methodTimingNow = Date.now();
    }, 1000);
    this._optimizationPollTimer = window.setInterval(() => {
      if (
        this.activeView === "optimization" &&
        this.selectedOptimizationExperiment?.status === "running"
      ) {
        this.refreshSelectedOptimization();
      }
    }, 2500);
    if (this.activeView === "dashboard") this.loadDashboardData();
    if (this.activeView === "results") {
      this.refreshDirectResults();
      this.loadEvaluationSubmissions();
      this.loadEvaluationSchedules();
    }
    if (this.activeView === "optimization") {
      this.loadOptimizationWorkspace();
    }
    if (this.activeView === "settings") {
      this.loadAppSettings();
      this.loadEvaluationMethods();
      this.loadEvaluationCatalog();
    }
  },
  beforeDestroy() {
    if (this._methodTimingTimer) {
      window.clearInterval(this._methodTimingTimer);
      this._methodTimingTimer = null;
    }
    if (this._optimizationPollTimer) {
      window.clearInterval(this._optimizationPollTimer);
      this._optimizationPollTimer = null;
    }
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
        this.loadEvaluationSchedules();
      }
      if (view === "settings") {
        this.loadAppSettings();
        this.loadEvaluationMethods();
        this.loadEvaluationCatalog();
      }
      if (view === "optimization") {
        this.loadOptimizationWorkspace();
      }
      if (view === "dashboard" && !this.dashboardLoaded) {
        this.loadDashboardData();
      }
    },
    syncDashboardComparisonFilters() {
      if (
        this.dashboardModelFilter !== "Average" &&
        !this.dashboardModelOptions.includes(this.dashboardModelFilter)
      ) {
        this.dashboardModelFilter = "Average";
      }
      if (
        this.dashboardHarnessFilter !== "Average" &&
        !this.dashboardHarnessOptions.includes(this.dashboardHarnessFilter)
      ) {
        this.dashboardHarnessFilter = "Average";
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
    splitEvaluationTargetName(name) {
      const value = String(name || "");
      const separatorIndex = value.indexOf("@");
      if (separatorIndex < 0) {
        const legacyTarget = value.match(/^(.+?)\(([^()]*)\)(.*)$/);
        if (legacyTarget) {
          return {
            harness: `${legacyTarget[1]}${legacyTarget[3]}`.trim() || "-",
            model: legacyTarget[2].trim() || "-",
          };
        }
        return { harness: value || "-", model: "-" };
      }
      return {
        harness: value.slice(0, separatorIndex) || "-",
        model: value.slice(separatorIndex + 1) || "-",
      };
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
    async loadEvaluationCatalog() {
      try {
        const [harnesses, models, configuredSkills] = await Promise.all([
          analystBenchApi.listEvaluationHarnesses(),
          analystBenchApi.listEvaluationModels(),
          analystBenchApi.listSkills().catch(() => []),
        ]);
        this.evaluationHarnesses = harnesses;
        this.evaluationModels = models;
        this.configuredSkills = configuredSkills;
        this.connection = "connected";
      } catch {
        this.evaluationHarnesses = [];
        this.evaluationModels = [];
        this.configuredSkills = [];
      }
    },
    openSkillDialog() {
      const harness = this.skillConfigurationHarnesses[0];
      this.skillForm = { harness_id: harness?.id || "", key: "" };
      this.skillScanResults = [];
      this.showSkillDialog = true;
      if (harness) this.scanHarnessSkills();
    },
    async scanHarnessSkills() {
      this.skillForm.key = "";
      this.skillScanResults = [];
      if (!this.skillForm.harness_id) return;
      this.skillScanning = true;
      try {
        this.skillScanResults = await analystBenchApi.listHostSkills(
          this.skillForm.harness_id,
        );
      } catch (error) {
        this.showToast(error instanceof Error ? error.message : "扫描 Skill 失败");
      } finally {
        this.skillScanning = false;
      }
    },
    async saveHostSkill() {
      if (!this.skillForm.harness_id || !this.skillForm.key) {
        this.showToast("请选择 Harness 和宿主机 Skill");
        return;
      }
      this.skillSaving = true;
      try {
        await analystBenchApi.adoptHostSkill({
          harness_id: this.skillForm.harness_id,
          key: this.skillForm.key,
        });
        await this.loadEvaluationCatalog();
        this.showSkillDialog = false;
        this.showToast("Skill 已保存，可用于测评和自优化");
      } catch (error) {
        this.showToast(error instanceof Error ? error.message : "保存 Skill 失败");
      } finally {
        this.skillSaving = false;
      }
    },
    openHarnessDialog(harness = null) {
      this.editingHarnessId = harness ? harness.id : "";
      this.harnessForm = harness
        ? {
            key: harness.key,
            skill_base_dir: harness.skill_base_dir || "",
            command_template: harness.command_template,
            timeout_seconds: harness.timeout_seconds,
            concurrency_limit: harness.concurrency_limit,
          }
        : {
            key: "",
            skill_base_dir: "",
            command_template: "",
            timeout_seconds: 1800,
            concurrency_limit: 1,
          };
      this.showHarnessDialog = true;
    },
    async saveEvaluationHarness() {
      const form = this.harnessForm;
      if (
        !form.key.trim() ||
        !form.skill_base_dir.trim() ||
        !form.command_template.trim()
      ) {
        this.showToast("请填写 Harness Key、Skill 本地配置目录和命令模板");
        return;
      }
      this.harnessSaving = true;
      try {
        const payload = {
          skill_base_dir: form.skill_base_dir.trim(),
          command_template: form.command_template.trim(),
          timeout_seconds: form.timeout_seconds,
          concurrency_limit: form.concurrency_limit,
        };
        const item = this.editingHarnessId
          ? await analystBenchApi.reviseEvaluationHarness(this.editingHarnessId, payload)
          : await analystBenchApi.createEvaluationHarness({
              ...payload,
              key: form.key.trim(),
            });
        const probed = await analystBenchApi.probeEvaluationHarness(item.id);
        await this.loadEvaluationCatalog();
        this.showHarnessDialog = false;
        this.showToast(
          probed.probe?.available
            ? "Harness 已创建并检测成功，冻结后即可用于测评"
            : "Harness 已创建，但命令检测失败",
        );
      } catch (error) {
        this.showToast(error instanceof Error ? error.message : "保存 Harness 失败");
      } finally {
        this.harnessSaving = false;
      }
    },
    async probeEvaluationHarness(harness) {
      this.harnessActionId = harness.id;
      this.harnessAction = "probe";
      this.showToast(`正在检测 ${harness.name}…`);
      try {
        const updated = await analystBenchApi.probeEvaluationHarness(harness.id);
        await this.loadEvaluationCatalog();
        this.showToast(
          updated.probe?.available
            ? `${harness.name} 检测成功`
            : this.harnessProbeFailureMessage(updated),
        );
      } catch (error) {
        this.showToast(error instanceof Error ? error.message : "Harness 检测失败");
      } finally {
        this.harnessActionId = "";
        this.harnessAction = "";
      }
    },
    async freezeEvaluationHarness(harness) {
      if (!harness.probe?.available) {
        this.showToast(
          harness.probe?.checked_at
            ? this.harnessProbeFailureMessage(harness)
            : "请先检测 Harness 命令",
        );
        return;
      }
      this.harnessActionId = harness.id;
      this.harnessAction = "freeze";
      try {
        await analystBenchApi.freezeEvaluationHarness(harness.id);
        await this.loadEvaluationCatalog();
        this.showToast("Harness 已冻结");
      } catch (error) {
        this.showToast(error instanceof Error ? error.message : "冻结 Harness 失败");
      } finally {
        this.harnessActionId = "";
        this.harnessAction = "";
      }
    },
    harnessProbeFailureMessage(harness) {
      const probe = harness.probe || {};
      if (probe.reason === "tool_dir_not_found" || probe.tool_dir_ok === false) {
        return `Harness 检测失败：工具目录不存在（${harness.tool_dir || "未配置"}）`;
      }
      if (
        probe.reason === "skill_base_dir_not_found" ||
        probe.skill_base_dir_ok === false
      ) {
        return `Harness 检测失败：Skill 本地配置目录不存在（${harness.skill_base_dir || "未配置"}）`;
      }
      const executable =
        probe.requested_executable || probe.executable || "命令中的可执行文件";
      return `Harness 检测失败：找不到可执行命令 ${executable}。请使用绝对路径，或确保 AnalystBench 服务 PATH 可见。`;
    },
    async deleteEvaluationHarness(harness) {
      if (
        !window.confirm(
          `删除 Harness“${harness.key}”v${harness.version} 吗？历史测评仍会保留该版本快照。`,
        )
      ) {
        return;
      }
      this.catalogActionId = harness.id;
      try {
        await analystBenchApi.archiveEvaluationHarness(harness.id);
        await this.loadEvaluationCatalog();
        this.showToast(`Harness“${harness.key}”已删除`);
      } catch (error) {
        this.showToast(error instanceof Error ? error.message : "删除 Harness 失败");
      } finally {
        this.catalogActionId = "";
      }
    },
    openModelDialog() {
      this.modelForm = { key: "" };
      this.showModelDialog = true;
    },
    async saveEvaluationModel() {
      const form = this.modelForm;
      if (!form.key.trim()) {
        this.showToast("请填写模型名称");
        return;
      }
      this.modelSaving = true;
      try {
        await analystBenchApi.createEvaluationModel({ key: form.key.trim() });
        await this.loadEvaluationCatalog();
        this.showModelDialog = false;
        this.showToast("模型已保存");
      } catch (error) {
        this.showToast(error instanceof Error ? error.message : "保存模型失败");
      } finally {
        this.modelSaving = false;
      }
    },
    async deleteEvaluationModel(model) {
      if (
        !window.confirm(
          `删除模型“${model.key}”v${model.version} 吗？历史测评仍会保留该版本快照。`,
        )
      ) {
        return;
      }
      this.catalogActionId = model.id;
      try {
        await analystBenchApi.archiveEvaluationModel(model.id);
        await this.loadEvaluationCatalog();
        this.showToast(`模型“${model.key}”已删除`);
      } catch (error) {
        this.showToast(error instanceof Error ? error.message : "删除模型失败");
      } finally {
        this.catalogActionId = "";
      }
    },
    openMethodDialog(method = null) {
      this.editingMethodId = method ? method.id : "";
      this.methodForm = method
        ? {
            key: method.key,
            tool_dir: method.tool_dir || "",
            command_template: method.command_template,
            timeout_seconds: method.timeout_seconds,
            max_output_bytes: method.max_output_bytes,
            concurrency_limit: method.concurrency_limit,
          }
        : {
            key: "",
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
      if (!form.key.trim() || !form.command_template.trim()) {
        this.showToast("请填写 Key 和命令模板");
        return;
      }
      this.methodSaving = true;
      try {
        const shared = {
          name: form.key.trim(),
          command_template: form.command_template.trim(),
          tool_dir: form.tool_dir.trim(),
          timeout_seconds: form.timeout_seconds,
          max_output_bytes: form.max_output_bytes,
          concurrency_limit: form.concurrency_limit,
        };
        const created = this.editingMethodId
          ? await analystBenchApi.reviseEvaluationMethod(
              this.editingMethodId,
              shared,
            )
          : await analystBenchApi.createEvaluationMethod({
              ...shared,
              key: form.key.trim(),
            });
        const probed = await analystBenchApi.probeEvaluationMethod(created.id);
        await this.loadEvaluationMethods();
        this.showMethodDialog = false;
        this.showToast(
          `${this.editingMethodId ? `新版本 v${created.version} 已创建` : "测评方式已创建"}，${
            probed.probe && probed.probe.available
              ? "检测成功，请确认后冻结"
              : "命令不可用，请修改后重新检测"
          }`,
        );
      } catch (error) {
        this.showToast(error instanceof Error ? error.message : "保存测评方式失败");
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
    async deleteEvaluationMethod(method) {
      if (
        !window.confirm(
          `彻底删除测评方式 ${method.key} v${method.version} 吗？\n\n` +
            "所有引用该版本的测评批次、正式结果目录和定时执行历史都会一并删除；" +
            "包含其他测评方式的同一批次也会整体删除。此操作不可恢复。",
        )
      ) {
        return;
      }
      try {
        const result = await analystBenchApi.deleteEvaluationMethod(method.id);
        await Promise.all([
          this.loadEvaluationMethods(),
          this.loadEvaluationSubmissions(),
          this.loadEvaluationSchedules(),
          this.refreshDirectResults(),
        ]);
        this.showToast(
          `测评方式已删除，同时清理 ${result.submissions_deleted || 0} 个批次、` +
            `${result.local_directories_deleted || 0} 个本地结果目录`,
        );
      } catch (error) {
        this.showToast(error instanceof Error ? error.message : "删除失败");
      }
    },
    scheduleCasePath(caseItem) {
      return `${this.scheduleForm.dataset_key}/${caseItem.category}/${caseItem.key}`;
    },
    targetSelectionKey(harnessId, modelId, skillKey = null) {
      return `${harnessId}|${modelId || ""}|${skillKey || ""}`;
    },
    targetSelectionPayload(keys) {
      return keys.map((key) => {
        const [harness_id, modelId, skillKey] = key.split("|");
        return {
          harness_id,
          model_id: modelId || null,
          skill_key: skillKey || null,
        };
      });
    },
    resetScheduleCaseSelection() {
      this.scheduleForm.case_paths = [];
    },
    async loadEvaluationSchedules() {
      try {
        this.evaluationSchedules =
          await analystBenchApi.listEvaluationSchedules();
        this.connection = "connected";
      } catch {
        this.evaluationSchedules = [];
      }
    },
    async openScheduleDialog(schedule = null) {
      await Promise.all([
        this.loadLocalCaseTree(),
        this.loadEvaluationMethods(),
        this.loadEvaluationCatalog(),
      ]);
      this.editingScheduleId = schedule ? schedule.id : "";
      const scheduleSelectionKeys = schedule
        ? (schedule.target_selections || []).map((selection) =>
            this.targetSelectionKey(
              selection.harness_id,
              selection.model_id,
              selection.skill_key,
            ),
          )
        : [];
      this.scheduleForm = schedule
        ? {
            name: schedule.name,
            dataset_key: schedule.dataset_key,
            case_mode: schedule.case_mode,
            case_paths: [...schedule.case_paths],
            method_ids: scheduleSelectionKeys.length ? [] : [...schedule.method_ids],
            target_selection_keys: scheduleSelectionKeys,
            judge_runner: schedule.judge_runner,
            timezone: schedule.timezone,
            local_time: schedule.local_time,
            enabled: schedule.enabled,
          }
        : {
            name: "",
            dataset_key: this.localCaseTree.length
              ? this.localCaseTree[0].key
              : "",
            case_mode: "all_ready",
            case_paths: [],
            method_ids:
              !this.allEvaluationSelectionKeys.length &&
              this.frozenEvaluationMethods.length === 1
                ? [this.frozenEvaluationMethods[0].id]
                : [],
            target_selection_keys: [...this.allEvaluationSelectionKeys],
            judge_runner: "claude",
            timezone: "Asia/Shanghai",
            local_time: "23:00",
            enabled: true,
          };
      this.showScheduleDialog = true;
    },
    async saveEvaluationSchedule() {
      const form = this.scheduleForm;
      if (!form.name.trim()) return this.showToast("请输入计划名称");
      if (!form.dataset_key) return this.showToast("请选择测试集");
      if (!form.method_ids.length && !form.target_selection_keys.length) {
        return this.showToast("请选择至少一种运行组合");
      }
      if (form.case_mode === "selected" && !form.case_paths.length) {
        return this.showToast("固定选择模式至少选择一个 Case");
      }
      this.scheduleSaving = true;
      try {
        const { target_selection_keys, ...formPayload } = form;
        const payload = {
          ...formPayload,
          name: form.name.trim(),
          timezone: form.timezone.trim(),
          target_selections: this.targetSelectionPayload(target_selection_keys),
        };
        if (this.editingScheduleId) {
          await analystBenchApi.updateEvaluationSchedule(
            this.editingScheduleId,
            payload,
          );
        } else {
          await analystBenchApi.createEvaluationSchedule(payload);
        }
        await this.loadEvaluationSchedules();
        this.showScheduleDialog = false;
        this.showToast(
          this.editingScheduleId ? "定时计划已更新" : "定时计划已创建",
        );
      } catch (error) {
        this.showToast(error instanceof Error ? error.message : "保存定时计划失败");
      } finally {
        this.scheduleSaving = false;
      }
    },
    async toggleEvaluationSchedule(schedule) {
      try {
        await analystBenchApi.setEvaluationScheduleEnabled(
          schedule.id,
          !schedule.enabled,
        );
        await this.loadEvaluationSchedules();
        this.showToast(schedule.enabled ? "定时计划已停用" : "定时计划已启用");
      } catch (error) {
        this.showToast(error instanceof Error ? error.message : "更新计划状态失败");
      }
    },
    async runEvaluationScheduleNow(schedule) {
      try {
        const run = await analystBenchApi.runEvaluationScheduleNow(schedule.id);
        await this.loadEvaluationSchedules();
        this.showToast("定时计划已立即入队");
        this.pollEvaluationScheduleRun(run.id);
      } catch (error) {
        this.showToast(error instanceof Error ? error.message : "立即运行失败");
      }
    },
    async pollEvaluationScheduleRun(runId) {
      const terminal = [
        "completed",
        "completed_with_errors",
        "failed",
        "cancelled",
        "skipped_no_cases",
        "skipped_overlap",
        "failed_preflight",
      ];
      for (let index = 0; index < 720; index += 1) {
        await new Promise((resolve) => window.setTimeout(resolve, 5000));
        try {
          const run = await analystBenchApi.getEvaluationScheduleRun(runId);
          if (terminal.includes(run.status)) {
            await Promise.all([
              this.loadEvaluationSchedules(),
              this.loadEvaluationSubmissions(),
            ]);
            this.showToast(`定时测评已结束：${run.status}`);
            return;
          }
        } catch {
          // A transient polling error must not change the durable schedule run.
        }
      }
    },
    async openEvaluationScheduleRuns(schedule) {
      try {
        this.scheduleRuns =
          await analystBenchApi.listEvaluationScheduleRuns(schedule.id);
        this.scheduleRunsTitle = schedule.name;
        this.showScheduleRunsDialog = true;
      } catch (error) {
        this.showToast(error instanceof Error ? error.message : "读取执行记录失败");
      }
    },
    async deleteEvaluationSchedule(schedule) {
      if (!window.confirm(`删除定时计划“${schedule.name}”吗？`)) return;
      try {
        await analystBenchApi.deleteEvaluationSchedule(schedule.id);
        await this.loadEvaluationSchedules();
        this.showToast("定时计划已删除");
      } catch (error) {
        if (
          error &&
          error.code === "evaluation_schedule_in_use" &&
          window.confirm(`${error.message}\n\n是否停用该计划？`)
        ) {
          await analystBenchApi.setEvaluationScheduleEnabled(schedule.id, false);
          await this.loadEvaluationSchedules();
          this.showToast("定时计划已停用");
          return;
        }
        this.showToast(error instanceof Error ? error.message : "删除计划失败");
      }
    },
    formatScheduleDateTime(value, timezone = "Asia/Shanghai") {
      if (!value) return "—";
      try {
        return new Intl.DateTimeFormat("zh-CN", {
          timeZone: timezone,
          month: "2-digit",
          day: "2-digit",
          hour: "2-digit",
          minute: "2-digit",
          hour12: false,
        }).format(new Date(value));
      } catch {
        return String(value);
      }
    },
    formatDuration(value) {
      return formatDurationMs(value);
    },
    methodRunDuration(methodRun) {
      if (methodRun?.status === "running" && methodRun?.started_at) {
        return elapsedDurationMs(methodRun.started_at, this.methodTimingNow);
      }
      return methodRun?.duration_ms ?? null;
    },
    formatMethodRunTiming(methodRun) {
      const duration = this.methodRunDuration(methodRun);
      if (duration === null) return "—";
      const prefix = methodRun?.status === "running" ? "已运行" : "耗时";
      return `${prefix} ${formatDurationMs(duration)}`;
    },
    resultGenerationDuration(candidateName) {
      const targets = this.selectedResultData?.generation?.targets || [];
      const target = targets.find((item) => item.target_key === candidateName);
      if (target) return target.duration_ms ?? null;
      const methods = this.selectedResultData?.generation?.methods || [];
      const method = methods.find((item) => item.key === candidateName);
      return method?.duration_ms ?? null;
    },
    formatMethodTimestamp(value) {
      if (!value) return "—";
      const parsed = new Date(value);
      if (Number.isNaN(parsed.getTime())) return String(value);
      return new Intl.DateTimeFormat("zh-CN", {
        year: "numeric",
        month: "2-digit",
        day: "2-digit",
        hour: "2-digit",
        minute: "2-digit",
        second: "2-digit",
        hour12: false,
      }).format(parsed);
    },
    scheduleStatusClass(status) {
      if (status === "completed") return "tag-match";
      if (
        [
          "failed",
          "completed_with_errors",
          "failed_preflight",
          "skipped_no_cases",
        ].includes(status)
      ) {
        return "tag-missing";
      }
      return "tag-partial";
    },
    async openSubmitEvaluationDialog() {
      await Promise.all([
        this.loadLocalCaseTree(),
        this.loadEvaluationMethods(),
        this.loadEvaluationCatalog(),
      ]);
      const frozenMethods = this.frozenEvaluationMethods;
      this.submissionForm = {
        dataset_key: this.localCaseTree.length ? this.localCaseTree[0].key : "",
        case_paths: [],
        method_ids:
          !this.allEvaluationSelectionKeys.length && frozenMethods.length === 1
            ? [frozenMethods[0].id]
            : [],
        target_selection_keys: [...this.allEvaluationSelectionKeys],
        judge_runner: "claude",
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
      if (
        this.submissionStep === 2 &&
        !this.submissionForm.method_ids.length &&
        !this.submissionForm.target_selection_keys.length
      ) {
        this.showToast(
          this.frozenEvaluationMethods.length || this.allEvaluationSelectionKeys.length
            ? "请选择至少一种运行组合"
            : "没有可用组合，请先检测并冻结 Harness，并添加冻结模型",
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
      if (
        !this.submissionForm.method_ids.length &&
        !this.submissionForm.target_selection_keys.length
      ) {
        this.showToast("请选择至少一种运行组合");
        return;
      }
      this.submissionRunning = true;
      try {
        const { target_selection_keys, ...formPayload } = this.submissionForm;
        const submission = await analystBenchApi.createEvaluationSubmission(
          {
            ...formPayload,
            target_selections: this.targetSelectionPayload(target_selection_keys),
          },
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
    async deleteEvaluationSubmission(submission) {
      if (
        !window.confirm(
          `永久删除测评批次 ${submission.dataset_key} · ${submission.timestamp} 吗？\n\n` +
            "该批次的运行记录、审计任务和正式结果目录都会被删除。",
        )
      ) {
        return;
      }
      try {
        const result = await analystBenchApi.deleteEvaluationSubmission(
          submission.id,
        );
        if (this.selectedSubmissionId === submission.id) {
          this.selectedSubmissionId = "";
          this.submissionCaseRuns = [];
        }
        await this.loadEvaluationSubmissions();
        await this.refreshDirectResults();
        this.dashboardLoaded = false;
        this.showToast(
          `批次已删除，同时清理 ${result.local_directories_deleted || 0} 个正式结果目录`,
        );
      } catch (error) {
        this.showToast(error instanceof Error ? error.message : "删除批次失败");
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
    async openTargetComparison(submission) {
      try {
        this.targetComparison = await analystBenchApi.getEvaluationTargetComparison(
          submission.id,
        );
        this.showTargetComparisonDialog = true;
      } catch (error) {
        this.showToast(error instanceof Error ? error.message : "读取组合对比失败");
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
    async toggleDirectResultVisibility(item) {
      const includedInStatistics = item.included_in_statistics === false;
      this.loading = true;
      try {
        await analystBenchApi.setDirectResultVisibility(
          item.id,
          includedInStatistics,
        );
        await this.refreshDirectResults();
        this.dashboardLoaded = false;
        await this.loadDashboardData();
        this.showToast(
          includedInStatistics
            ? "该结果已显示并计入统计"
            : "该结果已隐藏且不计入统计",
        );
      } catch (error) {
        this.showToast(
          error instanceof Error ? error.message : "更新结果显示状态失败",
        );
      } finally {
        this.loading = false;
      }
    },
    async loadOptimizationWorkspace() {
      this.optimizationLoading = true;
      try {
        const [skills, targets, experiments] = await Promise.all([
          analystBenchApi.listSkills(),
          analystBenchApi.listEvaluationTargets(),
          analystBenchApi.listOptimizationExperiments(),
          this.loadLocalCaseTree(),
          this.loadEvaluationCatalog(),
        ]);
        this.skills = skills;
        this.evaluationTargets = targets;
        this.optimizationExperiments = experiments;
        this.connection = "connected";
        if (
          !this.selectedOptimizationExperimentId ||
          !experiments.some(
            (item) => item.id === this.selectedOptimizationExperimentId,
          )
        ) {
          this.selectedOptimizationExperimentId = experiments[0]?.id || "";
          this.optimizationEpochOffset = 0;
          this.optimizationPreflight = null;
        }
        if (this.selectedOptimizationExperimentId) {
          await this.refreshSelectedOptimization();
        } else {
          this.selectedOptimizationDetail = null;
          this.selectedOptimizationEvents = [];
          this.selectedOptimizationVersions = [];
          this.selectedOptimizationBinding = null;
          this.selectedOptimizationBindingHistory = [];
          this.optimizationPreflight = null;
          this.optimizationEpochOffset = 0;
        }
      } catch (error) {
        this.connection = "offline";
        this.showToast(
          error instanceof Error ? error.message : "读取 Skill 自优化数据失败",
        );
      } finally {
        this.optimizationLoading = false;
      }
    },
    async selectOptimizationExperiment(experimentId) {
      this.selectedOptimizationExperimentId = experimentId;
      this.optimizationCandidateDetail = null;
      this.optimizationPreflight = null;
      this.optimizationEpochOffset = 0;
      this.selectedOptimizationDetail = null;
      this.selectedOptimizationEvents = [];
      this.selectedOptimizationVersions = [];
      this.selectedOptimizationBinding = null;
      this.selectedOptimizationBindingHistory = [];
      await this.refreshSelectedOptimization();
    },
    async refreshSelectedOptimization() {
      if (!this.selectedOptimizationExperimentId) return;
      const experimentId = this.selectedOptimizationExperimentId;
      try {
        const [detail, events, experiments] = await Promise.all([
          analystBenchApi.getOptimizationExperimentDetail(
            experimentId,
            {
              epoch_offset: this.optimizationEpochOffset,
              epoch_limit: this.optimizationEpochLimit,
            },
          ),
          analystBenchApi.getOptimizationEvents(experimentId),
          analystBenchApi.listOptimizationExperiments(),
        ]);
        if (experimentId !== this.selectedOptimizationExperimentId) return;
        const total = Number(detail.pagination?.total || 0);
        if (total && this.optimizationEpochOffset >= total) {
          this.optimizationEpochOffset =
            Math.floor((total - 1) / this.optimizationEpochLimit) *
            this.optimizationEpochLimit;
          await this.refreshSelectedOptimization();
          return;
        }
        const [versions, bindings, bindingHistory] = await Promise.all([
          analystBenchApi.listSkillVersions(detail.experiment.skill_id),
          analystBenchApi.listSkillBindings(detail.experiment.skill_id),
          analystBenchApi.listSkillBindingHistory(detail.experiment.skill_id, {
            evaluation_target_id: detail.experiment.evaluation_target_id,
            limit: 500,
          }),
        ]);
        if (experimentId !== this.selectedOptimizationExperimentId) return;
        this.selectedOptimizationDetail = detail;
        this.selectedOptimizationEvents = events;
        this.optimizationExperiments = experiments;
        this.selectedOptimizationVersions = [...versions].sort(
          (left, right) => right.version - left.version,
        );
        this.selectedOptimizationBinding =
          bindings.find(
            (item) =>
              item.evaluation_target_id ===
              detail.experiment.evaluation_target_id,
          ) || null;
        this.selectedOptimizationBindingHistory = bindingHistory;
      } catch (error) {
        this.showToast(
          error instanceof Error ? error.message : "刷新优化实验失败",
        );
      }
    },
    async loadOptimizationEpochPage(offset) {
      const total = this.selectedOptimizationDetail?.pagination?.total || 0;
      const lastPageOffset = total
        ? Math.floor((total - 1) / this.optimizationEpochLimit) *
          this.optimizationEpochLimit
        : 0;
      this.optimizationEpochOffset = Math.max(
        0,
        Math.min(offset, lastPageOffset),
      );
      await this.refreshSelectedOptimization();
    },
    downloadBlob(blob, filename) {
      if (typeof Blob === "undefined" || !(blob instanceof Blob)) {
        throw new Error("服务端未返回可下载文件");
      }
      const url = window.URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = filename;
      anchor.style.display = "none";
      document.body.appendChild(anchor);
      anchor.click();
      anchor.remove();
      window.setTimeout(() => window.URL.revokeObjectURL(url), 1000);
    },
    async exportOptimizationLedger(format) {
      const experiment = this.selectedOptimizationDetail?.experiment;
      if (!experiment || this.optimizationExportFormat) return;
      this.optimizationExportFormat = format;
      try {
        const blob = await analystBenchApi.exportOptimizationLedger(
          experiment.id,
          format,
        );
        const suffix = format === "markdown" ? "md" : format;
        this.downloadBlob(blob, `skill-optimization-${experiment.id}.${suffix}`);
      } catch (error) {
        this.showToast(error instanceof Error ? error.message : "导出优化账本失败");
      } finally {
        this.optimizationExportFormat = "";
      }
    },
    async runOptimizationPreflight() {
      const experiment = this.selectedOptimizationDetail?.experiment;
      if (!experiment || this.optimizationPreflightRunning) return;
      const skill = this.skills.find((item) => item.id === experiment.skill_id);
      this.optimizationPreflightRunning = true;
      try {
        this.optimizationPreflight = await analystBenchApi.runOptimizationPreflight({
          skill_key: skill?.key || null,
          evaluation_target_id: experiment.evaluation_target_id,
          optimizer_policy_version_id: experiment.optimizer_policy_version_id,
          verifier_bundle_version_id: experiment.verifier_bundle_version_id,
          data_snapshot_id: experiment.data_snapshot_id,
        });
        this.showToast(`环境预检：${this.optimizationPreflight.status}`);
      } catch (error) {
        this.showToast(error instanceof Error ? error.message : "环境预检失败");
      } finally {
        this.optimizationPreflightRunning = false;
      }
    },
    async exportOptimizationVersion(version) {
      const experiment = this.selectedOptimizationDetail?.experiment;
      if (!experiment || this.optimizationVersionActionId) return;
      this.optimizationVersionActionId = `export:${version.id}`;
      try {
        const blob = await analystBenchApi.exportSkillVersion(
          experiment.skill_id,
          version.id,
        );
        this.downloadBlob(blob, `skill-v${version.version}-${version.id}.zip`);
      } catch (error) {
        this.showToast(error instanceof Error ? error.message : "导出 Skill 版本失败");
      } finally {
        this.optimizationVersionActionId = "";
      }
    },
    async rollbackOptimizationVersion(version) {
      const experiment = this.selectedOptimizationDetail?.experiment;
      const binding = this.selectedOptimizationBinding;
      if (
        !experiment ||
        !binding ||
        binding.active_version_id === version.id ||
        this.optimizationVersionActionId
      ) {
        return;
      }
      if (!this.optimizationRollbackVersionIds.has(version.id)) {
        this.showToast("该版本从未在当前 Target 上激活，不能回滚");
        return;
      }
      if (!window.confirm(`将 Active Skill 显式回滚到 v${version.version} 吗？`)) {
        return;
      }
      this.optimizationVersionActionId = `rollback:${version.id}`;
      try {
        await analystBenchApi.rollbackSkillVersion(
          experiment.skill_id,
          experiment.evaluation_target_id,
          {
            version_id: version.id,
            expected_lock_version: binding.lock_version,
            reason: "manual_ui_rollback",
          },
        );
        await this.refreshSelectedOptimization();
        this.showToast(`已回滚到 Skill v${version.version}`);
      } catch (error) {
        this.showToast(error instanceof Error ? error.message : "回滚 Skill 失败");
        await this.refreshSelectedOptimization();
      } finally {
        this.optimizationVersionActionId = "";
      }
    },
    openOptimizationDialog() {
      const readyCases = this.optimizationCaseOptions
        .filter((item) => item.ready)
        .map((item) => item.path);
      const defaultCombination = this.optimizationCombinationOptions[0];
      this.optimizationDialogStep = 1;
      this.optimizationForm = {
        name: `Skill 优化 ${new Date().toLocaleDateString()}`,
        combination_key: defaultCombination?.key || "",
        evaluation_target_id: "",
        data_mode: "development_regression",
        case_paths: readyCases,
        train_case_paths: [],
        validation_case_paths: [],
        hidden_test_case_paths: [],
        prospective_holdout_case_paths: [],
        optimizer_runner: "claude",
        optimizer_executable: "claude",
        optimizer_instruction:
          "根据失败证据对当前 Skill 做小步、通用、可迁移的优化。不要写入具体 Case 答案。",
        judge_runner: "claude",
        min_overall_delta: 1,
        max_latency_growth: 0.2,
        max_token_growth: 0.2,
        max_epochs: 1,
      };
      this.showOptimizationDialog = true;
    },
    toggleOptimizationCase(casePath) {
      const selected = new Set(this.optimizationForm.case_paths);
      if (selected.has(casePath)) selected.delete(casePath);
      else selected.add(casePath);
      this.optimizationForm.case_paths = [...selected];
    },
    optimizationCaseSplit(casePath) {
      const splitKeys = [
        "train_case_paths",
        "validation_case_paths",
        "hidden_test_case_paths",
        "prospective_holdout_case_paths",
      ];
      return (
        splitKeys.find((key) =>
          this.optimizationForm[key].includes(casePath),
        ) || ""
      );
    },
    setOptimizationCaseSplit(casePath, splitKey) {
      const splitKeys = [
        "train_case_paths",
        "validation_case_paths",
        "hidden_test_case_paths",
        "prospective_holdout_case_paths",
      ];
      splitKeys.forEach((key) => {
        this.optimizationForm[key] = this.optimizationForm[key].filter(
          (item) => item !== casePath,
        );
      });
      if (splitKeys.includes(splitKey)) {
        this.optimizationForm[splitKey] = [
          ...this.optimizationForm[splitKey],
          casePath,
        ];
      }
    },
    syncOptimizationDataMode() {
      if (this.optimizationForm.data_mode === "independent_validation") {
        this.optimizationForm.max_epochs = 1;
      }
    },
    syncOptimizationCombination() {
      this.optimizationForm.evaluation_target_id = "";
    },
    async createOptimizationExperiment() {
      const form = this.optimizationForm;
      const combination = this.selectedOptimizationCombination;
      if (!form.name || !combination) {
        this.showToast("请填写实验名称并选择 Harness × 模型 × Skill 组合");
        return;
      }
      if (
        form.data_mode === "development_regression" &&
        !form.case_paths.length
      ) {
        this.showToast("开发回归模式至少需要一个可用 Case");
        return;
      }
      if (
        form.data_mode === "independent_validation" &&
        (!form.train_case_paths.length || !form.validation_case_paths.length)
      ) {
        this.showToast("独立验证模式必须同时选择 Train 和 Validation Case");
        return;
      }
      const datasets = new Set(
        this.optimizationSelectedCasePaths.map((item) => item.split("/")[0]),
      );
      if (datasets.size !== 1) {
        this.showToast("所有 Split 的 Case 必须属于同一个测试集");
        return;
      }
      this.optimizationSaving = true;
      try {
        const resolved = await analystBenchApi.resolveHostSkillCombination({
          harness_id: combination.harness.id,
          model_id: combination.model?.id || null,
          key: combination.skill.key,
        });
        const target = resolved.target;
        const activeSkill = target.active_skill;
        const skillId = activeSkill.skill_id;
        const skillKey = activeSkill.skill_key;
        const baseVersionId = activeSkill.skill_package_version_id;
        form.evaluation_target_id = target.id;
        const snapshot = await analystBenchApi.createOptimizationSnapshot({
          dataset_key: [...datasets][0],
          mode: form.data_mode,
          validation_case_paths:
            form.data_mode === "development_regression"
              ? form.case_paths
              : form.validation_case_paths,
          train_case_paths:
            form.data_mode === "independent_validation"
              ? form.train_case_paths
              : [],
          hidden_test_case_paths:
            form.data_mode === "independent_validation"
              ? form.hidden_test_case_paths
              : [],
          prospective_holdout_case_paths:
            form.data_mode === "independent_validation"
              ? form.prospective_holdout_case_paths
              : [],
        });
        const profile = await analystBenchApi.createExecutionProfile({
          name: `${skillKey}-optimizer`,
          runner: form.optimizer_runner,
          configuration: {
            executable: form.optimizer_executable,
            timeout_seconds: 1800,
            max_output_bytes: 10485760,
            environment_mode: "local",
            allowed_tools: ["Read", "Grep", "Glob"],
          },
        });
        const probe = await analystBenchApi.validateExecutionProfile(profile.id);
        if (!probe.available) {
          throw new Error(probe.error || "Optimizer CLI 不可用");
        }
        await analystBenchApi.freezeExecutionProfile(profile.id);
        const suffix = Date.now();
        const policy = await analystBenchApi.createOptimizerPolicy({
          key: `${skillKey}-optimizer-${suffix}`,
          execution_profile_id: profile.id,
          prompt_bundle: { instruction: form.optimizer_instruction },
          config: {},
        });
        const verifier = await analystBenchApi.createVerifierBundle({
          key: `${skillKey}-gate-${suffix}`,
          static_policy: {
            allowed_operations: [
              "append",
              "insert_after",
              "replace",
              "delete",
            ],
            edit_budget_schedule: [4, 4, 3, 2, 1],
            max_changed_files: 2,
            max_added_tokens: 600,
            max_deleted_tokens: 300,
            max_single_file_change_ratio: 0.25,
            static_validation: {
              content_security_scan: { enabled: true },
              case_leak_scan: { enabled: true },
              referenced_file_check: { enabled: true },
              script_syntax: { enabled: true },
              package_tests: { enabled: true, max_timeout_seconds: 30 },
            },
          },
          gate_policy: {
            min_overall_delta: Number(form.min_overall_delta),
            max_latency_growth: Number(form.max_latency_growth),
            max_token_growth: Number(form.max_token_growth),
          },
          judge_config: { runner: form.judge_runner },
        });
        const preflight = await analystBenchApi.runOptimizationPreflight({
          skill_key: skillKey,
          evaluation_target_id: target.id,
          execution_profile_id: profile.id,
          optimizer_policy_version_id: policy.id,
          verifier_bundle_version_id: verifier.id,
          case_paths:
            form.data_mode === "independent_validation"
              ? [
                  ...form.train_case_paths,
                  ...form.validation_case_paths,
                  ...form.hidden_test_case_paths,
                  ...form.prospective_holdout_case_paths,
                ]
              : form.case_paths,
          data_snapshot_id: snapshot.id,
        });
        if (preflight.status === "FAIL") {
          const failedChecks = (preflight.checks || [])
            .filter((check) => check.status === "FAIL")
            .map((check) => check.code)
            .join("、");
          throw new Error(
            `私有环境预检未通过${failedChecks ? `：${failedChecks}` : ""}`,
          );
        }
        this.optimizationPreflight = preflight;
        const experiment = await analystBenchApi.createOptimizationExperiment({
          name: form.name,
          skill_id: skillId,
          base_skill_version_id: baseVersionId,
          evaluation_target_id: target.id,
          data_snapshot_id: snapshot.id,
          optimizer_policy_version_id: policy.id,
          verifier_bundle_version_id: verifier.id,
          max_epochs:
            form.data_mode === "independent_validation"
              ? 1
              : Number(form.max_epochs),
        });
        await analystBenchApi.startOptimizationExperiment(experiment.id);
        this.showOptimizationDialog = false;
        this.selectedOptimizationExperimentId = experiment.id;
        await this.loadOptimizationWorkspace();
        this.showToast("优化实验已启动");
      } catch (error) {
        this.showToast(
          error instanceof Error ? error.message : "创建优化实验失败",
        );
      } finally {
        this.optimizationSaving = false;
      }
    },
    async startOptimizationExperiment(experiment) {
      try {
        await analystBenchApi.startOptimizationExperiment(experiment.id);
        await this.loadOptimizationWorkspace();
        this.showToast("实验已启动");
      } catch (error) {
        this.showToast(error instanceof Error ? error.message : "启动实验失败");
      }
    },
    async resumeOptimizationExperiment(experiment) {
      try {
        await analystBenchApi.resumeOptimizationExperiment(experiment.id);
        await this.loadOptimizationWorkspace();
        this.showToast("实验已从已有 Run Group 恢复");
      } catch (error) {
        this.showToast(error instanceof Error ? error.message : "恢复实验失败");
      }
    },
    async cancelOptimizationExperiment(experiment) {
      if (!window.confirm(`取消实验“${experiment.name}”吗？`)) return;
      try {
        await analystBenchApi.cancelOptimizationExperiment(experiment.id);
        await this.loadOptimizationWorkspace();
        this.showToast("实验已取消");
      } catch (error) {
        this.showToast(error instanceof Error ? error.message : "取消实验失败");
      }
    },
    async openOptimizationCandidate(candidate) {
      try {
        this.optimizationCandidateDetail =
          await analystBenchApi.getOptimizationCandidate(candidate.id);
      } catch (error) {
        this.showToast(error instanceof Error ? error.message : "读取候选 Diff 失败");
      }
    },
    optimizationVersionTargetLabel(version) {
      if (this.selectedOptimizationBinding?.active_version_id === version.id) {
        return `ACTIVE · ${this.selectedOptimizationBinding.active_level}`;
      }
      const history = this.selectedOptimizationBindingHistory.find(
        (item) =>
          item.active_version_id === version.id &&
          item.evaluation_target_id ===
            this.selectedOptimizationDetail?.experiment?.evaluation_target_id,
      );
      if (history) return `曾 ACTIVE · ${history.active_level}`;
      if (version.status === "active") return "其他 Target 已激活";
      return this.optimizationStatusLabel(version.status);
    },
    hasOptimizationChangeStats(value) {
      return Boolean(
        value &&
          typeof value === "object" &&
          Object.keys(value).length,
      );
    },
    optimizationTokenChanges(value) {
      const added = value?.tokens_added ?? value?.added_tokens;
      const removed = value?.tokens_removed ?? value?.deleted_tokens;
      if (added === null || added === undefined) {
        if (removed === null || removed === undefined) return "—";
        return `+— / -${removed}`;
      }
      return `+${added} / -${removed ?? "—"}`;
    },
    optimizationReasonLabel(reason) {
      if (typeof reason === "string") return reason;
      if (!reason || typeof reason !== "object") return "未提供原因";
      const code = reason.code || reason.rule || "gate_reason";
      const observed = reason.observed ?? reason.actual;
      const required = reason.required ?? reason.limit;
      if (observed !== undefined || required !== undefined) {
        return `${code}（observed ${observed ?? "—"} / required ${required ?? "—"}）`;
      }
      return reason.message ? `${code}：${reason.message}` : code;
    },
    optimizationRejectionMessage(value) {
      const detail = value?.detail || value?.rejection_detail;
      if (!detail || typeof detail !== "object") return "";
      return detail.message || detail.reason || "";
    },
    optimizationStatusClass(status) {
      if (
        ["completed", "accepted", "screening_selected", "pass"].includes(status)
      ) {
        return "tag-match";
      }
      if (["failed", "cancelled", "rejected", "reject"].includes(status)) {
        return "tag-missing";
      }
      return "tag-partial";
    },
    optimizationStatusLabel(status) {
      return {
        created: "待启动",
        running: "运行中",
        completed: "已完成",
        failed: "失败",
        cancelled: "已取消",
        collecting_evidence: "采集证据",
        generating_candidates: "生成候选",
        screening: "筛选候选",
        full_validating: "完整验证",
        accepted: "已接受",
        rejected: "已拒绝",
        screening_selected: "筛选胜出",
        validating: "验证中",
        needs_more_runs: "灰区增采样",
        validated_static: "静态校验通过",
        candidate: "候选版本",
        active: "已激活",
        pass: "通过",
        reject: "拒绝",
        promote: "晋升",
        retain: "保留基线",
        no_screening_survivor: "无候选通过筛选",
      }[status] || status;
    },
    signedDelta(value) {
      const number = Number(value);
      if (!Number.isFinite(number)) return "—";
      return `${number > 0 ? "+" : ""}${number.toFixed(2)}`;
    },
    optimizationObjectRows(value) {
      return Object.entries(value || {}).map(([key, item]) => ({
        key,
        value: item,
      }));
    },
    candidateComparison(candidate, type) {
      return (candidate.comparisons || []).find((item) => item.type === type);
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
        this.syncDashboardComparisonFilters();
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
