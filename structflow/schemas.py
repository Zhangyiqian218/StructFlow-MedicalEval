"""StructFlow L0 Schema —— 医疗口述查房记录结构化输出的 Pydantic 装甲。

所有模型（被评测模型、裁判模型、人工复核结果）的 JSON 都必须通过本模块的
强类型校验：Literal 枚举锁死字段边界，extra='forbid' 拦截自创字段，
从结构层面压制大模型幻觉。
"""
from typing import Optional, Dict, Any, List, Union, Literal
from pydantic import BaseModel, Field, ConfigDict


class PatientProfile(BaseModel):
    age: Union[int, str, None] = None
    gender: Optional[str] = None
    admission_days: Union[int, str, None] = None
    post_op_days: Union[int, str, None] = None
    past_medical_history: List[str] = Field(default_factory=list)
    model_config = ConfigDict(extra='forbid')


class DiagnosisSchema(BaseModel):
    disease_name: str
    diagnosis_type: Optional[Literal["主要诊断", "次要诊断", "并发症", "既往疾病", "未指定"]] = None
    grading: Optional[str] = Field(None, description="疾病分级、分期或急性/慢性状态")
    status: Optional[Literal["确诊", "疑似", "排除", "待查"]] = None
    diagnostic_basis: List[str] = Field(default_factory=list)
    model_config = ConfigDict(extra='forbid')


class MedicationOrder(BaseModel):
    drug_name: str
    intent: List[Literal["新增", "停药", "换药", "改量", "维持"]] = Field(default_factory=list)
    indication: Optional[str] = None
    loading_dose: Optional[str] = None
    dosage: Optional[str] = None
    frequency: Optional[str] = None
    route: Optional[str] = None
    course_duration: Optional[str] = None
    infusion_method: Optional[str] = None
    timing_relation: Optional[str] = None
    application_site: Optional[str] = None
    monitoring_target: Optional[str] = None
    cautions: Optional[str] = None
    adjusted_from_drug: Optional[str] = None
    adjusted_from_dosage: Optional[str] = None
    conditional_actions: Optional[str] = None
    skin_test_required: bool = False
    combination_group: Optional[str] = None
    other_notes: List[str] = Field(default_factory=list)
    model_config = ConfigDict(extra='forbid')


class MedicalRecordSchema(BaseModel):
    """L0 输出顶层结构：11 大模块，稀疏输出（无内容的键直接省略）。"""
    metadata: Dict[str, Any] = Field(default_factory=dict)
    patient_profile: PatientProfile = Field(default_factory=PatientProfile)
    physical_examination: Dict[str, Any] = Field(default_factory=dict)
    clinical_scores: List[Dict[str, Any]] = Field(default_factory=list)
    diagnoses: List[DiagnosisSchema] = Field(default_factory=list)
    medication_orders: List[MedicationOrder] = Field(default_factory=list)
    fluid_management: List[Dict[str, Any]] = Field(default_factory=list)
    non_pharm_interventions: List[Dict[str, Any]] = Field(default_factory=list)
    exam_management: Dict[str, Any] = Field(default_factory=dict)
    medical_alerts: Dict[str, Any] = Field(default_factory=dict)
    patient_flow_plans: List[Dict[str, Any]] = Field(default_factory=list)
    model_config = ConfigDict(extra='forbid')


class HumanOverride(BaseModel):
    """Phase 3 人工复核留痕结构。"""
    field: str = Field(description="修改的字段路径，如 'medication_orders[0].dosage'")
    action: str = Field(description="动作: added, modified, deleted")
    reason: str = Field(description="修改依据，如 '原文第二句，模型集体遗漏'")


class TrueGoldRecord(BaseModel):
    """Phase 3 固化后的真值记录结构。"""
    record_id: str
    original_text: str
    gold_json: MedicalRecordSchema
    human_overrides: List[HumanOverride] = Field(default_factory=list)
    auto_changes: List[str] = Field(default_factory=list)
    review_status: Literal["confirmed", "modified"] = Field(
        "confirmed",
        description="人工复核结论：confirmed=人工确认自动版未改动；modified=人工做了实质修改")
