# SkillLearnBench 调研：AnalystBench 可借鉴之处

> 内部调研报告 · 2026-08-10
> 对比对象：SkillLearnBench（COLM 2026，CMU）↔ AnalystBench（自研，MVP）
> 调研材料：本地 `/home/jiqi/LLM/SkillLearnBench` 与 `AnalystBench` 全量源码与文档

---

## 0. 一句话结论

SkillLearnBench 与 AnalystBench 在「Skill」一词上指向不同抽象，但二者在**「Skill 质量评测」**和**「Skill 生成/迭代方法学」**两个维度上高度互补：SkillLearnBench 提供了一套**轻量、可移植、面向真实任务**的 Skill 评测与迭代范式（key-point coverage、teacher-feedback 多轮、Docker 沙箱统一 runner），AnalystBench 的 Skill 自优化纵切已经在**统计门禁与版本治理**上更深入。两者的交集是 AnalystBench 最值得借鉴的地方。

---

## 1. 两者到底在评测什么

先对齐抽象层级，避免拿不同维度的东西硬比。

| 维度 | SkillLearnBench | AnalystBench |
|---|---|---|
| 被测对象 | **Skill 生成方法**（one-shot / self-feedback / teacher-feedback / skill-creator）× 多模型 | **分析报告**本身，以及产出报告的 **Harness × Model × Skill 版本**组合 |
| Skill 的角色 | 被评测的「产物」——方法生成的 SKILL.md 文档质量直接影响下游任务通过率 | 被测的一个维度——Skill 是喂给 claude/OpenCode 的操作说明包，影响报告质量 |
| 「标准答案」形态 | 任务有确定 verifier（pytest + reward.txt 二值） | 自然语言标准答案 → 编译为 Eval Spec（Claim Graph + 因果边） |
| 评分是确定性的吗 | 任务成功=是（二值 reward）；Skill/Trajectory 质量=否（GPT-5-mini judge + key-point 对齐） | 是（LLM 只判 match/partial/missing/contradiction，计分由确定性引擎） |
| 任务领域 | 20 个真实任务，跨 6 类（软件工程、信息检索、生产力、数据分析、内容、工具） | 首个官方套件 KDiag（内核/OS 分析），Core 领域无关 |

**关键差异**：SkillLearnBench 的 Skill 是「给 agent 看的领域知识文档」，AnalystBench 的 Skill 是「给 agent 看的 AnalystBench 操作包」（调 CLI、准备输入）。二者都符合 claude `SKILL.md` 格式，但**语义层不同**——一个承载领域 know-how，一个承载工具流程。这决定了借鉴时哪些可平移、哪些需转译。

---

## 2. SkillLearnBench 的核心做法

### 2.1 三层评测维度（最值得借鉴的整体框架）

README 把评测明确拆成三个正交维度，每个维度有独立指标和独立实现：

| 维度 | 指标 | 度量什么 | 实现位置 |
|---|---|---|---|
| **Task Success** | Pass rate | 每个 trial 的二值 verifier 结果 | `core/eval_runner.py` + 任务 `tests/test.sh` |
| **Skill Quality** | Functional coverage / executability / safety | Skill 文档是否覆盖了 oracle 关键点、是否可执行、是否含不安全指令 | `evaluation/skill/metrics/compute_*.py` |
| **Trajectory Quality** | Key-point recall / execution order / completeness | agent 执行轨迹是否匹配预期解决路径 | `evaluation/trajectory/metrics/metric_*.py` |

**对 AnalystBench 的启示**：AnalystBench 当前 Phase 1 聚焦「最终结果 Benchmark」，Phase 3 才规划 Trace 评测。SkillLearnBench 证明**三层可以并行设计、独立落分**，且 Skill 质量维度（coverage/executability/safety）**不依赖 trace 基础设施**就能先做。AnalystBench 的 Skill 自优化纵切目前用「报告分数 Delta」作为唯一 Skill 质量代理，**没有对 Skill 文档本身的质量度量**——这是可借鉴的低成本增量。

### 2.2 Key-point Coverage：把「Skill 好不好」变成可对齐的检查项

这是 SkillLearnBench 最精巧的设计，也是和 AnalystBench 的 Claim-Graph 评分**理念同源**的地方。

**流程**（`evaluation/skill/metrics/compute_coverage.py`）：

1. 从 oracle（人工 skill + 成功轨迹）**离线生成** `skill-key-points.generated.json`——一组该任务 Skill「应该提到」的关键知识点，每条带 `reason` 和 `skill_reference`（原文出处）；
2. 对待评 Skill，把整个 Skill 包（SKILL.md + scripts/ + references/ + assets/）拼成一个文本 blob；
3. 逐条 key-point 让 LLM judge 判 `mentioned / missing / contradiction`；
4. `coverage = mentioned / total`。

