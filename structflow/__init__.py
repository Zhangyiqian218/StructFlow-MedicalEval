"""StructFlow —— LLM 嵌套 JSON 结构化抽取的人在回路评测与蒸馏框架。

高层入口
--------
- :class:`StructFlowEngine`：四阶段流水线统一引擎（抽取 → 共识 → 人工复核 → F1）；
- :class:`StructFlowExtractor`：单模型异步零样本抽取器（Pydantic 装甲 + 防复读）；
- :class:`MedicalRecordSchema`：医疗查房记录 L0 输出强类型装甲。
"""
from .schemas import MedicalRecordSchema
from .extractors import StructFlowExtractor, StructFlowJudge
from .engine import (
    StructFlowEngine,
    MODEL_REGISTRY,
    DEFAULT_JUDGE_MODEL,
    DEFAULT_SYSTEM_PROMPT,
)

__version__ = "0.1.0"

__all__ = [
    "StructFlowEngine",
    "StructFlowExtractor",
    "StructFlowJudge",
    "MedicalRecordSchema",
    "MODEL_REGISTRY",
    "DEFAULT_JUDGE_MODEL",
    "DEFAULT_SYSTEM_PROMPT",
    "__version__",
]
