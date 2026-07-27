module.exports = {
  outputDir: "dist/client",
  devServer: {
    host: "0.0.0.0",
    allowedHosts: "all",
    proxy: {
      "^/api": {
        target: "http://127.0.0.1:8000",
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
