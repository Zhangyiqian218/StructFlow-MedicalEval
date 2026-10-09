import os
import json
import asyncio
from pathlib import Path
from openai import AsyncOpenAI
from dotenv import load_dotenv

load_dotenv()
BASE_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = BASE_DIR / "output_eval"

pseudo = json.load(open(OUTPUT_DIR / "Phase2_Pseudo_Gold_Full.json", encoding="utf-8"))
checks = {c["record_id"]: c for c in
          json.load(open(OUTPUT_DIR / "Phase3_Audit_Checks.json", encoding="utf-8"))}

client = AsyncOpenAI(
    api_key=os.getenv("DASHSCOPE_API_KEY"),
    base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
    timeout=120)
sem = asyncio.Semaphore(5)

SYS = """你是严谨的临床信息抽取终审编辑。严格对照【原始病历口述】修正【待修JSON】，只输出JSON：
{ "fixed_json": { ... }, "changes": ["改了什么及原文依据"] }

【硬性字段白名单】（每个模块只能用下列键，严禁自创任何字段名！）
顶层键仅限：metadata, patient_profile, physical_examination, clinical_scores, diagnoses, medication_orders, fluid_management, non_pharm_interventions, exam_management, medical_alerts, patient_flow_plans
- metadata（自由键对象）：可放 chief_complaint(主诉), history_of_present_illness(现病史), department(科室), record_time, physician(医师/签名) 等
- patient_profile（仅这些键）：age, gender, admission_days, post_op_days, past_medical_history
- physical_examination（自由键对象）：可放 vital_signs, abnormal_signs(阳性症状/体征), normal_signs(阴性体征) 等
- clinical_scores（数组，每项自由键）：score_name, score_value, interpretation
- diagnoses（数组，每项仅这些键）：disease_name, diagnosis_type(枚举:主要诊断/次要诊断/并发症/既往疾病/未指定), grading, status(枚举:确诊/疑似/排除/待查), diagnostic_basis
- medication_orders（数组，每项仅这些键）：drug_name, intent(枚举数组:新增/停药/换药/改量/维持), indication, loading_dose, dosage, frequency, route, course_duration, infusion_method, timing_relation, application_site, monitoring_target, cautions, adjusted_from_drug, adjusted_from_dosage, conditional_actions, skin_test_required, combination_group, other_notes
- fluid_management（数组，每项自由键）
- non_pharm_interventions（数组，每项自由键，如 intervention_type/intervention_name/intent/details）
- exam_management（自由键对象，可放 results/orders，其内部自由键）
- medical_alerts（自由键对象，如 allergies/contraindications）
- patient_flow_plans（数组，每项自由键，如 flow_type/target_department/purpose/status_or_timing）

【信息归位映射】（重点）
- 主诉、现病史、科室、医师签名 → 放进 metadata 对应键
- 症状、体征（含阴性/阳性）→ physical_examination
- 所有药物（含平时维持、急救、替代、二选一中的每个）→ 在 medication_orders 各加一条，用上述键；替代/备选关系写进 other_notes，联合用药写 combination_group，条件干预写 conditional_actions，无法承载的用药细节写 other_notes
- 非药物处理（建立静脉通路、交叉配血、物理降温、监测、预防XX、对症护理等）→ non_pharm_interventions
- 补液/出入量 → fluid_management
- 检查化验 → exam_management
- 随访、转科、会诊 → patient_flow_plans

【修正规则】
1. 补遗漏：把"核查发现-遗漏"中原文确有明确依据的关键信息补到上述正确位置。
2. 删幻觉：把"核查发现-幻觉"中原文完全无依据的猜测删除；但能从原文直接做语义标准化的（如口语"继续吃"→intent=["维持"]、明确诊断→status="确诊"）保留。
3. 严格稀疏输出：没有的字段直接省略，不要空数组[]、空对象{}、null。
4. 不改变原文事实（数值/单位），JSON与原文冲突时以原文为准。
5. 输出前自检：fixed_json 里出现的每个键都必须在白名单内。"""

async def fix(rec):
    rid = rec["record_id"]
    pj = rec.get("proposed_json", {})
    chk = checks.get(rid, {})
    if chk.get("verdict") == "pass" and not rec.get("needs_human_review"):
        return {"record_id": rid, "original_text": rec["original_text"],
                "suggested_json": pj, "changes": [], "was_modified": False}
    user = (f"【原始病历口述】\n{rec['original_text']}\n\n"
            f"【待修JSON】\n{json.dumps(pj, ensure_ascii=False, indent=2)}\n\n"
            f"【核查发现】\n幻觉: {json.dumps(chk.get('hallucinations',[]), ensure_ascii=False)}\n"
            f"遗漏: {json.dumps(chk.get('missing',[]), ensure_ascii=False)}")
    async with sem:
        for attempt in range(3):
            try:
                r = await client.chat.completions.create(
                    model="deepseek-v3",
                    messages=[{"role": "system", "content": SYS},
                              {"role": "user", "content": user}],
                    temperature=0.0, max_tokens=4000)
                txt = r.choices[0].message.content or "{}"
                s, e = txt.find("{"), txt.rfind("}")
                out = json.loads(txt[s:e+1])
                return {"record_id": rid, "original_text": rec["original_text"],
                        "suggested_json": out.get("fixed_json", pj),
                        "changes": out.get("changes", []),
                        "was_modified": bool(out.get("changes"))}
            except Exception as ex:
                if attempt == 2:
                    return {"record_id": rid, "original_text": rec["original_text"],
                            "suggested_json": pj, "changes": [f"自动修正失败: {str(ex)[:80]}"],
                            "was_modified": False, "fix_error": True}
                await asyncio.sleep(2)

async def main():
    res = await asyncio.gather(*[fix(r) for r in pseudo])
    order = [r["record_id"] for r in pseudo]
    res.sort(key=lambda x: order.index(x["record_id"]))
    json.dump(res, open(OUTPUT_DIR / "Phase3_Suggested_Gold.json", "w", encoding="utf-8"),
              ensure_ascii=False, indent=2)

    # 立即用 Schema 校验，报告合规率
    from phase1 import MedicalRecordSchema
    from pydantic import ValidationError
    bad = []
    for x in res:
        try:
            MedicalRecordSchema(**x["suggested_json"])
        except ValidationError as e:
            locs = sorted({'.'.join(str(i) for i in err['loc'])
                           for err in e.errors() if err['type'] == 'extra_forbidden'})
            other = [err['type'] for err in e.errors() if err['type'] != 'extra_forbidden']
            bad.append((x['record_id'], locs, other[:2]))
    mod = sum(1 for x in res if x['was_modified'])
    print(f"建议修正完成：{len(res)} 条 | 修改 {mod} | Schema合规 {len(res)-len(bad)} | 不合规 {len(bad)}")
    for rid, locs, other in bad:
        print(f"  {rid}: 非法字段 {locs[:4]} 其他 {other}")

asyncio.run(main())
