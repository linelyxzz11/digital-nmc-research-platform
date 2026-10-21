# Digital NMC Research Platform

面向 GEMV 的数字近存计算（NMC）研究平台，研究有限 Bank、MAC、NoC 带宽和 FIFO 条件下的切分、放置与端点归约。

当前可运行：单 Tile GEMV、Packet/NI/2×2 XY Mesh，以及 Demo 1 行切分/FINAL 收集、Demo 2 K 切分/INT32 PSUM 归约的多 Tile 闭环，配套 Python 自检查测试。输入通过 NoC 写入并收齐 ACK 后启动计算，Root 完成收集或归约后向 Host 发送全局 DONE。Compute 和归约仍为功能模型，通信调度周期不代表完整 GEMV 延迟。Mapper v0.1 已提供 ROW/K 切分、容量检查和 ROW 优先的 auto 选择，生成与现有 TileTask/Packet 字段一致的 JSON 计划；Demo 1/2 CLI 可通过 `--plan` 直接读取并执行该计划。性能优化 Mapper、完整时序模型和 NMC RTL 仍待实现。早期 RV32I CPU 示例仅作本地学习参考。

## 从这里开始

- 定义计算/通信接口：[Compute v0.1](docs/spec/compute_architecture_v0.1.md)、[Communication v0.1](docs/spec/communication_architecture_v0.1.md)。
- 运行 Demo：[Demo 0 指南](docs/validation.md#demo0)、[Comm 0A–0C 记录](docs/validation.md#comm)、[NMC Demo 1](docs/validation.md#demo1)、[NMC Demo 2](docs/validation.md#demo2)。
- Mapper：[v0.1 使用说明与规则](src/compiler/README.md)。
- 阅读计划：[团队路线](docs/project_plan.zh-CN.md)、[Proposal](docs/proposal.md)。文献调研保存在本地 `docs/papers/`，不随 GitHub 仓库发布。
- 使用 VS Code：直接打开仓库根目录。本地的 `Digital-NMC.code-workspace` 与论文工作区为可选配置，不是运行依赖。

## 目录

```text
computer architecture project/
├── src/                 # 代码：Python 模型、Mapper、RTL、测试
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
