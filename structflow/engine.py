"""StructFlow SDK 统一引擎（规划占位）。

本模块将在后续整合 phase1~phase4 的流水线编排，对外暴露稳定的高层 API：
- Phase 1 多模型零样本抽取调度（现散落于根目录 phase1.py）；
- Phase 2 裁判共识与 IRT 难度标记（phase2.py）；
- Phase 3 事实核查 / 自动修正 / 归一化 / 人工真值（phase3*.py）；
- Phase 4 主键寻址实体级 Micro/Macro F1 评测（phase4.py）。

当前阶段可直接复用的 SDK 组件：
- schemas.MedicalRecordSchema：L0 输出强类型装甲；
- extractors.StructFlowExtractor：单模型异步抽取器。
"""
