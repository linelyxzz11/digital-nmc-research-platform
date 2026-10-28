# RTL

[nmc_tile/](nmc_tile/README.md) 实现单 Tile Compute 与宽写回，保留 Python 逐周期对照、Verilator lint 和 Yosys 存储资源检查。[nmc_mesh/](nmc_mesh/README.md) 进一步实例化 64 Tile，接通四 Flit NI、五端口 XY Router、有限归约端点、输入 ACK 与最终 Done；九项集成用例核对实际包和 SRAM，独立端点逐边沿对照 Python。完整系统输入/NI 周期差异另列，未做完整芯片综合/PPA。`legacy_cpu/` 是本地保留的早期 RV32I CPU 学习示例。

接口依据：[Compute v0.1](../../docs/spec/compute_architecture_v0.1.md)、[Communication v0.1](../../docs/spec/communication_architecture_v0.1.md)。
