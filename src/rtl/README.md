# RTL

[nmc_tile/](nmc_tile/README.md) 已实现单 Tile Compute RTL：4-Lane MAC、独立同步 SRAM 读、四个 Result Bank、控制器、结果缓冲与外部加载口，配套 Python 逐周期对照、Verilator lint 和 Yosys 存储资源检查。NI、Router 和 Endpoint Reduction 的 RTL 仍待实现。`legacy_cpu/` 是本地保留的早期 RV32I CPU 学习示例，不属于本次发布的 NMC 实现。

接口依据：[Compute v0.1](../../docs/spec/compute_architecture_v0.1.md)、[Communication v0.1](../../docs/spec/communication_architecture_v0.1.md)。
