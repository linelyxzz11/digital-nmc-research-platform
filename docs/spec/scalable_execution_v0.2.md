# 64 Tile 执行与映射合同 v0.2

状态：`IMPLEMENTATION DRAFT`。主配置为 8×8 Mesh、64 个物理 Tile；v0.1 的 2×2 入口和线格式默认值保留。新入口为 [Grid Mapper](../../src/compiler/grid_mapper.py)、[实例化 Mesh/Codec](../../src/simulator/communication/scalable.py)、[执行循环](../../src/simulator/system/scalable.py)。

## 复用与扩展边界

| 层 | 继续复用 | 本版扩展 |
|---|---|---|
| Mapper/Compute | TileTask 字段、INT8/INT32、`(group,k,lane)` 权重布局、地址/容量检查 | 多 worker，ROW/K/二维网格，确定性放置，归约父子图 |
| Tile 时序 | `PortScheduledCompute`、有限操作数槽、同步 SRAM、四个 Result Bank、Result/Done 反压 | Tile 实例增加逻辑 `tile_count`，计算时序不变 |
| Root | `TimedRoot` 的 RX 预留、读/加/更新/写回、Done 和错误检查 | 每个输出范围的来源表；多个有限端点组成归约树 |
| NoC | 128-bit Flit、先 X 后 Y、输入 FIFO、wormhole 输出锁和轮询仲裁 | 拓扑/路由/codec 属于实例，可同时运行 2×2 和 8×8 |
| RTL | 原 MAC、SRAM、Compute 和 Root 宽写口的校准结果 | 仍是小规模校准，未实现 64 Tile 系统 RTL |

两 worker 的 ROW/K 经新旧执行入口逐周期计数对照，结果、任务和 flit-hop 保持一致。新 Mapper 的三个二维分块叶任务还会送入原 Tile RTL，核对完整周期 Trace、ResultBeat 和 SRAM 读回。原 RTL 的 Tile ID 校验仍限 0..3，因此该校准仅重映射物理 Tile/目标 ID，保留数值、地址、global index 和时序。

## 物理资源与任务范围

所有策略使用同一个物理预算：64 Tile，每 Tile 4-Lane MAC、4 KiB Weight、1 KiB Input、1 KiB Result；Router 每输入 4 Flit，NI 4 Flit，链路每方向每周期一个 Flit。Host/最终 Root 默认均为 Tile 0，计算 Tile 和中间归约 Tile 互斥。主清单比较 16/32 个活跃计算 Tile，其他 Tile 可以承担归约，保持相同的 64 Tile 物理预算。

所有物理 Tile 都声明同样的端点能力：默认两 RX 槽、一项活动包、256 个 INT32 部分和条目、一项 Done；中间端点再有一项输出 Result 寄存器。来源/接收预留元数据按最多 64 来源计。这些端点资源是 6 KiB Tile SRAM 之外的预算，尚未综合，不能拿先前 49,152 bit 的统计代表完整 Tile。集中策略留下闲置归约能力，树策略使用这些已有 Tile，不额外增加节点或缓冲。

本版保持已校准的本地 SRAM 几何：每个 leaf 的 `M_local<=256`、`K_local<=1024` 且按 padding 后的 Weight 字节数检查容量。单 transaction 最终输出不超过 256 个 INT32。当前用于受控/缩小 GEMV 核心实验；尚未支持完整 4096×4096 LLM 权重常驻或外存分块搬运。

## v0.2 Packet 与完成协议

RESULT、MEM_WRITE、ACK 的字段位置和 Flit 数与 v0.1 相同；源/目的 Tile 字段原本就是 8 bit。TRANSACTION_DONE 的来源 mask 从 v0.1 的 4 bit 扩为实例的 `max(4,tile_count)` bit，仍从 bit 60 开始。64 Tile 时使用 `[123:60]`，整个 Done 仍为一个 HEADTAIL Flit，最高四位保留为零。Codec 必须和拓扑一起指定；旧 2×2 默认仍检查原 4-bit mask 与保留位。当前 transaction codec 限制 64 Tile，256 Tile 的更宽完成协议另行扩展。

来源 mask 表示该端点的**直接子来源**。二维分块下，每个输出行范围有独立的期望来源集合；完整 Packet 的全部 Lane 都检查范围、来源、重复和 source-last 后才入队。不会等待本行根本没有分配的其他 worker。

Host 发送输入 MEM_WRITE，逐项等待 ACK，收齐后同时启动各 leaf。权重预加载不计时，输入写提交仍为功能服务。所有 Compute、端点和网络按同一个模型时钟推进；计时窗口拆为输入加载、计算/归约、完成排空。输出是模型任务周期，不是完整系统 RTL 或器件时延。

## 集中与分层归约

集中模式：同一输出范围的全部 K shard 发到最终 Root，Root 按对应来源表归约。纯 ROW 直接 FINAL 收集，不执行部分和加法。

树模式：每个输出行范围按固定 fan-in 构造树，分组的来源集合不重叠、覆盖全部 leaf。中间 Tile 从剩余物理节点中按子节点距离与到 Root 的距离确定性选择；预算不足则拒绝计划。每个中间端点复用 `TimedRoot`，把完成的局部结果经一项输出寄存器发给父节点。中间边发送 PSUM，最上层完整输出发送 FINAL，最终 Root 做 FINAL 收集。

输出寄存器满时暂停下一次 WRITE_RESULT，反压通过 RX、Router 和 NI 传回子节点。最后一组输出等待本端点全部来源的 last 被服务并创建本地 Done，再向父节点标记 last；本地 Done 在中间端点内部消费，不冒充全局完成包。最终 Host Done 后还须所有 leaf LocalDone、全部端点和网络排空，transaction 才结束。

数据图在执行前按 Mapper 规范重建校验；修改 task 路由、范围或归约图而未同步规范会在运行前被拒绝。稀疏尾 Lane、非整除分块、INT32 回绕、高位 Tile ID、慢服务与输出阻塞均有测试。

## 实验与分发

主清单为 [main.json](../../ini/scalable/main.json)：四个长宽比 GEMV × 七种 16/32-worker 网格 × 两种放置 × 集中/树归约，共 112 项。冒烟清单 [smoke.json](../../ini/scalable/smoke.json) 为 12 项。固定清单、源码内容哈希、plan 哈希和 Git 版本后可拆到多台电脑；每台执行完整独立案例，仿真周期不跨机器同步。操作见 [多机运行](../distributed_experiments.zh-CN.md)。

不把集中策略换成弱基线后只报有利形状，不把树归约占用的端点资源隐藏。主指标是同物理配置下的任务周期及加速比，辅助报告 kernel/加载窗口、流量、停顿和端点占用；Root 服务可与 Compute/NoC 重叠，不能直接相加为总时间。清单内事后最优只作为有限候选参考，不称为已实现的新 mapper 或 UniNDP 移植。
