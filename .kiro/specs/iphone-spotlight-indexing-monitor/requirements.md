# Requirements Document

## Introduction

本功能旨在为 **Windows** 平台提供一个工具，用于读取通过 USB 连接的 iPhone 的实时系统日志，并从中提取、展示 Spotlight 索引（indexing）进度信息，从而替代用户原本只能在 Mac 上通过 Console.app（勾选 Include Debug Messages 并过滤 spotlight indexing progress）才能完成的操作。

用户场景：用户拥有一台升级到 iOS 27 beta 1 的 iPhone 16 Pro，设置界面长期显示 "indexing in progress" 但无可见进度。用户没有 Mac，只有 Windows 笔记本。本工具的核心目标是在 Windows 上获取并展示该索引进度信息。

技术背景：Mac Console 通过 usbmux/lockdown 协议经 USB 读取设备 os_log/syslog。Windows 上的等价开源方案为 libimobiledevice 套件中的 `idevicesyslog`，可经 USB 流式读取 iPhone 实时系统日志。工具将对日志输出按关键字（如 spotlight / indexing / progress / mds / corespotlight）进行过滤，以呈现索引进度。由于 iOS 各版本日志字段名与进度输出格式可能变化（iOS 27 beta 1 尤其不确定是否仍打印可读进度），工具必须支持可调整的过滤与解析规则。

## Glossary

- **Indexing_Monitor**: 本工具的主程序，负责协调设备连接、日志采集、过滤、解析与展示。
- **Device_Connector**: 负责检测并识别经 USB 连接的 iOS 设备的模块。
- **Log_Streamer**: 封装底层 `idevicesyslog`（libimobiledevice）以流式获取设备实时系统日志的模块。
- **Log_Filter**: 依据关键字/规则对原始日志行进行筛选的模块。
- **Progress_Parser**: 从已过滤的日志行中识别并提取索引进度数值（如百分比）的模块。
- **Config_Manager**: 负责加载、保存与校验用户可配置的过滤规则与解析规则的模块。
- **Output_Display**: 向用户展示过滤后日志行与索引进度的输出界面（命令行或图形界面）。
- **Filter_Rule**: 一条用于匹配日志行的规则，通常为关键字集合或正则表达式。
- **Indexing_Progress**: 从设备日志中提取出的、表示 Spotlight 索引完成程度的信息（百分比或阶段描述）。
- **iOS 设备**: 经 USB 连接到 Windows 主机的 iPhone 或 iPad。

## Requirements

### 需求 1：设备检测与识别

**用户故事（User Story）:** 作为用户，我希望工具能自动检测通过 USB 连接的 iPhone，以便我无需手动配置设备信息即可开始采集日志。

#### 验收标准（Acceptance Criteria）

1. WHEN 用户启动 Indexing_Monitor，THE Device_Connector SHALL 在 5 秒内枚举当前经 USB 连接的所有 iOS 设备。
2. WHERE 存在至少一台已连接且已配对的 iOS 设备，THE Device_Connector SHALL 显示每台设备的唯一标识（UDID）与设备名称。
3. IF 未检测到任何已连接的 iOS 设备，THEN THE Indexing_Monitor SHALL 显示提示信息，告知用户连接设备并解锁屏幕。
4. IF 检测到设备但该设备尚未与本主机配对，THEN THE Indexing_Monitor SHALL 提示用户在 iPhone 上点击"信任此电脑"。
5. WHERE 同时连接了多台 iOS 设备，THE Indexing_Monitor SHALL 允许用户选择要监控的目标设备。

### 需求 2：实时系统日志采集

**用户故事:** 作为用户，我希望工具能持续读取所选 iPhone 的实时系统日志，以便捕获索引相关的日志输出。

#### 验收标准

1. WHEN 用户对已选定的 iOS 设备开始监控，THE Log_Streamer SHALL 经 USB 启动该设备的实时系统日志流。
2. WHILE 日志流处于活动状态，THE Log_Streamer SHALL 将接收到的每一行日志按到达顺序传递给 Log_Filter。
3. IF 在监控期间 iOS 设备被拔出或连接中断，THEN THE Log_Streamer SHALL 停止当前流并通知 Indexing_Monitor 连接已断开。
4. WHERE 设备在断开后于 10 秒内重新连接，THE Log_Streamer SHALL 自动尝试恢复日志流。
5. WHEN 用户请求停止监控，THE Log_Streamer SHALL 终止底层日志流进程并释放相关资源。

### 需求 3：日志过滤

**用户故事:** 作为用户，我希望工具只展示与 Spotlight 索引相关的日志行，以便我能在海量系统日志中聚焦于进度信息。

