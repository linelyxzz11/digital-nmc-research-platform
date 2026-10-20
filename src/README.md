# 源码

- `simulator/functional/`：冻结的 Compute v0.1 功能语义。
- `simulator/communication/`：Packet、NI 和 2×2 Mesh 周期通信模型。
- `simulator/system/`：计算与通信闭环；`demo1.py` 实现行切分/FINAL 收集及共享调度链；`demo2.py` 复用该链，实现 K 切分和目标端 INT32 PSUM 归约。
- `compiler/`：[Mapper v0.1](compiler/README.md)，生成 ROW/K 切分、权重布局、输入写请求和 Root 配置；auto 使用 ROW 优先的容量基线。JSON 计划尚未直接接入 Demo CLI。
- `rtl/`：后续 NMC SystemVerilog；`legacy_cpu/` 仅保留早期 RV32I 示例。
- `fpga/`：可选后续部署。
- `tests/`：当前 Python 自检查测试。

Python 搜索路径为 `src`，现有包名 `simulator` 保持不变。接口依据见 [计算规范](../docs/spec/compute_architecture_v0.1.md) 和 [通信规范](../docs/spec/communication_architecture_v0.1.md)。
