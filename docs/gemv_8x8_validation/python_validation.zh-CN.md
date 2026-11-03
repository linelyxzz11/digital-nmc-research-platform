> 历史实验报告的团队发布版：数值保留，本机路径已移除。请以 [统一复现入口](reproduce.zh-CN.md) 的仓库相对命令为准。

# 仿真验证与可复现报告

日期：2026-10-31（Asia/Shanghai）。本报告来自本机实际运行、历史记录完整性核查和明确标记的历史 RTL 记录。

## 研究问题与结果

1. 既有模型、数值、完成协议与分片合并是否可复现？完整回归最终 **381 passed、0 failed、5 skipped**；12 项 smoke、112 项 v0.2 主实验、16 形状 v0.3 和八形状 v0.4 均已重新执行，测得周期与历史记录逐候选一致。
2. v0.4 相对强基线的收益能否延伸到新集合？本轮预先冻结 12 个形状，尝试 336 个配置，292 个可行；预算 1 相对强基线 2 胜/10 平/0 负，几何平均 1.018812×，最坏 1.000000×。全部退化保留于 quality.csv。
3. 非默认硬件点是否延续默认预测精度？分别报告四种单变量变更；不外推历史默认点误差。搜索耗时只对 81×81 做一次预热、五次计时，并轮换顺序。

## 材料、版本和状态边界

正式 checkout：`<local-path-omitted>`。工作分支 `sim/validation-20261031`，基点 **f128f237a5393dbc1fadb8e47278e10d82d81f86**。改动尚未提交、推送或合并；基点 SHA 不代替未提交补丁的来源指纹。具体代码和协议见 protocol_final.json 与 source_snapshot.tar.gz。

| 分支/角色 | 完整 SHA | 本轮用途 |
|---|---|---|
| main | cdf4b3425762ddde6974078850f3ba1287ad1d84 | 核对版本；开发能力不称为已合入 main |
| sim/scalable-mesh-experiments | 8c0c6eeebdba6d81355d360698d679c61484bd20 | 独立 detached worktree，原始 smoke/main 清单不变 |
| compiler/communication-aware-mapper | 005246acf0a2231a5490a628a0f38c5bc13a9e06 | 独立 detached worktree，v0.3 复跑和 compact 审计 |
| compiler/bounded-timing-predictor | f128f237a5393dbc1fadb8e47278e10d82d81f86 | 本轮 v0.4 与新实验 |
| 历史 v0.4 冻结实验 | 9aaf78037f155ad5249451dfe0950ad29a8c15a4 | 历史来源，区别于当前分支 HEAD |

原 `review-tip` 仍为参考源码快照，没有把它初始化或冒充正式 checkout。仓库与父目录未找到适用 AGENTS.md；已读取 README、CONTRIBUTING、规范、入口、配置、测试和精选结果。用户明确要求实际实验且不自动提交/推送，因此没有按 CONTRIBUTING 创建远端 Issue。

| 要求 | 来源页/截图 | 对应实现或实验 | 验收证据/缺口 |
|---|---|---|---|
| 仿真、复现、公平基线、统计与报告 | 用户指定 Codex_仿真测试提示词.md（Markdown 无页码） | 本轮全部工作 | 本报告、commands.json、原始 JSON、图 |
| 课程硬要求、个人分工、截止日期 | Project_topics.pdf、计组project_midreport_direction.md 缺失 | 依赖原件的要求待确认 | 在 Documents/Codex 可访问范围未找到；部分旧依赖目录拒绝读取，非全磁盘穷尽搜索 |
| 微信实验截图 | 本轮未取得原截图 | 不据转述补造要求 | 不能提供逐截图验收或重建缺失图表列名 |
| 真实应用线性层 | 官方 MLCommons 固定提交 | 层形状驱动合成 INT8 GEMV | official/ 源码、来源 URL 与哈希；不是完整模型推理 |

## 可核查现状

