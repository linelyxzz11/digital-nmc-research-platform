# 测试

在仓库根目录运行：

```bash
python -m pytest -q -p no:cacheprovider
python -m pytest -q -p no:cacheprovider src/tests/test_demo0_functional.py
python -m pytest -q -p no:cacheprovider src/tests/test_communication_v0_1.py
python -m pytest -q -p no:cacheprovider src/tests/test_nmc_demo1.py
python -m pytest -q -p no:cacheprovider src/tests/test_nmc_demo2.py
python -m pytest -q -p no:cacheprovider src/tests/test_mapper_v0_1.py
python -m pytest -q -p no:cacheprovider src/tests/test_plan_runner.py
python -m pytest -q -p no:cacheprovider src/tests/test_mapper_sweep.py
python -m pytest -q -p no:cacheprovider src/tests/test_compute_timing.py src/tests/test_tile_timing.py
python -m pytest -q -p no:cacheprovider src/tests/test_tile_rtl.py
python -m pytest -q -p no:cacheprovider src/tests/test_timed_tile_system.py
python -m pytest -q -p no:cacheprovider src/tests/test_timed_root.py
```

2026-10-28 当前共 312 项 pytest 回归。Compute 公式参考时序 17 项、逐请求 Tile/Bank 时序 28 项、Tile 系统接入 31 项；覆盖完整基线 Trace、全部 24 个形状、Root/Host 放置、反压、端口敏感性、配置校验和 CLI。有限 Root 共 46 项，覆盖手算阶段周期、包头槽预留、标量/向量服务、Done 保持、原子错误、到达顺序/回绕、缓冲满后排空、系统回压、写端口保持及 CLI。其他回归包括 Compute Demo 0、Packet/Comm、NMC Demo 1/2、Mapper、Plan Runner/Sweep 与硬件配置。单 Tile RTL 回归为一个 pytest 入口，内部运行 47 个用例/53 段任务执行；独立扩大运行使用 71 个用例/77 段执行，并检查 SRAM 综合结构。新增 Root 宽写口 RTL 入口，内部验证 218 个请求与 51 组 Python/RTL 时间线，含 Host/Compute 争用、错误原子性和复位保持。Root 其余端点控制及多 Tile NMC RTL 尚待实现。

Mapper 回归将生成计划实例化为现有 TileTask、MEM_WRITE 和 GatherConfig，与 Demo 1/2 对照任务、预加载字节和 ACK 集合，再通过现有功能模型与 Root 端点核对数值和完成次数；同时检查一阶代价 `cost_score` 的组成与 auto 选择。Plan Runner 回归把 JSON 计划交给真实调度链执行，要求结果、事件 Trace、ACK 集合和摘要与硬编码 Demo 完全一致，并覆盖 CLI 往返、错误分区入口和 30 余种篡改计划的拒绝。Plan Sweep 回归覆盖候选枚举、形状预设、大 K 采样切分、不可行候选记录、确定性、排序、一阶代价校准、CSV 写出和 JSON/CSV CLI；它仍不代表完整周期模型或 RTL 等价。

64 Tile v0.2 后续新增 35 项，项目共 347 项：实例隔离、64-bit 来源完成 mask、新旧 2×2 周期/任务一致性、ROW/K/二维分块、有限多层归约与反压、错误来源原子拒绝、plan 篡改检查、三机分片的续跑/完整性/版本检查。新增 Mapper→RTL 入口将三个新 leaf 任务经 ID 重映射送入既有 Tile RTL，逐周期与 SRAM 对照；不声称实现完整 64 Tile RTL。
