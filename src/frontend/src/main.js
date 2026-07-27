import Vue from "vue";
import ElementUI from "element-ui";
import "element-ui/lib/theme-chalk/index.css";
import App from "./App.vue";
import router from "./router";
import store from "./store";
import "./styles.css";

Vue.use(ElementUI, { size: "small" });
Vue.config.productionTip = false;

new Vue({
  router,
  store,
  render: (createElement) => createElement(App),
}).$mount("#app");
