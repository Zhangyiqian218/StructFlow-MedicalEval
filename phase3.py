import os
import json
import shutil
from pathlib import Path
from typing import Optional, Dict, Any, List, Union, Literal
from pydantic import BaseModel, Field, ValidationError, ConfigDict

# ==========================================
# 1. 严格复用 Phase 1/2 的 Pydantic 装甲 (防止人工改错格式)
# ==========================================
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
    grading: Optional[str] = None
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

# ==========================================
# 2. 审计留痕结构定义
# ==========================================
class HumanOverride(BaseModel):
    field: str = Field(description="修改的字段路径，如 'medication_orders[0].dosage'")
    action: str = Field(description="动作: added, modified, deleted")
    reason: str = Field(description="修改依据，如 '原文第二句，模型集体遗漏'")

class TrueGoldRecord(BaseModel):
    record_id: str
    original_text: str
    gold_json: MedicalRecordSchema
    human_overrides: List[HumanOverride] = Field(default_factory=list)

# ==========================================
# 3. Phase 3 核心审计工作流
# ==========================================
def run_phase_3_human_audit(pseudo_gold_path: str, output_path: str, temp_edit_file: str):
    if not os.path.exists(pseudo_gold_path):
        raise FileNotFoundError(f"找不到伪金标准文件: {pseudo_gold_path}。请先运行 Phase 2。")

    with open(pseudo_gold_path, 'r', encoding='utf-8') as f:
        pseudo_data = json.load(f)

    # 支持断点续审：读取已固化的 True Gold
    true_gold_data = []
    processed_ids = set()
    if os.path.exists(output_path):
        with open(output_path, 'r', encoding='utf-8') as f:
            true_gold_data = json.load(f)
            processed_ids = {str(item['record_id']) for item in true_gold_data}

    total = len(pseudo_data)
    print("=" * 60)
    print("🛡️ 启动 Phase 3 真值(True Gold) 人机协同审计终端")
    print("要求：独立阅读原文 -> 核对诊断/用药 -> 修改临时JSON -> 保存提交")
    print("=" * 60)

    for idx, record in enumerate(pseudo_data):
        record_id = str(record['record_id'])
        if record_id in processed_ids:
            continue

        needs_review = record.get('needs_human_review', False)
        pass_count = record.get('pass_count', 0)
        original_text = record['original_text']
        proposed_json = record.get('proposed_json', {})

        # 构建工作区对象
        workspace_data = {
            "__INSTRUCTIONS__": "请修改下方 'gold_json'。如果有事实性修改(漏药、错诊)，请务必在 'human_overrides' 中留下决策痕迹。",
            "record_id": record_id,
            "human_overrides": [
                {"field": "示例: diagnoses", "action": "added", "reason": "补充模型漏掉的高血压记录"} 
            ] if needs_review else [],
            "gold_json": proposed_json if proposed_json else MedicalRecordSchema().model_dump(exclude_none=True)
        }

        with open(temp_edit_file, 'w', encoding='utf-8') as f:
            json.dump(workspace_data, f, ensure_ascii=False, indent=2)

        os.system('cls' if os.name == 'nt' else 'clear')
        print(f"▶ 进度: [{idx+1}/{total}] | 病例 ID: {record_id}")
        tag = "⚠️ [低共识/崩溃]" if needs_review else "✅ [高共识]"
        print(f"状态: {tag} (Phase 1 难度: {pass_count}/9 模型通过)\n")
        
        # 强制执行“背对背”规则：先看原文，再看 JSON
        print("【原始口述文本】(请先独立阅读，提取关键诊断与药物)：")
        print("-" * 50)
        print(f"\033[96m{original_text}\033[0m") # 青色高亮显示原文
        print("-" * 50)
        print(f"\n👉 已生成工作文件: {temp_edit_file}")
        print("请在 VS Code 或其他编辑器中打开该文件，完成核对与修改。")

        # 人工审核与机器校验死循环，直到格式合法
        while True:
            cmd = input("\n编辑并保存后，按 [Enter] 提交当前病例校验 (输入 'q' 退出并保存进度): ").strip().lower()
            if cmd == 'q':
                print(f"⏸️ 审计已挂起。进度已保存至 {output_path}。")
                return

            try:
                # 1. 尝试解析人类修改后的 JSON
                with open(temp_edit_file, 'r', encoding='utf-8') as f:
                    edited_data = json.load(f)
                
                # 2. 剥离提示词，提取核心数据
                human_overrides = edited_data.get('human_overrides', [])
                edited_gold_json = edited_data.get('gold_json', {})
                
                # 3. 强制 Pydantic 校验 (防御人类手误)
                validated_model = MedicalRecordSchema(**edited_gold_json)
                
                # 4. 再次脱水压缩
                final_clean_dict = validated_model.model_dump(exclude_none=True, exclude_defaults=True, exclude_unset=True)
                if not final_clean_dict:
                    raise ValueError("金标准为空：请至少保留原始口述中的诊断/用药等有效信息")

                # 校验留痕结构
                valid_overrides = [HumanOverride(**h).model_dump() for h in human_overrides if "示例" not in h.get("field", "")]

                # 5. 打包为 True Gold 记录
                true_record = TrueGoldRecord(
                    record_id=record_id,
                    original_text=original_text,
                    gold_json=validated_model, # 这里直接传 model，Pydantic 会自动序列化
                    human_overrides=valid_overrides
                ).model_dump(exclude_none=True)

                true_record['gold_json'] = final_clean_dict # 替换为脱水后的极简版本

                true_gold_data.append(true_record)
                
                # 实时落盘防丢失
                with open(output_path, 'w', encoding='utf-8') as f:
                    json.dump(true_gold_data, f, ensure_ascii=False, indent=2)
                
                print(f"✅ 病例 {record_id} 校验通过并落盘！")
                break # 退出 while 循环，进入下一个病例

            except json.JSONDecodeError as e:
                print(f"❌ JSON 语法错误 (请检查括号或逗号): {e}")
            except ValidationError as e:
                print(f"❌ Schema 格式校验失败 (存在非法字段或类型错误):")
                print(e)
            except Exception as e:
                print(f"❌ 发生未知错误: {e}")
            print("请返回编辑器修正后，重新按 [Enter] 提交。")

    # 清理临时文件
    if os.path.exists(temp_edit_file):
        os.remove(temp_edit_file)

    print("\n" + "=" * 60)
    print("🎉 全量 50 条 True Gold 标准数据集审计完成！")
    print(f"📁 最终金标准路径: {output_path}")

if __name__ == "__main__":
    BASE_DIR = Path(__file__).resolve().parent
    OUTPUT_DIR = BASE_DIR / "output_eval"
    
    run_phase_3_human_audit(
        pseudo_gold_path=str(OUTPUT_DIR / "Phase2_Pseudo_Gold_Full.json"),
        output_path=str(OUTPUT_DIR / "Phase3_True_Gold_Final.json"),
        temp_edit_file=str(BASE_DIR / "_TEMP_AUDIT_WORKSPACE.json")
    )