**与 AnalystBench 的同构**：这几乎就是 AnalystBench 的 Gold-Claim → Candidate-Claim 对齐，只是：
- SkillLearnBench 的「claim」是 Skill 文档的关键知识点；
- AnalystBench 的「claim」是报告的结论/证据/因果。

**可直接平移**：AnalystBench 的 Skill 自优化目前在 Screening/Gate 阶段只看「报告分数 Delta」。可以新增一个 **Skill Coverage 维度**：对每个被优化的 Skill，离线生成「该 Skill 应覆盖的关键操作/约束点」（从 Eval Spec 和成功轨迹抽取），候选 Skill 同样做 mentioned/missing/contradiction 对齐，作为 Gate 的一个**硬约束或软维度**。这能抓住一类目前漏掉的问题——「候选 Skill 改得让报告分数涨了，但把一条必须的安全约束删了」。

### 2.3 四个 Skill 生成方法（连续学习方法学）

`baselines/` 下四个方法，是 AnalystBench「自优化」可对标的方法学光谱：

| 方法 | 信号来源 | 轮次 | AnalystBench 对应 |
|---|---|---|---|
| **b1 one-shot** | 无 | 单轮 | AnalystBench 的「Optimizer 生成候选 Patch」单次 |
| **b2 self-feedback** | agent 自评轨迹 | K=2 | AnalystBench 的 Evidence Builder + Mutation Generator（失败信号→patch） |
| **b3 teacher-feedback** | 外部 teacher LLM 给方向性建议（不露 ground-truth） | K=3，失败触发 QA | AnalystBench **目前没有**——只有 report 分数反馈，没有「领域专家方向性指导」层 |
| **b4 skill-creator** | 无，但遵守 claude 官方 skill-creator 结构化流程 | 单轮 | AnalystBench 的 OptimizerPolicyVersion + 结构化 Patch |

**b3 teacher-feedback 最值得借鉴**。AnalystBench 的 Evidence Builder 现在只从**评分结果**抽信号（wrong_claim / missing_evidence 等失败 tag）。b3 引入了一个**独立的 teacher 角色**，它**看得到 ground-truth skill**，但只给「修改建议」不给答案——`_B3_TEACHER_SYSTEM` prompt 明确写「Give modification suggestions only. Do NOT provide the full solution」。

**对 AnalystBench 的转译**：AnalystBench 的 ground-truth 是 Eval Spec（Gold Claim Graph）。可以引入一个 **EvalSpec-Aware Teacher** 角色：它读得到冻结的 Eval Spec，但在给 Mutation Generator 的反馈里只说「候选 Skill 缺少对 X 类证据的引导」「没有覆盖根因判别步骤」，**不直接写 Skill 内容**。这比纯分数反馈信息量大得多，且不会泄漏标准答案到被测 agent。这是个真正的**方法学增量**，不只是工程重构。

### 2.4 统一的 Docker + verifier 范式

每个任务实例的结构是固定的：

```
tasks/<task>/<task>-N/
├── instruction.md        # 任务描述
├── environment/
│   ├── Dockerfile        # 沙箱环境
│   └── skills/           # oracle skill（构建时排除，防泄漏）
├── tests/
│   ├── test.sh           # verifier 入口，写 reward.txt
│   └── test_outputs.py   # pytest 断言
└── task.toml             # 元数据：难度、类别、资源、required_env
```

`skill_runner.py` / `eval_runner.py` 用一套流程吃所有任务：build image → run container → install agent → exec agent → exec verifier → 读 reward.txt。

**关键工程细节值得抄**：
- **Oracle skill 防泄漏**：构建上下文用 `ignore_patterns("skills")` 排除 oracle skill，`_prepare_build_env` 注释明确写「prevent oracle skill leakage into no_skill or generated containers」。AnalystBench 的 Skill 自优化也有「prospective holdout 不进入优化器输入」的原则，但 SkillLearnBench 在**构建层**就物理隔离了，更稳。
- **预构建镜像 + 运行时注入**：`_inject_skills_runtime` 在已构建的 base image 上用 `docker cp` 注入候选 skill，避免每次 trial 重建镜像。AnalystBench 的隔离 workspace 也是运行时安装 skill，思路一致，但 SkillLearnBench 的 `_parse_skill_copies` 从 Dockerfile 的 `COPY skills ...` 指令自动推导注入路径，**比硬编码路径更鲁棒**。
- **token 用量抽取**：`_extract_token_usage` 从 agent tee 日志的最后一行 JSON 抽 token，按 agent 类型（codex `turn.completed` / claude CLI `result`）区分。AnalystBench 的 P19 设计把 token/耗时列为非目标，但这套抽取逻辑很轻，可作为 Phase 2 成本核算的参考。

### 2.5 方法插件契约（method.py）

