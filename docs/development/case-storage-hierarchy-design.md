# P12 Case 分层存储设计

状态：Implemented（依据用户给出的测试集/问题分类要求，2026-07-22）

## 目标

Case 不再作为孤立对象、也不再每个 Case 创建一个独立 Dataset。正式层级为：

```text
Dataset（测试集）
└── CaseCategory（问题分类）
    └── Case（一个可评分样本）
        ├── CaseRevision（标准答案和评分版本）
        └── CaseTrace（日志、snapshot、堆栈等材料）
```

例如测试集“Kernel 日志分析”包含 `panic`、`lowdog`、`highdog` 分类，每个分类包含多个 Case。

## 命名规则

- `case_key` 必须等于 Case JSON 文件名去掉 `.json` 后的完整名称，保留大小写、下划线和连字符。
- `HM_PANIC_SYSMGR-case1.json` 的 `case_key` 固定为 `HM_PANIC_SYSMGR-case1`。
- JSON 内模型生成的 `case.case_key` 只作为草稿内容；通过文件上传或 CLI 导入时由文件名覆盖。
- 测试集和问题分类是正式结构字段，不使用 tags 替代。

## 持久化字段

### Dataset（测试集）

- `dataset_key`：稳定标识。
- `name`：用户可读名称。
- `description`：说明。

### CaseCategory（问题分类）

- `dataset_id`：所属测试集。
- `category_key`：测试集内稳定标识，如 `panic`。
- `name`：展示名称。
- `description`：说明。

### Case

- `dataset_id`：所属测试集。
- `category_id`：所属问题分类。
- `case_key`：Case JSON 文件名 stem。
- `source_filename`：原始 Case 文件名，用于审计和自动匹配。

### CaseTrace

- `case_revision_id`：所属 Case Revision。
- `trace_key`、`filename`、`media_type`。
- `content_hash`：内容仓库引用。
- `metadata_json`：Trace 类型、来源等扩展信息。

## 导入行为

`case-import` 接收测试集和分类；缺失时由 CLI 询问。发布时：

1. 按 `dataset_key` 查找或创建测试集。
2. 按测试集内 `category_key` 查找或创建分类。
3. 以源文件名 stem 创建 Case。
4. 创建 Case Revision、Trace 和 Eval Spec。
5. 冻结包含该测试集全部最新 Case Revision 的新 Dataset Version。

前端未来上传 Case 时使用相同契约：提交 `source_filename`、测试集和分类，不依赖模型生成标识。