| 功能 | 源码位置 | 本轮状态 |
|---|---|---|
| INT8 GEMV、INT32 回绕 | src/simulator/functional、src/tests/test_demo0_functional.py | 源码存在；本机回归通过 |
| ROW/K/二维、放置 | src/compiler/grid_mapper.py、mapping_search.py | 源码存在；全目录数值逐元素通过 |
| Mesh/FIFO/NI | src/simulator/communication/scalable.py | 源码存在；Python 回归与实验通过 |
| 有限 Root/树归约 | src/simulator/system/scalable.py、timed_endpoint.py | 本机通过；包含来源 mask、反压、唯一 Done 等既有回归 |
| 自动搜索 | src/compiler/mapping_search.py、bounded_predictor.py | v0.3/v0.4 已复跑；预测与实测分开 |
| 分片/续跑/合并 | tools/experiments/scalable_experiments.py | smoke 三分片及续跑、main 三分片严格合并通过 |
| RTL | src/rtl/nmc_tile、nmc_mesh | 源码存在；已有历史记录；本机 5 项跳过，未验证 |
| 新集合/参数扫描 | tools/experiments/validation_campaign.py | 本机已运行、数值通过；原始错误/不可行不隐藏 |
| 大层/多事务/完整拓扑替换 | 当前接口未提供 | 未实现或不支持；树端点仍运行于 Mesh |

## 环境、回归与失败记录

Python 3.12.14 (main, Aug 25 2026, 14:01:42) [MSC v.1944 64 bit (AMD64)]，Windows-11-10.0.26300-SP0；CPU 标识 Intel64 Family 6 Model 170 Stepping 4, GenuineIntel，逻辑 CPU 22。完整依赖版本见 dependencies.log。隔离 .venv，没有覆盖其他环境；Git 2.54.0.windows.1。Icarus/vvp、Verilator、Yosys 未找到，WSL 列举也返回非零。

先执行 `python -m pytest -q -p no:cacheprovider -ra` 与 `python tools/check_layout.py --tracked`。安装依赖前 pytest 启动失败；安装后首次完整结果为 378 passed/1 failed/5 skipped。失败定位为 pressure_sweep 的来源路径在 Windows 使用反斜杠，改为 Path.as_posix()，没有修改数值断言或模型延迟。随后 379 passed/5 skipped；新增两个独立 golden 与篡改拒绝测试后，最终 **381 passed/5 skipped**。新增汇总器测试曾发现 JSON 元组/列表差异，已改用规范化 digest 比较，失败日志保留。

布局检查通过；Mapper `--self-test` 通过。5 个 skipped 分别为 Mapper→Tile、Tile、Root 写回、独立归约、集成 Mesh RTL；缺工具不能算通过。原始 pytest 日志和 JUnit XML 均保存。

既有回归覆盖 INT8 边界/尾 Lane、非法计划、来源/重复包、ACK 身份、唯一 Done、反压、复位、容量及分片完整性。本轮新数据 golden 使用 Python 宽整数乘加后显式 `((s+2**31)%2**32)-2**31`，不调用 DUT 的回绕函数；验证脚本重新从 seed 构造 W/x，逐元素检查、检查计划摘要/硬件配置/阶段求和/唯一完成。新增的合成大整数 golden 测试只验证回绕公式，不声称合法小 K INT8 GEMV 触发 INT32 溢出。

## Baseline 合同和计量范围

固定 8×8/64 物理 Tile；每 Tile 4-Lane MAC、4 KiB W＋1 KiB X＋1 KiB Y、四 Flit Router 输入与 NI；同一有限 Root 和端点能力。16/32 worker 为映射选择，树节点使用剩余物理 Tile，额外部分和与来源元数据预算见 RTL 说明，不隐藏资源。

