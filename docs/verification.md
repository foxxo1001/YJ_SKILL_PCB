# 0.1.0 交付实证

验证环境为 Windows 及 KiCad 10.0.6。原生用例启用了 EHE_REQUIRE_KICAD=1。

| 验证项 | 实际结果 | 工件 |
|---|---|---|
| Python 3.14.5，unittest discover -s tests -v | 31 通过，0 失败，0 跳过 | [final-tests.txt](../logs/final-tests.txt) |
| Python 3.11.16，同一测试入口 | 31 通过，0 失败，0 跳过 | [final-tests-python311.txt](../logs/final-tests-python311.txt) |
| eval run --suite core --baseline logs/M5-run1.json --repeat 2 | 每轮 36 通过，0 失败，0 跳过。回归对照通过，两轮无观测差异 | [release-eval.json](../logs/release-eval.json) |
| 修改候选源代码以漏检峰值过载，重新运行全套评测 | 候选退出 1。AT08 回归，独立门禁阻断 | [release-negative.txt](../logs/release-negative.txt) |
| 真实 ERC/DRC 端到端 Loop | 断线原生退出 5/5，停滞暂停，恢复后原生退出 0/0。四次调用 | [发现报告](../logs/demo/01-findings.md) · [复验报告](../logs/demo/03-regression.md) · [原始证据](../logs/demo/artifact-index.json) |
| KiCad 安装样例 StickHub 与原生导出 | 真实参考工程 ERC/DRC 退出 0/0。原生 BOM / XML 由通用适配器解析 | [已安装参考证据](../logs/demo/04-installed-reference.json) · [导出验证](../logs/final-e1-exports.txt) |
| 嘉立创EDA 4.1.60.198f38ab | 真连接、版本、打开与关闭、工程名和 PCB 元数据。早期查询失败保留原始响应 | [eprj3 索引](../logs/M6-easyeda-index.txt) · [UUID 绑定读取](../logs/M6-easyeda-focused.json) |
| Altium Designer 26.1.1.7 | 发现二进制版本，真实 PrjPcb 文档清单解析通过，原生规则未运行 | [版本](../logs/M6-altium-version.json) · [样例日志](../logs/M6-altium-reference.txt) |
| 知识命令 | 已导入案例，按年份检索并返回出处 | [导入](../logs/final-knowledge-import.txt) · [检索](../logs/final-knowledge-search.txt) |
| Codex / Claude Code | 项目级 Skill 均被发现。在线激活 not_run | [M6-hosts.txt](../logs/M6-hosts.txt) |
| Wheel | 安装到独立 Python 3.11 venv，经安装入口运行 doctor、init 和整套金标准 | [安装日志](../logs/wheel-install.txt) · [wheel-eval.json](../logs/wheel-eval.json) |

正向金标准在限定数据中检出 5/5 关键问题和 17/17 诊断真值，数值 7/7。
关键检出率的 95% Wilson 区间为 56.6%–100%。这些比例对应已枚举观察。
依赖实测的 L5 一律 not_run。正式 core 发布门禁因覆盖缺口保持 blocked。

原生证据中保留了命令、运行时绝对路径、版本和输入哈希。解压后用
logs/demo/artifact-index.json 查找可移植的原始报告副本。
第三方参考工程文件仅用于本机验证，分发内容限于原创夹具、证据和来源链接。
