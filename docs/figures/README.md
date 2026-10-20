# Architecture figure design and evidence notes

三张图描述当前项目规划，不代表模块已经实现或获得实验结果。SVG 中所有文字保持可编辑，PNG 以 2× 原始分辨率导出。

## Figure 1 — NMC Tile microarchitecture

- **主张：** 一个数字 NMC Tile 把本地 Bank、Buffer、向量 MAC、累加、控制与 NoC 端点组成一条逐周期数据通路。
- **放置：** 团队文档第 2 节第一张主图。
- **图注：** Planned NMC Tile microarchitecture. Commands configure local memory and vector execution; INT8 products accumulate in INT32, while the endpoint NI transports operands and tagged partial sums under valid/ready flow control.
- **证据边界：** 4 路 MAC 已由 Compute v0.1 冻结；通信 v0.1 采用 128-bit Flit、单 VC 和每 Router 输入 4-Flit FIFO。旧图中若仍显示 2 VC，以通信规范为准，后续修图时改为 1 VC。

## Figure 2 — Multi-tile mesh organization

- **主张：** 行切分主要产生输入分发，K 维切分产生部分和归约；有限 FIFO 会使拥塞通过 ready 信号向上游传播。
- **放置：** 图 1 后，用于解释 NoC 为什么属于核心研究问题。
- **图注：** Planned multi-tile digital NMC system and two GEMV decompositions. Solid operand routes show input distribution, dashed routes show partial-sum reduction, and the dotted path marks backpressure from a full endpoint FIFO.
- **证据边界：** 图中的 3×3 Mesh 仅作扩展性示意；可执行 v0.1 固定为 2×2。v0.1 的输入“广播”由源端复制为多个单播 Packet，归约位于目标 NI/Endpoint，Router 不执行组播或数值归约。

## Figure 3 — Mapping and validation flow

- **主张：** Mapper 联合考虑分块、放置与归约，并让一份 ISA/Packet Trace 驱动数值、周期与 RTL 后端。
- **放置：** 图 2 后，用于衔接软件任务分工、Baseline 和实施路线。
- **图注：** Cross-layer mapping and validation flow. Legal GEMV decompositions are placed on a mesh and ranked with Bank, NoC, FIFO and reduction costs; the selected plan emits one executable trace shared by the Golden model, event model and RTL testbench.
- **证据边界：** 图中 Llama 类 shape 是计划使用的 workload 例子；selected plan 和柱状图是数据结构示意，不是实测结论。

## 已完成检查

- SVG 结构可由 `xmllint` 解析；文字保留为 `<text>`，没有栅格化进 SVG。
- PNG 尺寸分别为 3200×1800、3200×2040、3200×1800。
- 视觉复检覆盖标题重叠、箭头终点、面板裁切、图例位置和最小文字可读性。
- 颜色同时配合位置、实线/虚线/点线和文字标签，灰度下仍可区分主要语义。

## 后续人工确认

- 通信 v0.1 冻结后，把图 1 中的 VC/FIFO/Flit 参数与规范逐项核对。
- 生成 Demo 1/2 Trace 后，把图 2 的路径改成与真实 Tile ID、XY 路由和 Root 位置一致。
- 第一批实验完成后，将图 3 的示意柱状图替换为真实结果；在此之前不得把它作为性能证据。
