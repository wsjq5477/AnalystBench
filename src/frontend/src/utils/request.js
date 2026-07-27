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
  (error) => {
    const response = error.response;
    const payload = response && response.data;
    const message =
      (payload && payload.error && payload.error.message) ||
      (payload && payload.detail) ||
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
