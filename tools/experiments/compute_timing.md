# Compute/SRAM 时序敏感性实验

日期：2026-10-27。目的：检验 ROW/K 的优劣是否依赖此前“Compute 瞬时完成”的假设。当前为参考时序模型，端点归约仍为功能操作，不是完整硬件性能结果。

## 模型与实现

[compute_timing.py](../../src/simulator/system/compute_timing.py) 逐周期执行 INIT、MAC、结果写回、ResultBeat 握手和 LocalDone。沿用现有 TileTask、SRAM 布局、INT8/INT32 wrap 语义和 NI；通过 `run_plan(..., compute_timing=ComputeTiming())` 接入原有 Host/NI/Mesh 调度循环。默认调用仍使用原功能模型。

基准时序直接来自 Compute v0.1 第 9.1 节：独立 Weight/Input SRAM 读端口，同步读延迟 1 周期；流水填充后每周期完成一个 k 的 4-Lane MAC；每个 row group 单独初始化和写回；结果被 NI 接受前不启动下一组。

```text
G = ceil(M_local / 4)
T_local_no_stall = G * (K_local + 4) + 1
```

不计命令握手周期。5×3 手算 Trace 共 15 周期；在周期 5 首次写 SRAM，周期 6/13 输出 ResultBeat，周期 14 发出 LocalDone。测试验证实际数值和写入可见性；不会在任务启动时预计算并提前写入所有结果。

扩展参数是显式假设：读返回延迟 `L`，Weight/Input 请求启动间隔、MAC 间隔，Result SRAM 写回周期 `W`。定义 `II = max(weight_read_interval, input_read_interval, mac_interval)`，则：

```text
T_group = 1 + L + 1 + (K_local - 1) * II + W + 1
```

每项分别是初始化、读流水填充、首次 MAC、后续 MAC 间隔、写回、握手。端口启动间隔建模服务速率，不是多 Bank 地址冲突模型；`banks_per_tile=1` 的资源元数据不应被解释为三块 SRAM 共用单个物理端口。这里采用规范中 Weight/Input 独立端口的组织。

两端 Compute 与 NoC 在同一模拟时钟上推进，可边计算后续组边传输前组结果；不能把各模块总周期简单相加。Result FIFO/NI 反压会冻结当前结果并延迟下一组。数值读取发生在模拟的 MAC/读返回事件；计算阶段 SRAM 内容不可被外部修改，因此与此前的读请求采样数值等价。

## 对照矩阵

固定 workers=(1,2)、Root=3，使用已有 suite 的全部 24 种形状，不根据结果删选工作负载。

| 模型 | 计算/存储服务 |
|---|---|
| functional | 原始瞬时计算 + NoC 周期模型 |
| reference | 规范中的每周期 4-Lane MAC，SRAM 读延迟 1 |
| weight-ii2 | reference 基础上，Weight 读端口每两周期接受一组请求；读延迟仍为 1 |

每个模型比较 ROW/K，运行 open、half、quarter、eighth 和 burst64 接收压力，遍历周期窗口全部相位。压力含义见 [Root 实验](pressure_sweep.md)。模型之间是时序假设敏感性对照；只在同一模型、同一接收条件内比较映射策略。

```bash
for model in functional reference weight-ii2; do
  PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src .venv/bin/python -B \
    tools/experiments/pressure_sweep.py --shapes suite --placements default \
    --phase-offsets 0,1,2,3,4,5,6,7 --compute-model "$model" \
    --output-dir "data/results/compute_timing_20261027/$model"
done

PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -B \
  tools/experiments/summarize_compute_timing.py data/results/compute_timing_20261027 \
  --output data/results/compute_timing_20261027/summary.json
```

每个模型 768 条记录：688 个执行通过、80 个容量/形状不可行、0 超时、0 数值或完成语义错误。三组共 2,304 条记录、2,064 个成功执行；80 个不可行记录实际来自 5 种只有一个合法候选的形状在 16 个压力/相位条件下重复，不是 80 个独立形状。

项目回归 205 项通过。原瞬时模型行为保持兼容；新增测试覆盖手算 Trace、未到写回时不得改写 SRAM、尾部 Lane、随机带符号数、非默认结果基址、结果/LocalDone 反压、端口时序变体及多 Tile 数值/ACK/流量一致性。

## 按形状计数的结果

排除 5 种被容量/形状强制选择的情况，分母固定为两种策略都合法的 19 种形状。每种形状只计一次，胜者必须在该压力全部相位下均获胜。本次没有相位依赖或平局形状。

