# PRD v3.0 需求覆盖

状态为 implemented、partial、blocked 或 not_implemented。测试记录同时保留了诊断和修复过程。公开样例通过表示限定测试路径满足对应判分。

## M0–M6

| 阶段 | 状态 | 实际验收 |
|---|---|---|
| M0 审计与基线 | implemented | 工作区原有三份交接文件，无旧版 Skill。PRD 哈希、真实环境探测、风险登记、初始测试。logs/M0.txt、logs/M0-doctor.txt |
| M1 Skill 与统一数据 | partial | 共享 Skill、模板、Schema、需求抽取、基础预算投入使用。完整非结构化输入抽取和证据绑定追踪仍有缺口。logs/M1.txt、logs/M1-skill.txt |
| M2 真实 KiCad 与通用导出 | implemented | KiCad 10.0.6 ERC/DRC 真实运行，正确样板与断线故障对照，BOM/网表/诊断导入。E3 限对象子集。logs/M2.txt、logs/demo/artifact-index.json |
| M3 工程 Loop | implemented | 项目内持久化、审批、DAG、预算、恢复、停滞与回归。真实整改演示、进程崩溃和原始结果篡改测试。logs/M3.txt、logs/demo |
| M4 Reality 与高级工程 | partial | 正常和异常模型、公式、裕量、证据分级。几何精度、自动拓扑证据和实物验证缺失。logs/M4.txt、logs/M4-model.txt |
| M5 Knowledge / EvalOps | partial | 36 个公开开发金标准、版本对照、发布阻断、PDF 和工程对象来源索引、隔离注册表。未提供独立私有盲测组。logs/M5.txt、logs/M5-run1.json、logs/release-eval.json |
| M6 多 EDA / 受控改板 | partial | 获批 KiCad 和嘉立创 PCB 工作副本，嘉立创原生网表/BOM、单面测试板生成和部分几何，Altium 清单和通用导出。通用 PCB 自动设计、Altium 原生规则和深度仿真仍缺。见兼容矩阵与 0.1.3 更新日志 |

## RQ 与硬件能力

| ID | 状态 | 边界 |
|---|---|---|
| RQ-01 | partial | Markdown/PDF 文本段落、量值、范围、评分、条件与冲突候选。图像走可选 Tesseract 路径，未实测，复杂表格、扫描 PDF、跨页约束与附件须人工处理 |
| RQ-02 | partial | Skill 方案模板、约束筛选和 Pareto。提供用户建模的候选比较，未建立自动选型和文档参数核验服务 |
| RQ-03 | partial | 需求到设计元素、验证方法和证据 ID 的映射。未自动确认赛题或硬件验收 |
| HW-01 | partial | 峰值轨预算及电池瞬态。复位、供电时序和反接路径依赖适用数据 |
| HW-02 | partial | 共模、摆幅、建立、压摆、带宽和绝对误差预算。噪声频谱、稳定性仿真未覆盖 |
| HW-03 | partial | 引脚重复与声明的电压限值。芯片复用、启动、调试和时钟资源核对由 Skill 引导 |
| HW-04 | partial | 最坏串行延迟和吞吐。调度证明、真实固件轨迹集成未完成 |
| HW-05 | partial | 冷态堵转、驱动 SOA 时间与估算温升。编码器、续流和控制稳定性依赖现场数据 |
| HW-06 | partial | Buck CCM 纹波、饱和及限流预算。Boost、补偿和磁芯的完整仿真未实现 |
| HW-07 | partial | KiCad ERC、嘉立创关联原理图页的原生规则、声明引脚及 BOM 审查。设计语义中电源域、偏置与去耦尚无全图覆盖 |
| HW-08 | partial | 原生 DRC，几何及层/网/器件子集。封装、叠层材料和项目制造规则仍需用户确认 |
| HW-09 | partial | 干扰源、耦合和受扰体、热环路面积。EMC 信号发生、频谱与认证均未运行 |
| HW-10 | partial | 声明参考面窗口的几何定位及换层耦合，缺失证据转 inconclusive。未重建所有原生铜区的回流网络 |
| HW-11 | partial | 单线路传播和阻性端接反射风险。串扰、差分不对称、眼图需要模型和波形 |
| HW-12 | partial | PDN 目标阻抗与 RLC 离散频点评估。未解析完整叠层寄生与连续频率响应 |
| HW-13 | partial | 投影开关环路、Buck 振幅和器件损耗估算。自动追踪开关状态和真实回路需拓扑数据 |
| HW-14 | partial | 限定 DC 模型和保护曲线条件、故障电流、I²t。真实异常测试未运行 |
| HW-15 | partial | 有效热阻结温和温度条件。复杂换热、老化寿命和热实测未运行 |
| HW-16 | partial | 板框、器件、连接器、线束、工具空间包络。STEP 与实体 3D 干涉未实现 |
| HW-17 | partial | KiCad DRC、BOM 位号，制造和治具审查指导。专用 DFM/DFA/DFT 验证未实现 |
| HW-18 | partial | 六状态、外围功耗、分状态效率、续航估计。没有实测证据 |
| HW-19 | partial | 供应商、报价日期、交期、采购量、成本和 Pareto。实时价格、可替代料验证与供应风险监控未实现 |
| HW-20 | not_implemented | 仅射频审查参考，未提供可执行链路或天线仿真 |
| HW-21 | partial | 计量模板、数据文档导入与来源索引。独立仪表数据链与 L5 测试未实现 |
| HW-22 | partial | 声明条件的峰值供电、故障曲线和热降额。ESD、浪涌、反接试验、保护协调待实测 |

