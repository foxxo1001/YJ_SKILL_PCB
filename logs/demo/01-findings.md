# 连通性验证原生样板

运行 ea3a8223-14a0-43c9-9110-b4208e1424c4 · REVISE · 第 1 轮

工程 D:\AI_project\YJ_SKILLS\ehe\logs\demo\design

## 验收

| 项目 | 状态 | 证据 | 依据 |
|---|---|---|---|
| erc | fail | ev-e9abc70c9bd6a2793066 | 2 native rule violations |
| drc | fail | ev-8f1fdfac0b50f8499f7b | 1 native rule violations |

## 工程检查与证据

### erc · fail · E2

- P0 pin_not_connected · {'object': '/', 'items': [{'description': 'Symbol J1 引脚 1 [1, Passive, Line]', 'pos': {'x': 0.5334, 'y': 0.508}, 'uuid': '927fa780-0611-58d3-a4d8-8cc2dfb64ee8'}]} · Pin not connected。定位对象并根据项目规则修正。验证方法为对变更版本重新运行原生规则检查。
- P0 pin_not_connected · {'object': '/', 'items': [{'description': 'Symbol J2 引脚 1 [1, Passive, Line]', 'pos': {'x': 0.7874, 'y': 0.508}, 'uuid': '889ad5a0-65fc-59fe-8786-edb7d5f4acc6'}]} · Pin not connected。定位对象并根据项目规则修正。验证方法为对变更版本重新运行原生规则检查。
- ev-e9abc70c9bd6a2793066 · tool_verified · D:\AI_project\YJ_SKILLS\ehe\logs\demo\design\.ehe\eda\389e280620c0473c9984eb6d1f1430be\erc.json · erc/violations · SHA-256 12bc82b4ba52e31a0caa42af3ad545a7f997bd790c4494b22f93d28530529a5f

### drc · fail · E2

- P0 unconnected_items · {'object': 'unconnected_items', 'items': [{'description': 'J1 的PTH 焊盘 1 [LINK]', 'pos': {'x': 30.0, 'y': 30.0}, 'uuid': 'f585f41d-23f1-5d60-bea9-6d487feaa948'}, {'description': 'J2 的PTH 焊盘 1 [LINK]', 'pos': {'x': 40.0, 'y': 30.0}, 'uuid': '4de21c8d-f9ee-592f-a802-16b84f40aac6'}]} · Missing connection between items。定位对象并根据项目规则修正。验证方法为对变更版本重新运行原生规则检查。
- ev-8f1fdfac0b50f8499f7b · tool_verified · D:\AI_project\YJ_SKILLS\ehe\logs\demo\design\.ehe\eda\57bc04da8a514545b2c13c71f92e4104\drc.json · drc/violations · SHA-256 5aebc2b79489e811ef7faf563365873450cfdb03aadfeb4b97c3940c3b0f18cf

## 整改与回归

影响域 control、cost、electrical、emi、energy、footprint、mechanical、pcb、pin_map、power、schematic、thermal。
任务尝试 2 次，执行预算已计 6.443 秒。

审计入口为 .ehe/run.json、.ehe/events.jsonl 和 .ehe/artifacts.json。
