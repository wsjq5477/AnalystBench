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
