# Root 宽写回 RTL 接口与校准 v0.1

状态：`IMPLEMENTED / RTL SIMULATION CALIBRATED`，2026-10-28。实现位于 [Tile 顶层](../../src/rtl/nmc_tile/nmc_tile_compute.sv) 和 [SRAM 仲裁](../../src/rtl/nmc_tile/nmc_tile_sram.sv)。这是 Root 最终结果到 Result SRAM 的写入通路；Root 的接收、部分和存储、加法及 Done 控制仍由 [Python 端点](../../src/simulator/system/timed_endpoint.py) 描述。

## 接口与提交边沿

| 信号 | 方向（相对 Tile） | 含义 |
|---|---|---|
| `root_wr_valid` | 输入 | 本周期有一组写请求 |
| `root_wr_ready` | 输出 | 能在本周期上升沿接收请求 |
| `root_wr_addr[15:0]` | 输入 | Lane 0 的本地 byte address，4-byte aligned |
| `root_wr_mask[3:0]` | 输入 | 每位选择一个完整 INT32；必须非零 |
| `root_wr_data[127:0]` | 输入 | Lane i 为 `[32*i +: 32]`，写地址 `addr+4*i` |
| `root_wr_error` | 输出 | 本次握手拒绝整组请求，无任何 SRAM 写入 |
| `trace_root_write` | 输出、观测 | 本次握手合法，选中的 Bank 在该上升沿写入 |

请求方仅在 `valid && ready` 的上升沿前进；等待期间保持 valid、地址、mask、数据。每组写入在握手的同一边沿提交，没有额外请求缓冲或响应周期。`root_wr_error` 是握手周期的组合完成状态，只有 `valid && ready` 时有效，必须由请求方在该边沿采样；没有单独的错误响应队列。未使用该接口的实例必须把 `root_wr_valid` 接 0。

地址基准必须位于 `0x2000..0x23fc`；全部有效 Lane 的完整四字节均须位于 1 KiB Result 区。未对齐、空 mask、错误区域或任一有效 Lane 越界，都接受为一项错误请求，并原子地拒绝全部写入；随后可继续接收下一请求。例：`addr=0x23fc, mask=0001` 合法，`mask=0011` 整组失败。只检查有效 Lane；允许稀疏 mask，也允许 `addr%16=4/8/12`。

## 仲裁与 Bank 组织

Compute 从任务握手到 LocalDone 握手独占 SRAM，Root 和 Host 均暂停。IDLE 时：

1. 未完成的 Host 请求或未握手的 Host 响应继续占用端口；
2. 新的 Host 存储请求优先；
3. 然后接受 Root 宽写；
4. 没有上述请求时接受新的 Compute 任务。

Host 响应握手当周期仍阻止 Root，下一周期恢复。该固定优先级没有公平性保证，持续 Host 请求可以阻塞 Root，持续 Root 请求可以阻塞新任务。现有系统采用加载、计算/Root 服务、读回分阶段执行，不依赖三者同时进展。

四个 Result Bank 仍各为 `64×32 bit`、一个共享读写服务端口。先把 Compute/Root 的向量写复用为一项向量，再逐 Bank 与 Host 复用；没有额外 SRAM 副本或访问端口。按 word address 的低两位旋转 Lane，跨 16-byte 边界时相应 Bank 使用下一行地址。四个连续 INT32 的有效 Lane 必然落到不同 Bank，因此可在一个边沿写入。

`rst_n` 为低有效同步复位。复位期间 ready/error/commit 均为 0；已提交 SRAM 数据保留。请求方在复位时取消未握手请求；复位后保持 valid 会被视为一项新的待接收请求。

## Python 对接和服务周期

`TimedRoot.write_request()` 在 `WRITE_RESULT` 的最后一个服务周期返回不可变的 `RootWriteRequest(address, lane_mask, data)`，无请求时返回 None。`packed_data` 对应 RTL 的 128-bit 总线；部分完成的 PSUM 组仅选择来源 mask 已齐的索引。地址由 `0x2000+4*(index-output_base)` 生成。

`step(write_ready=False)` 令该写请求保持，禁止修改 Result SRAM、释放部分和或推进完成状态，并增加 `result_write_stall_cycles`。`write_ready=True` 才在该边沿提交。配置 `write_cycles>1` 表示发出请求前的额外写服务等待；最终握手仍只提交一次。Trace 记录 `write_valid/write_ready/write_accepted`。

校准结论：空闲宽口每周期可接受一组、最多四个 INT32，支持 **16 byte/cycle**；默认 `write_cycles=1` 与此条件下的 RTL 一致。端口被占用的等待由 ready 反压单独累加，不能把该吞吐直接当成整个 Root 的包吞吐。读部分和、加法、更新、Packet 调度、Done 和 NoC 时间仍另外计算。系统 runner 当前默认 Root 写端口可用；将来与 Host/Compute 重叠时必须将实际仲裁结果反馈给 `write_ready`。

## 验证与复现

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -B tools/rtl/run_root_writeback_rtl.py \
  --seeds 32 --output-dir dump/rtl_root_writeback_20261028
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -B tools/rtl/run_tile_rtl.py \
  --seeds 32 --output-dir dump/rtl_tile_root_writeback_20261028
python -m pytest -q -p no:cacheprovider
```

32 个固定随机种子的扩大运行有 **602 个请求：585 次合法写入、17 次整组拒绝**；pytest 默认八个种子为 218 个请求。自检查涵盖：连续 16 周期的 16 个全宽向量、全部四种 Bank 旋转与 15 种非空 mask、随机地址/INT32 数据、边界和错误原子性、Host/Root/Task 同时请求、Host 响应保持、Compute Result/Done 阻塞期间的 Root 保持、复位取消与已写数据保留。每项错误请求后立即通过 Host 读完整 Result SRAM，防止后续写入掩盖非法的部分提交。

Python 到 RTL 有 51 组写请求/ready/提交时间线对照：FINAL/PSUM、1/2/4 通道、1/3/4/5/7/13/256 个输出、混合来源完成状态，以及六组真实 Host 争用。两来源四输出的默认手算仍为 cycle 9 写入 `[5,3,-3,-11]`、总计 13 个 Python 服务周期；Host 占用六周期后分别延到 15 和 19。此总周期包含 Python 的归约控制，不声称该控制已经综合。

原 Tile 71 个用例/77 段执行继续通过；5×3 无阻塞结果周期仍为 6、13，LocalDone 为 14。Yosys 验证六块单读口/单写口、同步读的 SRAM，合计 49,152 bit。该结果不包括未来 Root 部分和存储，不提供宏时序、Fmax、面积或功耗测量。

精简证据见 [结果 JSON](../../data/results/root_writeback_rtl_20261028/results.json)、[Tile 回归和综合结构](../../data/results/root_writeback_rtl_20261028/tile_regression.json)、[接口与争用 Trace](../../data/results/root_writeback_rtl_20261028/interface_trace.csv)、[源码快照](../../data/results/root_writeback_rtl_20261028/source_snapshot.tar.gz)。完整 Trace、综合日志和 VCD 由上述脚本在 `dump/` 重建。
