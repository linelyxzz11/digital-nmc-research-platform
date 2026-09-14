# Digital NMC Research Platform

面向小批量神经网络推理的可配置数字近存计算（NMC）课程与研究项目。

项目围绕 GEMV 构建从工作负载、Mapper、ISA/消息 Trace、功能与周期模型到多 Tile RTL 的完整链路，并研究有限 Bank、MAC、NoC 带宽和 FIFO 条件下的分块、放置与归约选择。

> 当前状态：架构规划与工程骨架。功能模拟器、Mapper、周期模型和 RTL 尚未实现；仓库不会把规划内容描述为已有结果。

## Planned system

**NMC Tile microarchitecture（主图）**

![NMC Tile microarchitecture](docs/figures/fig1_nmc_tile_microarchitecture.png)

**Multi-tile mesh organization**

![Multi-tile NMC mesh](docs/figures/fig2_multitile_mesh_organization.png)

**Mapping and validation flow**

![Mapping and validation flow](docs/figures/fig3_mapping_and_validation_flow.png)

系统由以下部分组成：

- 工作负载与 Operator IR；
- GEMV 分块、Tile/Bank 放置和归约规划；
- 统一 ISA 命令与 NoC 消息 Trace；
- NumPy Golden、功能解释器和周期事件模型；
- Local Bank、INT8 Vector MAC、INT32 Accumulator、NI、Router 与端点归约 RTL；
- 连接多个 NMC Tile 的有限缓冲 2D Mesh NoC。

FPGA 只作为主线完成后的可选验证，不属于项目完成条件。

## Repository layout

|目录|用途|
|---|---|
|`spec/`|架构、ISA、Packet、数值语义与接口约定|
|`compiler/`|Operator IR、分块、放置、归约计划和 Trace 生成|
|`simulator/`|NumPy Golden、功能解释器和周期事件模型|
|`rtl/`|Tile、Vector MAC、NI、Router 和 Reduction RTL|
|`workloads/`|GEMV、MLP、decode 形状与输入生成|
|`tests/`|单元、接口和端到端测试|
|`experiments/`|配置、批量运行和绘图脚本|
|`results/`|精简 CSV、图和结果索引；不提交大型原始输出|
|`docs/`|团队路线、架构图和设计决定|
|`fpga/`|可选板级验证|

## First milestone

1. 手算并记录一个 4×4 GEMV 的行切分、K 维切分和二维切分。
2. 冻结 Tile、数值语义、ISA 与 Packet v0.1。
3. 完成 NumPy Golden、VADD/REDUCE/GEMV 功能解释器。
4. 让 Mapper 自动改变指令 Trace 与通信矩阵。
5. 完成 Vector MAC、单 Tile、NI、Router，再连接 2×2 Mesh。

完整路线见 [团队版项目路线与研究设计](docs/project_plan.zh-CN.md)。

## Development setup

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

Windows 建议在 WSL2 Ubuntu 中开发。RTL 工具建议安装 Icarus Verilog、Verilator、GTKWave 与 Yosys。具体版本将在第一个可运行里程碑后锁定。

## Collaboration

- 用 GitHub Issue 记录负责人、接口、依赖和验收条件。
- 每个 Pull Request 关联 Issue，并附最小测试证据。
- `main` 只接收可运行或纯文档变更。
- 论文 PDF、他人仓库副本、虚拟环境、完整波形和大型原始结果不上传。

## License

许可证等待团队共同确认。在许可证确定前，仓库内容默认保留全部权利。

