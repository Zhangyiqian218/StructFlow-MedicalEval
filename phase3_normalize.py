import json
from pathlib import Path
from phase1 import MedicalRecordSchema
from pydantic import ValidationError

BASE = Path(__file__).resolve().parent
OUT = BASE / "output_eval"

# 这些键在 schema 里必须是数组；模型若写成单值则包装成单元素数组
LIST_KEYS = {
    'past_medical_history', 'diagnostic_basis', 'other_notes', 'intent',
    'clinical_scores', 'diagnoses', 'medication_orders', 'fluid_management',
    'non_pharm_interventions', 'patient_flow_plans', 'results', 'orders',
    'allergies', 'contraindications'
}
# 字段名纠正：模型误用的键 -> 正确键
RENAME = {'infusion_time': 'infusion_method'}

# intent 合法枚举
INTENT_ENUM = {"新增", "停药", "换药", "改量", "维持"}


def normalize(obj):
    if isinstance(obj, dict):
        new = {}
        for k, v in obj.items():
            k = RENAME.get(k, k)
            if k in LIST_KEYS and v is not None and not isinstance(v, list):
                v = [v]  # 单值包装为数组
            new[k] = normalize(v)
        return new
    if isinstance(obj, list):
        return [normalize(x) for x in obj]
    return obj


def fix_intent(med, changes):
    """intent 数组里出现非枚举值时：明显是动作的保留，疑似目的的移到 indication"""
    if not isinstance(med, dict) or 'intent' not in med:
        return
    valid, invalid = [], []
    for v in med['intent']:
        (valid if str(v) in INTENT_ENUM else invalid).append(v)
    if invalid:
        med['intent'] = valid
        note = f"intent非法值已移出: {invalid}"
        if not med.get('indication') and invalid:
            # 非动作用词，疑似用药目的，放进 indication 兜底（保留信息）
            med['indication'] = '；'.join(str(i) for i in invalid)
            note += "（已转入 indication）"
        changes.append(note)
    # intent 为空数组时直接删除，符合稀疏原则
    if isinstance(med.get('intent'), list) and len(med['intent']) == 0:
        med.pop('intent')


d = json.load(open(OUT / "Phase3_Suggested_Gold.json", encoding="utf-8"))
final = []
for x in d:
    fixed = normalize(x['suggested_json'])
    aux = []
    for m in fixed.get('medication_orders', []) or []:
        fix_intent(m, aux)
    # 校验
    try:
        MedicalRecordSchema(**fixed)
        status = "合规"
    except ValidationError as e:
        status = "仍失败: " + "; ".join(
            f"{'.'.join(str(i) for i in err['loc'])}({err['type']})" for err in e.errors())
    final.append({
        "record_id": x['record_id'],
        "original_text": x['original_text'],
        "suggested_json": fixed,
        "changes": x.get('changes', []) + aux,
        "was_modified": x.get('was_modified', False) or bool(aux),
        "status": status
    })

json.dump(final, open(OUT / "Phase3_Suggested_Gold.json", "w", encoding="utf-8"),
          ensure_ascii=False, indent=2)

ok = sum(1 for x in final if x['status'] == '合规')
print(f"归一化完成：{len(final)} 条 | 合规 {ok} | 待处理 {len(final)-ok}")
for x in final:
    if x['status'] != '合规':
        print(f"  {x['record_id']}: {x['status']}")
