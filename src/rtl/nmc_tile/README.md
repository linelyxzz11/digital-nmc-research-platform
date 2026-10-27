# Single-Tile Compute RTL v0.1

`nmc_tile_compute.sv` 实现有效/非法 TileTask 的接收、4-Lane signed INT8 MAC、INT32 回绕、独立同步 Weight/Input 读、四个 Result Bank、一项 ResultBeat 缓冲和 LocalDone。物理 Tile 默认编号为 1；其他编号可通过 `TILE_ID` 参数选择。接口和数值语义依据 [Compute v0.1](../../../docs/spec/compute_architecture_v0.1.md)。

## 实现边界

| 资源 | 实现 |
|---|---|
| Weight | 1024×32 bit，单读/写服务口，Host/Compute 显式复用 |
| Input | 1024×8 bit，单读/写服务口，与 Weight 独立 |
| Result | 4×64×32 bit，按 INT32 word 交错，每 Bank 单读/写服务口 |
| MAC | 4 个 signed INT8 乘法与 INT32 accumulator，流水填充后每周期一个 k |
| 结果缓冲 | 一项完整 ResultBeat；握手后开始下一组 |

SRAM 由可综合数组与同步读寄存器实现，尚未绑定具体 SRAM 宏或 FPGA RAM 原语。读请求在周期 c 的边沿接受，下一周期可用于 MAC。固定一周期读延迟下，响应寄存器与下一项读请求形成两项逻辑操作数预留；本版没有可变延迟的双槽 FIFO 或多 Bank 计算读仲裁。可变时序/共享 Bank 仍由 Python 实验模型研究。

时钟约定：任务握手后先有一周期 VALIDATE，**cycle 0 是 INIT_ACC**，计时不包含命令握手、VALIDATE、预加载和外部读回。5×3 无阻塞例子在 6、13 握手 ResultBeat，在 14 握手 LocalDone，共 15 周期。每个结果阻塞三周期、Done 再阻塞三周期时，共 24 周期。

外部端口一次接受一个 32-bit 请求；读、写都返回一次 `mem_rsp_valid/ready` 响应。仅在 IDLE 接受请求，响应握手前禁止接受任务或下一项请求；任务和存储请求同时出现时优先存储请求。Weight/Result 请求在接收后的下一个边沿服务；Input 写在随后四个边沿按 byte 服务，Input 读再用一个边沿组装最后一个返回 byte。byte-enable 控制实际写入，零 strobe 为合法空写。响应阻塞时字段稳定。复位取消未完成请求，但已提交的 SRAM byte/word 保留。

逻辑描述符采用展开端口。`task_opcode=8'd1` 表示 GEMV_LOCAL，`task_result_kind=0/1` 表示 FINAL/PSUM，2/3 非法；它们是本模块端口约定，尚不是 ISA 二进制编码。状态码与通信 `StatusCode` 的 0–7 一致。非法描述符先按规范优先级检查，产生一次错误 Done，不执行 MAC 或修改 SRAM。`trace_*` 是验证观测口，供逐周期比较，不参与软件 ABI。

## 运行与检查

需要 Icarus Verilog、Verilator、Yosys 和项目 Python 环境。在仓库根目录运行：

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -B tools/rtl/run_tile_rtl.py \
  --seeds 32 --output-dir dump/rtl_tile_20261028
python -m pytest -q -p no:cacheprovider src/tests/test_tile_rtl.py
```

也可在本目录执行 `make test PYTHON=/absolute/path/to/python`。`--no-synthesis` 可单独运行编译/仿真/lint；完整验收必须包含综合资源检查。

2026-10-28：**71 个用例、77 段任务执行全部通过**，其中含 32 个固定随机种子。逐边沿核对状态、读请求地址、同步返回值、MAC k、更新前后累加器、写地址/mask、全部 ResultBeat 字段与 Done；每段结束通过外部端口读回完整 1 KiB Result SRAM，并核对源端首个 Weight/Input word。覆盖：

- 5×3 手算、2×2、1×1、有符号极值、尾部 Lane、PSUM 和 `task_last=0`；
- 四种 Y_base word 对齐、非默认 W/X 基址、全局索引边界、Weight/Input/Result 容量边界；
- Result/Done/存储响应反压、任务字段锁存、忙碌期间的请求拒绝；
- 在 INIT、同步读返回、MAC、写回、结果阻塞、Done 阻塞阶段复位，保留 SRAM 并重新执行任务；
- 加载器逐 byte 写入中复位、三个区域的 byte-enable、非法存储请求；
- 22 个非法描述符与多错误优先级例子。

Yosys 执行 `check -assert`，并验证六块 SRAM 的几何参数、单读口/单写口和同步读；总存储量为 49,152 bit。Host/Compute 读写互斥，共用地址和服务控制，未增加同时访问端口。该检查不提供芯片面积、Fmax、功耗或宏时序结论。

[精选结果](../../../data/results/tile_rtl_20261028/results.json) 与 [5×3 逐周期对照](../../../data/results/tile_rtl_20261028/hand_5x3_trace.csv) 随仓库发布。完整 CSV、综合日志和 VCD 位于命令指定的输出目录；五份 VCD 对应手算、反压和三个复位阶段，用 Surfer 打开相应 `wave.vcd` 即可。GitHub Actions 对干净克隆运行全部回归，并保存波形/日志 artifact。

后续接口工作与另两台电脑的任务见 [并行推进计划](../../../docs/parallel_work.zh-CN.md)。
