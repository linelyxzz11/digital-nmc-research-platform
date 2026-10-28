# 同硬件映射搜索 v0.3

## 比较对象与合同

所有策略把 `W[M,K] × x[K]` 转成同一种 `GridPlan`，执行同一个 `run_grid`。物理硬件固定为 8×8 Mesh、每 Tile 四 Lane MAC、4/1/1 KiB W/X/Y SRAM、每输入四 Flit Router FIFO、四 Flit NI，以及相同的有限归约端点。权重常驻，计时包含输入 MEM_WRITE/ACK、计算/归约和完成排空。

候选沿用七种逻辑网格：`(1,16),(16,1),(4,4),(1,32),(32,1),(4,8),(8,4)`，各自配 compact/spread 和 central/tree，共尝试 28 项/形状。逻辑网格不等于物理 Mesh；活跃计算 Tile 为 16 或 32。不可行映射保留原因，性能只对共同目录中的可行项评价。纯 ROW 的 central/tree 是退化对照，不视为不同有效算法。

`catalogue()` 固定候选范围；`search()` 接收矩阵、向量、策略及 `exact_budget`，先预测排序，再对前 K 项精确执行，返回最佳计划、每次精确评估和计时。结果标签不传入预测器。固定 K/ROW 也能在其轴内选放置和归约，避免弱化基线。

## UniNDP 移植的范围

依据：[作者论文 VI 节](https://nicsefc.ee.tsinghua.edu.cn/nics_file/pdf/d356f1fd-6204-4b21-8849-a9c3b2e15065.pdf)、[作者固定提交](https://github.com/godfather991/UniNDP/tree/c8b21684358441d29fc81fa0866d07cec20582ed)。本项目策略名为 **`unindp-adapted`，UniNDP 方法适配**，不叫作者完整工具或其原生 DRAM predictor 的复现。

| 决策 | 保留或适配 | 验证方法 |
|---|---|---|
| 计算维度划分 | 作者 GEMV `(1,K,M,1)`，平坦单 Device、16/32 PU、power-of-two 划分 | 直接执行固定提交 `Partition.get_partition_space_mm` 并核对集合 |
| 资源利用剪枝 | `choose_from_partition_space_mm` 的满利用/饱和规则；额外去除本接口不允许的空 shard | 核对每个保留网格、PU 数和轴转换 |
| 逻辑资源分配 | `Mapping.assign_hw` 的连续逻辑 PU 编号 | 核对编号列表，再映射到公共物理放置目录 |
| Tile 内存布局 | 适配本项目 INT8、四 Lane 和字节地址，沿用冻结的打包方式 | 原 Tile 功能/RTL 回归、完整系统数值检查 |
| 快速筛选与精确选择 | 保留 predictor → top-K → 共用精确 evaluator 的两阶段搜索 | 检查排序先于标签、精确评估次数与预算 |
| 快速 predictor | 用 SRAM 命令/服务代价替换 DRAM 时序项；忽略 Mesh 物理距离和链路争用 | 放置不变性测试；另报排序质量，不报告未校准的 MAPE |
| DRAM 层级、row miss、原生 Codegen/scheduler | 不移植；与本 SRAM/XY Mesh 硬件不一致 | 原生作者实验另存，不能混用 raw cycles |

适配的分块/分配决策已对八个形状逐项核对作者实际方法，见结果目录中的 `author_parity.json`。没有把作者源码副本、DRAM 后端或其预测精度归于本项目。公平性的结论范围是“该方法适配到本硬件后的比较”，不能外推为击败 UniNDP 论文实现。

## 通信感知策略 `noc`

预测器不读取候选实测周期，也没有从这 16 个形状拟合参数：

1. 用零值独立 Tile 端口模型得到每组结果的释放时刻，保留 SRAM/Bank/缓冲参数；不同策略计时时清空同一缓存。
2. 按实际 stop-and-wait 输入单播顺序计入往返距离；复制输入按真实发送次数计算。
3. 将结果分解为四 INT32/包，按释放时刻对有向 XY 链路作两 Flit 资源预留，共用链路串行服务，目的地本地口也是资源。
4. 各端点串行执行 DISPATCH、READ/ADD/UPDATE、完成字写回和 PACKET_DONE；根据实际来源覆盖决定上层树节点的下一包。
5. 按预测周期排序，只精确模拟前 K 项。

它是**快速列表调度估计**：链路预留可解释路径共享，但没有模拟有限 FIFO/credit 的逐 Flit 反压和完整 wormhole 重叠。因此它可以误排，不等于另一个周期精确模拟器，也不是映射全局最优算法。最终延迟由相同的有限资源模拟器测量。

## 评估与复现

原有四个形状作为 calibration 对照，新增十二个非整除/容量边界形状作为 holdout；未进行数据拟合，分组用于防止只报告旧四例。随机种子固定为 20261028，每形状派生独立 INT8 数据；数据值的变化用于数值检查，不当作独立性能样本。

预算为 1/2/4/8 次精确模拟。每个策略先独立运行前八项，再用前缀计算预算曲线；策略执行后才补完候选目录的事后参考。报告 regret、最优命中、Top-K 最优召回、Spearman、逐形状延迟、最坏退化和搜索耗时。wall time 是本机单次观察，包含冷预测缓存；不据此宣称统计显著的编译加速。

```bash
python tools/experiments/verify_unindp_port.py --author-root lib/UniNDP \
  --output dump/mapping/author_parity.json
python tools/experiments/mapping_evaluation.py run --output-dir dump/mapping/cases
python tools/experiments/mapping_evaluation.py merge --inputs dump/mapping/cases \
  --output-dir dump/mapping/merged
```

作者源码只用于第一条方法核对；从 GitHub 拉取本仓库后可以直接执行后两条，模型实验只依赖 Python 标准库。需要多机时，各机器加 `--shards 3 --shard-index 0/1/2`，输出互不重叠的**完整形状**，收齐目录后用相同 merge。它们不通过网络同步仿真时钟。

单个策略也可直接调用：

```python
from compiler.mapping_search import search
result = search(len(W), len(x), W, x, policy='noc', exact_budget=4)
plan, cycles = result.plan, result.cycles
```

保存原始 case JSON、源码哈希、配置、随机种子、精简 CSV 和图形脚本。最优参考仅覆盖这份目录，不能写成整个设计空间的最优。
