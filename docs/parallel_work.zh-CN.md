# 三台电脑的下一阶段分工

当前单 Tile RTL 已与 Python 对齐，逐请求 SRAM/Bank/MAC 和有限 Root 队列/服务已接入执行循环。**最新目标（2026-10-28）：8×8、64 Tile 主实验。** 新入口复用上述组件，支持多 worker、二维分块和集中/分层归约，保留 2×2/原 RTL 校准。三台电脑各自拉取同一 GitHub commit 后运行固定清单的独立分片，操作见 [多机实验](distributed_experiments.zh-CN.md)。依据和指标见 [UniNDP 实验方案](unindp_evaluation.zh-CN.md)。

## 阶段 A：Tile 时序接入（已在本机完成）

`sim/timed-tile-system` 分支已复用 `PortScheduledCompute`，提供 `tile_timing=TileComputeConfig()` 和 CLI `--compute-model tile`，兼容旧入口。逐周期推进 Compute/NI/Mesh，记录每 Tile 的 MAC、Bank 等待、Result 反压和统一计时窗口。

验收已通过：ROW/K 数值、ACK 集合、ResultBeat/Done 次数不变；独立端口基线与原公式时序对照一致；改变单 Bank 启动间隔或 Result Bank 数产生可解释的时间差。全部 24 个形状、固定放置、两种接收条件的基线 86 次可行执行网络指标完全一致，10 次不可行状态一致；五组对照共 480 条记录。配置、源码哈希与快照已保存。

该阶段保留的功能 Root 已作为后续有限 Root 的对照；输入写提交仍为功能操作，权重预加载和命令/VALIDATE 不计时。

## 阶段 B：有限容量、有限吞吐的 Root 端点（已在本机完成）

`sim/finite-root-endpoint` 已新增独立 `timed_endpoint.py`、资源配置与测试，并接入系统。默认两项输入包槽、一项活动寄存器、256 项部分和、一个 Done 槽，四通道读/加/更新/写回均为显式服务周期。包头预留槽位，Done 持有到 NI 接收，真实反压可传回 NoC 和 Tile。规范见 [Root 服务](spec/root_service_v0.1.md)。

`accept` 只检查并入队，数值更新由 `step` 的服务状态机执行。全包原子检查、来源 mask、回绕、有限输出与错误终止均通过测试。为避免现有 FIFO/单 VC 接口的端点死锁，PSUM 条目须覆盖本次输出范围；更小缓冲需要另行实现调度、重排、credit 或溢写。

验收已通过：40 项新测试，独立四通道手算 13 周期/单通道 34 周期；五组系统实验 240 条记录，215 次成功、25 次不可行。覆盖队列满、部分和缓冲满后排空、慢写回、来源顺序、重复包和完成反压；统计区分接收阻塞、阶段服务和结果写回。默认四通道 Root 的 19 个双策略可行形状仍为 K 胜 14、ROW 胜 5。

## 本机继续维护、复现与实验整合

Root 四字写回已接入内部宽写路径并完成 [RTL 校准](spec/root_writeback_rtl_v0.1.md)：空闲时一周期写四字，Compute/Host 占用由 ready 反压处理，51 组 Python/RTL 时间线一致。输入写提交、Root 部分和 SRAM/加法器及完整端点 RTL 继续作为已知边界；先做同配置模型实验和时序敏感性，只在影响候选排序或结论时优先补校准。

主要比较指标为 **同一计时窗口内的模型任务完成周期**，主基线为固定 K；同时报告固定 ROW。辅助统计为 MAC 利用率、Bank 等待、NI 注入阻塞、Root 队列占用、NoC flit-hop 与映射可行性。权重预加载是否计时必须统一声明。不同 Bank/端点配置属于硬件敏感性实验，不能算成 mapper 在相同硬件下的收益。

每台电脑使用独立克隆和分支；PR 写明基准提交、复现命令和参数。改动通过 Git 提交交换，不共享构建目录和波形目录。把源码快照/配置/精简结果随提交保存，完整波形留在本机或 CI artifact。

先在 Python 中固定 Tile/Root 服务参数，扩大拓扑及任务接口，并与当前 2×2 回归对照。本机负责公共候选与策略评估；一台空闲电脑做 UniNDP 原生复现，另一台复现相同配置的候选扫描和压力分组。
