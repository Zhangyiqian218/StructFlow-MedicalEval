# 🩺 StructFlow-MedicalEval

[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![Pydantic v2](https://img.shields.io/badge/pydantic-v2-green.svg)](https://docs.pydantic.dev/)
[![Streamlit](https://img.shields.io/badge/Streamlit-观测舱-red.svg)](https://streamlit.io/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

> **🚀 A rigorous, human-in-the-loop evaluation and distillation framework for LLM nested JSON extraction.**
>
> 解决大模型复杂嵌套 JSON 抽取「评测难、清洗难、幻觉多」的端到端开源基准与数据蒸馏框架——以医疗口述查房记录为试金石，覆盖 **9 个主流大模型 × 50 条真实语料** 的零样本抽取、裁判共识、人工真值固化与实体级 F1 评测全闭环。

---

## 💡 核心痛点与破局

在医疗、金融等垂直领域，用大模型把非结构化长文本转成深层嵌套 JSON，普遍面临 **格式幻觉（凭空捏造字段）** 与 **数组乱序惩罚（评测失真）**。StructFlow 摒弃脆弱的正则后处理，提出一套工业级闭环方案：

- 🛡️ **Pydantic 强类型脱水装甲（Anti-Hallucination Armor）**：基于 Pydantic V2 动态校验与 `extra='forbid'` 绝对拦截，用 `Literal` 严格界定枚举边界，封死跨病例「串台」与字段篡改幻觉；通过装甲后再做脱水压缩，稀疏字段一律省略。
- ⚖️ **LLM-as-a-Judge 动态分流引擎**：以多模型通过率 `pass_count / N` 作为题难度代理（IRT 启发式难度分流），低共识争议题自动转人工；内置 **裁判避嫌机制（Judge Recusal）**——裁判不参考自己的输出，消除强模型自我偏好偏差。
- 👨‍⚕️ **Human-in-the-Loop 全景观测舱**：内置 Streamlit 交互式本地审计终端，对高危争议数据拒绝对「盲目共识」，提供全程留痕的人机对齐，确保 True Gold 的 100% 格式合规。
- 📊 **主键寻址多维 F1 碰撞（PK-Based F1 Metric）**：按业务主键（药名、诊断名、检查名等）把嵌套 JSON 打平为事实集合再碰撞，彻底解决多条医嘱/检查结果因数组乱序导致的评测失效，支持 **Micro / Macro 双口径**。

## 🎯 核心场景（Use Cases）

1. **大模型抽取能力 Benchmark**：快速评测 Qwen、DeepSeek、GLM、Kimi 等模型在复杂业务字典下的真实表现。
2. **SFT 高质量数据蒸馏**：利用多模型矩阵共识，低成本批量产出无格式幻觉的 Pseudo-Gold 语料，用于微调 7B/14B 私有化开源小模型。
3. **RAG / 知识图谱前置清洗基座**：为知识图谱的节点与边构建提供格式绝对正确的结构化底座。

## 🚀 极速上手（Quick Start）

```bash
git clone https://github.com/Zhangyiqian218/StructFlow-MedicalEval.git
cd StructFlow-MedicalEval
pip install -e .                 # 建议 Python 3.10+，依赖一次性装齐

cp .env.example .env             # 填入 DASHSCOPE / ZHIPU / MOONSHOT 的 API Key
```

> 分发包名 `structflow-eval`，Python 导入名为 `structflow`（二者相互独立）。PyPI 发布在 Roadmap 中，当前请用本地可编辑安装。

### 一键启动（SDK）

```python
from structflow import StructFlowEngine
from structflow.schemas import MedicalRecordSchema

# 1. 初始化引擎（异步并发信号量 + 滑动窗口防复读）
engine = StructFlowEngine(
    models=["qwen-max", "deepseek-v3", "glm-4-plus"],
    concurrency_mode="async",          # 当前版本支持异步并发
)

# 2. 一键执行：零样本抽取 → 判官共识 →（人工复核）→ F1 评分
leaderboard = engine.run_pipeline(
    data_path="./data/医生口述查房记录_50条.xlsx",
    schema=MedicalRecordSchema,
    output_dir="./output_eval",
)
print(leaderboard)
```

`run_pipeline` 的人在回路边界（设计如此，不伪装全自动）：

- **当 `output_eval` 下已存在 True Gold** 时，一路跑完 Phase 4 并直接返回排行榜 `pandas.DataFrame`；
- **首次运行（尚无 True Gold）** 时，在 Phase 2 产出伪金标准后 **暂停并返回 `None`**，同时打印指引——Phase 3 必须人工复核。在观测舱固化 True Gold 后，再调用：

```python
leaderboard = engine.score_f1(output_dir="./output_eval")
```

也可以分步调用：`engine.run_extraction(...)` → `engine.run_consensus(...)` → 观测舱人工复核 → `engine.score_f1(...)`。

### 命令行入口（等价）

```bash
python phase1.py               # Phase 1：多模型零样本抽取（耗时最长，支持断点续跑）
python phase2.py               # Phase 2：qwen-max 裁判避嫌仲裁，产出伪金标准
streamlit run ui/dashboard.py  # Phase 3：观测舱人工复核，固化 True Gold
python phase4.py               # Phase 4：主键寻址实体级 Micro/Macro F1 排行榜
```

## 🖥️ 全景观测舱

```bash
streamlit run ui/dashboard.py     # http://localhost:8501
```

四个 Tab：**Phase 1** 抽取矩阵与明细日志（50/50 热力图、通过率）、**Phase 2** 判官共识与难度标记、**Phase 3** 审计舱（逐条人工复核并落盘 True Gold）、**Phase 4** F1 排行榜。

## 🏆 评测榜单（50 条 True Gold，Micro F1 主口径）

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

> 榜单数字已由独立复算脚本 `phase4_verify.py` 逐格核对一致；逐记录明细见 `output_eval/Phase4_Per_Record_F1.csv`。glm-4-flash 的低分主要来自 9 条 JSON 解析失败（按 0 分计入）。

## 🧩 工程结构

```
StructFlow-MedicalEval/
├── structflow/                    # SDK 核心源码包（pip 安装后即可 import）
│   ├── __init__.py                #   高层导出：StructFlowEngine / Extractor / Schema
│   ├── schemas.py                 #   L0 Pydantic 装甲：MedicalRecordSchema 等（extra=forbid + 枚举）
│   ├── extractors.py              #   StructFlowExtractor（被测模型）+ StructFlowJudge（裁判）
│   └── engine.py                  #   StructFlowEngine：模型注册表 + 四阶段编排 + F1 工具
├── ui/dashboard.py                # Streamlit 观测舱
├── data/医生口述查房记录_50条.xlsx  # 50 条语料（项目输入）
├── output_eval/                   # 各阶段落盘产物（矩阵/日志/金标准/排行榜）
├── phase1.py ~ phase4*.py         # 引用 structflow 包的薄命令行入口
├── SCHEMA_V3.md                   # L0 Schema 规范（11 大模块）
└── pyproject.toml
```

## 📐 Schema 与评测口径

- Schema 规范见 [`SCHEMA_V3.md`](SCHEMA_V3.md)：11 大模块稀疏输出；意图与修改解耦（`adjusted_from_*` 追溯换药/改量）、条件干预入 `conditional_actions`、疑似/排除状态入 `diagnoses.status`。
- Phase 4 将 JSON 按业务主键（`drug_name`、`disease_name`、`exam_name` 等）打平为事实集合，对频次 / 给药途径 / 剂量单位做字段路由归一化后再做实体级碰撞；解析失败记录按 0 分计入。
- 单模型抽取器同时具备：独立信号量限流、跨记录滑动窗口去重（相似度 > 0.90 判雷同并阻断重试）、网络错误指数退避、结构错误/幻觉直接分类留痕。

## 🔑 数据与密钥

- `data/医生口述查房记录_50条.xlsx`：50 条口述查房语料（记录编号、记录日期时间、年龄、性别、口述全文）。
- `.env` 存放 API 密钥，**不纳入版本管理**（模板见 `.env.example`）：`DASHSCOPE_API_KEY`（Qwen / DeepSeek）、`ZHIPU_API_KEY`（GLM）、`MOONSHOT_API_KEY`（Kimi）。

## 🗺️ Roadmap

- [ ] 发布到 PyPI（`pip install structflow-eval`）
- [ ] 装甲 Schema 泛化：支持用户自定义嵌套字典（当前内置 `MedicalRecordSchema`）
- [ ] Phase 3 自动核查/归一化进一步收敛人工成本
- [ ] 非思考组 / 思考组消融评测模板化

## 📄 License

[MIT](LICENSE) © StructFlow-MedicalEval contributors
