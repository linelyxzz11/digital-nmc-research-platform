# Digital NMC Research Platform

面向 GEMV 的数字近存计算（NMC）研究平台，研究有限 Bank、MAC、NoC 带宽和 FIFO 条件下的切分、放置与端点归约。

当前可运行：单 Tile GEMV、Packet/NI/2×2 XY Mesh，以及 Demo 1 行切分/FINAL 收集、Demo 2 K 切分/INT32 PSUM 归约的多 Tile 闭环。输入通过 NoC 写入并收齐 ACK 后启动计算，Root 完成收集或归约后向 Host 发送全局 DONE。系统支持功能、公式和逐请求 Tile 三种 Compute 入口，以及可选的有限 Root 接收队列、部分和读/加/更新/写回与 Done 队列；它们与 NI/Mesh 共享时钟。输入写提交仍为功能操作，完整系统延迟尚未校准。单 Tile Compute RTL 已实现，并与 Python 模型逐周期对照；Root 最终宽写回也已接入 Tile，并校准空闲时每周期最多四个 INT32 的提交。Mapper v0.1 提供 ROW/K 切分、容量检查和一阶代价选择，Demo 1/2 CLI 可通过 `--plan` 执行 JSON 计划。Root 其余端点 RTL、多 Tile NMC RTL 与加载提交时序校准是后续工作。早期 RV32I CPU 示例仅作本地学习参考。

## 从这里开始