| 策略 | 候选与排序规则 |
|---|---|
| fixed-row / fixed-k | 仅保留纯 ROW / 纯 K；放置和 central/tree 仍在合法目录中；本轮按 v0.4 排序，再取前 K |
| UniNDP-adapted＋compact | 作者方法适配的满利用剪枝、资源得分和两阶段搜索；固定 compact 为主要强基线 |
| NoC v0.3 | predict_v03()，保留旧误选 |
| NoC v0.4 | 有界整包逐跳近似预测；不是精确 flit evaluator 或严格周期上界 |
| exhaustive oracle | 同一七网格×两放置×两归约有限目录的事后最小周期，不是零成本在线算法 |

预算 1/2/4（v0.3 历史另含 8）。新集合先冻结所有形状、硬件点和五种策略排名到 *.ranking.json，再执行完整目录；预算质量属于记录前缀回放，不伪称为独立计时搜索。独立搜索耗时实验实际执行每策略前四项数值 evaluator，以前缀报告 1/2/4；额外 oracle 收集不计为在线免费收益。所有 predictor/proxy 调用、推演周期、剪枝、缓存与逐次时长保存在原始记录。fixed-row/k 也使用包级 proxy，开销完整披露。

所有运行均权重常驻，输入提交仍为功能写；计时窗口为 input_load、compute_and_gather、completion_drain，三者不重叠。NoC/Compute/Root 忙周期只作诊断，不能相加为任务总周期。没有频率校准，报告 cycles，不换算纳秒或 TOPS/W。

evidence_level 区分 functional_reference、timed_python_evaluator、timing_predictor、module_rtl、integrated_rtl。本轮新测量为 timed_python_evaluator；预测诊断为 timing_predictor；历史 RTL 数据标为历史 integrated_rtl，不能当作本机通过。

## 历史恢复、统计重算和完整复跑

历史 records/source_snapshot 保持只读。核查压缩记录封印、源码文件 SHA256，重构输入并独立验算输出；详见 historical_integrity.json 和 archive_inventory.json。三套数据分别恢复记录并重算统计，随后 smoke、小规模回归及完整 campaign 重新执行，三类证据没有混称。

- v0.2：112/112 项成功，0 错误、0 不可行；56 对中 tree 40 胜、16 平，几何平均 1.485106×，周期与历史逐项一致。保留独立 1×4 反例 central 64、tree 74；不宣称树总快。
- v0.3：448 attempted、440 feasible、8 infeasible，完整复跑的逐候选周期与历史一致。原 4 calibration＋12 holdout 现在均为已知数据。强 compact 基线的事后审计在原 holdout 预算 1/2 为 0.985048×/0.982207×，预算 4/8 持平；最坏 0.806189×。弱放置 tie-break 的 1.0633× 只作历史分析。该 compact 审计仍标为回放，不称为盲测或新搜索耗时。
- v0.4：八历史形状 224 attempted、220 feasible、4 空分块不可行；完整重新执行并与历史逐候选周期/输出核对。预算 1 为 1 胜/7 平/0 负，约 1.0205×。159×91 强基线 1640、新 NoC 1394；129×65 的 990 和 192×96 的 1465 由回归再次验证。历史八形状不称为本轮未见数据。
- 历史任务 MAPE 约 0.366%、kernel 最大误差 17.44% 属于默认 Python evaluator 对照，既非 RTL 准确度也非严格上界。历史并行单次 wall time 0.550/0.653/1.462 秒及强基线预算 2 的 1.174 秒仅为历史描述，不能据此推断显著性。本轮历史复跑存在其他验证进程并行，耗时不作搜索性能结论。

## 新冻结集合与完整覆盖

唯一执行协议为 ini/experiments/validation_20261031/protocol_final.json，seed=20261031。预检阶段保留了旧协议草案；256×513 在任何性能标签产生前即因容量不可行移入 unsupported，合法边界用 256×511。其余形状未按收益筛选；未调整预测器。候选为原七种 16/32-worker 网格×compact/spread×central/tree。默认点 336 attempted、292 feasible、44 infeasible；另外八个硬件敏感性案例和三个应用层形状案例独立保存，总计 23 个形状/硬件案例。

