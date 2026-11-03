# Mapper 时序预测修复 v0.4

## 问题与不变条件

v0.3 在 129×65 中把 990 周期的二维方案排在后面，选择 1228 周期的 K 切分；192×96 也误选。修复仅改变 NoC 预测器，参考 evaluator、Tile/Root/NoC 硬件、候选目录和 UniNDP-adapted＋compact 强基线保持不变。原始数据保留在 v0.3 结果目录。

旧预测器保留为 `predict_v03()`；`predict(..., communication=False)` 保持原资源排序得分，逐项检查原 440 个可行计划的得分不变。默认 `predict()` 使用新的有界包级模型。

## 输入协议的固定开销

参考模型每次只允许一个 MEM_WRITE 等待 ACK。距离为 d 时，两 Flit 写包需要 d+3 个边沿，单 Flit ACK 需要 d+2 个边沿，因此每次请求为 `2*d+5`。旧公式使用 `2*d+7`，每包多算两周期，复制输入较多的二维切分受影响更大。

输入请求完全串行且在计算前排空，因此总开销可按每个 worker 的 `ceil(K_local/4)` 次写入求和，不需要再次模拟整个加载阶段。部分字节 mask 不改变参考模型功能提交的服务时间。

加载还会改变 Router 的 RR 指针。新预测按原 worker/sequence 顺序恢复输入阶段最后一次写/ACK 对各端口的影响；各 worker 路径不变，只处理其最后一对包即可得到相同的最终 RR 状态。已有独立协议测试对照实际 Mesh 的所有端口。

这里对齐的是**既有 Python 功能输入提交**。RTL 的真实窄 SRAM 写端口、任务接受/VALIDATE、注册 NI 仍有额外时间，不把预测修复声称为全系统 RTL 周期完全相等。

## 有限队列与反压

`bounded_predictor.py` 执行只涉及时间的包级推演：

- 每输入四 Flit、NI 四 Flit；RESULT 两 Flit、Done 一 Flit，按实际 Flit 数占用 credit。
- 每输出保持 RR 和阻塞授权，只发送输入队头；接收包开始时预留端点槽位，完成时提交。
- 归约服务复用同一 `TimedRoot/ReductionEndpoint` 合同，数据为零；保留两 RX＋一活动包、一输出寄存器、来源完成和最后一包门控。
- 计算侧使用独立 Tile 时序得到的结果释放间隔。NI 满时持有结果，下一组的释放随握手推迟，反压可以从归约端点传回源 Tile。
- 最终 Done、所有源 LocalDone、队列及端点排空后才结束。

网络按**整包逐跳服务**近似 wormhole：包在跳传完成前占用相应 Flit credit，没有逐 Flit 的跨跳流水重叠。这是一个偏保守的近似，不能叫精确的 Flit 模拟或 RTL 模拟。孤立 Tile 间隔也是近似；当前周期误差和选解验证针对冻结协议中的默认 Tile 时序，非默认参数需另作验证。

预测不读取真实 W/x，不调用完整 `run_grid()`，不读取实测周期。它会对每个候选进行包级推演，因此不能把它当作零成本或隐藏为“只评估一个候选”。记录 `packet_proxy_calls`、推演周期总数和实测排序耗时；编译开销包含候选生成、预测与最终数值仿真。

## 验证协议

原 16 形状为 development replay，可用于定位错误，不再称为新的独立验证。先检查旧测量的 evaluator、布局与工作负载源码不变，再在旧数值测量上回放排序；不由回放计算编译加速。

新增八个形状和种子在 [协议文件](../../ini/prediction_v0_4.json) 固定。冻结预测器后，先生成全部三种策略的排序，再执行每策略前四项独立数值仿真，最后补全有限目录的最优参考。比较强基线、旧 NoC 与新 NoC，预算 1/2/4；生成/排序/数值评估均计时，缓存按策略清空。所有结果与独立 INT32 golden 对照，合并时重查数值、硬件、来源、预算前缀和完整性。

```bash
python tools/experiments/prediction_validation.py replay --output-dir dump/prediction_v04/development
python tools/experiments/prediction_validation.py run --output-dir dump/prediction_v04/fresh
python tools/experiments/prediction_validation.py merge --inputs dump/prediction_v04/fresh \
  --replay dump/prediction_v04/development/replay.json --output-dir dump/prediction_v04/merged
```

`run` 支持 `--shards N --shard-index i`，每个形状完整地由一台电脑执行。源码或协议变化须使用新目录，不把不同版本的结果混合。

报告的周期误差以同配置完整 Python evaluator 为参照；不等同于芯片或 RTL 准确度。绝对周期误差只比较两种 NoC 时间模型，不给未经周期标定的资源得分计算 MAPE。

冻结后的结果见 [验证数据与成本](../../data/results/prediction_v04_20261028/README.md)：新八形状首选命中从旧 NoC 的 6/8 改为 8/8，任务周期 MAPE 从 10.04% 降至 0.366%，kernel MAPE 为 2.07%。排序约 1.055 秒/形状，较旧版增加；不能把更少的数值评估次数当作整体搜索加速。
