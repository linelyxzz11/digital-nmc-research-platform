# ROW 多最终 Root 执行合同 v0.1

## 1. 研究问题与范围

本合同研究同一套 8×8、64 Tile 数字 NMC 架构中，ROW 切分的 GEMV 是否能通过增加最终 Root 数量，缓解结果汇聚、写回和局部链路压力。

Python 周期模型支持 1、2、4 个最终 Root。所有 Root 都占用原有 64 Tile 预算，不增加计算 Tile、MAC、Bank 或 NoC 带宽。阶段一只支持 ROW 切分；K/二维多 Root、4096×4096、完整多 Root SystemVerilog RTL、综合和 PPA 均不在本合同的完成范围内。

## 2. 计划接口

`GridPlan` 新增：

```text
final_roots: tuple[int, ...]
final_root_ranges: tuple[FinalRootRange, ...]

FinalRootRange:
  root_tile: int
  row_start: int
  row_end: int
  source_mask: int
```

`root_tile` 继续表示第一个最终 Root，旧调用默认等价于 `final_roots=(root_tile,)`。为保持既有基线摘要和 digest 不变，单 Root 计划仍按 v0.2 格式序列化；只有多 Root 计划才按 v0.3 输出新增字段。

多 Root 计划必须满足：

- Root 数量只能为 1、2 或 4，编号合法、互不重复，第一个 Root 等于 `root_tile`；
- 多 Root 仅允许 `grid=(row_parts, 1)`；
- Root、Host 与 Worker 不能发生非法资源重叠；
- 每个 Root 至少拥有一个完整 ROW Worker；
- Root 的输出范围连续、互不重叠并完整覆盖 `[0, M)`。

## 3. ROW 到 Root 的映射

Mapper 先按原规则把 M 行均衡切给 ROW Worker，再把相邻 Worker 组成尽量均衡的连续组，每组绑定一个最终 Root。Worker 仍只有一个 `result_dst_tile`，因此不改变 MAC 数据通路和结果包格式。

例如 16 个 Worker、2 个 Root：

```text
Worker 0..7  -> Root A -> y[0:128]
Worker 8..15 -> Root B -> y[128:256]
```

每个 Root 只校验并接收本组 Worker 的来源 mask。每个 Root 的 Result SRAM 从局部地址 0 保存自己的输出；仿真结束后按 `FinalRootRange.row_start` 将各段拼回全局向量 y，并逐项与 CPU golden 比较。

## 4. 完成协议

每个最终 Root 完成本地范围后发送一个现有的 `TransactionDonePacket`。Host 的 `RootDoneTracker` 检查 Root 来源、TID、目标 Host、状态、完成元素数、来源 mask 和重复包。

网络中会有 1、2 或 4 个 Root Done 包，但 Host 只有在所有计划 Root 均完成后，才产生一次全局 transaction 完成事件。因此：

```text
root_done_packets = 最终 Root 数量
global_transaction_done = 1
```

系统还必须等待全部 Worker 本地完成、全部 Root 空闲且 Mesh 排空后才能返回。

## 5. 公平对比规则

首轮固定 Root 集合为：

```text
1 Root : (0,)
2 Roots: (0, 63)
4 Roots: (0, 7, 56, 63)
```

所有对比都先为 4 Root 情况选出共同 Worker 集合，并在 1/2/4 Root 中原样复用；候选 Root Tile `{0, 7, 56, 63}` 始终保留，不允许少 Root 案例把空闲候选变成额外 Worker。这样比较中改变的只有最终汇聚点数量，而不是计算资源。

实验覆盖 3 个工作负载/Worker 组合、compact/spread 两种放置、baseline/pressure 两种 Root 服务配置和 1/2/4 Root，共 36 组。pressure 配置使用 1 包接收队列、每周期 1 lane、写回 2 周期，用于观察 Root 明显受限时的趋势；它不是默认硬件性能。

## 6. 指标

主指标是完成整个 GEMV 的总周期，包含计算、结果传输、Root 服务和完成聚合。辅助指标包括：

- `speedup_vs_1root = cycles_1root / cycles_nroot`；
- `flit_hops` 和最忙链路利用率；
- 每个 Root 的输出行数、结果包数和完成周期；
- Root 结果包负载不均衡度；
- 最早与最晚 Root 完成周期之差；
- Root Done 包数和 Host 全局完成次数。

## 7. 本地实验结果（2026-11-01）

36/36 个案例数值正确，且每个案例均只产生一次全局完成事件。

| Root 服务 | Root 数 | 平均总周期 | 相对 1 Root 平均加速 |
|---|---:|---:|---:|
| baseline | 1 | 5606.00 | 1.000× |
| baseline | 2 | 5575.83 | 约 1.01× |
| baseline | 4 | 5561.17 | 约 1.01× |
| pressure | 1 | 5959.33 | 1.000× |
| pressure | 2 | 5707.83 | 约 1.05× |
| pressure | 4 | 5603.50 | 约 1.07× |

本轮最佳案例为 `M=128, K=32, 16 workers, compact, pressure, 4 Roots`，总周期 2377，相对同组单 Root 加速 1.0947×。默认 baseline 只有约 1% 收益，说明这些小规模案例中单 Root 并非主要瓶颈；当 Root 服务被有意收紧时，多 Root 才体现约 5%–9.5% 的收益。该结论只适用于本合同的 36 组 Python 周期模型实验，不外推为 RTL、芯片或 4096×4096 性能结论。

原始本地结果位于 `dump/multi_root_row_20261101/`，该目录可重建且不随 Git 发布。

## 8. 复现命令

在仓库根目录执行：

```bash
python tools/experiments/multi_root_row.py --output-dir dump/multi_root_row_20261101
python -m pytest -q -p no:cacheprovider src/tests/test_multi_root_row.py
```

配置入口为 `ini/scalable/multi_root_row.json`，测试覆盖计划合法性、1/2/4 Root 数值正确性、局部 SRAM 窗口、完成聚合、反压边界、单 Root 兼容性和实验文件生成。