形状：37×19, 19×31, 35×69, 67×35, 81×81, 97×161, 161×97, 201×29, 29×201, 251×127, 128×640, 256×511。覆盖奇数、四 Lane/四 byte 尾部、方形、高瘦/矮胖、最大 M=256 与权重容量边界。各配置拒绝原因在 rejected；超时/异常将单独保存而不归为不可行，本轮最终通过的目录没有丢弃失败案例。

| policy | budget | n | excluded | speedup | W_T_L | hit | worst |
| --- | --- | --- | --- | --- | --- | --- | --- |
| unindp-compact | 1 | 12 | 0 | 1.000000 | 0/12/0 | 10 | 1.000000 |
| noc-v03 | 1 | 12 | 0 | 0.986430 | 1/9/2 | 9 | 0.863760 |
| noc-v04 | 1 | 12 | 0 | 1.018812 | 2/10/0 | 12 | 1.000000 |
| fixed-row | 1 | 12 | 0 | 0.195794 | 0/0/12 | 0 | 0.042597 |
| fixed-k | 1 | 12 | 0 | 0.901320 | 1/7/4 | 7 | 0.424344 |
| unindp-compact | 2 | 12 | 0 | 1.000000 | 0/12/0 | 12 | 1.000000 |
| noc-v03 | 2 | 12 | 0 | 0.980105 | 0/10/2 | 10 | 0.881472 |
| noc-v04 | 2 | 12 | 0 | 1.000000 | 0/12/0 | 12 | 1.000000 |
| fixed-row | 2 | 12 | 0 | 0.192179 | 0/0/12 | 0 | 0.042597 |
| fixed-k | 2 | 12 | 0 | 0.884842 | 0/7/5 | 7 | 0.425294 |
| unindp-compact | 4 | 12 | 0 | 1.000000 | 0/12/0 | 12 | 1.000000 |
| noc-v03 | 4 | 12 | 0 | 0.980105 | 0/10/2 | 10 | 0.881472 |
| noc-v04 | 4 | 12 | 0 | 1.000000 | 0/12/0 | 12 | 1.000000 |
| fixed-row | 4 | 12 | 0 | 0.192179 | 0/0/12 | 0 | 0.042597 |
| fixed-k | 4 | 12 | 0 | 0.884842 | 0/7/5 | 7 | 0.425294 |

新十二形状的实际生成/预测调用计数如下；数值 evaluator 在公共目录中每个可行计划执行一次，默认集合实际 292 次，含敏感性与应用形状共 584 次。质量预算表是这些数值记录的排名前缀回放，未把重复引用当作额外真实 evaluator 调用。

| policy | predictor_calls | packet_proxy_calls | proxy_modeled_cycles | pruned |
| --- | --- | --- | --- | --- |
| unindp-compact | 146 | 0 | 0 | 146 |
| noc-v03 | 292 | 0 | 0 | 0 |
| noc-v04 | 292 | 292 | 268747 | 0 |
| fixed-row | 80 | 80 | 24498 | 212 |
| fixed-k | 76 | 76 | 165578 | 216 |

speedup_i=T_baseline/T_policy，逐形状配对后对 log 求均值并取 exp；regret=T_selected/T_oracle−1。命中按周期相同（允许并列），Top-K 按前缀是否包含任意并列最优；Spearman 使用平均并列秩。MAPE 仅给时间预测器，以全可行候选为分母，不对资源得分算 MAPE。共同可行子集和排除分母见上表；全部候选记录也保留。

## 硬件敏感性、误差与瓶颈

代表形状 67×35 与 161×97；分别只改 MAC interval=2、Weight issue_interval=2、Root lanes_per_cycle=1、Root rx_queue_packets=1，其余默认。每个硬件点内重新排名并完整测量公共目录；这些是硬件敏感性，不混入固定硬件 Mapper 收益。

