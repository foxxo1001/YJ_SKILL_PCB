# Embedded Hardware Engineering System 0.1.0

竞赛硬件工程的可运行预览版。唯一产品基线为 [PRD v3.0](docs/PRD_v3.0.md)。
包内含 Codex / Claude Code 共享 Skill、Python CLI、独立 Loop、真实 KiCad ERC/DRC、
可追溯知识检索、参数化工程计算和独立评测。完整 PRD core 发布状态见 [覆盖矩阵](docs/requirements_coverage.md)。

## 安装

要求 Python 3.11+。Windows PowerShell 在解压得到的 ehe 项目目录运行。

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install ".\dist\ehe_engineering-0.1.0-py3-none-any.whl"
.\.venv\Scripts\ehe.exe doctor
.\.venv\Scripts\python.exe scripts/install_skill.py --project . --host both
```

源码开发环境用 `python -m pip install -e ".[test]"`。macOS / Linux 创建虚拟环境用
`python3 -m venv .venv`，把解释器和入口改为 `.venv/bin/python` 与 `.venv/bin/ehe`。
Python 依赖版本在 pyproject.toml 固定。KiCad 使用系统安装的本机工具，如未探测到，
将 `EHE_KICAD_CLI` 设为实际可执行文件的绝对路径。默认注册表探测覆盖 Windows 自定义安装路径。
原生 JSON E2 实测版本为 10.0.6。

安装器将同一份 Skill 复制到项目目录的 `.agents/skills` 与 `.claude/skills`。
需启动新宿主会话。Codex 显式选用 `$embedded-hardware-engineer`，Claude Code 用
`/embedded-hardware-engineer`。宿主命令执行时需让其 PATH 包含虚拟环境，或给出 ehe 绝对路径。
可运行 `python scripts/probe_hosts.py` 离线检查 Codex 发现。加 `--claude` 启动一次 Claude Code 会话检查初始化元数据。

## 试跑真实工程

下列示例假定虚拟环境已启用。为保留发布证据，复制连通性样板到一个新的可写目录后再运行。

```text
ehe doctor
ehe init ./contest-project
ehe analyze ./problem.md
ehe review schematic ./continuity-copy
ehe review pcb ./continuity-copy
ehe review geometry ./continuity-copy/continuity.kicad_pcb
ehe budget ./examples/engineering-model --scope all
ehe loop run --project ./continuity-copy --goal ./continuity-copy/goal.yaml
ehe loop status --project ./continuity-copy
ehe loop resume --project ./continuity-copy
ehe report --project ./continuity-copy
ehe eval run --suite core --repeat 2
```

连通性样板为 [examples/continuity](examples/continuity/README.md)。
`python scripts/demo_loop.py ./my-demo` 在新目录注入断线，保留原生报告，模拟用户恢复文件，
重新执行并由独立 Gate 核验。`python scripts/archive_demo.py ./my-demo` 可导出可移植审计文件。
交付中包含已实际运行的 [整改前报告](logs/demo/01-findings.md)、[复验报告](logs/demo/03-regression.md) 和
[原始报告索引](logs/demo/artifact-index.json)。`.ehe/runtime.sqlite3` 是运行状态源，重启会重建
`run.json`、`events.jsonl` 和 `artifacts.json`。源哈希变更触发影响域与关键回归，
无新输入和可执行动作时停止。一个目录保存一个 Loop，新的独立实验可使用新的目录。

## CLI 合约

| 命令 | 输入与结果 |
|---|---|
| doctor | Python、工具安装与 KiCad 实际版本 |
| init | 创建 project.json、goal.yaml、engineering.json 和验收台账 |
| analyze | PDF 文本层、Markdown、文本，或安装 Tesseract 后带 --ocr-lang 的受支持图像。输出候选条款、位置、冲突、追踪表 |
| review schematic / pcb | 目录中唯一的 KiCad 原生文件，或 project.json 中显式选择的相对路径 |
| review export | BOM CSV/TSV、KiCad XML 网表、KiCad JSON 或 ehe-rule-report-v1。--producer 仅记录声明来源 |
| review geometry | KiCad 板原生对象子集 |
| budget | engineering.json。--scope core、reality、advanced、all |
| loop run / status / resume / decide / cancel | 按 YAML/JSON 目标执行、查询、恢复、审批或取消 |
| report | 最近一次 Loop 的中文报告，可用 --output 写入新文件 |
| impact | part、board_outline、pwm、power_architecture 等变更影响域 |
| knowledge import / search / claims | 本地 SQLite、权属与来源、文本语义类别与关键词混合检索、冲突 |
| eval run / isolate | 金标准计分、同基线回归或隐藏集指纹。评测报告含单项判定与发布门禁 |
| eda easyeda / altium | 真实连接与会话探测、指定会话只读元数据或种类受限的文件导入 |

退出码 0 表示命令执行或所请求检查成功，1 表示失败或需要整改，2 表示输入无效，3 表示缺证据、
暂停、工具受阻、待审批或预算结束。工程状态、输出证据和发布状态在 JSON 中具名。
评测进程退出 0 表示本次金标准质量门禁通过，结果还含独立的 `release_gate`。
默认物理验收未运行，Loop 的 `physical_required: false` 仅用于明确限定的工具/计算检查。

## 工作副本审批

准备类型相同的替代 KiCad 文件和 [审批目标示例](examples/continuity/patch-goal.yaml.example)。
运行目标后审阅 proposal 的 diff、范围和两侧 SHA-256，由工程复核人执行：

```text
ehe loop decide chg-<id> approve --project ./continuity-copy --reviewer <name> --reason <scope>
ehe loop resume --project ./continuity-copy
```

可用 deny 拒绝。Runtime 只提交到 `.ehe/work` 下的受控版本，保留原工程。
回滚使用原始修订，保留已经产生的审计和副本。审批细节见 [安全与审批](docs/security-and-approval.md)。

## 知识与评测

```text
ehe knowledge import examples/knowledge/case.md --metadata examples/knowledge/metadata.json --db ./cases.sqlite3
ehe knowledge search "低功耗 供电瞬态" --db ./cases.sqlite3 --year 2026
ehe eval run --suite core --output ./current.json
ehe eval run --suite core --baseline ./current.json --repeat 2
```

隐藏集由评测方另行持有。`ehe eval isolate private.json --knowledge-db ./cases.sqlite3 --output ./fingerprints.json`
生成防泄漏注册表。导入时传 `--registry` 启用对照。运行时不把金标准答卷或私有目录注入 Skill。
详见 [接口、模型与评测](docs/interfaces.md)。

## 开发验证

```text
python -m pip install -e ".[test]"
python -m unittest discover -s tests -v
ehe eval run --suite core --repeat 2
```

集成组默认在无 KiCad 时跳过。设置 `EHE_REQUIRE_KICAD=1` 使缺失工具成为测试失败。
发布证据、工具版本和能力边界见 [兼容矩阵](docs/compatibility-matrix.md)、
[覆盖矩阵](docs/requirements_coverage.md) 与 [已知限制](docs/known-limitations.md)。
