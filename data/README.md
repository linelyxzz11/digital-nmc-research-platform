# 数据

- `workloads/`：GEMV 输入；`demo1_gemv.json` 与 Demo 1/2 使用的 4×4 工作负载一致。
- `results/`：精选可复现结果；`mapper_sweep_demo.*` 是 4×4 Demo 的放置对比，`mapper_sweep_suite.*` 是 24 个形状的 ROW/K 对比；`early_feasibility/` 仅是早期单次冒烟测量。
- `results/tile_timing_20261028/`：单 Tile SRAM/Bank/MAC 逐请求验证，301 个用例；包括原始 JSON、5×3 手算 Trace 和源码快照。采用预加载 SRAM，属于 Python 时序模型验证。
- `results/tile_rtl_20261028/`：单 Tile RTL 的 71 个用例/77 段执行、综合存储检查与手算/反压 CSV；完整波形保存在 `dump/rtl_tile_20261028/`，通过脚本重建。

Root 压力与 Compute 时序的较大原始 JSON 以 `results.json.gz` 发布，内容不变，可用 `gzip -dk` 解压；对应 CSV、汇总和源码快照一并保留。仿真脚本仍生成普通 JSON。例如读取后直接分析：

```python
import gzip, json
from pathlib import Path
result = json.loads(gzip.decompress(Path("data/results/root_pressure_20261027/results.json.gz").read_bytes()))
```

批量 trace、波形和临时日志放根目录 `dump/`。论文 PDF 在 `docs/papers/`，不混入 Benchmark 数据。
