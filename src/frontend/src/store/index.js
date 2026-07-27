import Vue from "vue";
import Vuex from "vuex";

Vue.use(Vuex);

export default new Vuex.Store({
  modules: {
    analystbench: {
      namespaced: true,
      state: {
        activeView: "dashboard",
      },
      mutations: {
        setActiveView(state, view) {
          state.activeView = view;
        },
      },
    },
  },
});