- 定义计算/通信接口：[Compute v0.1](docs/spec/compute_architecture_v0.1.md)、[Communication v0.1](docs/spec/communication_architecture_v0.1.md)。
- 运行 Demo：[Demo 0 指南](docs/validation.md#demo0)、[Comm 0A–0C 记录](docs/validation.md#comm)、[NMC Demo 1](docs/validation.md#demo1)、[NMC Demo 2](docs/validation.md#demo2)。
- Mapper：[v0.1 使用说明与规则](src/compiler/README.md)。
- 实验：[Root 接收压力](tools/experiments/pressure_sweep.md)、[Compute/SRAM 参考时序对照与单 Tile 逐请求验证](tools/experiments/compute_timing.md)。另见 [Tile 端口与 Bank 参考合同](docs/spec/compute_architecture_v0.1.md#92-单-tile-端口与-bank-参考组织2026-10-28-补充)；默认 Demo 行为不变，端点归约仍为功能操作。
- 硬件验证：[单 Tile RTL](src/rtl/nmc_tile/README.md)，包含 SRAM/控制/MAC 和 Python 逐周期对照；另两台电脑的分工见 [并行推进计划](docs/parallel_work.zh-CN.md)。
- Root 时序：[有限接收/部分和/Done 资源与服务合同](docs/spec/root_service_v0.1.md)，含独立手算 Trace 和同配置 ROW/K 实验；[宽写回 RTL 校准](docs/spec/root_writeback_rtl_v0.1.md) 包含 ready/valid、Bank 仲裁、错误原子性和 51 组逐周期对照。
- 当前实验方向：[UniNDP 对照指标与收益空间审计](docs/unindp_evaluation.zh-CN.md)。已复跑三个作者原生 GEMV，并完成 1,680 个本项目候选；下一阶段优先扩大 Python 映射/拓扑空间，保留既有 RTL 作小规模校准。
- 64 Tile 主实验：[v0.2 执行合同](docs/spec/scalable_execution_v0.2.md) 复用原 Tile/Root/NoC，提供 ROW/K/二维分块、集中/分层归约；[三台电脑运行说明](docs/distributed_experiments.zh-CN.md) 使用固定 112 项清单、续跑和严格合并。
- 阅读计划：[团队路线](docs/project_plan.zh-CN.md)、[Proposal](docs/proposal.md)。文献调研保存在本地 `docs/papers/`，不随 GitHub 仓库发布。
- 使用 VS Code：直接打开仓库根目录。本地的 `Digital-NMC.code-workspace` 与论文工作区为可选配置，不是运行依赖。

## 目录

```text
computer architecture project/
├── src/                 # 代码：Python 模型、Mapper、RTL、测试
│   ├── hardware.py      # 统一硬件资源配置（SRAM、Bank、FIFO、Mesh）
│   ├── simulator/
│   ├── compiler/
│   ├── rtl/             # legacy_cpu/ 仅为早期示例
│   ├── fpga/            # 后续可选
│   └── tests/
├── ini/                 # 模型/实验配置；保留实际格式 .cfg/.yaml
├── tools/               # 检查工具、实验运行与绘图脚本
├── data/                # 工作负载和精选实验结果
├── dump/                # 可重建的运行输出、trace、波形
├── lib/                 # 本地作者仓库：PIMCOMP-NN、pimsim-nn、UniNDP
└── docs/                # 规范、架构图、计划、论文和验证记录
```

目录参考 [SAITPublic/PIMSimulator](https://github.com/SAITPublic/PIMSimulator/tree/dev) 的 `src / src/tests / ini / tools / data / dump / lib`。为本项目额外保留 `docs`，集中存放说明和论文。只参考工程组织，不改变既有数字 NMC 架构。星标与适用范围见 [目录依据和迁移表](docs/README.md#layout)。

## 环境和运行

在仓库根目录执行：

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt

# 全部当前测试
python -m pytest -q -p no:cacheprovider
# Compute Demo 0
python -m pytest -q -p no:cacheprovider src/tests/test_demo0_functional.py
# Packet + Comm 0A–0C
python -m pytest -q -p no:cacheprovider src/tests/test_communication_v0_1.py
# 多 Tile NMC Demo 1：打印计算/通信事件，并保存 Trace
PYTHONPATH=src python -m simulator.system.demo1 --trace dump/demo1_trace.json
# Demo 1 验收测试
python -m pytest -q -p no:cacheprovider src/tests/test_nmc_demo1.py
# NMC Demo 2：K 切分与 PSUM 归约
PYTHONPATH=src python -m simulator.system.demo2 --trace dump/demo2_trace.json
python -m pytest -q -p no:cacheprovider src/tests/test_nmc_demo2.py
# Mapper v0.1：生成计划
PYTHONPATH=src python -m compiler.auto_mapper_v0_1 --workload data/workloads/demo1_gemv.json --strategy auto -o dump/mapper_plan.json
PYTHONPATH=src python -m compiler.auto_mapper_v0_1 --demo --strategy k --k-split 1
PYTHONPATH=src python -m compiler.auto_mapper_v0_1 --self-test
# Demo CLI 直接执行计划；--workload 可选，用于 golden 校验
PYTHONPATH=src python -m simulator.system.demo1 --plan dump/mapper_plan.json --workload data/workloads/demo1_gemv.json
PYTHONPATH=src python -m compiler.auto_mapper_v0_1 --workload data/workloads/demo1_gemv.json --strategy k -o dump/mapper_plan_k.json
PYTHONPATH=src python -m simulator.system.demo2 --plan dump/mapper_plan_k.json --workload data/workloads/demo1_gemv.json
python -m pytest -q -p no:cacheprovider src/tests/test_mapper_v0_1.py src/tests/test_plan_runner.py
# 批量计划对比：ROW/K × worker/Root 放置，输出 JSON/CSV
PYTHONPATH=src python tools/experiments/mapper_sweep.py --demo --placements all --output data/results/mapper_sweep_demo.json --csv data/results/mapper_sweep_demo.csv
PYTHONPATH=src python tools/experiments/mapper_sweep.py --workload data/workloads/demo1_gemv.json --k-splits all
# 形状矩阵：suite = frontier + half + extremes + aspect
PYTHONPATH=src python tools/experiments/mapper_sweep.py --shapes suite --output data/results/mapper_sweep_suite.json --csv data/results/mapper_sweep_suite.csv
PYTHONPATH=src python tools/experiments/mapper_sweep.py --shapes 16x128,128x16,32x64,64x32 --top 2
python -m pytest -q -p no:cacheprovider src/tests/test_mapper_sweep.py
# 详细 Tile SRAM/Bank 时序，与 NI/Mesh 共用时钟；--plan 同样支持这些选项
PYTHONPATH=src python -m simulator.system.demo1 --compute-model tile --tile-config ini/tile_system/baseline.json --tile-trace --trace dump/tile_row.json
PYTHONPATH=src python -m simulator.system.demo2 --compute-model tile --tile-config ini/tile_system/result_1bank.json --trace dump/tile_k.json
python tools/experiments/timed_tile_system.py --output-dir dump/timed_tile_system_20261028
# 同时启用实际有限 Root 服务；不需要人工接收压力窗口
PYTHONPATH=src python -m simulator.system.demo2 --compute-model tile --root-model timed --root-trace --trace dump/root_k.json
python tools/experiments/finite_root_system.py --output-dir dump/finite_root_system_20261028
# 检查随 Git 发布的文档链接
python tools/check_layout.py --tracked
```

Python 导入路径为 `src`，包名仍为 `simulator`；直接运行解释器时使用 `PYTHONPATH=src`。已有 `.venv` 保留在根目录，无需因目录整理重装环境。

## 总体架构

![NMC Tile microarchitecture](docs/figures/fig1_nmc_tile_microarchitecture.png)

![Multi-tile NMC mesh](docs/figures/fig2_multitile_mesh_organization.png)

![Mapping and validation flow](docs/figures/fig3_mapping_and_validation_flow.png)

以上为规划图，不能当作 RTL 或性能实验结果。FPGA 为可选验证，不属于当前完成条件。

## 协作与文件规则

- 接口修改先更新 `docs/spec/`，重要决定记录到 `docs/spec/design_decisions_v0.1.md`。
- 测试源码放 `src/tests/`，原始输出放 `dump/`，精选结果放 `data/results/`。
- 本地论文 `docs/papers/`、作者仓库 `lib/`、虚拟环境和大型输出不上传 GitHub。
- 分支和 PR 工作流程见 [CONTRIBUTING](CONTRIBUTING.md)。

许可证仍待团队确认；没有因为参考目录结构而采用对方的许可证。