#### 验收标准

1. WHEN Log_Filter 收到一行日志，THE Log_Filter SHALL 依据当前生效的 Filter_Rule 集合判断该行是否匹配。
2. WHERE 一行日志匹配任一启用的 Filter_Rule，THE Log_Filter SHALL 将该行传递给 Progress_Parser 与 Output_Display。
3. WHERE 一行日志未匹配任何启用的 Filter_Rule，THE Log_Filter SHALL 丢弃该行且不传递给 Output_Display。
4. THE Indexing_Monitor SHALL 提供一组默认 Filter_Rule，至少包含关键字 spotlight、indexing、progress、mds、corespotlight（不区分大小写）。
5. WHEN 用户启用大小写敏感匹配选项，THE Log_Filter SHALL 按区分大小写的方式进行匹配。

### 需求 4：索引进度解析与展示

**用户故事:** 作为用户，我希望工具能从过滤后的日志中识别并清晰展示索引完成进度，以便我了解当前进度状态。

#### 验收标准

1. WHEN Progress_Parser 收到一行已过滤日志，THE Progress_Parser SHALL 依据当前生效的解析规则尝试提取 Indexing_Progress 数值。
2. WHERE 一行日志中包含可识别的进度数值，THE Progress_Parser SHALL 将该数值规范化为 0 到 100 之间的百分比。
3. WHEN 成功提取到新的 Indexing_Progress，THE Output_Display SHALL 更新并展示最新的进度数值及其对应的原始日志行。
4. WHERE 一段时间内未提取到任何进度数值，THE Output_Display SHALL 持续展示最近一次成功提取的 Indexing_Progress 及其时间戳。
5. THE Output_Display SHALL 为每一条展示的日志行附带本机接收时间戳。

### 需求 5：可配置的过滤与解析规则

**用户故事:** 作为用户，我希望能够调整过滤关键字与进度解析规则，以便在 iOS 版本变化导致日志格式改变时仍能继续提取进度。

#### 验收标准

1. WHEN Indexing_Monitor 启动，THE Config_Manager SHALL 从配置文件加载用户定义的 Filter_Rule 与解析规则。
2. IF 配置文件不存在，THEN THE Config_Manager SHALL 使用内置默认规则并生成一份默认配置文件。
3. IF 配置文件存在但格式无效，THEN THE Config_Manager SHALL 显示错误信息并回退到内置默认规则。
4. WHEN 用户保存修改后的规则，THE Config_Manager SHALL 校验规则的有效性并将其持久化到配置文件。
5. WHERE 用户提供的进度解析规则为正则表达式，THE Config_Manager SHALL 校验该正则表达式可被成功编译。
6. WHEN 用户在监控期间应用新的规则集，THE Indexing_Monitor SHALL 在不重启日志流的前提下对后续日志行应用新规则。

### 需求 6：错误处理与依赖检查

**用户故事:** 作为用户，我希望工具在缺少依赖或发生错误时给出清晰提示，以便我能快速定位并解决问题。

#### 验收标准

1. WHEN Indexing_Monitor 启动，THE Indexing_Monitor SHALL 检查所需的底层组件（libimobiledevice 相关可执行文件与 USB 驱动）是否可用。
2. IF 所需的底层组件缺失或不可执行，THEN THE Indexing_Monitor SHALL 显示具体缺失项及修复指引。
3. IF 底层日志采集进程异常退出，THEN THE Indexing_Monitor SHALL 捕获其退出状态并向用户显示错误原因。
4. WHEN 发生任一错误，THE Indexing_Monitor SHALL 将错误详情写入本地日志文件以供排查。
5. IF iOS 设备已连接但因被锁定而无法读取日志，THEN THE Indexing_Monitor SHALL 提示用户解锁设备。

### 需求 7：原始日志保存与导出

**用户故事:** 作为用户，我希望能将采集到的（过滤前或过滤后的）日志保存到文件，以便在工具无法自动解析进度时进行人工分析或寻求帮助。

#### 验收标准

1. WHERE 用户启用了日志保存选项，THE Indexing_Monitor SHALL 将采集到的日志行写入用户指定路径的文件。
2. WHEN 用户导出日志，THE Indexing_Monitor SHALL 允许用户选择导出全部原始日志或仅导出过滤后的日志。
3. WHILE 日志保存处于启用状态，THE Indexing_Monitor SHALL 为每一条写入的日志行保留其本机接收时间戳。
4. IF 指定的保存路径不可写，THEN THE Indexing_Monitor SHALL 显示错误信息并停止保存操作。
