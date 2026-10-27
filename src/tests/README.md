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
```

2026-10-28 当前共 234 项 pytest 回归，覆盖 Compute Demo 0、Packet/Comm、NMC Demo 1/2、Mapper、Plan Runner/Sweep、硬件配置及 Root 压力。Compute 公式参考时序 17 项，逐请求 Tile/Bank 时序 28 项；后者核对精确读请求/返回、写回可见性、轮询仲裁、有限缓冲、反压、偏移地址和尾部 Lane。单 Tile RTL 回归为一个 pytest 入口，内部运行 47 个用例/53 段任务执行，并在安装 Yosys 时检查单口 SRAM 的综合结构；本机完整独立运行扩大为 71 个用例/77 段任务执行。Demo 1 验证行切分/FINAL 收集；Demo 2 验证 K 切分/PSUM 归约、逐索引来源 mask、重复来源拒绝、INT32 wrap、到达顺序和反压。多 Tile NMC RTL 尚待实现。

Mapper 回归将生成计划实例化为现有 TileTask、MEM_WRITE 和 GatherConfig，与 Demo 1/2 对照任务、预加载字节和 ACK 集合，再通过现有功能模型与 Root 端点核对数值和完成次数；同时检查一阶代价 `cost_score` 的组成与 auto 选择。Plan Runner 回归把 JSON 计划交给真实调度链执行，要求结果、事件 Trace、ACK 集合和摘要与硬编码 Demo 完全一致，并覆盖 CLI 往返、错误分区入口和 30 余种篡改计划的拒绝。Plan Sweep 回归覆盖候选枚举、形状预设、大 K 采样切分、不可行候选记录、确定性、排序、一阶代价校准、CSV 写出和 JSON/CSV CLI；它仍不代表完整周期模型或 RTL 等价。