`CONTRIBUTING.md` 定义了两种加方法的方式：
- **Option A（prompt-only）**：放个 `method.md`，内容拼到 instruction 后；
- **Option B（custom orchestration）**：放个 `method.py`，实现固定签名 `run(*, container_name, task_path, trial_path, agent, model_name, instruction, task_workdir, max_rounds, max_steps) -> (passed, steps_used, stdout, stderr, rounds_used)`。

b3 teacher-feedback 就是 Option B 的实现（`_run_teacher_student_loop`）。**这个「prompt 注入 vs 插件编排」二分**对 AnalystBench 的 `OptimizerPolicyVersion` 设计有启发：AnalystBench 现在的 optimizer 是「prompt bundle + 结构化 patch」，等价于 Option A 的增强版。当未来要接入更复杂的迭代方法（如 teacher-student、RL curator）时，需要一个 Option B 式的**插件契约**，而不是把多轮逻辑硬塞进一个 prompt。

### 2.6 skills_only 模式：解耦「生成 Skill」与「用 Skill 解题」

`skill_runner.py` 的 `--skills-only` 让 agent 只跑「生成 SKILL.md」阶段，写完文件就中断（`_poll_skills_done` 轮询 + 稳定窗口），跳过 verifier。这样**生成 Skill 和评估 Skill 是两次独立运行**，可以单独评测「方法生成的 Skill 好不好」而不混入「agent 解题能力强不强」。

**对 AnalystBench 的启示**：AnalystBench 的 Skill 自优化现在是「改 Skill → 跑 agent 出报告 → 评报告」一体化。如果未来想把「Optimizer 生成的 Skill 本身的质量」和「Skill 喂给 agent 后的报告质量」分开度量（比如做 ablation：是 Skill 改得好，还是这次 agent 发挥好），这种**两阶段解耦**是现成范式。

---

## 3. AnalystBench 已经更强的地方

借鉴不是单向的。这些地方 AnalystBench 已超出 SkillLearnBench，**不要为了「对齐」而退化**：

| 方面 | AnalystBench | SkillLearnBench | 建议 |
|---|---|---|---|
| **统计门禁** | Bootstrap CI + 胜率 + 灰区增采样 + 硬约束分层判定 + Case 级中位数 | 无统计框架，单次 pass rate | 保持，这是 AnalystBench 的核心壁垒 |
| **版本治理** | 内部 Git + 不可变 package_hash + 乐观锁 Active + provisional/validated 分级 | 文件目录 `skills/<method>-<model>/`，无版本概念 | 保持，SkillLearnBench 是「快照集合」不是「版本谱系」 |
| **数据切分诚实度** | 明确 4-Case 用 `development_regression`，不宣称统计显著；prospective holdout 不进优化器 | 20 任务 100 实例，但无 train/val/test 切分意识 | 保持，AnalystBench 的小样本方法论更严谨 |
| **评分可解释性** | Claim Graph + 因果边 + match/partial/missing/contradiction + 确定性计分 | Skill 质量也用 LLM judge，但粒度是 key-point 二三分类 | 评分哲学更深，但 key-point 的「原文绑定」做法可吸收 |
| **运行隔离** | 独立 HOME + workspace + 只复制登记 Skill 包 + 不覆盖全局 | Docker 容器 + 构建时排除 oracle | 两者隔离强度相当，AnalystBench 不用 Docker 更轻 |

---

## 4. 可行性矩阵：哪些值得做、按什么顺序

按「价值 × 实现成本」排序，给出优先级判断。成本是相对 AnalystBench 现有架构的增量。

| # | 借鉴项 | 价值 | 成本 | 优先级 | 落点 |
|---|---|---|---|---|---|
| 1 | **Skill Coverage 维度**（key-point 对齐，作为 Gate 软约束） | 高 | 中 | **P1** | `skill_optimization/evidence.py` 旁新增 `skill_coverage.py`；离线生成 key-points 复用 Eval Spec 生成链路 |
| 2 | **EvalSpec-Aware Teacher 角色**（b3 转译） | 高 | 中高 | **P1** | 新增 optimizer prompt `evalspec_teacher.md`；接入 `experiment.py` 的 reflect 阶段，产出「方向性建议」喂给 Mutation Generator |
| 3 | **三层评测维度并立**（Task/Skill/Trajectory 显式分离） | 中高 | 低 | **P2** | 主要是文档与指标命名层；先把 Skill 质量维度从「报告分数代理」里独立出来报 |
| 4 | **method.py 插件契约**（为复杂迭代方法留扩展点） | 中 | 中 | **P2** | `MutationStrategy` Protocol 已在附录 32 预留，补一个 `run()` 签名契约 + 一个 plugin loader |
| 5 | **构建层 oracle 防泄漏**（物理隔离，非仅逻辑隔离） | 中 | 低 | **P2** | Skill 自优化 workspace preparer 里，对 Eval Spec / Gold Claim 物理不复制进 candidate sandbox |
| 6 | **skills_only 两阶段解耦**（单独评测 Skill 本身） | 中 | 中 | **P3** | 需要先有 Skill Coverage 指标支撑，否则单独跑 Skill 无度量 |
| 7 | **token/耗时抽取**（Phase 2 成本核算） | 低 | 低 | **P3** | P19 已列为非目标，Phase 2 再说，逻辑可抄 `_extract_token_usage` |
| 8 | **Dockerfile COPY 路径自动推导** | 低 | 低 | P3 | AnalystBench 不用 Docker，仅作设计参考 |

