# EHE 更新日志

## 未发布 · 2026-11-23

- 建立更新日志，在 README 添加入口。项目规则要求每次更新后补记实际变更和验证。
- **ZCode 宿主。** 安装器增加 `--host zcode`，把 Skill 装到 `.zcode/skills`；`--host all` 覆盖 Codex、Claude Code 和 ZCode，`both` 仍只含前两者。`probe_hosts.py --zcode` 检查 ZCode 在项目中的发现，只统计被测项目内的副本，已有用户级副本时不再误报。Codex 和 ZCode 的发现均已实测。 [探测记录](logs/skill-upgrade-probe-zcode.txt)
- **安装范围。** 用户级 `.agents/skills` 由外部工具同步，直接装入的副本在 2026-11-23 被清除过，用户级安装因此改用各宿主自己的技能目录：Codex 用 `--codex-home` 装到 `~/.codex/skills`，ZCode 用 `--host zcode` 装到 `~/.zcode/skills`。两处用户级副本和本项目的 `.agents/skills` 副本与源码逐字节一致，启动器在项目外解析到 0.1.1。项目内已有 `.agents/skills` 副本时，ZCode 会把 Skill 列出两次。 [Codex 安装](logs/skill-upgrade-install-global.txt) · [ZCode 安装](logs/skill-upgrade-install-zcode-user.txt)
- **验证。** 40 项自动测试通过。开发环境的可编辑安装元数据此前仍是 0.1.0，重新安装后 `pip list` 与 CLI 一致显示 0.1.1。 [测试日志](logs/skill-upgrade-hosts-tests.txt) · [重装记录](logs/skill-upgrade-editable-reinstall.txt)

## 0.1.3 · 2026-11-23

- **网表与 BOM。** 从嘉立创客户端导出所选 PCB 和关联原理图页的网表，核对器件、封装和针脚网络。交付验证保存原生 BOM，校验位号、数量、装配排除和采购料号。RA6E2 的 PCB3 检查副本中，PCB 有 35 个器件，关联页有 0 个可传到 PCB 的器件，原生 BOM 有 20 行、32 个位号。副本的规则文件曾被编辑器改写，原始版本保留待核验。 [原生网表与 BOM 验证](logs/easyeda-0.1.3-native-exports.json)
- **自动画板。** 固定焊盘位置、板框和布线约束后，可自动生成单面铜测试板或简单转接板，给出真实 `.eprj3` 和 PCB 源文件。30 × 20 mm 样板及其获批改版均通过嘉立创原生 DRC。 [生成规格](examples/easyeda-interconnect.json)
- **审批改版。** 支持 PCB 走线宽度修改、同 UUID 的完整 PCB 文件替换、变更预览、哈希锁定、显式审批、独立基线备份、版本化工作副本及回滚复验。测试验证先拦截未审批变更，再把 SIGNAL 走线从 0.30 改为 0.45 mm。测试源随后变动，仍能从备份恢复获批前的 PCB，哈希一致且原生 DRC 通过。 [生成、审批和回滚记录](logs/easyeda-0.1.3-authoring.json) · [原始工具证据](logs/easyeda-0.1.3-native-evidence.zip)
- **回归与部署。** 51 项自动测试、36 项公开评测通过。Codex 全局和工作区 Skill 同步至 0.1.3。PRD core 发布门禁保持 blocked。 [自动测试](logs/easyeda-0.1.3-tests.txt) · [公开评测](logs/easyeda-0.1.3-gold-final.json) · [全局验证](logs/easyeda-0.1.3-global-smoke.json)
- **边界。** 跨页或分层网表校验、多层与通用器件自动布局、自动画原理图、原件覆盖仍为 not_implemented。嘉立创原生会话可能超时，保留受阻结果，PCB 自动生成和写回覆盖为 partial。 [兼容性矩阵](docs/compatibility-matrix.md)

## 0.1.2 · 2026-11-23

- **嘉立创EDA专业版。** `review full` 支持本地 `.eprj3` 工程和指定 PCB，按项目关联选择原理图页。在隔离副本调用原生规则，报告附网络、对象、位置、整改和复验。可以离线读取矩形板框、层、位号、焊盘网络、直线走线段和过孔。整板报告继续列出 EMI、装配、最坏工况和实物验证的证据缺项。
- **客户端实测。** Windows 的嘉立创 4.1.60 在 RA6E2 样例中返回 PCB3 的 173 条原生 DRC 违规及关联页 P1 的规则通过结果，标称板框为 56.515 × 27.051 mm。检查副本曾被编辑器改写，报告限定原生结果的适用版本，原件哈希验证一致。 [原生摘要](logs/easyeda-0.1.2-native-review.json) · [原始证据包](logs/easyeda-0.1.2-native-evidence.zip)
- **部署。** Codex 用户级和当前工作区 Skill 同步到 0.1.2。项目外发现全局副本，启动器调用 0.1.2 完成离线选板审查。 [全局验证](logs/easyeda-0.1.2-global-smoke.json)
- **回归。** 46 项自动测试和 36 项公开金标准通过。在独立客户端测试副本中删除 PCB1 的 P013 走线，原生 DRC 检出 2 条未连接，重建走线后 DRC 通过。日志保留原生客户端的启动超时和受阻尝试。PRD core 发布门禁仍为 blocked。 [自动测试](logs/easyeda-0.1.2-tests.txt) · [公开评测](logs/easyeda-0.1.2-gold-final.json) · [断线试验](logs/easyeda-0.1.2-fault-review.json)
- **能力边界。** 嘉立创范围目前为只读。网表一致性、原生 BOM、自动生成原理图或 PCB、工程写回和嘉立创 Loop 未实现。EMC、装配和实测指标依赖各自的工程证据。 [兼容性矩阵](docs/compatibility-matrix.md)

## 0.1.1 · 2026-11-23

- **Codex 全局启动。** Skill 可以从其他项目调用登记的运行器。已同步当前项目副本，并从项目外验证全局发现和运行。 [全局试用记录](logs/skill-upgrade-global-smoke.json)
- **整板审查。** `review full` 增加原理图与 PCB 一致性检查、原生板框尺寸和逐项覆盖报告。EMI、回流、装配、温升、掉压、器差和实物验证缺输入时，会显示缺项。修复了 `budget --scope all` 在缺少整类输入时仍可能显示通过的问题。
- **整改与设计。** 关键建议按位置、机理、改法、影响和复验输出。增加从零设计流程和 `design verify`，用 KiCad 核验原生工程并导出 BOM。测试板的原理图与 PCB 网络名不一致得到定位，修正副本后原生检查通过。 [设计与验证说明](docs/skill-0.1.1.md)
- **验证。** 37 项自动测试和 36 项公开评测通过。电机驱动与模拟采集整板仍待专项验收。 [测试日志](logs/skill-upgrade-tests.txt) · [评测报告](logs/skill-upgrade-gold.json)

## 0.1.0 · 2026-11-23

- 首次交付工程预览版，包含共享 Skill、独立工程 Loop、KiCad 原生 ERC/DRC、工程计算、知识库和独立评测。
- 实测 KiCad 断线检出、暂停与修复复验，以及获批工作副本变更的恢复。嘉立创EDA 验证了真实会话及 PCB 元数据读取，Altium 验证了真实工程清单解析。
- Windows 的 Python 3.11 和 3.14 各通过 31 项测试，36 项公开评测连续两轮通过。实物验收未运行，PRD `core` 发布门禁保持阻断。 [交付实证](docs/verification.md)
