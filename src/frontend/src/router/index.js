import Vue from "vue";
import Router from "vue-router";

Vue.use(Router);

export const viewRoutes = [
  { path: "/", meta: { view: "dashboard", title: "总览" } },
  { path: "/dashboard", meta: { view: "dashboard", title: "总览" } },
  { path: "/datasets", meta: { view: "dataset", title: "测试集" } },
  { path: "/results", meta: { view: "results", title: "评测结果" } },
  {
    path: "/skill-optimization",
    meta: { view: "optimization", title: "Skill 自优化" },
  },
  { path: "/settings", meta: { view: "settings", title: "设置" } },
  { path: "*", redirect: "/" },
];

export default new Router({
  mode: "history",
  routes: viewRoutes,
});