**两条 P1 的理由**：

- **#1 Skill Coverage**：填补了 AnalystBench 自优化当前最大的盲区——Gate 只看「报告分数变没变」，看不到「Skill 文档本身有没有退化掉关键约束」。SkillLearnBench 已验证 key-point 对齐可独立于任务结果工作。实现上可复用 AnalystBench 已有的 Claim 对齐引擎，把「Gold Claim」换成「Skill 应覆盖的关键点」即可，工程量中等。

- **#2 EvalSpec-Aware Teacher**：这是**方法学**而非工程上的增量。AnalystBench 现在的优化信号只有「报告哪里错了」（失败 tag），没有「Skill 应该怎么改」的方向性指导。b3 证明了 teacher 看 ground-truth、只给建议、不泄漏答案的闭环是可行且有效的。转译到 AnalystBench，teacher 看的是冻结 Eval Spec（不是被测 agent 能看到的），产出「候选 Skill 缺什么」的结构化建议，喂给现有的 Mutation Generator。这能显著提升候选质量，直接提升 Gate 通过率和收敛速度。

---

## 5. 借鉴时的转译风险

几处不能直接照搬、需要按 AnalystBench 语义转译的：

1. **「Skill」语义不同**：SkillLearnBench 的 Skill 是领域知识文档，key-point 是「该知道的领域知识」。AnalystBench 的 Skill 是操作包，key-point 要重新定义为「该执行的操作步骤 / 该遵守的约束 / 该调用的接口」，不能直接套「领域知识点」。
2. **verifier 形态不同**：SkillLearnBench 任务有确定 pytest verifier（二值 reward）。AnalystBench 是开放式报告，没有二值 reward，所以「Task Success」维度不能平移——AnalystBench 的等价物是「报告分数 + 通过阈值」，已经是 SkillLearnBench 没有的更细粒度度量。借鉴三层**框架**，不借鉴 Task Success 的**实现**。
3. **teacher 的 ground-truth 不同**：b3 teacher 看的是 oracle SKILL.md。AnalystBench 的 teacher 应看 Eval Spec（Gold Claim Graph），二者抽象层级不同，prompt 要重写，不能复用 `_B3_TEACHER_USER` 模板。
4. **多模型矩阵**：SkillLearnBench 跑 6 个模型 × 4 方法，是为了做「方法×模型」研究。AnalystBench V1 明确单 Skill/单 Harness/单 Model，不需要这个矩阵，不要为了「像 benchmark」而扩大被测面。

---

## 6. 不建议借鉴的

- **Docker 全沙箱**：AnalystBench 的 workspace 隔离已够，引入 Docker 会拖慢 trial、增加部署依赖，得不偿失。
- **dataclaw / CTRF 日志格式**：SkillLearnBench 依赖 `dataclaw` CLI 和 `ctrf.json`，是它自己生态的产物，AnalystBench 的 `run.json/result.json/artifact_json` 已是稳定事实源，不要换。
- **GPT-5-mini 作为唯一 judge**：SkillLearnBench 的 Skill/Trajectory 质量维度全用 GPT-5-mini judge。AnalystBench 的哲学是「LLM 只判关系，确定性代码计分」，不要为了对齐而退化成「LLM 直接给分」。

---

## 7. 建议的下一步

1. 把本报告的 #1、#2 两条 P1 拆成两个独立的设计任务，分别出 spec：
   - `skill-coverage-metric-design.md`（Skill 质量维度设计）
   - `evalspec-aware-teacher-design.md`（b3 转译设计）
2. 在出 spec 前，**再读一遍** SkillLearnBench 的 `compute_coverage.py` 全文和 `skill_runner.py` 的 `_run_teacher_student_loop`，这两个函数是借鉴的最小可行参考实现。
3. 两个 spec 都要遵守 AnalystBench 自优化设计文档（Codex v1.1）第 0 节的 Codex 执行约束——复用现有 ORM/队列/Runner，不造并行 Evaluation Runner。

---

*本报告基于 2026-08-10 本地仓库快照。SkillLearnBench paper 已被 COLM 2026 接收（arXiv:2604.20087）。*
