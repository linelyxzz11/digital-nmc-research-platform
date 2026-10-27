# 源码

- `hardware.py`：Digital NMC v0.1 的容量、地址和网络资源配置（Mesh、Tile、SRAM 区域、粗粒度 Bank 元数据、FIFO、Flit、全局索引位宽）；Mapper、仿真模型和计划校验共用。单 Tile 端口时序试验的每区 Bank 组织通过 `simulator/system/tile_memory.py` 中的 `TileMemoryConfig` 显式指定。
- `simulator/functional/`：冻结的 Compute v0.1 功能语义。
- `simulator/communication/`：Packet、NI 和 2×2 Mesh 周期通信模型。
- `simulator/system/`：计算与通信闭环；`demo1.py` 实现行切分/FINAL 收集、共享调度链和 MappingPlan 加载校验；`demo2.py` 复用该链，实现 K 切分和目标端 INT32 PSUM 归约。两个 CLI 都可用 `--plan` 执行 Mapper 生成的 JSON。
- `compiler/`：[Mapper v0.1](compiler/README.md)，生成 ROW/K 切分、权重布局、输入写请求和 Root 配置；auto 按一阶 MAC+包计数代价选择。生成计划可直接由 Demo CLI 读取执行。
- `rtl/`：已实现 [单 Tile Compute SystemVerilog](rtl/nmc_tile/README.md)；`legacy_cpu/` 仅保留早期 RV32I 示例。
- `fpga/`：可选后续部署。
- `tests/`：当前 Python 自检查测试。

Python 搜索路径为 `src`，现有包名 `simulator` 保持不变。接口依据见 [计算规范](../docs/spec/compute_architecture_v0.1.md) 和 [通信规范](../docs/spec/communication_architecture_v0.1.md)。