## 现实约束、集成与评测

| 需求 | 状态 | 边界或证据 |
|---|---|---|
| RL-01 机械 | partial | 多对象三轴包络、公差、扫掠、重量及禁区 |
| RL-02 偏差 | partial | 公差温漂边界、低温内阻/线束/连接器、有效热阻和解析敏感度。未实现 Monte Carlo、通用 PCB 寄生拟合 |
| RL-03 能耗 | partial | 状态完整性、效率输入与电池估算，容量老化使用明示降额 |
| RL-04 成本 | partial | 阶梯价、MOQ、日期、交期、成本项、硬约束与 Pareto。部分供应字段需人工维护 |
| RL-05 联动 | implemented | 改器件、板框、PWM、电源的影响域及关键回归 |
| 统一工程数据 §8 | partial | EDA 对象、Evidence/Finding/Gate/Goal 等 Schema 已测。实测与决策的跨项目证据联结未闭合 |
| KiCad E2 | implemented | 10.0.6 原生 JSON，输入哈希和退出码有独立复核 |
| KiCad E3 | partial | 板对象子集，复合铜区和材料模型未闭合 |
| KiCad E4 | partial | 审批绑定文件替换到版本副本及复验，提交至原件或任意对象写回无实现 |
| 嘉立创EDA | partial | 实测 4.1.60 隔离副本原生 ERC/DRC、单关联页原生网表对照和 PCB BOM。支持可自动出图的单面测试板、审批后的 PCB 线宽修改和同 UUID 文件替换。多页网表、自动原理图、多层和完整器件布局仍有缺口 |
| Altium | partial | 真实 PrjPcb 文档清单和通用导出。26.1.1.7 原生执行 blocked |
| EDA 通用格式 | implemented | E1 CSV/TSV BOM、XML 网表、JSON 规则报告。Gerber、钻孔与其他网表未实现 |
| Loop 权限和预算 §9 | implemented | 状态、恢复、DAG、预算、审批、日志、影响域与关键回归。审批身份作为本机审计记录 |
| 知识 §10 | partial | 本地索引、授权记录、检索、冲突、近重复隔离。无嵌入服务、真实盲测语料或模型微调 |
| EvalOps L1–L4 | partial | 公共 gold AT01–AT36、真实文件、软件失效注入、版本回归和重复运行。模型间 A/B、设备矩阵、私有 holdout 未验证 |
| EvalOps L5 | blocked | 物理验收无真实实验输入或现场测量，运行状态为 not_run |
| 发布 §11.4 | partial | 独立质量门禁、真实回归比较。正式 core 放行被明确阻断 |
| 宿主 §12 | partial | CLI、Skill 安装与 Codex/Claude Code 实际发现。两宿主在线工具调用未验收 |
| 深度 3D / 仿真 / 多物理 | not_implemented | AABB 与解析模型有可运行审查，未接 SPICE、3D 碰撞求解、EMC 仿真 |
| 外部副作用 | not_implemented | 未提供烧录、仪器控制、上传、采购、制造发布或原件写回 |
| 工件交付 §16 | implemented | 本地源码仓库、PRD、Skill、模板、Schema、安装 wheel、验证日志和完整 ZIP |
