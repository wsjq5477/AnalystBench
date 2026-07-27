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
