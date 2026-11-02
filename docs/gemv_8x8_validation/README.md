# GEMV 8×8 Mesh 仿真成果

本分支 `sim/gemv-8x8-validation` 汇总本人负责的 GEMV 数值/协议验证、映射比较、实验复现、参数敏感性、真实 RTL 周期对照和报告。共同基线为 `compiler/bounded-timing-predictor` 的 `f128f237a5393dbc1fadb8e47278e10d82d81f86`；团队共享分支未被修改。本分支不自动合入 main。

## 阅读顺序

1. [中文 Python 报告](python_validation.zh-CN.md)：第一轮，周期模型实验。
2. [中文 RTL 报告](rtl_validation.zh-CN.md) 与 [组会总结](meeting_summary.zh-CN.md)：第二轮，真实 Icarus/vvp 执行。
3. [统一复现步骤](reproduce.zh-CN.md)：从仓库相对路径运行，不依赖个人电脑目录。
4. [本次整合检查](integration_validation.zh-CN.md)：区分旧实测记录与新 worktree 实际检查。
5. [English overview](README.en.md)。

## 两轮实验与主要结果

| 证据 | 范围 | 结果 |
|---|---|---|
| timed Python evaluator | 112 项历史 Mesh 实验复跑、v0.3/v0.4 历史复现、新 12 形状与硬件点 | 新集合 336 attempted / 292 feasible / 44 infeasible；预算 1 v0.4 对强基线 2 胜/10 平/0 负，几何平均 1.018812× |
| integrated RTL | 原 9 例、四映射×两种子、2 个边界案例 | 9/9、8/8、2/2；原五项 RTL pytest 5 passed / 0 skipped（原执行记录） |
| RTL 161×97 | 32-worker baseline / 16-worker v0.4 | 总周期 2611 / 2375，减少 9.04%；kernel 298 / 354，v0.4 kernel 更慢 |
| RTL 251×127 | 同上 | 总周期 3380 / 3218，减少 4.79%；kernel 411 / 625，v0.4 kernel 更慢 |

Python 与 RTL 有不同输入服务：Python 功能提交，RTL 真实窄 X SRAM 写、注册 NI 与任务接受流水。没有把 Python 周期写成 RTL 周期。总周期收益来自输入路径节省超过 kernel 代价，不是“16-worker 计算更快”。搜索开销也单列，未宣称 v0.4 搜索更快。

## GEMV 如何在 Mesh 上执行

物理系统固定为 8×8、64 Tile，每 Tile 四 Lane INT8 MAC、INT32 回绕累加、4/1/1 KiB W/X/Y SRAM、四 Flit Router FIFO/NI 和有限归约端点。ROW/K/二维网格属于逻辑分块；compact/spread 决定物理摆放；central/tree 决定 Mesh 内端点归约，不改变物理拓扑。

RTL 中先预加载 W/配置，X 经真实 MEM_WRITE/ACK 进入 SRAM；同步启动各 leaf MAC，PSUM/FINAL 经 NI/XY Router 和归约树，Root 写回 SRAM并发送唯一 Done，随后等待 LocalDone/idle。16/32 是活跃 leaf 数，均保留完整 64 物理 Tile；树节点也占同一预算。生产 MAC/SRAM/Router/NI/Reducer RTL 未修改，只扩展 TB 观测与验证。

## 代码与数据

- Python：[运行器](../../tools/experiments/validation_campaign.py)、[审计/绘图](../../tools/experiments/summarize_validation_campaign.py)、[发布记录恢复](../../tools/experiments/review_validation_results.py)、[冻结协议](../../ini/experiments/validation_20261031/protocol_final.json)。
- RTL：[运行器](../../tools/rtl/run_mesh_rtl.py)、[Testbench](../../src/rtl/nmc_mesh/tb_nmc_mesh_system.sv)、[审计/绘图](../../tools/rtl/summarize_gemv_rtl.py)、[紧凑证据复核](../../tools/rtl/review_gemv_results.py)、[案例清单](../../ini/rtl_gemv_20261031.json)。
- [Python 数据与六组 PNG/SVG](../../data/results/validation_20261031/README.md)。
- [RTL 数据与四组 PNG/SVG](../../data/results/rtl_gemv_20261031/README.md)。
- [交付来源哈希](delivery_lineage.json) 对照两轮原始补丁；[逐文件整合清单](integration_files.json)。

完整 trace、波形、失败日志和本机交付 ZIP 留在提交者本地，可按案例名/Plan SHA/原始文件 SHA 索取；不上传大型波形、安装包、虚拟环境、作者 UniNDP 仓库或私人环境文件。仅重绘/恢复发布记录不称为重新执行仿真。

## 已验证与未验证边界

已验证数值、包字段/来源/尾 Lane/ACK/唯一 Done、队列上界、Tile ResultBeat/Router 输出反压保持、两组固定映射周期。额外拟议的 NI/Root 保持断言曾因自动审批超时未运行，没有纳入本分支通过项。

RTL 限 Root/Host=0、tid=1、默认硬件、单事务、M≤256；权重/配置预加载不计时，kernel 包含归约与完成。不是器件实测、完整芯片 PPA、任意 Root/多事务验证或 RTL 全目录最优证明。真实层形状实验只生成合成 INT8 数据，没有复现完整 MLPerf 模型或精度。课程原件/截图缺失部分保留待确认。
