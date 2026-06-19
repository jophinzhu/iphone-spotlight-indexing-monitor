# Implementation Plan: iPhone Spotlight Indexing Monitor

## Overview

实现一个运行在 Windows 上的 Python 3.11+ CLI 工具，通过 libimobiledevice (`idevicesyslog`) 经 USB 流式读取 iPhone 系统日志，过滤 Spotlight 索引相关行并解析、展示索引进度。实现遵循「I/O 层与纯逻辑层分离」的设计：先构建可用属性测试覆盖的纯逻辑组件（Config_Manager、Log_Filter、Progress_Parser、数据模型），再封装 I/O 组件（Device_Connector、Log_Streamer、Output_Display、Log_Writer），最后由 Indexing_Monitor 协调器串联全部模块。

属性测试使用 [Hypothesis](https://hypothesis.readthedocs.io/)，每个属性以单个测试实现，至少运行 100 次随机迭代，并以注释标注对应设计属性。

## Tasks

- [x] 1. 搭建项目结构与核心数据模型
  - 创建 Python 包目录结构（`src/spotlight_monitor/`、`tests/`）
  - 配置 `pyproject.toml`，声明依赖（`hypothesis`、`pytest`），并设置可打包为 Windows 可执行（PyInstaller）的入口
  - 实现核心不可变数据模型与枚举：`DeviceState`、`DeviceInfo`、`RawLogLine`、`FilterRule`、`ParseRule`、`IndexingProgress`、`AppConfig`、`ExportMode`、`StreamEvent`
  - 这些数据类（`@dataclass(frozen=True)`）作为各层共享类型，无副作用
  - _Requirements: 1.2, 3.1, 4.1, 4.5, 5.1, 7.2_

- [x] 2. 实现配置管理（Config_Manager，纯逻辑）
  - [x] 2.1 实现默认配置与配置校验
    - 实现 `ConfigManager.default_config()`，内置默认关键字 `spotlight、indexing、progress、mds、corespotlight` 与默认解析规则
    - 实现 `ConfigManager.validate(config)`，校验所有 `FilterRule.pattern` 与 `ParseRule.pattern` 可被 `re.compile` 成功编译，返回错误列表
    - _Requirements: 3.4, 5.4, 5.5_

  - [x] 2.2 编写正则校验属性测试
    - **Property 10: 正则校验正确性** — validate 接受 pattern 当且仅当其可被成功编译为正则
    - **Validates: Requirements 5.5**
    - 生成器需同时产出可编译与不可编译的字符串样本

  - [x] 2.3 实现配置的加载与保存（JSON 序列化）
    - 实现 `ConfigManager.save(path, config)`：校验后以 JSON 持久化
    - 实现 `ConfigManager.load(path)`：文件缺失则写默认配置并返回默认值；JSON 无效/字段非法/正则不可编译时不抛未捕获异常，返回内置默认并报告错误
    - 定义 `ConfigError` 异常类型
    - _Requirements: 5.1, 5.2, 5.3, 5.4_

  - [x] 2.4 编写配置序列化往返属性测试
    - **Property 9: 配置序列化往返** — 任意有效 AppConfig 经 save 再 load 得到等价对象
    - **Validates: Requirements 5.1, 5.4**

  - [x] 2.5 编写无效配置回退默认属性测试
    - **Property 11: 无效配置回退默认（边界）** — 任意无效/损坏配置内容下 load 不抛未捕获异常，返回默认配置并报告错误
    - **Validates: Requirements 5.3**

  - [x] 2.6 编写配置缺失生成默认的单元测试
    - 验证文件不存在时使用默认规则并生成默认配置文件
    - _Requirements: 5.2_

- [x] 3. 实现日志过滤（Log_Filter，纯逻辑）
  - [x] 3.1 实现关键字/正则匹配与流式过滤
    - 实现 `LogFilter.matches(line)`：匹配任一启用规则返回 True，支持大小写敏感/不敏感模式
    - 实现 `LogFilter.filter_stream(lines)`：仅产出匹配行，保持到达顺序（保序子集）
    - _Requirements: 3.1, 3.2, 3.3, 3.5_

  - [x] 3.2 编写过滤正确性属性测试
    - **Property 1: 过滤正确性（保留当且仅当匹配）** — 一行被保留当且仅当匹配至少一条启用规则，且输出为输入的保序子集
    - **Validates: Requirements 3.1, 3.2, 3.3**

  - [x] 3.3 编写默认关键字大小写不敏感匹配属性测试
    - **Property 2: 默认关键字大小写不敏感匹配** — 含默认关键字任意大小写变体的行在不敏感模式下判定为匹配
    - **Validates: Requirements 3.4**

  - [x] 3.4 编写大小写敏感性元变换属性测试
    - **Property 3: 大小写敏感性元变换** — 不敏感模式匹配集合是敏感模式匹配集合的超集
    - **Validates: Requirements 3.5**

- [x] 4. 实现进度解析（Progress_Parser，纯逻辑）
  - [x] 4.1 实现进度提取与规范化
    - 实现 `ProgressParser.normalize(value, scale_max)`：将 `[0, scale_max]` 规范化并钳制到 `[0, 100]`
    - 实现 `ProgressParser.parse(line)`：依据解析规则尝试提取进度，无法识别返回 None（非致命），成功则构造带 `source_line` 与 `observed_at` 的 `IndexingProgress`
    - _Requirements: 4.1, 4.2, 4.3_

  - [x] 4.2 编写进度规范化区间不变量属性测试
    - **Property 4: 进度规范化区间不变量** — 任意数值与 scale_max 规范化结果恒落在 [0, 100]
    - **Validates: Requirements 4.2**

  - [x] 4.3 编写进度解析往返属性测试
    - **Property 5: 进度解析往返** — 对 p ∈ [0,100] 构造匹配模板日志，解析得到的 percent 等于 p（浮点容差内）
    - **Validates: Requirements 4.1, 4.2**

- [x] 5. Checkpoint - 确保纯逻辑层测试通过
  - Ensure all tests pass, ask the user if questions arise.

- [x] 6. 实现日志写盘与导出（Log_Writer）
  - [x] 6.1 实现日志文件写入与按模式导出
    - 实现 `LogWriter.open(path)`：不可写时抛出错误
    - 实现 `LogWriter.write(line)`：写入一行并保留本机接收时间戳
    - 实现 `LogWriter.export(path, lines, mode)`：RAW 导出全部输入行，FILTERED 仅导出 Log_Filter 作用后的结果
    - _Requirements: 7.1, 7.2, 7.3, 7.4_

  - [x] 6.2 编写导出模式正确性属性测试
    - **Property 13: 导出模式正确性** — RAW 模式等于全部输入行，FILTERED 模式恰好等于过滤结果
    - **Validates: Requirements 7.2**

  - [x] 6.3 编写写盘往返属性测试
    - **Property 14: 写盘往返** — 写入后读回保留每行文本与接收时间戳，顺序不变
    - **Validates: Requirements 7.1, 7.3**

  - [x] 6.4 编写保存路径不可写的单元测试
    - 验证目标文件不可写时显示错误并停止保存操作
    - _Requirements: 7.4_

- [x] 7. 实现设备检测（Device_Connector，I/O）
  - [x] 7.1 实现设备枚举与配对/锁屏状态识别
    - 实现 `DeviceConnector.enumerate_devices(timeout_s)`：封装 `idevice_id -l` 与 `ideviceinfo`，5 秒内枚举设备并返回 `DeviceInfo` 列表
    - 实现 `DeviceConnector.get_pairing_state(udid)`：区分已配对/未配对/锁屏状态
    - _Requirements: 1.1, 1.2, 1.4, 6.5_

  - [x] 7.2 编写设备枚举与超时的集成测试
    - 使用 mock 子进程验证枚举、超时与状态识别（1–3 个代表性示例）
    - _Requirements: 1.1_

- [x] 8. 实现日志流采集（Log_Streamer，I/O）
  - [x] 8.1 实现流式采集、时间戳标注与生命周期管理
    - 实现 `LogStreamer.start(udid, sink)`：在独立线程中以阻塞方式逐行读取 `idevicesyslog` stdout，每行附「本机接收时间戳」并按到达顺序放入线程安全队列
    - 实现 `LogStreamer.stop()`：终止底层进程并释放资源
    - 实现 `LogStreamer.on_event(callback)`：检测子进程退出，回传退出码并发出 `DISCONNECTED`/`PROCESS_EXITED` 事件
    - _Requirements: 2.1, 2.2, 2.3, 2.5, 6.3_

  - [x] 8.2 编写到达顺序保持属性测试
    - **Property 8: 到达顺序保持** — 传递给下游的顺序与到达顺序一致
    - **Validates: Requirements 2.2**

  - [x] 8.3 编写流生命周期与异常退出的集成测试
    - 使用 mock 子进程验证启动逐行读取 (2.1, 2.2)、断开通知 (2.3)、停止释放 (2.5)、异常退出码捕获 (6.3)
    - _Requirements: 2.1, 2.3, 2.5, 6.3_

- [x] 9. 实现输出展示（Output_Display）
  - [x] 9.1 实现设备列表、日志行、进度与提示/错误展示
    - 实现 `show_devices`、`show_log_line`（附本机接收时间戳）、`show_notice`、`show_error`
    - 实现 `update_progress`：维护并展示「最近一次成功解析的进度」，未解析到新进度时持续展示上一次进度及其时间戳；尚无成功解析时不展示进度
    - CLI 进度区采用就地刷新 + 滚动日志区的混合输出
    - _Requirements: 1.2, 1.5, 4.3, 4.4, 4.5, 6.2, 6.5_

  - [x] 9.2 编写最近进度保持属性测试
    - **Property 6: 最近进度保持** — 任一时刻展示的最近进度等于至此最后一个成功解析的 IndexingProgress；无成功解析则不展示
    - **Validates: Requirements 4.4**

  - [x] 9.3 编写设备展示与选择、进度展示的单元测试
    - 验证设备列表展示 (1.2)、多设备选择 (1.5)、进度展示 (4.3)
    - _Requirements: 1.2, 1.5, 4.3_

- [x] 10. Checkpoint - 确保 I/O 层测试通过
  - Ensure all tests pass, ask the user if questions arise.

- [x] 11. 实现协调器与系统集成（Indexing_Monitor）
  - [x] 11.1 实现依赖检查与诊断日志
    - 实现 `IndexingMonitor.check_dependencies()`：检查 libimobiledevice 可执行文件与 USB 驱动可用性，缺失时列出具体缺失项与修复指引并终止启动
    - 实现 `IndexingMonitor.log_diagnostic(detail)`：将带时间戳、类别与详情的错误写入本地诊断日志文件
    - _Requirements: 6.1, 6.2, 6.4_

  - [x] 11.2 编写依赖检查与错误提示的单元测试
    - 验证缺失依赖提示 (6.2)、异常退出错误显示 (6.3)、诊断日志写入 (6.4)、锁屏提示 (6.5)
    - _Requirements: 6.2, 6.3, 6.4, 6.5_

  - [x] 11.3 实现规则热更新（不可变快照原子替换）
    - `ConfigManager` 持有当前规则的不可变快照，`apply_rules(config)` 原子替换快照引用，处理线程对后续行读取新快照
    - _Requirements: 5.6_

  - [x] 11.4 编写规则热更新等价属性测试
    - **Property 12: 规则热更新等价** — 热更新后对后续行的处理结果等于直接以新规则从头处理该行的结果
    - **Validates: Requirements 5.6**

  - [x] 11.5 实现主入口与处理流水线、连接监控状态机
    - 实现 `IndexingMonitor.run()`：串联依赖检查 → 枚举/选择设备（无设备/未配对提示）→ 启动流采集 → 处理循环（队列取行 → Log_Filter → Progress_Parser → Output_Display 与可选 Log_Writer）
    - 实现连接监控：检测到断开后进入「等待重连」状态，10 秒窗口内轮询 Device_Connector，设备回来自动重启流；超时或用户停止则结束
    - 将 received_at 贯穿过滤、解析、展示、写盘各阶段保持不变
    - _Requirements: 1.3, 1.4, 1.5, 2.3, 2.4, 4.5, 7.3_

  - [x] 11.6 编写跨阶段时间戳保持属性测试
    - **Property 7: 跨阶段时间戳保持** — RawLogLine 的 received_at 经过滤、解析、展示、写盘后保持不变
    - **Validates: Requirements 4.5, 7.3**

- [x] 12. Final checkpoint - 确保全部测试通过
  - Ensure all tests pass, ask the user if questions arise.

## Notes

- 标记 `*` 的子任务为可选（测试任务），可为加快 MVP 跳过；核心实现任务不标记可选。
- 每个任务引用具体的需求子条款以保证可追溯性。
- 属性测试覆盖设计文档 Property 1–14，每条属性以单个 Hypothesis 测试实现并标注属性编号与所验证的需求条款。
- 解析失败（返回 None）非致命，不中断日志流——应对 iOS 27 beta 1 日志格式不确定性的关键设计。
- 纯逻辑层（Config_Manager、Log_Filter、Progress_Parser）先于 I/O 层实现，便于尽早通过属性测试验证。

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1"] },
    { "id": 1, "tasks": ["2.1", "3.1", "4.1"] },
    { "id": 2, "tasks": ["2.2", "2.3", "3.2", "3.3", "3.4", "4.2", "4.3"] },
    { "id": 3, "tasks": ["2.4", "2.5", "2.6", "6.1", "7.1", "8.1", "9.1"] },
    { "id": 4, "tasks": ["6.2", "6.3", "6.4", "7.2", "8.2", "8.3", "9.2", "9.3", "11.1", "11.3"] },
    { "id": 5, "tasks": ["11.2", "11.4", "11.5"] },
    { "id": 6, "tasks": ["11.6"] }
  ]
}
```
