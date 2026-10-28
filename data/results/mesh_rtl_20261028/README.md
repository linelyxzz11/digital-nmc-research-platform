# 64 Tile 集成 RTL 验证数据

九项集成用例均通过：手算、非整除 tree、ROW、二维 central/tree、256×64 的 32-worker central/tree、反压和在途复位重放。每次实际实例化 64 个 Tile；活跃计算为 2/12/16/32 个，其余按计划承担归约或空闲。

[results.json](results.json) 保存数值/包协议/完成计数和分阶段周期；[traces.tar.gz](traces.tar.gz) 保存九例输入、计划、完整 ejection Flit、Root SRAM 读回与周期 CSV。[reduce_cycle_trace.csv](reduce_cycle_trace.csv) 为独立端点逐边沿对照，包含 31/63 号来源、INT32 回绕、队列满和写阻塞；同一自检查另拒绝五类非法包。

完整系统数值、实际通信字段、唯一 Done 与资源边界一致。默认未阻塞用例的 kernel+drain 周期差异为 4–8 周期；5×3 的相对差异为 10.81%，256×64 central 为 0.039%，tree 为 0.371%。反压用例给双方相同的 300 周期端点 gate，RTL/模型为 563/560 周期。

输入经过真实窄 SRAM 写端口，NI 也是寄存队列；Python 原入口采用功能输入提交，任务接受/VALIDATE 口径也不同。因此不声称整任务逐周期等价。原单 Tile/宽写回、独立完整归约端点具有各自的逐周期证据。没有由本数据推导完整芯片 PPA。

实现/运行见 [RTL 说明](../../../src/rtl/nmc_mesh/README.md)。公共源码快照在 [映射实验目录](../mapping_search_20261028/source_snapshot.tar.gz)。
