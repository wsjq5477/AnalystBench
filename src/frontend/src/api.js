import request, { ApiError } from "./utils/request";

export { ApiError };

export const analystBenchApi = {
  listDatasets() {
    return request({ url: "/datasets", method: "get" });
  },
  createDataset(data) {
    return request({ url: "/datasets", method: "post", data });
  },
  deleteDataset(datasetId) {
    return request({ url: `/datasets/${datasetId}`, method: "delete" });
  },
  listDatasetCases(datasetId) {
    return request({ url: `/datasets/${datasetId}/cases`, method: "get" });
  },
  listDatasetCategories(datasetId) {
    return request({ url: `/datasets/${datasetId}/categories`, method: "get" });
  },
  createDatasetCategory(datasetId, data) {
    return request({
      url: `/datasets/${datasetId}/categories`,
      method: "post",
      data,
    });
  },
  deleteDatasetCategory(datasetId, categoryId) {
    return request({
      url: `/datasets/${datasetId}/categories/${categoryId}`,
      method: "delete",
    });
  },
  createCase(datasetId, data) {
    return request({ url: `/datasets/${datasetId}/cases`, method: "post", data });
  },
  deleteCase(caseId) {
    return request({ url: `/cases/${caseId}`, method: "delete" });
  },
  createCaseRevision(caseId, data) {
    return request({ url: `/cases/${caseId}/revisions`, method: "post", data });
  },
  getCaseRevisionContent(revisionId) {
    return request({ url: `/case-revisions/${revisionId}/content`, method: "get" });
  },
  createBenchmarkRun(data) {
    return request({ url: "/benchmark-runs", method: "post", data });
  },
  getBenchmarkRun(runId) {
    return request({ url: `/benchmark-runs/${runId}`, method: "get" });
  },
  getBenchmarkCaseRuns(runId) {
    return request({ url: `/benchmark-runs/${runId}/case-runs`, method: "get" });
  },
  getBenchmarkCaseResult(caseRunId) {
    return request({ url: `/benchmark-case-runs/${caseRunId}/result`, method: "get" });
  },
  listDirectResults() {
    return request({ url: "/direct-results", method: "get" });
  },
  getDirectResultStats() {
    return request({ url: "/direct-results/stats", method: "get" });
  },
  getDirectResult(resultId) {
    return request({
      url: `/direct-results/${encodeURIComponent(resultId)}`,
      method: "get",
    });
  },
  deleteDirectResult(resultId) {
    return request({
      url: `/direct-results/${encodeURIComponent(resultId)}`,
      method: "delete",
    });
  },
  promoteDirectResult(resultId, data) {
    return request({
      url: `/direct-results/${encodeURIComponent(resultId)}/promote`,
      method: "post",
      data,
    });
  },
  moveDirectResult(resultId, data) {
    return request({
      url: `/direct-results/${encodeURIComponent(resultId)}/move`,
      method: "post",
      data,
    });
  },
  setDirectResultVisibility(resultId, includedInStatistics) {
    return request({
      url: `/direct-results/${encodeURIComponent(resultId)}/visibility`,
      method: "patch",
      data: { included_in_statistics: includedInStatistics },
    });
  },
  getBenchmarkRuns() {
    return request({ url: "/benchmark-runs", method: "get" });
  },
  getAppSettings() {
    return request({ url: "/settings", method: "get" });
  },
  updateAppSettings(data) {
    return request({ url: "/settings", method: "put", data });
  },
  getLocalCaseTree() {
    return request({ url: "/local-cases/tree", method: "get" });
  },
  getLocalCase(casePath) {
    return request({
      url: `/local-cases/${encodeURIComponent(casePath)}`,
      method: "get",
    });
  },
  getLocalCaseLogs(testSet, category, caseKey) {
    return request({
      url: `/local-cases/${encodeURIComponent(testSet)}/${encodeURIComponent(category)}/${encodeURIComponent(caseKey)}/logs`,
      method: "get",
    });
  },
  uploadLocalCaseLogs(testSet, category, caseKey, files, primary) {
    const data = new FormData();
    files.forEach((file) => data.append("files", file));
    if (primary) data.append("primary", primary);
    return request({
      url: `/local-cases/${encodeURIComponent(testSet)}/${encodeURIComponent(category)}/${encodeURIComponent(caseKey)}/logs`,
      method: "post",
      data,
    });
  },
  setLocalCasePrimaryLog(testSet, category, caseKey, filename) {
    return request({
      url: `/local-cases/${encodeURIComponent(testSet)}/${encodeURIComponent(category)}/${encodeURIComponent(caseKey)}/logs/primary`,
      method: "put",
      data: { filename },
    });
  },
  deleteLocalCaseLog(testSet, category, caseKey, filename) {
    return request({
      url: `/local-cases/${encodeURIComponent(testSet)}/${encodeURIComponent(category)}/${encodeURIComponent(caseKey)}/logs`,
      method: "delete",
      params: { filename },
    });
  },
  listEvaluationMethods() {
    return request({ url: "/evaluation-methods", method: "get" });
  },
  createEvaluationMethod(data) {
    return request({ url: "/evaluation-methods", method: "post", data });
  },
  reviseEvaluationMethod(methodId, data) {
    return request({
      url: `/evaluation-methods/${methodId}:revise`,
      method: "post",
      data,
    });
  },
  probeEvaluationMethod(methodId) {
    return request({
      url: `/evaluation-methods/${methodId}:probe`,
      method: "post",
    });
  },
  freezeEvaluationMethod(methodId) {
    return request({
      url: `/evaluation-methods/${methodId}:freeze`,
      method: "post",
    });
  },
  archiveEvaluationMethod(methodId) {
    return request({
      url: `/evaluation-methods/${methodId}:archive`,
      method: "post",
    });
  },
  deleteEvaluationMethod(methodId) {
    return request({
      url: `/evaluation-methods/${methodId}`,
      method: "delete",
    });
  },
  listEvaluationHarnesses() {
    return request({ url: "/evaluation-harnesses", method: "get" });
  },
  createEvaluationHarness(data) {
    return request({ url: "/evaluation-harnesses", method: "post", data });
  },
  reviseEvaluationHarness(harnessId, data) {
    return request({ url: `/evaluation-harnesses/${harnessId}:revise`, method: "post", data });
  },
  probeEvaluationHarness(harnessId) {
    return request({ url: `/evaluation-harnesses/${harnessId}:probe`, method: "post" });
  },
  freezeEvaluationHarness(harnessId) {
    return request({ url: `/evaluation-harnesses/${harnessId}:freeze`, method: "post" });
  },
  archiveEvaluationHarness(harnessId) {
    return request({ url: `/evaluation-harnesses/${harnessId}:archive`, method: "post" });
  },
  listEvaluationModels() {
    return request({ url: "/evaluation-models", method: "get" });
  },
  createEvaluationModel(data) {
    return request({ url: "/evaluation-models", method: "post", data });
  },
  reviseEvaluationModel(modelId, data) {
    return request({ url: `/evaluation-models/${modelId}:revise`, method: "post", data });
  },
  archiveEvaluationModel(modelId) {
    return request({ url: `/evaluation-models/${modelId}:archive`, method: "post" });
  },
  listEvaluationTargets() {
    return request({ url: "/evaluation-targets", method: "get" });
  },
  createEvaluationTarget(data) {
    return request({ url: "/evaluation-targets", method: "post", data });
  },
  probeEvaluationTarget(targetId) {
    return request({ url: `/evaluation-targets/${targetId}:probe`, method: "post" });
  },
  freezeEvaluationTarget(targetId) {
    return request({ url: `/evaluation-targets/${targetId}:freeze`, method: "post" });
  },
  archiveEvaluationTarget(targetId) {
    return request({ url: `/evaluation-targets/${targetId}:archive`, method: "post" });
  },
  listEvaluationSubmissions() {
    return request({ url: "/evaluation-submissions", method: "get" });
  },
  createEvaluationSubmission(data) {
    return request({ url: "/evaluation-submissions", method: "post", data });
  },
  getEvaluationSubmission(submissionId) {
    return request({
      url: `/evaluation-submissions/${submissionId}`,
      method: "get",
    });
  },
  getEvaluationTargetComparison(submissionId) {
    return request({
      url: `/evaluation-submissions/${submissionId}/target-comparison`,
      method: "get",
    });
  },
  deleteEvaluationSubmission(submissionId) {
    return request({
      url: `/evaluation-submissions/${submissionId}`,
      method: "delete",
    });
  },
  getEvaluationSubmissionCaseRuns(submissionId) {
    return request({
      url: `/evaluation-submissions/${submissionId}/case-runs`,
      method: "get",
    });
  },
  cancelEvaluationSubmission(submissionId) {
    return request({
      url: `/evaluation-submissions/${submissionId}:cancel`,
      method: "post",
    });
  },
  retryEvaluationCaseRun(caseRunId) {
    return request({
      url: `/evaluation-case-runs/${caseRunId}:retry-failed`,
      method: "post",
    });
  },
  getEvaluationMethodRunArtifacts(methodRunId) {
    return request({
      url: `/evaluation-method-runs/${methodRunId}/artifacts`,
      method: "get",
    });
  },
  listEvaluationSchedules() {
    return request({ url: "/evaluation-schedules", method: "get" });
  },
  createEvaluationSchedule(data) {
    return request({ url: "/evaluation-schedules", method: "post", data });
  },
  updateEvaluationSchedule(scheduleId, data) {
    return request({
      url: `/evaluation-schedules/${scheduleId}`,
      method: "put",
      data,
    });
  },
  deleteEvaluationSchedule(scheduleId) {
    return request({
      url: `/evaluation-schedules/${scheduleId}`,
      method: "delete",
    });
  },
  setEvaluationScheduleEnabled(scheduleId, enabled) {
    return request({
      url: `/evaluation-schedules/${scheduleId}:${enabled ? "enable" : "disable"}`,
      method: "post",
    });
  },
  runEvaluationScheduleNow(scheduleId) {
    return request({
      url: `/evaluation-schedules/${scheduleId}:run-now`,
      method: "post",
    });
  },
  listEvaluationScheduleRuns(scheduleId) {
    return request({
      url: `/evaluation-schedules/${scheduleId}/runs`,
      method: "get",
    });
  },
  getEvaluationScheduleRun(runId) {
    return request({
      url: `/evaluation-schedule-runs/${runId}`,
      method: "get",
    });
  },
  listSkills() {
    return request({ url: "/skills", method: "get" });
  },
  listHostSkills() {
    return request({ url: "/host-skills", method: "get" });
  },
  adoptHostSkill(data) {
    return request({ url: "/host-skills:adopt", method: "post", data });
  },
  listSkillVersions(skillId) {
    return request({ url: `/skills/${skillId}/versions`, method: "get" });
  },
  listSkillBindings(skillId) {
    return request({ url: `/skills/${skillId}/bindings`, method: "get" });
  },
  listSkillBindingHistory(skillId, params = {}) {
    return request({
      url: `/skills/${skillId}/binding-history`,
      method: "get",
      params,
    });
  },
  listExecutionProfiles() {
    return request({ url: "/execution-profiles", method: "get" });
  },
  createExecutionProfile(data) {
    return request({ url: "/execution-profiles", method: "post", data });
  },
  validateExecutionProfile(profileId) {
    return request({
      url: `/execution-profiles/${profileId}:validate`,
      method: "post",
    });
  },
  freezeExecutionProfile(profileId) {
    return request({
      url: `/execution-profiles/${profileId}:freeze`,
      method: "post",
    });
  },
  listOptimizerPolicies() {
    return request({ url: "/skill-optimization/policies", method: "get" });
  },
  createOptimizerPolicy(data) {
    return request({
      url: "/skill-optimization/policies",
      method: "post",
      data,
    });
  },
  listVerifierBundles() {
    return request({ url: "/skill-optimization/verifiers", method: "get" });
  },
  createVerifierBundle(data) {
    return request({
      url: "/skill-optimization/verifiers",
      method: "post",
      data,
    });
  },
  listOptimizationSnapshots() {
    return request({
      url: "/skill-optimization/data-snapshots",
      method: "get",
    });
  },
  createOptimizationSnapshot(data) {
    return request({
      url: "/skill-optimization/data-snapshots",
      method: "post",
      data,
    });
  },
  listOptimizationExperiments() {
    return request({ url: "/skill-optimization/experiments", method: "get" });
  },
  createOptimizationExperiment(data) {
    return request({
      url: "/skill-optimization/experiments",
      method: "post",
      data,
    });
  },
  getOptimizationExperimentDetail(experimentId, params = {}) {
    return request({
      url: `/skill-optimization/experiments/${experimentId}/detail`,
      method: "get",
      params,
    });
  },
  getOptimizationLedger(experimentId) {
    return request({
      url: `/skill-optimization/experiments/${experimentId}/ledger`,
      method: "get",
    });
  },
  exportOptimizationLedger(experimentId, format) {
    return request({
      url: `/skill-optimization/experiments/${experimentId}/export`,
      method: "get",
      params: { format },
      responseType: "blob",
    });
  },
  runOptimizationPreflight(data = {}) {
    return request({
      url: "/skill-optimization/preflight",
      method: "post",
      data,
    });
  },
  getOptimizationEvents(experimentId) {
    return request({
      url: `/skill-optimization/experiments/${experimentId}/events`,
      method: "get",
    });
  },
  startOptimizationExperiment(experimentId) {
    return request({
      url: `/skill-optimization/experiments/${experimentId}:start`,
      method: "post",
    });
  },
  resumeOptimizationExperiment(experimentId) {
    return request({
      url: `/skill-optimization/experiments/${experimentId}:resume`,
      method: "post",
    });
  },
  cancelOptimizationExperiment(experimentId) {
    return request({
      url: `/skill-optimization/experiments/${experimentId}:cancel`,
      method: "post",
    });
  },
  getOptimizationCandidate(candidateId) {
    return request({
      url: `/skill-optimization/candidates/${candidateId}`,
      method: "get",
    });
  },
  exportSkillVersion(skillId, versionId) {
    return request({
      url: `/skills/${skillId}/versions/${versionId}/export`,
      method: "get",
      responseType: "blob",
    });
  },
  rollbackSkillVersion(skillId, targetId, data) {
    return request({
      url: `/skills/${skillId}/bindings/${targetId}/rollback`,
      method: "post",
      data,
    });
  },
  evaluateLocalCase(casePath, judge, files) {
    const data = new FormData();
    data.append("case_path", casePath);
    data.append("judge", judge);
    files.forEach((file) => data.append("reports", file));
    return request({
      url: "/evaluate-direct",
      method: "post",
      data,
      timeout: 600000,
    });
  },
  generateCaseDraft(data) {
    return request({ url: "/case-drafts-generate", method: "post", data });
  },
  getCaseDraft(draftId) {
    return request({
      url: `/case-drafts/${encodeURIComponent(draftId)}`,
      method: "get",
    });
  },
  submitCaseDraftAnswers(draftId, answers) {
    return request({
      url: `/case-drafts/${encodeURIComponent(draftId)}/answers`,
      method: "post",
      data: { answers },
    });
  },
  publishCaseDraft(draftId) {
    return request({
      url: `/case-drafts/${encodeURIComponent(draftId)}/publish`,
      method: "post",
    });
  },
};