| 模型 | open：K / ROW 胜出数 | half | quarter | eighth |
|---|---:|---:|---:|---:|
| functional | 14 / 5 | 12 / 7 | 11 / 8 | 9 / 10 |
| reference | 14 / 5 | 14 / 5 | 14 / 5 | 13 / 6 |
| weight-ii2 | 15 / 4 | 15 / 4 | 15 / 4 | 15 / 4 |

因此，补入参考 Compute/SRAM 时序后，K 在这套固定 suite 中多数情况下仍更快。此前瞬时计算使全部结果短时间内集中注入网络，会放大接收压力的作用；结果持续产生后，部分拥塞和排名反转消失。这是模型精化改变结论的证据，不能把旧模型反转次数当作最终收益。

## 具体正、负案例

下表都是 reference 模型，周期包含输入加载、计算/结果收集与完成排空；权重加载和真实归约时序仍未计入。范围是全部接收相位的最小/最大值，不是置信区间。

| 形状 | 接收条件 | ROW 模型周期 | K 模型周期 | 结论 |
|---|---|---:|---:|---|
| 32×64 | open | 505 | 409 | K 更快 |
| 32×64 | eighth | 526–533 | 430–437 | K 仍更快，旧模型的反转消失 |
| 64×32 | open | 409 | 385 | K 更快 |
| 64×32 | eighth | 430–437 | 586–593 | ROW 更快，反转仍存在 |
| 128×16 | open | 385 | 421 | ROW 小幅更快 |
| 128×16 | eighth | 586–593 | 1062–1069 | ROW 明显更快 |
| 128×64 | eighth | 1342–1349 | 1294–1301 | K 仍更快，旧模型的反转消失 |

例如 32×64、eighth、相位 0：ROW/K 的输入加载分别为 224/112 周期，计算与收集分别为 305/321 周期，DONE 排空均为 4 周期。K 的输入优势仍超过更长的结果处理阶段；两个策略的 FIFO 满和 NI 注入 stall 都为 0，虽然 Root Local 输出存在接收等待。这说明有接收等待并不必然形成传播到源端的拥塞。

64×32、eighth、相位 0：K 的 FIFO 满计数为 1,216 输入 FIFO·周期，NI 注入阻塞 321 Tile·周期，Compute 结果反压 22 Tile·周期；ROW 对应均为 0，且模型总周期 437 对 589。它适合保留为进一步研究拥塞的案例，但仍不能单凭此结果断言 FIFO 深度或真实归约硬件是独立原因。

## 解释边界与下一步

- 不能把胜出次数解释为现实应用概率：suite 是确定性形状集合，Root 压力是合成服务窗口。
- “ROW 占优时优势总很小”不成立；在偏输出维度且接收受限的形状中，ROW 可以明显更快。
- K 的优势可能来自输入分发更少、Lane 利用和本地 K 缩短，必须结合阶段时间与每 Tile 统计解释。
- Weight 服务变慢后反转进一步减少，说明源端结果产生速率与接收能力需要联合考虑；不是权重端口变慢提升了系统性能。
- 仍缺：多 Bank 冲突/仲裁、真实归约吞吐与缓存、完整有限 NI 接收队列、权重加载、优化输入广播、RTL 校准。现阶段使用 `modeled_schedule_cycles`，不称为已验证的完整端到端性能。
- 下一项高优先级对照应补有限端点归约服务，并把输入逐包 ACK 与更合理的输入分发方式分开研究，保留 ROW/K 的固定硬件公平比较。暂不通过只挑拥塞形状来保证某策略获胜。

原先 2,304 案例压力实验的旧源码已保存为 [source_snapshot.tar.gz](../../data/results/root_pressure_20261027/source_snapshot.tar.gz)，可与其 JSON 哈希对应。新三组 JSON 同样记录实际源码哈希、运行参数和 Git 状态。

## 2026-10-28：先验证单 Tile 端口时序

