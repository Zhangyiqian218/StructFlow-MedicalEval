# StructFlow-MedicalEval

[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![Pydantic v2](https://img.shields.io/badge/pydantic-v2-green.svg)](https://docs.pydantic.dev/)
[![Streamlit](https://img.shields.io/badge/Streamlit-dashboard-red.svg)](https://streamlit.io/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

> A rigorous, human-in-the-loop evaluation and distillation framework for LLM nested JSON extraction.

StructFlow-MedicalEval 是一套面向垂直领域的大模型深层嵌套 JSON 抽取评测与数据蒸馏框架，以真实脱敏的医疗口述查房记录为试金石。项目针对非结构化长文本结构化过程中的两类核心难题——**格式幻觉（模型凭空捏造字段）** 与 **数组乱序导致的评测失真**——提供从多模型零样本抽取、裁判共识、人机协同真值固化到实体级 F1 评测的端到端闭环，覆盖 **9 个主流大模型 × 50 条真实语料**。

## 目录

- [核心特性](#核心特性)
- [适用场景](#适用场景)
- [环境要求](#环境要求)
- [安装](#安装)
- [配置](#配置)
- [快速开始](#快速开始)
- [全景观测舱](#全景观测舱)
- [评测结果](#评测结果)
- [项目结构](#项目结构)
- [Schema 与评测口径](#schema-与评测口径)
- [数据与合规](#数据与合规)
- [路线图](#路线图)
- [许可证](#许可证)

## 核心特性

1. **Pydantic 强类型装甲（Anti-Hallucination Armor）**：基于 Pydantic V2 动态校验，以 `extra='forbid'` 绝对拦截未声明字段，并用 `Literal` 锁定枚举边界，从结构层面抑制跨病例「串台」与字段篡改幻觉；通过校验后再做脱水压缩，稀疏字段一律省略，不依赖脆弱的正则后处理。
2. **LLM-as-a-Judge 动态分流**：以多模型通过率 `pass_count / N` 作为题目难度代理（IRT 启发式难度分流），低共识争议题自动转人工；内置**裁判避嫌机制（Judge Recusal）**，裁判不参考自身输出，消除强模型自我偏好偏差。
3. **人在回路（Human-in-the-Loop）审计台**：提供 Streamlit 本地交互式审计终端，对高争议数据拒绝盲目共识，全程留痕完成人机对齐，确保金标准（True Gold）格式合规。
4. **主键寻址实体级 F1（PK-Based F1 Metric）**：按业务主键（药名、诊断名、检查名等）将嵌套 JSON 打平为事实集合后再碰撞，消除多条医嘱/检查结果因数组顺序不同导致的误判，支持 **Micro / Macro 双口径**。

## 适用场景

- **大模型抽取能力 Benchmark**：横向评测 Qwen、DeepSeek、GLM、Kimi 等模型在复杂业务字典下的真实表现，为模型选型提供依据。
- **SFT 高质量数据蒸馏**：利用多模型共识与裁判仲裁，低成本批量产出无格式幻觉的（文本, JSON）训练对，用于微调 7B/14B 私有化开源小模型。
- **RAG / 知识图谱前置清洗基座**：为知识图谱的节点与边构建提供格式严格正确的结构化底座。

## 环境要求

- Python 3.10 及以上；
- 至少一个模型平台的 API Key（DashScope、智谱 BigModel、Moonshot 任一即可，未配置的模型自动跳过）。

## 安装

```bash
git clone https://github.com/Zhangyiqian218/StructFlow-MedicalEval.git
cd StructFlow-MedicalEval
pip install -e .
```

分发包名为 `structflow-eval`，Python 导入名为 `structflow`（二者相互独立）。当前请使用本地可编辑安装，PyPI 发布见[路线图](#路线图)。

## 配置

复制环境变量模板并填入你自己的 API Key：

```bash
cp .env.example .env
```

需要的环境变量如下：

| 环境变量 | 提供方 | 覆盖模型 |
|---|---|---|
| `DASHSCOPE_API_KEY` | 阿里云百炼 DashScope | Qwen、DeepSeek 系列 |
| `ZHIPU_API_KEY` | 智谱 BigModel | GLM 系列 |
| `MOONSHOT_API_KEY` | 月之暗面 Moonshot | Kimi 系列 |

`.env` 已写入 `.gitignore`，不会纳入版本管理，仓库仅提供 `.env.example` 模板。

## 快速开始

### SDK 一键调用

```python
from structflow import StructFlowEngine
from structflow.schemas import MedicalRecordSchema

# 1. 初始化引擎（异步并发信号量 + 滑动窗口防复读）
engine = StructFlowEngine(
    models=["qwen-max", "deepseek-v3", "glm-4-plus"],
    concurrency_mode="async",          # 当前版本支持异步并发
)

# 2. 一键执行：零样本抽取 → 裁判共识 →（人工复核）→ F1 评分
leaderboard = engine.run_pipeline(
    data_path="./data/医生口述查房记录_50条.xlsx",
    schema=MedicalRecordSchema,
    output_dir="./output_eval",
)
print(leaderboard)
```

人在回路边界（设计如此，不伪装为全自动）：

- 当 `output_eval` 下已存在金标准（True Gold）时，`run_pipeline` 一路执行到 Phase 4 并直接返回排行榜 `pandas.DataFrame`；
- 首次运行（尚无 True Gold）时，在 Phase 2 产出伪金标准后**暂停并返回 `None`**，同时打印操作指引。Phase 3 必须人工复核；在观测舱固化金标准后，再调用：

```python
leaderboard = engine.score_f1(output_dir="./output_eval")
```

也可分步调用：`engine.run_extraction(...)` → `engine.run_consensus(...)` → 观测舱人工复核 → `engine.score_f1(...)`。

### 命令行入口

```bash
python phase1.py               # Phase 1：多模型零样本抽取（耗时最长，支持断点续跑）
python phase2.py               # Phase 2：裁判避嫌仲裁，产出伪金标准
streamlit run ui/dashboard.py  # Phase 3：观测舱人工复核，固化 True Gold
python phase4.py               # Phase 4：主键寻址实体级 Micro/Macro F1 排行榜
```

## 全景观测舱

```bash
streamlit run ui/dashboard.py     # http://localhost:8501
```

四个标签页与四个阶段一一对应：

- **Phase 1**：0/1 抽取矩阵热力图、各模型通过率、可筛选错误并并排查看脱水 JSON 与原始返回；
- **Phase 2**：总数 / 需人工 / 高共识指标、IRT 难度分布、逐条查看伪金标准；
- **Phase 3**：审计舱——先读原文再改 JSON、自动修正留痕、关键实体原文核对、提交即强制 Pydantic 校验、进度实时保存；
- **Phase 4**：排行榜表格与 Micro F1 柱状图。

## 评测结果

基于 50 条经人工审计的 True Gold，以 Micro F1 为主口径：

| 排名 | 模型 | Micro Precision | Micro Recall | Micro F1 | Macro F1 |
|---|---|---|---|---|---|
| 1 | kimi-k2.6 | 72.4% | 64.7% | **68.3%** | 66.6% |
| 2 | deepseek-v3 | 73.1% | 53.9% | **62.1%** | 62.4% |
| 3 | qwen-turbo | 50.2% | 64.5% | **56.5%** | 56.6% |
| — | qwen-max（裁判，不参与排名） | 63.0% | 47.1% | 53.9% | 53.3% |
| 4 | deepseek-r1 | 60.2% | 47.8% | 53.3% | 53.2% |
| 5 | glm-4-air | 54.3% | 51.0% | 52.6% | 52.6% |
| 6 | glm-4-plus | 55.0% | 49.1% | 51.9% | 50.9% |
| 7 | qwen-plus | 46.7% | 42.9% | 44.7% | 42.7% |
| 8 | glm-4-flash | 47.3% | 25.4% | 33.1% | 30.5% |

榜单数字已由独立复算脚本 `phase4_verify.py` 逐格核对一致；逐记录明细见 `output_eval/Phase4_Per_Record_F1.csv`。其中 glm-4-flash 的低分主要来自 9 条 JSON 解析失败（按 0 分计入）。

## 项目结构

```
StructFlow-MedicalEval/
├── structflow/                    # SDK 核心源码包（pip 安装后即可 import）
│   ├── __init__.py                #   高层导出：StructFlowEngine / Extractor / Schema
│   ├── schemas.py                 #   Pydantic 装甲：MedicalRecordSchema 等（extra=forbid + 枚举）
│   ├── extractors.py              #   StructFlowExtractor（被测模型）与 StructFlowJudge（裁判）
│   └── engine.py                  #   模型注册表 + 四阶段编排 + F1 工具
├── ui/dashboard.py                # Streamlit 观测舱
├── data/医生口述查房记录_50条.xlsx  # 50 条脱敏语料（项目输入）
├── output_eval/                   # 各阶段落盘产物（矩阵 / 日志 / 金标准 / 排行榜）
├── phase1.py ~ phase4*.py         # 引用 structflow 包的薄命令行入口
├── SCHEMA_V3.md                   # Schema 规范（11 大模块）
└── pyproject.toml
```

## Schema 与评测口径

- Schema 规范见 [SCHEMA_V3.md](SCHEMA_V3.md)：11 大模块稀疏输出；意图与修改解耦（`adjusted_from_*` 追溯换药/改量）、条件干预入 `conditional_actions`、疑似/排除状态入 `diagnoses.status`。
- Phase 4 将 JSON 按业务主键（`drug_name`、`disease_name`、`exam_name` 等）打平为事实集合，对频次、给药途径、剂量单位做字段路由归一化后再做实体级碰撞；解析失败的记录按 0 分计入。
- 单模型抽取器内置：独立信号量限流、跨记录滑动窗口去重（相似度 > 0.90 判雷同并阻断重试）、网络错误指数退避、结构错误/幻觉直接分类留痕。

## 数据与合规

- 数据为 50 条真实医生口述查房记录，Excel 存储，字段包含记录编号、记录日期时间、年龄、性别、口述全文；所有可识别个人信息均已脱敏，仓库仅分发脱敏数据。
- API Key 存放于 `.env` 并通过 `.gitignore` 排除，仓库仅提供 `.env.example` 模板。

## 路线图

- [ ] 发布到 PyPI（`pip install structflow-eval`）
- [ ] 装甲 Schema 泛化：支持用户自定义嵌套字典（当前内置 `MedicalRecordSchema`）
- [ ] Phase 3 自动核查与归一化进一步收敛人工成本
- [ ] 非思考组 / 思考组消融评测模板化

## 许可证

本项目基于 [MIT License](LICENSE) 开源。
