# 固定预取容量下的权重长包实验

关联 Issue #20；接续 [Central/NMC 首轮合同](central_nmc_comparison_v0.1.md)。状态：Python 模型；按当前任务安排，RTL 校准后置。

## 集中端的物理含义

集中端是一个网络节点后面的 32 组四 Lane MAC（共 128 MAC），不是原规格只有四 Lane 的单 Tile。
这些计算组拥有独立 X/Y 缓冲和控制器，内部可以并行；共用一个网络 NI。32 个权重 Bank 留在原存储节点。
不假设它在原 Tile 面积内放得下，也没有完成布局/频率/功耗验证。

## 控制变量与资源

- 只改变权重有效载荷：16、32、64、128 B；每包仍有一个 16 B Header。
- 每簇权重预取/在途信用预算固定为 128 B，分别允许 8、4、2、1 个未完成的包。
- 输入与结果仍是最多 16 B 的原流式包，信用仍是一 Flit；精度、W/X/Y 端口、MAC、布局、Host、时钟不变。
- 每个源统一预留 128 B 组包空间、128 B 序列化发送空间、4 B 在途同步读空间，另有 Header/控制元数据。
  这套最大尺寸的容量预算对本轮所有点（含 16 B 和 NMC）一致。NMC 不启用远程权重发送资源。
  它比首轮描述的 16 B 组包空间更大，因此新预算须单独报告；不能声称所有资源与首轮字面合同完全相同。
- NI 和 Router FIFO 均保持 4 Flit。长包最多九 Flit，不把整个包强塞进四项 NI，也不扩大其深度。
- 包长同时改变包信用个数和信用返回频率。128 B 时只有一个包信用，可能出现流量减少但流水重叠变差；不要求周期单调下降。

## 发送、接收与信用语义

Bank 每周期读四字节，下一周期返回；完整组好一个包后，提交给有界序列化器。
不超过四 Flit 的包沿用首轮原子入 NI 时序。更长的包填入 NI 剩余槽位，其余 Flit 留在一个源包发送缓冲中，
以后按 NI 空间继续入队。下一包不得插进当前包的 BODY/TAIL 之间。
源可在发送缓冲排空过程中组装下一包，但组包缓冲和发送缓冲各只有一个，且都受信用约束。
内部写 NI 可以填可用槽位；向 Router 的实际链路仍每个网络 tick 最多搬一个 Flit，没有增加网络带宽。

路由器沿用 HEAD 占用路径到 TAIL 的包锁与有限反压。
接收端收到 TAIL 后，完整包才对远程 W FIFO 可见；不是边收边给 MAC。
预取 FIFO 最后四字节被读取、转入独立操作数响应流水后，释放对应包槽并发出信用。
信用须实际穿过 Mesh 回到源；它不代表该包最后一次 MAC 或最终结果已经完成。
最后一个不足标称长度的包保留真实 payload 长度，但仍保守占用一个完整包信用。

这轮保留整包组装/接收与按包信用，不把提高突发长度等同于理想有效带宽。
潜在后续方向是字节信用、接收侧流式消费及预取容量敏感性，应分别列为新变量。

## 配置和计时

[weight_bursts.json](../../ini/central_comparison/weight_bursts.json)：三个 GEMV 形状、16/8/4 B/核心周期三档原始链路带宽；集中端固定内部节点 36。
每个形状/带宽组合运行一次 NMC 和四种 Central 包长，共 45 点、36 对比较。
继续采用首轮 warm-resident 任务边界；输入就绪后的阶段仍包含计算、通信、等待、输出和排空。

a) 验证所有 Host INT32 输出与独立 Golden 相同；b) 断言队列/预留字节上界；
c) 重放首轮对应的九个 NMC 和九个 16 B Central 点，输出、任务/输入/输出就绪周期、Flit-hop 与分类型 Flit 数必须完全一致。

报告 `Central/NMC` 周期比、Central 相对 16 B 的周期减少、实际权重/信用 Flit 数、MAC 利用率。
Header 理论载荷比例分别为 50%、66.7%、80%、88.9%，只代表满包编码效率；不是系统可达到的吞吐率。

## 复现与 RTL 后续范围

```bash
python -m pytest -q -p no:cacheprovider src/tests/test_central_comparison.py
python tools/experiments/weight_burst_sweep.py --output-dir dump/weight_bursts
python tools/experiments/plot_weight_bursts.py --input-dir dump/weight_bursts
```

本轮只运行 Python 验证。下一阶段再用 RTL 验证长包序列化、HEAD/BODY/TAIL、信用释放、停顿字段稳定、复位恢复及 Tail 后交付时序，
先做端点和 Router 小闭环逐周期对照，再决定是否扩到整个集中阵列。既有短包 RTL 通过不自动覆盖这些新接口。
即便 RTL 功能/时序一致，也不等价于已证明等面积、等最高频率或能耗收益。
