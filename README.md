# StructFlow-MedicalEval

将医生口述查房文本按 **L0 Schema V3.1** 抽取为深层嵌套 JSON（用药调整追溯、条件干预、液体管理、临床流转等），并对多家大模型进行零样本实体级 F1 评测。

项目正在 SDK 化：核心装甲与抽取器已沉淀为 `structflow` 包；四阶段流水线脚本仍位于仓库根目录，后续将整合进 `structflow/engine.py`。

## 目录结构

```
StructFlow-MedicalEval/
├── structflow/                    # SDK 核心源码包
│   ├── __init__.py
│   ├── schemas.py                 # L0 Pydantic 装甲：MedicalRecordSchema 等（extra=forbid + 枚举边界）
│   ├── extractors.py              # StructFlowExtractor：单模型异步抽取（校验/去重/重试）
│   └── engine.py                  # 统一引擎（规划占位，后续整合四阶段编排）
├── ui/
│   └── dashboard.py               # Streamlit 观测舱（四阶段可视化 + Phase3 人工复核）
├── data/
│   └── 医生口述查房记录_50条.xlsx   # 50 条语料（项目输入）
├── output_eval/                   # 各阶段落盘产物（矩阵/日志/金标准/排行榜）
├── phase1.py                      # Phase 1：多模型零样本抽取调度
├── phase2.py                      # Phase 2：裁判共识与 IRT 难度标记
├── phase3.py                      # Phase 3：终端人机协同审计（另一入口）
├── phase3_preaudit.py             # Phase 3：独立事实核查（幻觉/遗漏）
├── phase3_autofix.py              # Phase 3：终审自动修正
├── phase3_normalize.py            # Phase 3：字段归一化与 Schema 复校
├── phase3_finalcheck.py           # Phase 3：跑 Phase4 前的终检闸门
├── phase3_finalclean.py           # Phase 3：空值清洗（备份 + 留痕）
├── phase4.py                      # Phase 4：主键寻址实体级 Micro/Macro F1
├── phase4_verify.py               # Phase 4：独立复算与逐记录 F1 留底
├── SCHEMA_V3.md                   # L0 Schema 规范（11 大模块）
├── pyproject.toml
└── README.md
```

## 安装

```bash
# 建议 Python 3.10+；可编辑安装（依赖含 pydantic/openai/pandas/openpyxl/python-dotenv/streamlit）
pip install -e .

# 配置 API Key（.env 不纳入版本管理）
cp .env.example .env   # 填入 DASHSCOPE / ZHIPU / MOONSHOT 的 Key
```

> 分发包名 `structflow-eval`，Python 导入名为 `structflow`（两者相互独立）。

## 作为 SDK 使用

```python
import asyncio
from structflow.schemas import MedicalRecordSchema
from structflow.extractors import StructFlowExtractor

extractor = StructFlowExtractor(
    name="qwen-turbo",
    api_key="<your-key>",
    base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
    concurrency=4,
)

raw, cleaned_json, parsed, error = asyncio.run(extractor.run(
    system_prompt="<L0 抽取提示词>",
    user_text="<医生口述查房文本>",
))
# parsed 为通过 MedicalRecordSchema 校验、脱水压缩后的 dict；
# error 非 None 时表示被 Schema 装甲/JSON 解析/雷同阻断拦截。
```

## 运行四阶段流水线

```bash
python phase1.py                  # Phase 1：9 模型抽取（耗时最长，支持断点续跑）
python phase2.py                  # Phase 2：qwen-max 裁判避嫌仲裁
python phase3_preaudit.py         # Phase 3：事实核查
python phase3_autofix.py          # Phase 3：自动修正
python phase3_normalize.py        # Phase 3：归一化复校
# 方式 A：观测舱在 "Phase 3" Tab 逐条人工复核并提交为 True Gold
# 方式 B：python phase3.py 走终端人机协同审计
python phase3_finalcheck.py       # 终检闸门（ID/Schema/稀疏原则/原文错位/日志覆盖）
python phase4.py                  # Phase 4：F1 排行榜
python phase4_verify.py           # 独立复算与逐记录留底
```

## 观测舱

```bash
streamlit run ui/dashboard.py     # http://localhost:8501
```

四个 Tab：Phase 1 矩阵与日志、Phase 2 判官共识、Phase 3 审计舱（人工复核落盘 True Gold）、Phase 4 F1 排行榜。

## Schema 与评测口径

- Schema 规范见 [`SCHEMA_V3.md`](SCHEMA_V3.md)，11 大模块稀疏输出；核心规则：意图与修改解耦（`adjusted_from_*` 追溯）、条件干预入 `conditional_actions`、疑似/排除状态入 `diagnoses.status`。
- Phase 4 将 JSON 按业务主键（`drug_name`、`disease_name`、`exam_name` 等）打平为事实集合，频次/途径/单位归一化后做实体级碰撞；解析失败记录按 0 分计入。

## 最终评测结果（50 条 True Gold，Micro F1 主口径）

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

排行榜数字已由 `phase4_verify.py` 独立复算核对一致。

## 数据与密钥

- `data/医生口述查房记录_50条.xlsx`：50 条口述查房语料（记录编号、记录日期时间、年龄、性别、口述全文）。
- `.env`：API 密钥，**不纳入版本管理**（模板见 `.env.example`）。