| point | policy | n | total_MAPE | kernel_MAPE | kernel_max | input_MAPE |
| --- | --- | --- | --- | --- | --- | --- |
| default | noc-v03 | 360 | 10.0455% | 3.9276% | 33.8800% | 13.4193% |
| default | noc-v04 | 360 | 0.5358% | 2.9767% | 20.2703% | 0.0000% |
| mac2 | noc-v03 | 56 | 9.5926% | 4.0962% | 34.6231% | 13.4366% |
| mac2 | noc-v04 | 56 | 0.5891% | 2.4177% | 12.2807% | 0.0000% |
| weight_issue2 | noc-v03 | 56 | 9.5926% | 4.0962% | 34.6231% | 13.4366% |
| weight_issue2 | noc-v04 | 56 | 0.5891% | 2.4177% | 12.2807% | 0.0000% |
| root_scalar | noc-v03 | 56 | 11.0076% | 10.9195% | 48.8639% | 13.4366% |
| root_scalar | noc-v04 | 56 | 0.3864% | 1.1855% | 10.0271% | 0.0000% |
| root_rx1 | noc-v03 | 56 | 9.8707% | 4.7879% | 31.3675% | 13.4366% |
| root_rx1 | noc-v04 | 56 | 0.6515% | 3.0851% | 14.2612% | 0.0000% |

默认点误差表包含新集合及应用形状，非默认点各包含两个代表形状，不能不加说明直接比较分母。带符号逐候选误差见 phase_errors.csv，图 5 使用仅新 12 形状。每个非默认点核对 v0.4 flit-hop 流量守恒及 Router≤4、NI≤4、RX≤当前配置的上界；汇总器另核对实际 FIFO、端点 RX/Done/输出槽上界、ACK 数和唯一 Done。

| point | phase | run_id | plan_prefix | signed_error |
| --- | --- | --- | --- | --- |
| default | kernel | default-19x31 | 73f46bae1efa | +20.2703% |
| default | total | default-128x128 | 7148c6f49044 | +5.2960% |
| mac2 | kernel | mac2-67x35 | dba5c1a3204a | +12.2807% |
| mac2 | total | mac2-67x35 | 20543b8ef471 | +4.7354% |
| root_rx1 | kernel | root_rx1-67x35 | 20543b8ef471 | +14.2612% |
| root_rx1 | total | root_rx1-67x35 | 20543b8ef471 | +8.0116% |
| root_scalar | kernel | root_scalar-67x35 | ad818f58dee3 | +10.0271% |
| root_scalar | total | root_scalar-67x35 | ad818f58dee3 | +3.7487% |
| weight_issue2 | kernel | weight_issue2-67x35 | dba5c1a3204a | +12.2807% |
| weight_issue2 | total | weight_issue2-67x35 | 20543b8ef471 | +4.7354% |
| point | shape | cycles | flit_hops | ni_stalls | rx_blocked |
| --- | --- | --- | --- | --- | --- |
| default | 67x35 | 634 | 1088 | 0 | 173 |
| default | 161x97 | 1469 | 2408 | 0 | 11 |
| mac2 | 67x35 | 641 | 1088 | 0 | 29 |
| mac2 | 161x97 | 1633 | 5466 | 6739 | 1928 |
| root_rx1 | 67x35 | 634 | 1088 | 0 | 203 |
| root_rx1 | 161x97 | 1469 | 2408 | 0 | 235 |
| root_scalar | 67x35 | 806 | 1616 | 0 | 742 |
| root_scalar | 161x97 | 1967 | 3728 | 1287 | 1766 |
| weight_issue2 | 67x35 | 641 | 1088 | 0 | 29 |
| weight_issue2 | 161x97 | 1633 | 5466 | 6739 | 1928 |

