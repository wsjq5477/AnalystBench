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

- `case_key` 由用户在导入时显式命名（CLI `--case-key` 或 API `case_key` 字段），不再从文件名推断。
- 导入时后端把 `case_key` 写回 `case.case_key`，Case JSON 文件因此自包含该标识；AI 生成阶段不写 `case_key`。
- `test_set` 和 `category` 在 Case JSON 中是纯字符串标识，不再嵌套 `key`/`name`。
- 测试集和问题分类是正式结构字段，不使用 tags 替代。

## 持久化字段

### Dataset（测试集）

- `dataset_key`：稳定标识，与 Case JSON 中 `test_set` 字符串一致。
- `name`：用户可读名称，缺省时 fallback 为 `dataset_key`。
- `description`：说明。

### CaseCategory（问题分类）

- `dataset_id`：所属测试集。
- `category_key`：测试集内稳定标识，与 Case JSON 中 `category` 字符串一致。
- `name`：展示名称，缺省时 fallback 为 `category_key`。
- `description`：说明。

### Case

- `dataset_id`：所属测试集。
- `category_id`：所属问题分类。
- `case_key`：用户在导入时命名的稳定标识。
- `source_filename`：原始 Case 文件名，用于审计和自动匹配。

### CaseTrace

- `case_revision_id`：所属 Case Revision。
- `trace_key`、`filename`、`media_type`。
- `content_hash`：内容仓库引用。
- `metadata_json`：Trace 类型、来源等扩展信息。

## 导入行为

`case-import` 接收用户命名的 `case_key`、测试集和分类；缺失时由 CLI 询问。发布时：

1. 按 `dataset_key` 查找或创建测试集。
2. 按测试集内 `category_key` 查找或创建分类。
3. 以用户命名的 `case_key` 创建 Case。
4. 创建 Case Revision、Trace 和 Eval Spec。
5. 冻结包含该测试集全部最新 Case Revision 的新 Dataset Version。

前端未来上传 Case 时使用相同契约：提交 `case_key`、`source_filename`、测试集和分类，不依赖模型生成标识。
