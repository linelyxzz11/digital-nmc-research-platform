# 测试

在仓库根目录运行：

```bash
python -m pytest -q -p no:cacheprovider
python -m pytest -q -p no:cacheprovider src/tests/test_demo0_functional.py
python -m pytest -q -p no:cacheprovider src/tests/test_communication_v0_1.py
python -m pytest -q -p no:cacheprovider src/tests/test_nmc_demo1.py
python -m pytest -q -p no:cacheprovider src/tests/test_nmc_demo2.py
python -m pytest -q -p no:cacheprovider src/tests/test_mapper_v0_1.py
```

当前共 105 项 pytest 回归：Compute Demo 0（17 项）、Packet/Comm 0A–0C（10 项）、NMC Demo 1（18 项）、NMC Demo 2（34 项）和 Mapper v0.1（26 项）。Demo 1 验证行切分/FINAL 收集；Demo 2 验证 K 切分/PSUM 归约、逐索引来源 mask、重复来源拒绝、INT32 wrap、到达顺序和反压。NMC RTL 等价尚待验证。

Mapper 回归将生成计划实例化为现有 TileTask、MEM_WRITE 和 GatherConfig，与 Demo 1/2 对照任务、预加载字节和 ACK 集合，再通过现有功能模型与 Root 端点核对数值和完成次数。另覆盖 auto 容量回退、填充占用、非法输入以及 JSON CLI。它验证计划兼容性，不代表 Demo CLI 已接入自动计划或已经实现完整周期模型。
