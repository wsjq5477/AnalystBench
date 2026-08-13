import axios from "axios";

export class ApiError extends Error {
  constructor(message, status, code) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
  }
}

const service = axios.create({
  baseURL: process.env.VUE_APP_ANALYSTBENCH_API_BASE || "/api/v1",
  timeout: 30000,
  headers: { Accept: "application/json" },
});

service.interceptors.response.use(
  (response) => response.data,
  async (error) => {
    const response = error.response;
    let payload = response && response.data;
    const contentType = String(response?.headers?.["content-type"] || "");
    if (
      typeof Blob !== "undefined" &&
      payload instanceof Blob &&
      contentType.includes("json")
    ) {
      try {
        payload = JSON.parse(await payload.text());
      } catch {
        // Keep the original Blob and fall back to Axios' transport error.
      }
    }
    const validationMessage =
      payload && Array.isArray(payload.detail)
        ? payload.detail
            .map((item) => {
              const field = Array.isArray(item.loc)
                ? item.loc.filter((part) => part !== "body").join(".")
                : "";
              return `${field ? `${field}：` : ""}${item.msg || "参数无效"}`;
            })
            .join("；")
        : payload && payload.detail;
    const message =
      (payload && payload.error && payload.error.message) ||
      validationMessage ||
      error.message ||
      "请求失败";
    return Promise.reject(
      new ApiError(
        String(message),
        response ? response.status : 0,
        payload && payload.error ? payload.error.code : undefined,
      ),
    );
  },
);

export default service;
