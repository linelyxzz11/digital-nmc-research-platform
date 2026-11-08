# 二维切分多最终 Root 执行合同 v0.1

## 1. 范围

本合同支持central二维GEMV切分使用1、2、4个最终Root。二维Worker已经同时绑定一个ROW范围和一个K范围，因此每个Worker保持一个单目的地任务；Mapper只把完整ROW分区组分配给不同Root。

所有方案使用相同8×8/64 Tile、相同二维网格、相同Worker、相同MAC总数及相同Tile/Root/NoC参数。当前不支持tree二维多Root、相关自动预测、完整RTL或4096×4096。

## 2. 计划与映射

二维多Root计划使用v0.5：

```text
v0.2 单Root
v0.3 ROW多Root
v0.4 纯K多Root任务队列
v0.5 二维多Root
```

对于 `grid=(row_parts,k_parts)`，要求两维均大于1、规约为central且Root数量不超过ROW分区数。Mapper按现有ROW边界把连续ROW分区尽量均衡地交给Root。

例如4×2网格、2 Root：

```text
ROW 0的两个K Worker ┐
ROW 1的两个K Worker ┴→ Root A
ROW 2的两个K Worker ┐
ROW 3的两个K Worker ┴→ Root B
```

`root_ranges` 为每个ROW分区保留精确K Worker来源mask；Root逐行检查来源。每个Worker只有一个 `TileTask` 和一个目的Root：

```text
subtasks_per_worker = 1
task_switch_cycles = 0
useful_mac_ops = M*K
```

## 3. 执行与完成

Worker发送PSUM，Root只规约并写回自己负责的输出窗口。每个Root完成后分别发送Done；Host收齐后只产生一次全局完成。系统还要等待所有Worker完成、全部Root空闲且Mesh排空，再从各Root本地SRAM拼接完整y并与CPU参考比较。

未适配多Root的预测器收到v0.5时会明确拒绝：

```text
2D multi-Root plans are not supported by this predictor
```

## 4. 公平实验

Root集合：

```text
1 Root : (0,)
2 Roots: (0,63)
4 Roots: (0,7,56,63)
```

工作负载：

- M=128、K=64、grid=4×4；
- M=256、K=64、grid=8×2；
- M=256、K=128、grid=8×4；
- compact/spread；
- baseline/pressure；
- 1/2/4 Root。

共36组。所有Root数量冻结同一Worker集合；未启用候选Root保持空闲。pressure配置为1包Root接收队列、每周期1 lane、写回2周期，仅用于敏感性分析。

## 5. 指标

- 完整任务周期和相对1 Root加速比；
- 输入、计算/规约、完成排空阶段周期；
- flit-hop、最忙链路、FIFO与端口阻塞；
- 每Root输出行、结果包、服务、写回和完成时间；
- Root负载不均衡；
- Root Done数量及唯一全局完成；
- `task_switch_cycles==0`；
- `useful_mac_ops==M*K`。

## 6. 本地实验结果（2026-11-01）

36/36案例数值、MAC、Done和零任务切换均通过。

| Root服务 | Root数 | 平均周期 | 平均加速 | 最小–最大加速 |
|---|---:|---:|---:|---:|
| baseline | 1 | 3624.67 | 1.000× | 1.000–1.000× |
| baseline | 2 | 3186.50 | 1.153× | 1.122–1.214× |
| baseline | 4 | 2993.50 | 1.239× | 1.156–1.368× |
| pressure | 1 | 5534.00 | 1.000× | 1.000–1.000× |
| pressure | 2 | 4141.17 | 1.362× | 1.300–1.462× |
| pressure | 4 | 3438.17 | 1.674× | 1.531–1.909× |

最佳案例为 `M=128,K=64,grid=4×4,compact,pressure,4 Roots`，周期1673，加速1.9091×。最弱的多Root案例仍为1.1216×。

二维收益位于ROW和纯K之间：ROW没有部分和规约，所以默认多Root收益很小；纯K让所有K Worker为完整M输出集中到Root，瓶颈最强；二维只在各ROW分区内汇聚K Worker，因此存在可分散的规约压力，但集中程度低于纯K。该结论只适用于本轮Python周期模型实验。

原始结果位于 `dump/multi_root_2d_20261101/`，不随Git发布。

## 7. 复现

```bash
python tools/experiments/multi_root_2d.py --output-dir dump/multi_root_2d_20261101
python -m pytest -q -p no:cacheprovider src/tests/test_multi_root_2d.py
```

配置入口为 `ini/scalable/multi_root_2d.json`。
