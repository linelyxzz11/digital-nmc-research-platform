# 项目文档

| 需要做什么 | 入口 |
|---|---|
| 看项目目标、分工和实施路线 | [团队路线](project_plan.zh-CN.md) |
| 查看课程提交内容 | [Proposal](proposal.md)；提交 PDF 位于本地 `submissions/proposal_group1.pdf` |
| 运行 Demo、追踪周期、查看测试记录 | [验证指南](validation.md) |
| 补背景与阅读论文 | 本地 `papers/README.md`、`papers/learning_guide.zh-CN.md`、`papers/literature_review.zh-CN.md`，不随仓库发布 |
| 生成 Demo 1/2 的任务与通信计划 | [Mapper v0.1](../src/compiler/README.md) |
| 查看图的来源与证据边界 | [架构图说明](figures/README.md) |

<a id="specs"></a>

## 正式规范

- [计算 v0.1](spec/compute_architecture_v0.1.md)：数值、存储布局、TileTask、ResultBeat 与 LocalDone；状态为 `FROZEN v0.1`。
- [通信 v0.1](spec/communication_architecture_v0.1.md)：Packet/Flit、2×2 Mesh、有限 FIFO、反压、端点归约与全局完成；状态为 `IMPLEMENTATION DRAFT v0.1`。
- [设计决定与歧义审计](spec/design_decisions_v0.1.md)：选择理由、关闭记录与未完成证据。

接口变更须同步检查计算与通信两侧，并更新规范版本及设计记录。

<a id="originals"></a>

## 原始材料

`team_inputs/` 保存成员 DOCX 原稿，用于追溯讨论，不具有规范效力；冲突以正式规范为准。`figures/` 保留三张图的 SVG 和 PNG，`submissions/` 保存提交 PDF。团队路线与英文 Proposal 面向不同用途，分别保留。

<a id="layout"></a>

## 目录依据与迁移记录

<details>
<summary>查看目录参考、迁移表与校验记录</summary>

### 参考仓库与检索边界

GitHub API 按 stars 降序检索 `near-memory`、`near-data` 和 `processing-in-memory` 的 name/description，以及 PIM simulator。排除产品信息管理、资料清单和同名无关项目。本次核对的相关研究模拟器如下：

| 仓库 | Stars（本次查询） | 适用范围 |
|---|---:|---|
| [SAITPublic/PIMSimulator](https://github.com/SAITPublic/PIMSimulator) | 256 | 三星 HBM-PIM 模拟器；本次相关候选中最高，作为目录参考 |
| [vineodd/PIMSim](https://github.com/vineodd/PIMSim) | 220 | 通用 PIM 与 gem5 仿真 |
| [CMU-SAFARI/ramulator-pim](https://github.com/CMU-SAFARI/ramulator-pim) | 203 | NMC/PIM 仿真；严格 near-memory name/description 搜索中最高的相关候选 |

不宣称搜索覆盖 GitHub 全部仓库。PIMSimulator 属于广义 PIM，不能称为与本项目架构相同的 NMC 系统。参考分支为 `dev`，查询时 commit 为 `3703d1f19c8f027360cc33a3243eb271e3bb6898`。

它的实际根目录包含 `src / ini / tools / data / dump / lib`，测试位于 `src/tests`。本项目采用这些名称，并额外保留 `docs`。不复制其代码、HBM 参数、FP16 数据通路、SCons 构建或许可证。

### 目录迁移表

| 原位置 | 新位置 |
|---|---|
| `software/simulator/` | `src/simulator/` |
| `software/compiler/` | `src/compiler/` |
| `hardware/rtl/` | `src/rtl/` |
| `hardware/fpga/` | `src/fpga/` |
| `validation/tests/` | `src/tests/` |
| `validation/workloads/` | `data/workloads/` |
| `validation/results/` | `data/results/` |
| `validation/experiments/` | `tools/experiments/` |
| `spec/` | `docs/spec/` |
| `papers/` | `docs/papers/` |
| `open_source_references/` | `lib/` |
| 早期 `cpu_baseline/` | `src/rtl/legacy_cpu/` |
| 早期 `mesh4.cfg` | `ini/early_feasibility/mesh4.cfg` |
| 早期 `measurement_summary.json` | `data/results/early_feasibility/measurement_summary.json` |

原功能包名 `simulator`、测试名称、二进制论文/文档和作者仓库内部结构保持一致。同步更新 pytest、README、规范路径、阅读指南、VS Code 工作区及忽略规则；保留原 `.venv`。规范正文只更新位置引用，没有改变 Packet 或计算语义。

### 目录迁移校验（本次文档合并前）

- 正式迁移后：27 项测试全部通过（0.06 s）。
- 50 个本地 Markdown 链接、两个 VS Code 工作区路径检查通过。
- 36 篇 PDF 和三个作者仓库完整保留；原代码与测试内容未改变。
- 原文件逐项哈希核对：3176 个保持一致，18 个文档或配置按计划更新，0 个缺失。
- 原 Git 索引、配置、HEAD 及虚拟环境配置保持一致；`git diff --check` 通过。

迁移校验记录保存在 `/private/tmp/nmc-layout-20261006/actual-evidence/`，修改前的文档备份保存在 `/private/tmp/nmc-layout-20261006/original-documents/`。

</details>
