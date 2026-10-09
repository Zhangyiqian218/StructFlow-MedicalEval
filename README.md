# StructFlow 医疗口述查房记录 · 结构化抽取评测流水线

将医生口述查房文本按 **L0 Schema V3.1** 抽取为深层嵌套 JSON（用药调整追溯、条件干预、液体管理、临床流转等），并对多家大模型进行零样本实体级 F1 评测。全流程分为 4 个 Phase，配套 Streamlit 观测舱完成人机协同复核。

## 流水线总览

| 阶段 | 脚本 | 作用 | 主要产出（`output_eval/`） |
|---|---|---|---|
| Phase 1 | `phase1.py` | 9 个模型异步零样本抽取；Pydantic V2 强类型装甲（字段白名单 + 枚举边界）拦截幻觉；雷同阻断、断点续跑 | `Phase1_01_Matrix.csv`、`Phase1_Detailed_Logs.csv` |
| Phase 2 | `phase2.py` | `qwen-max` 担任裁判（避嫌，不参考自身输出），融合前 3 个高质量专家结果仲裁生成伪金标准；以成功解析数 `pass_count/9` 作为 IRT 难度，<4 标记人工复核 | `Phase2_Pseudo_Gold_Full.json` |
| Phase 3 | `phase3_preaudit.py` → `phase3_autofix.py` → `phase3_normalize.py` → `dashboard.py` 人工复核 | 独立事实核查（幻觉/遗漏）→ 终审编辑自动修正 → 字段归一化与 Schema 复校 → 观测舱逐条人工确认为真值 | `Phase3_Audit_Checks.json`、`Phase3_Suggested_Gold.json`、`Phase3_True_Gold_Final.json` |
| Phase 4 | `phase4.py` | 以 True Gold 为基准，按业务主键寻址打平为事实集合，做实体级碰撞，输出 Micro/Macro 双口径 F1 | `Phase4_F1_Leaderboard.csv`、`Phase4_Per_Record_F1.csv` |

辅助脚本：

- `phase3_finalcheck.py`：跑 Phase4 前的终检闸门（ID 对齐、Schema、稀疏原则、原文错位、日志覆盖、主键碰撞预警）。
- `phase3_finalclean.py`：清除金标准中残留空值字段（自动备份 + 留痕）。
- `phase4_verify.py`：独立复算排行榜并落盘逐记录 F1，量化主键重复记录影响。
- `dashboard.py`：StructFlow 观测舱，4 个 Tab 覆盖全流程可视化与人工复核。

## Schema 规范

见 [`SCHEMA_V3.md`](SCHEMA_V3.md)，11 大模块稀疏输出。核心业务规则：

1. **意图与修改解耦**：修改动作入 `intent`，生效值写当前字段，原值写 `adjusted_from_*` 追溯，禁止布尔值表达修改。
2. **条件触发与排除限制**：条件医嘱入 `conditional_actions`，禁忌/免做入 `exclusion_conditions` 或 `medical_alerts`。
3. **状态诊断识别**：疑似/排除/待查语义拆入 `diagnoses.status`，拦截幻觉。

## 最终评测结果（50 条 True Gold，实体级 Micro F1 主口径）

| 排名 | 模型 | Micro P | Micro R | Micro F1 | Macro F1 |
|---|---|---|---|---|---|
| 🥇 | kimi-k2.6 | 72.4% | 64.7% | **68.3%** | 66.6% |
| 🥈 | deepseek-v3 | 73.1% | 53.9% | **62.1%** | 62.4% |
| 🥉 | qwen-turbo | 50.2% | 64.5% | **56.5%** | 56.6% |
| — | qwen-max（裁判，不参与排名） | 63.0% | 47.1% | 53.9% | 53.3% |
| 4 | deepseek-r1 | 60.2% | 47.8% | 53.3% | 53.2% |
| 5 | glm-4-air | 54.3% | 51.0% | 52.6% | 52.6% |
| 6 | glm-4-plus | 55.0% | 49.1% | 51.9% | 50.9% |
| 7 | qwen-plus | 46.7% | 42.9% | 44.7% | 42.7% |
| 8 | glm-4-flash | 47.3% | 25.4% | 33.1% | 30.5% |

口径说明：事实集合按业务主键（如 `drug_name`、`disease_name`、`exam_name`）寻址，频次/途径/单位做归一化；解析失败记录按 0 分计入。排行榜数字已由 `phase4_verify.py` 独立复算核对一致。

## 复现方式

```bash
pip install pandas openpyxl pydantic openai python-dotenv streamlit
cp .env.example .env   # 填入 API Key
python phase1.py                  # Phase 1：多模型抽取（耗时最长，支持断点续跑）
python phase2.py                  # Phase 2：裁判共识
python phase3_preaudit.py         # Phase 3：事实核查
python phase3_autofix.py          # Phase 3：自动修正
python phase3_normalize.py        # Phase 3：归一化复校
# 浏览器打开观测舱，在 "Phase 3" Tab 逐条人工复核并提交为 True Gold
python phase3_finalcheck.py       # 终检闸门
python phase4.py                  # Phase 4：F1 排行榜
python phase4_verify.py           # 独立复算与逐记录留底
streamlit run dashboard.py        # 观测舱：http://localhost:8501
```

## 数据与目录

- `医生口述查房记录_50条.xlsx`：50 条口述查房语料（字段：记录编号、记录日期时间、年龄、性别、口述全文），为项目输入。
- `output_eval/`：各阶段落盘产物，保留完整中间结果以便审计。
- `.env`：API 密钥，**不纳入版本管理**（见 `.env.example`）。
