# EHE Skill 0.1.1

在新 Codex 会话选择 `$embedded-hardware-engineer` 后，直接描述要审查、整改或设计的板子。启动器从已安装 Skill 读取运行环境，在其他项目也能调用 `ehe`。

## 审查和整改

`review full` 运行实际 ERC、DRC。存在同名的 KiCad 配套工程时，另报告原理图与 PCB 的一致性。板框取原生 Edge.Cuts。EMI、回流、机械、温升、掉压、公差和实物各有状态。缺输入的检查列为未运行，源文件与测试记录保留出处。关键整改按位置、机理、具体改法、影响和复验输出。

## 设计交付

Skill 会把需求转成选型、原理图、PCB、BOM 和验证任务。原生工程完成后用 `design verify` 核对规则、原理图与 PCB 网络一致性和 KiCad 导出的 BOM。报告分别给出 `eda_status`、`handoff_status` 和实物证据状态。

设计交付路径用从空目录生成的连通性测试板验证。默认整板审查发现原理图 `/LINK` 与 PCB `LINK` 不一致。修正测试副本后原生检查通过，采购料号和实物验收仍列为缺项。含电机驱动和模拟采集的整板设计尚待专项验收。

## 本次验证

- 37 项自动测试通过，覆盖缺项报告、全局启动、原生网表冲突和修复复验。 [测试日志](../logs/skill-upgrade-tests.txt)
- 36 项公开金标准通过，与 0.1.0 基线对照无退化。 [评测报告](../logs/skill-upgrade-gold.json)
- 从项目外的新目录发现全局 Skill。使用登记的解释器运行整板审查和修复验证，原生输入在检查前后哈希相同。 [全局试用证据](../logs/skill-upgrade-global-smoke.json)
