module.exports = {
  outputDir: "dist/client",
  devServer: {
    host: "0.0.0.0",
    port: 5173,
    allowedHosts: "all",
    proxy: {
      "^/api": {
        target:
          process.env.VITE_API_TARGET ||
          process.env.VUE_APP_API_TARGET ||
          "http://127.0.0.1:8000",
        changeOrigin: true,
      },
    },
  },
  chainWebpack(config) {
    config.module
      .rule("vue")
      .use("vue-loader")
      .tap((options) => ({
        ...options,
        compiler: require("vue-template-babel-compiler"),
      }));
  },
};