服务速率变更与固定协议的比较可用于解释 MAC/Bank/Root 限制；NI 停顿与 RX 阻塞是重叠诊断量，不能作为可相加的时间分解。策略可能随硬件点改变，因而上表包含选择效应；不以单一相关性推断因果。详细 per-Tile compute_stats、endpoint_stats 保存在原始 JSON，可进一步检查服务忙周期。

## 搜索耗时与质量

独立 81×81，默认硬件、固定输入；一次预热＋五次计时；五策略轮换，预测缓存按策略清空；所有其他本轮实验已结束后单进程测量。原始预热、排序、数值调用、proxy 次数/推演周期均保存于 walltime.json。中位数及 IQR 只描述该机器、该形状的分布，不作跨工作负载或统计显著性结论。

| policy | budget | samples | median_s | IQR_s |
| --- | --- | --- | --- | --- |
| unindp-compact | 1 | 5 | 1.09855 | [1.08560, 1.11016] |
| unindp-compact | 2 | 5 | 2.21723 | [2.21106, 2.22292] |
| unindp-compact | 4 | 5 | 3.80145 | [3.75456, 3.81780] |
| noc-v03 | 1 | 5 | 1.12464 | [1.10376, 1.16921] |
| noc-v03 | 2 | 5 | 2.27425 | [2.22617, 2.30201] |
| noc-v03 | 4 | 5 | 4.35001 | [4.23747, 4.38884] |
| noc-v04 | 1 | 5 | 2.79741 | [2.79472, 2.93170] |
| noc-v04 | 2 | 5 | 3.43171 | [3.40141, 3.62095] |
| noc-v04 | 4 | 5 | 5.38708 | [5.33185, 5.83545] |
| fixed-row | 1 | 5 | 2.04185 | [2.00624, 2.15604] |
| fixed-row | 2 | 5 | 3.85883 | [3.83882, 3.96310] |
| fixed-row | 4 | 5 | 9.44766 | [9.35669, 9.82470] |
| fixed-k | 1 | 5 | 2.30508 | [2.23921, 2.31619] |
| fixed-k | 2 | 5 | 3.13421 | [3.12993, 3.15931] |
| fixed-k | 4 | 5 | 5.45232 | [5.38668, 5.46376] |

81×81 预算 1 的搜索中位时间：强基线 1.09855 秒，v0.4 2.79741 秒。这个代表点显示更高预测开销，不支持“搜索更快”的结论；选解质量和搜索成本分开报告。

图 4 展示预算—时间、生成/预测（包含 proxy）/完整数值评估分解，以及同一 81×81 的质量—时间关系。一次精确评估前仍对所有候选预测，不称为“一次仿真完成全部搜索”。确定性周期没有用重复运行制造误差条。

## 真实层形状、量化与容量

官方仓库 mlcommons/tiny 固定提交 **4addd0fa08d216e20637637874e084895f289da4**。来源：

