# 64 Tile 集成 RTL

`nmc_mesh_system.sv` 实例化 64 个真实 `nmc_tile_compute`（保留每 Tile 的六块 SRAM）、64 个五端口 Router 和有限 NI/归约端点。物理 Tile ID 直接为 0–63，不再把叶任务改名为 1/3 后单独验证。

- `nmc_router.sv`：每输入四 Flit，确定性 XY，包边界 RR，HEAD→TAIL 锁定，阻塞时锁住授权；valid/data 不依赖 ready。
- `nmc_packet_tx.sv`：四 Flit NI，原子接受一/两 Flit 包。
- `nmc_reduce.sv`：两项接收包、一项活动寄存器、256 项 INT32 部分和、每输出 64-bit 来源表和接收元数据、一项输出寄存器/Done。READ/ADD/UPDATE/WRITE 均为一周期，四 Lane；最终输出等来源 last 被处理后才转发。
- `nmc_mesh_system.sv`：将 Compute、真实窄 SRAM 写、MEM_ACK、RESULT、最终 Root 写回与 TRANSACTION_DONE 接成闭环。树节点占用同一物理预算的闲置 Tile。

合法配置由 `GridPlan` 生成，Descriptor、来源配置和权重通过侧带 setup 加载；输入经 NoC MEM_WRITE 写入真正的 X SRAM，收齐 ACK 后统一发任务，结果全程经 NoC。Root/Host 固定为 Tile 0，单次事务、至多 256 输出；算术为 INT8 乘法与 INT32 回绕。每次新事务复位控制与来源表，SRAM 内容不靠复位清零。

运行：

```bash
python tools/rtl/run_mesh_rtl.py --output-dir dump/mesh_rtl --wave
python -m pytest -q -p no:cacheprovider src/tests/test_reduce_rtl.py src/tests/test_mesh_rtl.py
```

共九个集成用例：5×3 手算、非整除 tree、32-worker ROW、16-worker 二维 central、32-worker 二维 tree、256×64 的 32-worker central/tree、端点阻塞和在途复位重放。检查每个 MEM_WRITE/ACK/RESULT/Done 的真实线格式与数值、Root SRAM 读回、唯一完成、队列上界及反压稳定性。独立端点另与 Python `TimedRoot` **每个服务边沿对齐**，覆盖 31/63 号来源、回绕和五类非法包。

保存 `results.json`、`plan.json`、`packets.csv`、`cycles.csv`、`memory.csv`、编译/静态检查日志；`--wave` 只为反压用例保存 `wave.vcd`，不把构建文件和大波形提交到 Git。

## 周期口径

现有 Python 系统采用功能输入写提交，RTL 经过每周期一字节的 X 写端口，因此整任务周期不能声称完全相等。RTL 还包括真实任务接受/VALIDATE 与注册 NI 队列。报告同时保存模型总周期、模型 kernel+drain、RTL input/kernel 和两者偏差，逐例列出，不用单 Tile 对齐代替完整系统误差。

集成验证证明的是默认硬件组织下的数值与通信闭环；未进行完整芯片综合、布局布线、频率/面积/功耗验证，也没有覆盖任意多事务、任意 Root、所有可配 Bank 时序。原单 Tile/宽写回的独立综合与逐周期回归继续保留。
