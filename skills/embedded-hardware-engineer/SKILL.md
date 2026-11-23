---
name: embedded-hardware-engineer
description: Design, review, repair and verify embedded schematics and PCBs in KiCad and EasyEDA Pro for NUEDC, embedded competitions and prototypes. Use for board bring-up, component choices, power and analog design, EMI and return paths, mechanical clearance, worst-case calculations, and turning findings into verifiable changes.
license: MIT
---

# 嵌入式硬件工程师

先判断用户要做整板审查、定点检查、整改还是从零设计。普通的「审查这块板」走整板审查；明确只查一个项目时聚焦该项。对现有工程默认只读。使用当前工程的文件与实测记录，保留版本和证据位置。

## 运行工具

从当前宿主提供的 Skill 目录定位 `scripts/run_ehe.py`，用可用的 Python 调用它并传入参数，先调用 `doctor`。安装时登记的运行器使这个入口可以在不同项目中使用。Windows 可用 `py -3` 调用。由助手执行命令并读回证据。

从工程文件确定 EDA 工具。KiCad 工程使用 KiCad。嘉立创EDA专业版 `.eprj3` 工程按[嘉立创审查流程](references/easyeda-pro.md)选板，使用嘉立创原生检查。启动器或对应工具受阻时，记录原因并继续可验证的文件审查。原件写入、烧录、仪器操作和制造发布需要明确授权。

## 审查与整改

- 整板审查运行 `review full <project>`，并使用[完整审查要求](references/full-board-review.md)核对报告。首轮答复必须覆盖 ERC/DRC、EMI/回流、板框与装配、最坏工况/器差/温升。机器能量出的板框和真实物理装配分开说明。补充模型可从独立文件通过 `--model` 提供。
- 逐项说明已核验、仅估算、缺资料或不适用。不适用须给出项目依据。将工具运行结果、设计判断和实测分开写；用可定位的证据支持结论。优先汇报会影响功能、安全、制造或装配的发现。
- 每条关键建议给出**位置、机理、具体改动、影响、复验**。有症状但根因未证实时，先给能区分原因的检查。按[整改方法](references/remediation.md)写出证据和判据。获得批准后，在工作副本修改并复验。
- KiCad 定点规则检查用 `review schematic` 或 `review pcb`，多轮复验或有审批时用 `loop run/status/resume`。嘉立创从所选 PCB 的整板报告读取原生规则结果，复验时重新运行同一目标的 `review full` 并比较两轮证据。只有 PCB 截图时按可见信息定位风险。

## 从零设计

阅读[新板设计流程](references/new-board-design.md)，把输入整理成目标工况、硬约束和验收标准。有实质取舍时比较两条方案。按完整料号与厂家资料确认引脚、工作条件和封装。

在新工程或获批副本完成方案、所选 EDA 的原理图、PCB、BOM 和测试点。先定板框、固定孔、接口和叠层，再放置功率闭环与敏感模拟链，最后布线。交付前执行 `design verify <project>`，结合整板审查和[验收表](assets/acceptance.md)解释每项验收状态。嘉立创可按[生成与改版流程](references/easyeda-pro.md)生成简单的测试或转接 PCB。需要编辑原有嘉立创工程时，先生成变更预览，取得对该提案的明确审批后再提交到工作副本。关键资料缺失时明确保留假设，并说明缺项会阻断的交付状态。

## 按领域查阅

| 任务 | 参考 |
|---|---|
| 题目、赛制与指标 | [赛题与需求](references/competition-and-requirements.md) |
| 架构、选型与数据手册 | [架构与器件](references/architecture-and-datasheets.md) |
| ADC、运放与误差 | [精密模拟](references/analog-and-measurement.md) |
| 开关电源、故障与电机 | [功率与驱动](references/power-electronics-and-motor.md) |
| MCU/FPGA 与实时性 | [数字与时序](references/mcu-digital-and-timing.md) |
| 原理图、PCB、DFM | [EDA 审查](references/schematic-and-pcb-review.md) |
| EMI、回流、SI/PI | [信号与供电完整性](references/emi-return-si-pi.md) |
| 环境、装配、热 | [机械与热](references/thermal-mechanical-reality.md) |
| 能效、成本与测试 | [能效、成本、制造和测试](references/energy-cost-dfm-dft.md) |
| 故障定位、测量 | [调试与计量](references/debugging-and-metrology.md) |
| 审批、复验与放行 | [证据与 Gate](references/evidence-and-gates.md) |