- [keras_model.py](https://github.com/mlcommons/tiny/blob/4addd0fa08d216e20637637874e084895f289da4/benchmark/training/anomaly_detection/keras_model.py)
- [baseline.yaml](https://github.com/mlcommons/tiny/blob/4addd0fa08d216e20637637874e084895f289da4/benchmark/training/anomaly_detection/baseline.yaml)
- [00_train.py](https://github.com/mlcommons/tiny/blob/4addd0fa08d216e20637637874e084895f289da4/benchmark/training/anomaly_detection/00_train.py)
- [02_convert.py](https://github.com/mlcommons/tiny/blob/4addd0fa08d216e20637637874e084895f289da4/benchmark/training/anomaly_detection/02_convert.py)

baseline 为 128 mel bins×5 frames=640 输入。Dense 序列为 640→128→128→128→128→8→128→128→128→128→640；通过 AST 和 YAML 提取的逐层表为 official/application_layers.csv。依据 [Keras Dense 轴合同](https://keras.io/api/layers/core_layers/dense/)，kernel 轴为 [K,M]；本 GEMV W 轴 [M,K]，相当于转置参数矩阵。独特形状 128×640、128×128、8×128、128×8 已做合成 INT8 GEMV；640×128 超出 M≤256，28 配置全部拒绝。4096×4096 与 256×513 也保留逐配置拒绝原因，没有扩大 SRAM 或把合法子块冒充整层。

模型源码没有显式固定训练 dtype；官方转换脚本包含动态/全整数及 INT8 输入输出路径。本轮没有加载原模型核实实际 Tensor dtype，没有下载训练权重、校准数据或调用 TensorFlow 转换：仅提取层形状，用 seed 生成 [-128,127] 合成权重/输入，INT32 累加；不模拟 bias、BatchNorm、ReLU、重定标/zero-point、音频特征处理、模型精度或端到端推理。因此准确称为“真实层形状驱动的 GEMV”。official/ 文件保存抓取内容与本地哈希；路径猜测 convert_to_tflite.py 曾返回 404，随后通过官方目录定位实际 02_convert.py。

## RTL 历史记录与本机限制

本机缺少工具，提供可执行入口而不以历史通过替代本机通过。以下表格来自 data/results/mesh_rtl_20261028/results.json，evidence_level=integrated_rtl（历史）；每例包括数值/线格式、ACK、唯一 Done 与 Python/RTL 阶段偏差。

| case | numeric | wire | python_total | python_input | python_kernel | rtl_phases | delta |
| --- | --- | --- | --- | --- | --- | --- | --- |
| hand_5x3_central | True | True | 77 | 40 | 37 | [{'phase': 0, 'input_cycles': 59, 'kernel_cycles': 41, 'acks': 2, 'dones': 1, 'aborted': 0}] | 4 |
| tail_tree | True | True | 760 | 690 | 70 | [{'phase': 0, 'input_cycles': 1015, 'kernel_cycles': 75, 'acks': 36, 'dones': 1, 'aborted': 0}] | 5 |
| row32 | True | True | 3770 | 3632 | 138 | [{'phase': 0, 'input_cycles': 5937, 'kernel_cycles': 142, 'acks': 256, 'dones': 1, 'aborted': 0}] | 4 |
| grid16_central | True | True | 1650 | 1440 | 210 | [{'phase': 0, 'input_cycles': 2593, 'kernel_cycles': 214, 'acks': 128, 'dones': 1, 'aborted': 0}] | 4 |
| grid32_tree | True | True | 2369 | 2264 | 105 | [{'phase': 0, 'input_cycles': 3417, 'kernel_cycles': 111, 'acks': 128, 'dones': 1, 'aborted': 0}] | 6 |
| k32_central | True | True | 10882 | 566 | 10316 | [{'phase': 0, 'input_cycles': 855, 'kernel_cycles': 10320, 'acks': 32, 'dones': 1, 'aborted': 0}] | 4 |
| k32_tree | True | True | 2724 | 566 | 2158 | [{'phase': 0, 'input_cycles': 855, 'kernel_cycles': 2166, 'acks': 32, 'dones': 1, 'aborted': 0}] | 8 |
| backpressure | True | True | 848 | 288 | 560 | [{'phase': 0, 'input_cycles': 433, 'kernel_cycles': 563, 'acks': 16, 'dones': 1, 'aborted': 0}] | 3 |
| reset_in_flight | True | True | 554 | 288 | 266 | [{'phase': 0, 'input_cycles': 433, 'kernel_cycles': 20, 'acks': 16, 'dones': 0, 'aborted': 1}, {'phase': 1, 'input_cycles': 433, 'kernel_cycles': 271, 'acks': 16, 'dones': 1, 'aborted': 0}] | 5 |

历史覆盖限制：Root/Host=0、tid=1、单事务、默认硬件，九场景包括反压与复位重放。Python 功能输入写与 RTL 窄 SRAM 写口、注册 NI、任务接受/VALIDATE 不同；独立端点或模块边沿对齐不证明全系统周期等价。单 Tile SRAM 结构检查也不能替代完整芯片频率/面积/功耗。没有完成作者原生 UniNDP 复跑，不将适配器称为完整原工具复现，不跨平台相除 raw cycles；没有可公平运行的分层物理网络，因此不做拓扑更换收益声明。

## 图、数据和来源追踪

六组图均由 tools/experiments/summarize_validation_campaign.py 从真实新结果生成 PNG＋可编辑 SVG，图中说明数据集、预算、单位和模型证据。打开 contact sheet 与重点原图检查遮挡；不把重绘历史数据说成重新仿真。

1. figure/01_latency_ratios：逐形状强基线比值，预算 1/2/4，保留退化。
2. figure/02_phases：不重叠 input/compute-and-gather/completion-drain。
3. figure/03_budget_quality：最大 regret 和 Top-K/并列最优命中。
4. figure/04_search_cost：质量—时间、全部 proxy 开销和独立数值评估。
5. figure/05_prediction_errors：input/kernel/total 带符号误差分布。
6. figure/06_sensitivity：两代表形状的硬件点曲线。

完整来源链：protocol_final.json（完整基点 SHA、源码/协议 fingerprint、seed、目录/预算/硬件）→ *.ranking.json（排名先于数值）→ run_id/计划 SHA/workload SHA/实际结果 → records.json.gz、quality.csv、summary.json → 图。walltime.json 保存每次实际调用与轮换；commands.json、new_commands.json 保存 argv/cwd/exit code。data/results/validation_20261031/source_snapshot.tar.gz 包含本轮源代码与配置，manifest_sha256.json 列出交付物字节哈希。旧数据及来源快照从未改写。

## 未完成和后续事项

- 课程原件、原始微信截图及逐页要求表依赖用户提供材料，未自行填充课程要求或截止日期。
- RTL 在本机未运行；需在已有 Linux/CI 工具链复跑。作者原生 UniNDP、完整真实模型、完整芯片 PPA、任意 Root/多事务/全部参数均未完成。
- 新搜索 wall time 重复统计仅覆盖代表形状 81×81，不外推整个形状集；历史复跑时的单次耗时不用于公平计时结论。
- 既有 prediction_validation.fingerprint 的额外工具键在 Windows 用反斜杠，在 Linux 用正斜杠；本轮单机数据按原检查严格合并，未关闭校验。跨系统混合该版本结果仍需单独修复与验证，不能声称跨平台来源指纹已经统一。pressure_sweep 本轮修复的是另一处已失败的导出路径。
- 本轮范围是模型验证和可复现性，未重写 Mapper、延迟模型或扩展 RTL。后续可增加更多单变量硬件点、更多形状重复搜索耗时，并在取得材料后对照课程验收。

## 实际改动与交付清单

- tools/experiments/pressure_sweep.py：仅修复来源路径的 POSIX 序列化。
- tools/experiments/validation_campaign.py：薄封装冻结协议、公共目录测量、敏感性、应用形状及独立重复计时。
- tools/experiments/summarize_validation_campaign.py：独立核查、CSV/JSON 汇总与六组图。
- src/tests/test_validation_campaign.py：独立回绕 golden 和重签名错误/重复/缺失记录拒绝测试。
- src/tests/README.md、docs/spec/scalable_execution_v0.2.md：标明旧测试数量和 RTL 状态为历史记录。
- ini/experiments/validation_20261031/protocol_final.json：唯一执行的新冻结协议。
- docs/simulation_validation_report.zh-CN.md、docs/simulation_reproduce.zh-CN.md：报告和 PowerShell 复现步骤。
- data/results/validation_20261031/：实测数据、摘要、图、来源与源码快照；dump/validation_20261031/：完整原始日志及证据。

交付包含完整 changed_files.json、changes.patch、repository_files/、原始数据及 manifest_sha256.json。修改仅在本地，无自动提交、推送或 PR 合并。
