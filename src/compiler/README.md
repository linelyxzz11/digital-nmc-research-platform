# Mapper v0.1

为现有 Demo 1/2 生成 JSON 执行计划。脚本只依赖 Python 3.10+ 标准库；项目完整回归仍使用根目录 requirements.txt。

## 范围与状态

- 固定 2×2 Mesh、Host=0、两个非 Host worker，每 worker 4 路 MAC。
- 每 worker 一个 TileTask；Root 与 worker 不重合，Root 可以是 Host。
- ROW 使用 `ceil(M/2)` 连续行切分；K 默认使用 `ceil(K/2)`，可指定 `k_split`。
- auto 在默认 ROW/K 候选间按 `cost_score = 每 Tile MAC/4 周期 + 输入写/ACK/结果包/DONE 包数` 选择，平局取 ROW；这是一阶代理，不预测真实周期。
- 权重按现有 (group,k,lane) 布局预加载，输入通过 MEM_WRITE/ACK 分发。
- 计划字段通过现有模型与 Demo 对照测试；Demo CLI 通过 `--plan` 直接读取并执行该 JSON 计划。
- 执行阶段会重新校验计划：固定硬件参数、切分覆盖、TileTask 字段、权重镜像长度、输入写覆盖与 ACK 身份；非法计划在启动仿真前被拒绝。
- 完整 Bank 时序、带宽代价、多任务 tiling、更多 worker、自动放置、二维切分和树形归约留待后续版本。

## 运行

从仓库根目录执行：

```bash
PYTHONPATH=src python -m compiler.auto_mapper_v0_1 --demo --strategy auto -o dump/mapper_plan.json
PYTHONPATH=src python -m compiler.auto_mapper_v0_1 --demo --strategy k --k-split 1
PYTHONPATH=src python -m compiler.auto_mapper_v0_1 --self-test
python -m pytest -q -p no:cacheprovider src/tests/test_mapper_v0_1.py

# 计划由 Demo CLI 直接执行；--workload 可选，用于 golden 校验
PYTHONPATH=src python -m simulator.system.demo1 --plan dump/mapper_plan.json --workload data/workloads/demo1_gemv.json
PYTHONPATH=src python -m compiler.auto_mapper_v0_1 --workload data/workloads/demo1_gemv.json --strategy k -o dump/mapper_plan_k.json
PYTHONPATH=src python -m simulator.system.demo2 --plan dump/mapper_plan_k.json --workload data/workloads/demo1_gemv.json
python -m pytest -q -p no:cacheprovider src/tests/test_plan_runner.py
```

也可直接运行 `python src/compiler/auto_mapper_v0_1.py --demo`，无需设置 PYTHONPATH。

工作负载 JSON：

```json
{"W": [[1, 2], [3, 4]], "x": [5, 6]}
```

```bash
PYTHONPATH=src python -m compiler.auto_mapper_v0_1 --workload /path/to/workload.json --strategy auto
```

作为模块调用：

```python
from compiler.auto_mapper_v0_1 import map_gemv

plan = map_gemv(W, x, strategy="auto", workers=(1, 2), root_tile=3)
```

## 计划与执行约定

- `tasks`：可实例化为现有 TileTask，不添加自定义执行字段。
- `tensor_slices`：原始 W 的行区间和 K 区间，均为左闭右开。
- `weight_images`：各 worker 的 32-bit little-endian 权重 word 镜像。
- `input_writes`：可实例化为现有 MemWritePacket，保留 word offset 优先的发送顺序。
- `gather_config`：可实例化为现有 GatherConfig；ROW 为 GATHER_FINAL，K 为 REDUCE_PSUM。
- `expected_ack_keys`：排序后的 `[dst_tile, tid, sequence]` 清单。
- `diagnostics`：各 Tile 容量占用、`mac_cycles`、`estimated_compute_cycles`、`estimated_packets` 和 `cost_score`；它们是一阶代理，不是完整 GEMV 周期。

先配置 Root 并预加载权重；Host 依次发送输入写请求并等待 ACK，收齐全部预期 ACK 后才启动任务。所有最终输出与所有来源 last 收齐后才能发送一次全局 DONE。LocalDone 不等于全局完成。

## 容量规则

对每个任务检查：

```text
1 <= m_local <= 256
1 <= k_local <= 1024
4 * ceil(m_local/4) * k_local <= 4096  # 含无效 Lane 填充
k_local <= 1024
4 * m_local <= 1024
```

Root 要保存 M 个最终输出，因此全局 M 也不超过 256。所有输出索引必须符合 16-bit 全局索引字段。默认本地基址为 W=0x0000、x=0x1000、y=0x2000。

规则来源：[Compute v0.1](../../docs/spec/compute_architecture_v0.1.md)、[Communication v0.1](../../docs/spec/communication_architecture_v0.1.md)。验证记录见 [Mapper 验证](../../docs/validation.md#mapper)。
