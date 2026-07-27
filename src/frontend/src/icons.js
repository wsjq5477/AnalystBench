import Vue from "vue";

function createIcon(name, elementClass) {
  return Vue.component(name, {
    functional: true,
    props: {
      size: {
        type: [Number, String],
        default: 16,
      },
    },
    render(createElement, context) {
      const data = context.data || {};
      const classes = ["ab-icon", elementClass, data.class, data.staticClass];
      return createElement("i", {
        ...data,
        class: classes,
        style: {
          ...(data.style || {}),
          fontSize: `${context.props.size}px`,
        },
        attrs: {
          ...(data.attrs || {}),
          "aria-hidden": "true",
        },
      });
    },
  });
}

export const IconAlertCircle = createIcon("IconAlertCircle", "el-icon-warning-outline");
export const IconChevronDown = createIcon("IconChevronDown", "el-icon-arrow-down");
export const IconChevronRight = createIcon("IconChevronRight", "el-icon-arrow-right");
export const IconCircleCheck = createIcon("IconCircleCheck", "el-icon-circle-check");
export const IconCloudUpload = createIcon("IconCloudUpload", "el-icon-upload2");
export const IconDatabase = createIcon("IconDatabase", "el-icon-coin");
export const IconFileExport = createIcon("IconFileExport", "el-icon-document-copy");
export const IconFolder = createIcon("IconFolder", "el-icon-folder");
export const IconInfoCircle = createIcon("IconInfoCircle", "el-icon-info");
export const IconLayoutDashboard = createIcon("IconLayoutDashboard", "el-icon-menu");
export const IconLoader2 = createIcon("IconLoader2", "el-icon-loading");
export const IconPlus = createIcon("IconPlus", "el-icon-plus");
export const IconRefresh = createIcon("IconRefresh", "el-icon-refresh");
export const IconSettings = createIcon("IconSettings", "el-icon-setting");
export const IconSparkles = createIcon("IconSparkles", "el-icon-magic-stick");
export const IconTerminal2 = createIcon("IconTerminal2", "el-icon-monitor");
export const IconTrash = createIcon("IconTrash", "el-icon-delete");
export const IconClipboardData = createIcon("IconClipboardData", "el-icon-tickets");
export const IconFlask = createIcon("IconFlask", "el-icon-data-analysis");

export const analystBenchIcons = {
  IconAlertCircle,
  IconChevronDown,
  IconChevronRight,
  IconCircleCheck,
  IconCloudUpload,
  IconDatabase,
  IconFileExport,
  IconFolder,
  IconInfoCircle,
  IconLayoutDashboard,
  IconLoader2,
  IconPlus,
  IconRefresh,
  IconSettings,
  IconSparkles,
  IconTerminal2,
  IconTrash,
  IconClipboardData,
  IconFlask,
};
