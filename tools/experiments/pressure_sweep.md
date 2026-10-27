# Root 接收压力实验

日期：2026-10-27。入口：[pressure_sweep.py](pressure_sweep.py)。

目的：在相同硬件、工作负载、切分和放置下，改变 Root 接收 RESULT Flit 的服务窗口，检查通信调度、反压及候选排序的变化。计算和归约仍为功能模型；本实验不测完整 GEMV 时延、不模拟真实归约流水，也不隔离 FIFO 深度的因果效应。

## 复现

在项目根目录执行：

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src .venv/bin/python -B \
  tools/experiments/pressure_sweep.py \
  --phase-offsets 0,1,2,3,4,5,6,7 \
  --output-dir data/results/root_pressure_20261027

PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -B -m pytest -q \
  -p no:cacheprovider src/tests/test_pressure_sweep.py
```

结果：[压缩 JSON](../../data/results/root_pressure_20261027/results.json.gz)、[CSV](../../data/results/root_pressure_20261027/results.csv)。JSON 包含运行参数、硬件配置、Git HEAD、脏工作区状态和实际模型/实验代码的 SHA-256。采集时使用了未提交源码，不能仅靠其中记录的旧 Git HEAD 重建；公开版本同时保存了源码快照与压缩原始记录。可用 Python `gzip.decompress` 或 `gzip -dk` 解压。

## 实验控制

- 固定 2×2 Mesh、两个 worker、4-lane MAC、4-Flit Router/NI 注入 FIFO、XY 路由和原有 SRAM 容量。
- 预先选定六种形状：16×128、32×64、64×32、128×16、33×5、128×64。包含相反长宽比、非整 Lane 和较大结果流。
- 每个形状比较 ROW/K 的默认近似均分，枚举 12 种有序 worker/Root 放置；未搜索不均匀 K 切分。
- 权重预加载不计时。输入仍按原模型逐包等待 ACK，所有策略使用同一执行规则。
- 压力只作用于 COMPUTE_AND_GATHER 阶段的 Root RESULT 接收；LOAD/ACK、其他端点及结果完成后的 DONE 均保持可接收。Root 可以与 Host 重合。
- 接收窗口从该次执行的 TASK_START 开始计时，而非从全局 cycle=0 开始，避免不同输入加载时长引入压力起点偏差。
- 每个允许接收的周期最多接收一个 Flit，不是一个 Packet。Packet 重组和握手均使用现有 Mesh 模型。

| Profile | 服务规则 | 本次相位数 |
|---|---|---:|
| open | 每周期可接收 | 1 |
| half | 每 2 周期开放 1 周期 | 2 |
| quarter | 每 4 周期开放 1 周期 | 4 |
| eighth | 每 8 周期开放 1 周期 | 8 |
| burst64 | TASK_START 起关闭 64 周期，此后持续开放 | 1 |

共 6 × 12 × 2 × (1+2+4+8+1) = 2,304 个确定性案例。对 worker 顺序和服务窗口相位的枚举不等同于独立随机样本，不能据此报告统计置信区间。

## 指标定义

- `network_schedule_cycles`：沿用现有模型从输入加载开始到事务完成并排空网络的周期；不是完整 GEMV 延迟。
- `input_load_cycles`：TASK_START 周期。
- `result_gather_cycles`：TRANSACTION_DONE_CREATE 减 TASK_START；包含结果注入、路由和接收服务等待，不能称为纯归约计算耗时。
- `completion_drain_cycles`：网络结束减 TRANSACTION_DONE_CREATE。以上三个阶段之和等于通信调度总周期。
- `fifo_full_input_cycles`：对所有 Router 输入 FIFO 的“满”状态逐周期求和；单位为输入 FIFO·周期，可以超过总周期。
- `router_output_blocked_cycles`：所有有选中 Flit 但下游不 ready 的 Router 输出·周期，包含 Local 出口。
- `ni_inject_stall_tile_cycles`：所有 NI 因 Router Local 输入不能接收而阻塞的 Tile·周期。
- `resultbeat_stall_tile_cycles`：功能 Compute 的 ResultBeat 等待 NI 的 Tile·周期；不是真实 MAC 停顿测量。
- `root_scheduled_closed_cycles`：Root 被设为不 ready 的周期，包括没有有效 Flit 的周期；不是实际阻塞计数。
- `flit_hops`：只累计 Router 间传输。链路利用率以整个通信调度窗口为分母；平均值包含八条方向链路中的空闲链路。
- 数值输出、ACK 身份集合、全局 DONE 恰好一次及 FIFO 不溢出均需检查。

## 本次实测

全部 2,304 个案例通过，0 不可行、0 超时、0 验证失败。新增 13 项测试通过，项目全部 188 项测试通过。保存后核对了 JSON 的源码哈希与当前代码一致。

下面固定 workers=(1,2)、Root=3。区间是遍历该 Profile 全部相位得到的最小/最大值，不是置信区间。

| 形状 | 服务 | ROW 通信周期 | K 通信周期 | 较优 |
|---|---|---:|---:|---|
| 16×128 | open | 462 | 246 | K |
| 16×128 | eighth | 511–518 | 351–358 | K |
| 32×64 | open | 246 | 150 | K |
| 32×64 | eighth | 351–358 | 367–374 | ROW |
| 64×32 | open | 150 | 126 | K |
| 64×32 | half | 181–182 | 189–190 | ROW |
| 128×16 | open | 126 | 162 | ROW |
| 128×16 | eighth | 567–574 | 1051–1058 | ROW |
| 128×64 | open | 294 | 246 | K |
| 128×64 | half | 357–358 | 373–374 | ROW |

32×64 案例的解释：ROW/K 输入加载分别固定为 224/112 周期，结果 Packet 数分别为 8/16。K 的输入传输优势在慢速 Root 接收下被更多结果数据抵消；eighth 下结果收集分别为 123–130/251–258 周期。ROW/K 的 flit-hops 始终分别为 114/82，因此更少的 flit-hops 并不保证更短的通信调度。

该案例中 K 的 NI 注入阻塞从 open 的 4 Tile·周期增至 eighth 的 200–214 Tile·周期，FIFO 满计数从 38 增至 635–664 输入 FIFO·周期。硬件 FIFO 深度始终为 4。这能展示反压传播，但不能单独证明改变 FIFO 深度会改变排名。

在全部配对条件中，固定相同 worker/Root 的 ROW/K 严格排名反转共 336 条。另有 36 个“形状×压力×相位”条件下，所有 open 服务下并列最优候选均不再最优。原始配对记录在 JSON 的 `comparison` 中；比较保留全部并列最优方案，不任意挑一个作为基线。上述计数包含不同放置和相位，不是 336 个独立工作负载。

负结果也保留：默认放置下，16×128 在全部上述压力档位仍优先 K，128×16 和 33×5 仍优先 ROW；一次性关闭 64 周期也没有使这六种形状的默认放置 ROW/K 优劣反转。持续服务带宽与一次性停顿的效果不同。

## 可以与不能得出的结论

当前证据支持：在这个功能 Compute + 周期 NoC 模型中，Root 结果接收服务约束能改变某些映射方案的通信调度排序，并伴随可观测反压；这些案例的反转不是单个周期相位造成的。

尚未证明：真实 GEMV 端到端加速、真实归约计算瓶颈、FIFO 容量本身的独立效果、优于 UniNDP、4×4 扩展性、树形归约收益或 RTL 一致性。Root 降速来自明确的合成服务约束，不能宣称它是已实现的归约硬件。

下一步应把 Bank/MAC 与端点归约的真实服务时序加入执行模型，并保留本组案例作对照；只有加入这些时序后，才可检查上述排序在完整任务时间上是否仍成立。
