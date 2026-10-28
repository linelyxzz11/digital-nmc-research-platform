# 数据

- `workloads/`：GEMV 输入；`demo1_gemv.json` 与 Demo 1/2 使用的 4×4 工作负载一致。
- `results/`：精选可复现结果；`mapper_sweep_demo.*` 是 4×4 Demo 的放置对比，`mapper_sweep_suite.*` 是 24 个形状的 ROW/K 对比；`early_feasibility/` 仅是早期单次冒烟测量。
- `results/tile_timing_20261028/`：单 Tile SRAM/Bank/MAC 逐请求验证，301 个用例；包括原始 JSON、5×3 手算 Trace 和源码快照。采用预加载 SRAM，属于 Python 时序模型验证。
- `results/tile_rtl_20261028/`：单 Tile RTL 的 71 个用例/77 段执行、综合存储检查与手算/反压 CSV；完整波形保存在 `dump/rtl_tile_20261028/`，通过脚本重建。
- `results/timed_tile_system_20261028/`：详细 Tile/2×2 系统接入，五组 480 条记录（430 次成功、50 次容量不可行），含 86 次可行基线的全部网络指标一致性检查；open/eighth 固定相位 0，Root 仍为功能归约。
- `results/finite_root_system_20261028/`：有限 Root 的五组实际服务对照，240 条记录（215 次成功、25 次不可行），含标量/向量手算 Trace、CSV、压缩原始 JSON 和源码快照；无人工接收压力窗口。
- `results/root_writeback_rtl_20261028/`：Root 128-bit 写回的连续吞吐、接口争用/复位及 51 组 Python/RTL 逐周期校准，含精简 Trace、单 Tile 存储综合结构与对应源码快照；Root 部分和和加法控制仍为 Python。
- `results/mapping_headroom_20261028/`：现有固定放置/均衡 ROW/K 的收益上限重分析，19 项共同比较与全部 24 项可行覆盖。
- `results/mapping_candidates_20261028/`：扩大放置/采样 K 切分后 1,680 候选，1,188 次正确执行、492 次预期不可行；压缩完整记录、CSV、汇总和源码快照。
- `results/unindp_native_20261028/`：固定作者版本/依赖的三个原生 AiM GEMV 对照，只表示 UniNDP 自身相对其 baseline 的结果，不表示本项目相对 UniNDP 的加速比。
- `results/scalable64_20261028/`：8×8、64 Tile 的 112 项主实验，三分片合并及源码快照，含 56 组集中/树归约配对和单独的小任务边界探针。全部记录以 `records.json.gz` 发布；源码版本/模型范围和物理预算见 [v0.2 合同](../docs/spec/scalable_execution_v0.2.md)。
- [results/mapping_search_20261028/](results/mapping_search_20261028/README.md)：同硬件方法适配与自动 mapper，16 形状、448 项尝试/440 项可行、四种精确评估预算，保留全部退化、预测排序、作者方法核对和图形数据。
- [results/mesh_rtl_20261028/](results/mesh_rtl_20261028/README.md)：九项真实 64 Tile RTL 闭环与独立完整归约端点的逐边沿验证，真实包/SRAM/周期记录，输入/NI 模型差异明确列出。
- [results/prediction_v04_20261028/](results/prediction_v04_20261028/README.md)：输入 ACK/有限反压预测修复；十六旧形状开发回放、八个预先固定的新形状验证，记录首选质量、分阶段误差和包含包级推演的真实搜索成本。

Root 压力与 Compute 时序的较大原始 JSON 以 `results.json.gz` 发布，内容不变，可用 `gzip -dk` 解压；对应 CSV、汇总和源码快照一并保留。仿真脚本仍生成普通 JSON。例如读取后直接分析：

```python
import gzip, json
from pathlib import Path
result = json.loads(gzip.decompress(Path("data/results/root_pressure_20261027/results.json.gz").read_bytes()))
```

批量 trace、波形和临时日志放根目录 `dump/`。论文 PDF 在 `docs/papers/`，不混入 Benchmark 数据。
