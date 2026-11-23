# EHE 更新日志

## 未发布 · 2026-11-23

- 建立更新日志，在 README 添加入口。项目规则要求每次更新后补记实际变更和验证。
- **ZCode 宿主。** 安装器增加 `--host zcode`，把 Skill 装到 `.zcode/skills`；`--host all` 覆盖 Codex、Claude Code 和 ZCode，`both` 仍只含前两者。`probe_hosts.py --zcode` 检查 ZCode 在项目中的发现，只统计被测项目内的副本，已有用户级副本时不再误报。Codex 和 ZCode 的发现均已实测。 [探测记录](logs/skill-upgrade-probe-zcode.txt)
- **安装范围。** 用户级 `.agents/skills` 由外部工具同步，直接装入的副本在 2026-11-23 被清除过，用户级安装因此改用各宿主自己的技能目录：Codex 用 `--codex-home` 装到 `~/.codex/skills`，ZCode 用 `--host zcode` 装到 `~/.zcode/skills`。两处用户级副本和本项目的 `.agents/skills` 副本与源码逐字节一致，启动器在项目外解析到 0.1.1。项目内已有 `.agents/skills` 副本时，ZCode 会把 Skill 列出两次。 [Codex 安装](logs/skill-upgrade-install-global.txt) · [ZCode 安装](logs/skill-upgrade-install-zcode-user.txt)
- **验证。** 40 项自动测试通过。开发环境的可编辑安装元数据此前仍是 0.1.0，重新安装后 `pip list` 与 CLI 一致显示 0.1.1。 [测试日志](logs/skill-upgrade-hosts-tests.txt) · [重装记录](logs/skill-upgrade-editable-reinstall.txt)

## 0.1.1 · 2026-11-23

- **Codex 全局启动。** Skill 可以从其他项目调用登记的运行器。已同步当前项目副本，并从项目外验证全局发现和运行。 [全局试用记录](logs/skill-upgrade-global-smoke.json)
- **整板审查。** `review full` 增加原理图与 PCB 一致性检查、原生板框尺寸和逐项覆盖报告。EMI、回流、装配、温升、掉压、器差和实物验证缺输入时，会显示缺项。修复了 `budget --scope all` 在缺少整类输入时仍可能显示通过的问题。
- **整改与设计。** 关键建议按位置、机理、改法、影响和复验输出。增加从零设计流程和 `design verify`，用 KiCad 核验原生工程并导出 BOM。测试板的原理图与 PCB 网络名不一致得到定位，修正副本后原生检查通过。 [设计与验证说明](docs/skill-0.1.1.md)
- **验证。** 37 项自动测试和 36 项公开评测通过。电机驱动与模拟采集整板仍待专项验收。 [测试日志](logs/skill-upgrade-tests.txt) · [评测报告](logs/skill-upgrade-gold.json)

## 0.1.0 · 2026-11-23

- 首次交付工程预览版，包含共享 Skill、独立工程 Loop、KiCad 原生 ERC/DRC、工程计算、知识库和独立评测。
- 实测 KiCad 断线检出、暂停与修复复验，以及获批工作副本变更的恢复。嘉立创EDA 验证了真实会话及 PCB 元数据读取，Altium 验证了真实工程清单解析。
- Windows 的 Python 3.11 和 3.14 各通过 31 项测试，36 项公开评测连续两轮通过。实物验收未运行，PRD `core` 发布门禁保持阻断。 [交付实证](docs/verification.md)
