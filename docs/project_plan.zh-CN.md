# 团队版项目路线与研究设计

## 1. 项目定位与描述

项目暂定名：**面向小批量神经网络推理的可配置数字 NMC 系统**。

我们设计的是数字近存计算（NMC）系统：每个 Tile 包含本地 Bank、Buffer、INT8 Vector MAC、INT32 累加器、控制器和网络接口，多个 Tile 通过 2D Mesh NoC 连接。Mapper 把 GEMV 切分并放到各个 Tile，NoC 负责输入广播、部分和传输及归约。

项目以 GEMV 为核心算子，以 VADD、REDUCE 验证基本指令和通信，以两层 MLP 与 Llama 类 decode GEMV 形状作为应用场景。最终应形成一条可以运行和解释的链路：

```text
工作负载 → Mapper → ISA/消息 Trace → 功能模型/周期模型/RTL → 结果与性能统计
```

课程项目的基本目标是功能正确、模块完整、能够比较普通执行与 NMC 执行；进一步目标是在有限 Bank、MAC、NoC 和 FIFO 资源下研究映射与归约方案。FPGA 仅作条件允许时的附加验证。

## 2. 总体架构与架构图

项目用三张图分别固定硬件、系统和软件边界，避免把所有内容压进一张方框流程图。

### 图 1：NMC Tile 微架构（主图）