本轮将验证边界收回到单个 Tile，补充逐请求 SRAM/Bank 模型，作为上述公式模型的独立细化。端口合同见 [Compute 9.2](../../docs/spec/compute_architecture_v0.1.md#92-单-tile-端口与-bank-参考组织2026-10-28-补充)，代码为 [tile_memory.py](../../src/simulator/system/tile_memory.py) 和 [tile_timing.py](../../src/simulator/system/tile_timing.py)。

固定基线：Weight 单 Bank/32-bit 读，Input 单 Bank/8-bit 读，两者独立、同步读延迟 1、每周期接受一个请求；Result 四个 32-bit Bank，支持所有合法 Y_base 的连续四结果单周期写回。两个操作数对槽位、一周期 4-Lane MAC 更新、一组结果缓冲，不重叠 row group。预加载和外部读回不计时。

新模型实际发出带地址与 `(group,k)` 标记的读请求；数据在请求获准时采样，经过延迟返回后才执行 MAC。逐 Bank 轮询仲裁决定获准与等待；Result 写入也受端口数限制，不再通过预先计算组周期来安排写回。每周期记录请求、返回、Bank、MAC、累加器和握手。

12 套配置 × 12 种形状 × 2 个固定随机种子，共 288 个随机用例；另加每套配置的手算 5×3 Trace 与一例结果/Done 反压，共 **301 个用例，全部通过**。包括尾部 Lane、有符号极值数据、Weight/Input 容量边界 `4×1024` 与 Result 容量边界 `256×4`。项目 pytest 共 **233 项通过**，其中逐请求 Tile 测试 28 项。默认端口模型与原 `TimedCompute` 在测试中逐周期对照输出信号和 Result SRAM，验证基线兼容。

同一个 5×3 手算例子的对照如下。除注明项目外均采用基线参数，周期包含 LocalDone，冲突/繁忙列单位为请求·周期：

| 端口/计算配置 | 任务周期 | Bank 竞争等待 | 端口繁忙等待 |
|---|---:|---:|---:|
| 独立端口基线 | 15 | 0 | 0 |
| Weight 每两周期接受一次请求 | 19 | 0 | 4 |
| Input 每两周期接受一次请求 | 19 | 0 | 4 |
| 两种读延迟均为 3，4 个操作数对槽位 | 19 | 0 | 0 |
| 两种读延迟均为 3，2 个操作数对槽位 | 23 | 0 | 0 |
| 只有 1 个操作数对槽位 | 19 | 0 | 0 |
| Result 改为一个 32-bit Bank | 18 | 6 | 0 |
| Result 改为两个 32-bit Bank | 16 | 2 | 0 |
| MAC 更新间隔改为两周期 | 19 | 0 | 0 |
| Weight/Input 共享一个 32-bit Bank | 21 | 10 | 0 |
| Weight/Input 共享两个 32-bit Bank | 18 | 3 | 0 |
| Weight/Input 共享四个 32-bit Bank | 17 | 2 | 0 |

基线首组时序为 `INIT@0 → W/X 请求@1 → MAC@2,3,4 → 写回@5 → ResultBeat@6`。第二组 ResultBeat 在 13、LocalDone 在 14。Result 变成单 Bank 时四个有效 Lane 在 5/6/7/8 分别写入，首组只能在 9 发出；等待计数是 `3+2+1=6`，任务仅多 3 周期，二者不可混淆。

读延迟为 3、4 槽位时首组 MAC 在 4/5/6 连续执行，说明读延迟不等于请求启动间隔；保持 2 槽位时变为 4/5/8，新增空泡来自缓冲容量。共享四 Bank 仍不等于独立端口：本例部分 W/X 地址落到同一 Bank，所以仍有冲突。

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -B -m pytest -q -p no:cacheprovider src/tests/test_tile_timing.py
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -B tools/experiments/tile_timing.py \
  --output-dir data/results/tile_timing_20261028
```

[原始结果](../../data/results/tile_timing_20261028/results.json)、[逐周期 Trace](../../data/results/tile_timing_20261028/hand_traces.json)、[源码快照](../../data/results/tile_timing_20261028/source_snapshot.tar.gz) 已保存。JSON 记录完整端口配置、Git 状态和源码 SHA256；Trace 中 byte 串按十六进制输出。

这轮明确了一个可实现的内部端口合同及其 Python 时序验证；尚未完成 SRAM 宏选择、外部加载控制器或 Tile RTL 等价。上述共享 Bank 对照属于不同端口组织，不能作为同一硬件下 ROW/K 的策略收益。后续应先让 Tile RTL 重现基线手算 Trace，再接入系统并补归约端点的真实服务时间。

同日后续：[单 Tile RTL](../../src/rtl/nmc_tile/README.md) 已实现固定端口基线、外部加载和错误/复位控制，并通过 71 个用例/77 段执行的逐周期对照；源码、精简结果和复现命令随该说明发布。具体 SRAM 宏选择和完整系统服务时间仍待验证。
