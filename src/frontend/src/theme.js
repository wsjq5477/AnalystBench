export const THEME_STORAGE_KEY = "analystbench-theme";

export const THEME_PALETTES = {
  dark: [
    "#e6b85f",
    "#5eaeff",
    "#b07dd8",
    "#a4a4a7",
    "#e6765f",
    "#5ed4a7",
    "#c8a45e",
    "#7eb5d6",
  ],
  light: [
    "#a35b00",
    "#0969da",
    "#8250b5",
    "#57606a",
    "#cf3b22",
    "#087f5b",
    "#8a6500",
    "#25739a",
  ],
};

export const CHART_THEMES = {
  light: {
    tooltipBg: "rgba(255, 255, 255, .98)",
    tooltipBorder: "rgba(9, 105, 218, .22)",
    tooltipText: "#1a1a2e",
    legend: "#344054",
    axis: "#667085",
    line: "rgba(26, 26, 46, .13)",
    split: "rgba(26, 26, 46, .09)",
  },
  dark: {
    tooltipBg: "rgba(10, 15, 26, .98)",
    tooltipBorder: "rgba(122, 177, 255, .25)",
    tooltipText: "#f8fafc",
    legend: "rgba(255, 255, 255, .76)",
    axis: "rgba(255, 255, 255, .56)",
    line: "rgba(122, 177, 255, .18)",
    split: "rgba(122, 177, 255, .11)",
  },
};

export function getInitialTheme() {
  try {
    const savedTheme = window.localStorage.getItem(THEME_STORAGE_KEY);
    if (savedTheme === "dark" || savedTheme === "light") return savedTheme;
  } catch {
    // Storage may be unavailable in hardened browser contexts.
  }
  return window.matchMedia &&
    window.matchMedia("(prefers-color-scheme: light)").matches
    ? "light"
    : "dark";
}

export function applyThemeToDocument(theme) {
  const nextTheme = theme === "light" ? "light" : "dark";
  const oppositeTheme = nextTheme === "dark" ? "light" : "dark";
  [document.documentElement, document.querySelector("#app")]
    .filter(Boolean)
    .forEach((element) => {
      element.classList.remove(`${oppositeTheme}-theme`);
      element.classList.add(`${nextTheme}-theme`);
    });
  document.documentElement.style.colorScheme = nextTheme;
  const themeMeta = document.querySelector('meta[name="theme-color"]');
  if (themeMeta) {
    themeMeta.setAttribute(
      "content",
      nextTheme === "dark" ? "#02050d" : "#f5f7fa",
    );
  }
}

export function persistTheme(theme) {
  try {
    window.localStorage.setItem(THEME_STORAGE_KEY, theme);
  } catch {
    // The active theme still applies for the current session.
  }
}
