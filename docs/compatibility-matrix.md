# 已验证的兼容性

测试日期为 2026-11-23（中国标准时间）。等级按 PRD v3.0 的证据定义归类。

| 平台与版本 | E0 文档 | E1 导出 / 索引 | E2 原生规则 | E3 几何 | E4 写回 |
|---|---|---|---|---|---|
| KiCad 10.0.6，Windows | implemented | implemented，JSON、XML 和 BOM 的所列格式 | implemented，原生 ERC/DRC，干净及断线 | partial，足印、焊盘、网络、图元、铜区和板框原始字段 | partial，审批后仅同类型文件替换到版本副本 |
| 嘉立创EDA专业版 4.1.60.198f38ab，Windows | implemented，连接、打开、关闭和编辑器执行探测 | partial，工程索引、原生 BOM、单关联页和 PCB 的原生网表对照 | partial，原生 PCB DRC 和原理图规则检查，检查前后核对身份和输入哈希 | partial，标称矩形板框及部分对象，单面测试或转接板原生生成 | partial，线宽变更或同 PCB UUID 文件替换，明确审批、版本副本、回滚及复验 |
| Altium Designer 26.1.1.7，Windows | implemented，安装发现及真实 PrjPcb 文档清单 | partial，通用 BOM / 规则导出路径经过受控导出样例验证 | blocked，未确认授权运行的脚本及原生规则场景 | not_implemented | not_implemented |
| 通用导出 | implemented，未结构化材料保留 E0 | implemented，CSV/TSV、KiCad XML 和指定 JSON | not_implemented | not_implemented | not_implemented |

- KiCad 依据本机帮助和报告版本核对。原创 continuity 用例及安装目录中的 Stefan Hamminga StickHub 样例实际运行。分发包保留后者的工具日志、哈希和来源，第三方设计源文件仅在本机验证缓存中。 [KiCad CLI 文档](https://docs.kicad.org/10.0/en/cli/cli.html)
- 嘉立创测试使用官方 viewer 的 RA6E2 文件夹工程。PCB3 的 DRC 报告了未连接，关联 P1 的原理图检查返回通过。客户端曾重写检查副本的索引和规则配置，原始工程哈希保持一致。有实质变更时，报告保留副本结果，原始版本状态为待核验。当前路径需要本地 `.eprj3` 及配套文件，客户端初始化仍可能超时。 [原生审查证据](../logs/easyeda-0.1.2-native-review.json) · [官方参考工程](https://github.com/easyeda/easyeda-viewer/tree/5cdb87c9562d35d98a55317589efcf0de8d55879/samples/RA6E2-eprj3) · [原生 CLI 文档](https://prodocs.lceda.cn/en/api/guide/cli.html)
- 同一嘉立创参考工程的 PCB1 副本中，原生 DRC 在 P013 走线删除前通过，删后报告 2 条未连接，重建走线后再次通过。该试验仅在已核对的临时会话内执行，独立于 Skill 面向用户的只读路径。 [断线试验和恢复](../logs/easyeda-0.1.2-fault-review.json)
- 嘉立创生成器接受用户明确给定的几何和两端网络，产出可继续在客户端编辑的原生测试板。多层、通用器件自动摆放和自动画原理图仍为 not_implemented。变更只能提交到版本化工作副本。 [生成与审批测试](../logs/easyeda-0.1.3-authoring.json)
- Altium 用 Josephulrich 公开项目中的真实 STM32F103C8T6.PrjPcb 文件验证清单读取，提交 9e78d13358d3396c47eb3f1164060ee9f0c86d91。原生规则或脚本运行和授权状态均未验证。 [公开原始项目](https://github.com/Josephulrich/design-blue-pill-stm32f103c8t6/tree/9e78d13358d3396c47eb3f1164060ee9f0c86d91) [Altium Scripting](https://www.altium.com/documentation/altium-designer/scripting)
- Windows 实测 Python 3.14.5 与 3.11.16。macOS / Linux 的路径实现按标准库设计，实际环境未运行。其他 KiCad 主版本需要独立验证。
- Windows Codex CLI 0.171.0 和 Claude Code 2.1.330 已观测到共享 Skill 的项目级发现。Claude 的发现元数据已返回，进程退出 1。两宿主的在线激活和执行为 not_run。 [Agent Skills 规范](https://agentskills.io/specification) [Codex 项目级安装位置](https://developers.openai.com/api/reference/cli) [Claude Code Skill](https://code.claude.com/docs/en/skills)