![图1：NMC Tile 微架构与逐周期数据通路](https://raw.githubusercontent.com/linelyxzz11/digital-nmc-research-platform/main/docs/figures/fig1_nmc_tile_microarchitecture.png)

单个 Tile 包含 Command FIFO、控制器、本地 Bank/Scratchpad、输入与权重 Buffer、4 路 INT8 Vector MAC、INT32 累加器、端点 Reduction Buffer、NI 和 Router local port。蓝色路径表示命令与控制，橙色表示操作数，绿色表示部分和与结果；所有逐周期传输都遵循 valid/ready。

### 图 2：多 Tile NMC 与 NoC

![图2：多 Tile NMC、有限 FIFO、反压和两类 GEMV 切分](https://raw.githubusercontent.com/linelyxzz11/digital-nmc-research-platform/main/docs/figures/fig2_multitile_mesh_organization.png)

Host 通过命令队列和 DMA 描述符向 2D Mesh 注入任务。实线橙色路径展示输入广播，绿色虚线展示层次归约，紫色点线展示 FIFO 满后向上游传播的反压。下方直接对比输出行切分与 K 维切分，说明两者分别产生广播和跨 Tile 部分和归约。

### 图 3：映射、执行与校验链

![图3：从 Operator IR 到 ISA、周期模型和 RTL 的统一执行链](https://raw.githubusercontent.com/linelyxzz11/digital-nmc-research-platform/main/docs/figures/fig3_mapping_and_validation_flow.png)

Mapper 对同一个 GEMV 枚举行、K 维和二维切分，再联合选择 Tile 放置、归约根、拓扑与顺序。选定方案生成唯一一份 ISA/Packet Trace，同时驱动 NumPy Golden、事件模型和 RTL Testbench，以数值、完成事件和资源计数器做一致性检查。

每张图都提供可编辑 SVG。可执行 v0.1 固定为 4 路 MAC、单 VC、128-bit Flit、每 Router 输入 4-Flit FIFO 和 2×2 Mesh。图中的 3×3 Mesh 只用于展示扩展路径；旧图若仍标注 2 VC，以 `docs/spec/communication_architecture_v0.1.md` 为准。它们是规划参数，不代表已经实现或测得的结果。FPGA 不在三张主图中，也不作为当前贡献。

## 3. 核心研究问题、候选创新点、Baseline 与 Benchmark

### 核心问题

**在有限 Bank 服务能力、MAC 吞吐、NoC 带宽和 FIFO 深度下，怎样联合选择 GEMV 分块、Tile 放置与部分和归约方式，使任务完成时间更短，同时减少热点和反压？**

### 五篇重点论文留下的可拓展方向

|参考工作|我们借鉴的内容|本项目可继续做的部分|
|---|---|---|
|PIMSIM-NN|应用、编译、ISA、模拟器链路|改成数字 NMC，并让统一 Trace 可以驱动 RTL|
|UniNDP|资源抽象、指令和映射搜索|补充直接 Mesh NoC、有限 FIFO、反压和链路竞争|
|Scalability Limitations|区分 kernel 与端到端时间，关注扩展瓶颈|在可修改模型中复现“Tile 增多但性能不再增长”的区域|
|Allspark|分区、调度、放置与 NoC 联合考虑|针对 GEMV 建立更小、更透明、能与 RTL 对照的选择方法|
|PID-Comm|集中、树形、分组等集合通信思路|在直接 Mesh 上比较端点归约，不照搬其 Host/DIMM 实现|

首选拓展方向有三个：

1. **通信感知映射：** Mapper 不只平分 MAC 工作量，还考虑 Bank 等待、链路热点、FIFO 反压和归约依赖。
2. **映射与归约联合选择：** 对同一个 GEMV 自动比较行切分、K 维切分、二维切分，以及集中、树形和分组归约。
3. **可综合端点归约：** 在 NI 或 Tile 端点实现匹配 Buffer、INT32 累加、完成计数和反压；首版 Router 只负责转发。

这些是需要通过实验验证的方向，不预设一定有效。运行时拥塞反馈、Router 内归约和 FPGA 部署放到后续扩展。

### Baseline

|Baseline|作用|
|---|---|
|CPU/NumPy Golden|检查数值正确性和课程要求中的普通执行|
|单 Tile 顺序执行|确认多 Tile 收益不是由错误计时造成|
|行切分|输入广播基线|
|K 维切分 + 集中归约|部分和通信基线|
|均匀二维切分|常规负载均衡基线|
|仅计算均衡|观察忽略通信后的错误选择|
|仅平均 hop 最小|观察忽略热点和反压后的错误选择|
|理想无竞争 NoC|性能上界和瓶颈定位|

所有方法必须使用相同的总 MAC 数、Bank 容量、Buffer 预算、链路带宽、数据精度和输入数据。

### Benchmark 与指标

- **正确性小例子：** VADD、REDUCE、2×2 与 4×4 GEMV，可手算并逐周期看波形。
- **性能算子：** 方形及长宽不对称 GEMV，覆盖计算密集、Bank 受限和通信受限情况。
- **应用级：** 两层 MLP；Llama2/Llama 类 decode 阶段的代表性 GEMV 形状 Trace，不要求运行完整大模型。
- **规模：** batch=1、4、16；Tile=1、4、16；分别测试首次加载权重和权重常驻。
- **指标：** 总周期、kernel 周期、packet 数、flit-hop、最大链路利用率、FIFO 满周期、Bank busy、MAC 利用率、归约等待和 Mapper 用时。

## 4. GitHub 框架

GitHub 作为代码、接口、任务和结果的统一来源；飞书用于即时沟通、周会和汇报协作，重要决定回写 GitHub Issue 或 `docs/spec/design_decisions_v0.1.md`。

```text
project/
├── README.md / requirements.txt / pyproject.toml
├── src/                 # simulator、compiler、rtl、fpga、tests
├── ini/                 # 架构和实验配置
├── tools/               # 批量运行、检查和绘图工具
├── data/                # workloads 和精选 results
├── dump/                # 可重建的原始 trace、日志和波形
├── lib/                 # 作者仓库，本地保留
└── docs/                # spec、计划、图、论文与验证记录
```

每个 Issue 写清负责人、输入、输出、依赖和验收条件；每个 Pull Request 附最小测试结果。主分支只合并能够运行的版本，大型波形、虚拟环境和完整原始数据不提交。

## 5. 实施路线与流程

|阶段|要做的事|阶段结果|
|---|---|---|
|0. 统一理解|手算 4×4 GEMV；确定行/K/二维切分；冻结 Tile、ISA、Packet 和数值语义 v0.1|一张架构图、一张 ISA 表、一个手算 Trace|
|1. 功能闭环|完成 NumPy Golden、功能解释器、VADD/REDUCE/GEMV|同一输入得到一致结果，非法命令能报错|
|2. Mapper|生成行、K、二维切分及集中/树形归约计划|映射变化能自动改变指令流和通信矩阵|
|3. 周期模型|加入 Bank、MAC、链路、FIFO、仲裁和反压事件|人工制造冲突时周期与队列变化合理|
|4. RTL 基线|依次完成 Vector MAC、Tile、NI、Router，再连成 2×2 Mesh|共同小例子与 Golden 一致，背压时不丢包|
|5. 对照实验|运行 Baseline、参数扫描、瓶颈案例和失败案例|得到 CSV、曲线、波形和可解释结论|
|6. 项目交付|接入两层 MLP 和 decode 形状，整理 README、演示和报告|新环境能按 README 复现最小结果|

只有阶段 0–4 稳定后才做候选优化。若基线没有出现稳定的 Bank/NoC 瓶颈，先修改资源配置和 workload，不直接宣称方法有效。FPGA 可以在主线完成后单独开分支尝试。

## 6. 五人任务分配与定位

|成员|定位与任务|必须交付|
|---|---|---|
|A：主负责人|总体架构；NoC/RTL；Tile 与端点归约；共同验证规则；主要实验；最终集成；可选 FPGA|架构与接口决定、RTL、TB、波形、网络统计、主结果、演示|
|B：ISA 与 Host|地址空间、命令编码、同步/完成/错误语义、Host 命令队列|ISA 表、编解码测试、命令与完成接口|
|C：Mapper 与应用|Operator IR、行/K/二维切分、Tile 放置、归约计划、GEMV/MLP/decode输入|Mapper、Trace、通信矩阵、workload|
|D：模型与实验|NumPy Golden、功能解释器、周期事件模型、代价估计和结果统计|数值测试、事件模型、参数扫描、CSV和绘图脚本|
|E：低负担支持|按固定清单运行小型回归、整理日志和截图、维护周进度、协助汇报排版|回归勾选表、结果索引、周会记录、汇报材料|

A 负责最终验证判断和主要汇报内容，E 只执行已经定义好的检查与整理工作。每个人都必须能解释自己的模块输入、输出和一条完整执行路径。

## 7. 虚拟环境、软件需求与初始结果假设

### 推荐环境

两台电脑使用同一 GitHub 仓库和锁定依赖：

- **联想拯救者：** i9-14900HX、32 GB DDR5-5600、1 TB SSD；建议 Windows 11 + WSL2 Ubuntu 22.04/24.04，承担批量仿真、Verilator 编译和较大参数扫描。
- **macOS 电脑：** 承担 Python、文档、小规模仿真、代码审查和结果整理。

基础软件：

|用途|建议软件|
|---|---|
|协作|Git、GitHub、VS Code|
|Python|Python 3.11、venv、NumPy、PyYAML、pytest、pandas、matplotlib|
|C/C++|CMake、Ninja、GCC/Clang|
|RTL|Icarus Verilog、Verilator、GTKWave、Yosys|
|NoC|自研事件模型；BookSim 作为趋势对照|
|可复现|requirements.txt 或 environment.yml；可选 Docker|
|FPGA 可选|确定开发板后再安装对应版本 Vivado，不提前作为全员依赖|

建议所有 Python 工具放在项目虚拟环境 `.venv` 中，使用配置文件而不是在代码中写死 Tile 数、FIFO 深度和延迟。

### 初始结果假设

以下是可证伪的实验假设，不是承诺的结果：

1. 在低通信负载下，简单行切分可能已经最好；联合策略不应为了“显得复杂”而强行获胜。
2. 在 K 维切分、Tile 数增加或 FIFO 较小时，集中归约会形成热点；树形或分组归约预计降低最大链路利用率和反压周期。
3. 只优化 MAC 均衡或平均 hop 的方案，在 Bank 冲突和链路竞争同时存在时可能选错；联合模型应更准确地预测候选方案排序。
4. 功能模型与 RTL 必须数值一致；周期模型先争取正确预测性能排序，再逐步缩小与 RTL 的周期误差。
5. 增加 Tile 不一定持续加速。项目应找到饱和点，并解释限制来自 Bank、MAC、NoC、归约还是 Host 数据移动。

项目最初应得到的结果不是一张夸张的加速比图，而是：一组可复现的正确性结果、一条能看懂的命令/Packet/波形链路、几种公平 Baseline，以及至少一个能够解释的性能瓶颈案例。
