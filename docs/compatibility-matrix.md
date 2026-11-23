# 已验证的兼容性

测试日期为 2026-11-23（中国标准时间）。等级按 PRD v3.0 的证据定义归类。

| 平台与版本 | E0 文档 | E1 导出 / 索引 | E2 原生规则 | E3 几何 | E4 写回 |
|---|---|---|---|---|---|
| KiCad 10.0.6，Windows | implemented | implemented，JSON、XML 和 BOM 的所列格式 | implemented，原生 ERC/DRC，干净及断线 | partial，足印、焊盘、网络、图元、铜区和板框原始字段 | partial，审批后仅同类型文件替换到版本副本 |
| 嘉立创EDA专业版 4.1.60.198f38ab，Windows | implemented，连接、打开、关闭和编辑器执行探测 | partial，真实 eprj3 文档索引和指定 PCB 元数据 | blocked，原生 ERC/DRC 未运行 | not_implemented | not_implemented |
| Altium Designer 26.1.1.7，Windows | implemented，安装发现及真实 PrjPcb 文档清单 | partial，通用 BOM / 规则导出路径经过受控导出样例验证 | blocked，未确认授权运行的脚本及原生规则场景 | not_implemented | not_implemented |
| 通用导出 | implemented，未结构化材料保留 E0 | implemented，CSV/TSV、KiCad XML 和指定 JSON | not_implemented | not_implemented | not_implemented |

- KiCad 依据本机帮助和报告版本核对。原创 continuity 用例及安装目录中的 Stefan Hamminga StickHub 样例实际运行。分发包保留后者的工具日志、哈希和来源，第三方设计源文件仅在本机验证缓存中。 [KiCad CLI 文档](https://docs.kicad.org/10.0/en/cli/cli.html)
- 嘉立创测试使用官方 viewer 的 RA6E2 参考工程副本，来源提交 5cdb87c9562d35d98a55317589efcf0de8d55879。空白打开在该版返回 path is required。明确路径的真实会话开关和软件版本有证据，就绪前查询曾返回空或错误。后续原生调用返回匹配索引 UUID 的 PCB 元数据，源文件哈希保持一致。eprj3 索引和原生对象读取证据分别记录。 [官方参考项目](https://github.com/easyeda/easyeda-viewer/tree/5cdb87c9562d35d98a55317589efcf0de8d55879/samples/RA6E2-eprj3) [原生 CLI 文档](https://prodocs.lceda.cn/en/api/guide/cli.html)
- Altium 用 Josephulrich 公开项目中的真实 STM32F103C8T6.PrjPcb 文件验证清单读取，提交 9e78d13358d3396c47eb3f1164060ee9f0c86d91。原生规则或脚本运行和授权状态均未验证。 [公开原始项目](https://github.com/Josephulrich/design-blue-pill-stm32f103c8t6/tree/9e78d13358d3396c47eb3f1164060ee9f0c86d91) [Altium Scripting](https://www.altium.com/documentation/altium-designer/scripting)
- Windows 实测 Python 3.14.5 与 3.11.16。macOS / Linux 的路径实现按标准库设计，实际环境未运行。其他 KiCad 主版本需要独立验证。
- Windows Codex CLI 0.171.0 和 Claude Code 2.1.330 已观测到共享 Skill 的项目级发现。Claude 的发现元数据已返回，进程退出 1。两宿主的在线激活和执行为 not_run。 [Agent Skills 规范](https://agentskills.io/specification) [Codex 项目级安装位置](https://developers.openai.com/api/reference/cli) [Claude Code Skill](https://code.claude.com/docs/en/skills)
