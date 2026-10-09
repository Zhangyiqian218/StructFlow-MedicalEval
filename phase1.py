import os
import time
import asyncio
import difflib
import pandas as pd
from pathlib import Path
from typing import Dict
from dotenv import load_dotenv

from structflow.extractors import StructFlowExtractor

# ==========================================
# 主调度：按记录聚合，完成即落盘
# ==========================================
async def run_phase_1(data_path: str, model_configs: dict, system_prompt: str, output_dir: str):
    os.makedirs(output_dir, exist_ok=True)
    matrix_path = os.path.join(output_dir, "Phase1_01_Matrix.csv")
    logs_path = os.path.join(output_dir, "Phase1_Detailed_Logs.csv")

    df = pd.read_excel(data_path)
    df['记录编号'] = df['记录编号'].astype(str)

    extractors: Dict[str, StructFlowExtractor] = {}
    for name, cfg in model_configs.items():
        if cfg.get("api_key"):
            extractors[name] = StructFlowExtractor(
                name, cfg["api_key"], cfg["base_url"], cfg.get("concurrency", 4))
    model_names = list(extractors.keys())

    matrix_cols = ["record_id"] + model_names
    log_cols = ["record_id", "model_name", "score", "latency_sec",
                "error_type", "cleaned_json", "raw_response"]

    # 断点续跑：跳过已完成记录
    processed = set()
    if os.path.exists(matrix_path):
        old = pd.read_csv(matrix_path)
        if list(old.columns) == matrix_cols:
            processed = set(old['record_id'].astype(str))
            print(f"🔄 断点续跑：跳过 {len(processed)} 条已完成记录")
        else:
            print("⚠️ 模型列与旧文件不一致，将重写（请删除旧文件后重跑）")

    write_lock = asyncio.Lock()
    records = df.to_dict('records')

    async def process_record(rid, user_text):
        start = time.time()
        sub = await asyncio.gather(
            *[extractors[m].run(system_prompt, user_text) for m in model_names])
        results = {}
        logs = []
        for m, (raw, cj, parsed, err) in zip(model_names, sub):
            score = 1 if parsed is not None else 0
            results[m] = score
            logs.append({
                "record_id": rid, "model_name": m, "score": score,
                "latency_sec": round(time.time()-start, 1),
                "error_type": err, "cleaned_json": cj, "raw_response": raw
            })
        row = [rid] + [results[m] for m in model_names]
        async with write_lock:
            pd.DataFrame([row], columns=matrix_cols).to_csv(
                matrix_path, mode='a', header=not os.path.exists(matrix_path), index=False)
            pd.DataFrame(logs, columns=log_cols).to_csv(
                logs_path, mode='a', header=not os.path.exists(logs_path),
                index=False, encoding='utf-8-sig')
        print(f"✅ [{rid}] 完成 | 成功率 {sum(results.values())}/{len(model_names)}")

    record_tasks = []
    for row in records:
        rid = str(row.get('记录编号'))
        if rid in processed:
            continue
        ctx = [f"{k}:{row[k]}" for k in ['记录日期时间', '年龄', '性别']
               if k in row and pd.notna(row[k])]
        user_text = f"【当前病历 {rid}】仅处理本病历，严禁输出任何其他病历内容！\n"
        if ctx:
            user_text += f"[辅助上下文]: {' '.join(ctx)}\n"
        user_text += f"[口述全文]:\n{row.get('口述全文', '')}"
        record_tasks.append(process_record(rid, user_text))

    print(f"🚀 启动 {len(record_tasks)} 条记录 × {len(model_names)} 模型...")
    await asyncio.gather(*record_tasks)

    # 最终全局雷同检测（双保险）
    if os.path.exists(logs_path):
        final = pd.read_csv(logs_path)
        data = {}
        for _, r in final.iterrows():
            if r["score"] == 1 and isinstance(r["cleaned_json"], str) and len(r["cleaned_json"]) > 20:
                data.setdefault(r["model_name"], {})[r["record_id"]] = r["cleaned_json"]
        dup_hits = 0
        for m, recs in data.items():
            ks = list(recs)
            for i in range(len(ks)):
                for j in range(i+1, len(ks)):
                    if difflib.SequenceMatcher(None, recs[ks[i]], recs[ks[j]]).ratio() > 0.9:
                        print(f"⚠️ 漏网雷同 [{m}] {ks[i]}~{ks[j]}")
                        dup_hits += 1
        print(f"\n🏁 全局雷同检测完成，漏网 {dup_hits} 对")

# ==========================================
# 主程序
# ==========================================
if __name__ == "__main__":
    load_dotenv()
    BASE_DIR = Path(__file__).resolve().parent
    DATA_FILE = BASE_DIR / "data" / "医生口述查房记录_50条.xlsx"
    OUTPUT_DIR = BASE_DIR / "output_eval"

    MODEL_CONFIGS = {
        "qwen-max": {"base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1", "api_key": os.getenv("DASHSCOPE_API_KEY"), "concurrency": 4},
        "qwen-plus": {"base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1", "api_key": os.getenv("DASHSCOPE_API_KEY"), "concurrency": 4},
        "qwen-turbo": {"base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1", "api_key": os.getenv("DASHSCOPE_API_KEY"), "concurrency": 5},
        "deepseek-v3": {"base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1", "api_key": os.getenv("DASHSCOPE_API_KEY"), "concurrency": 5},
        "deepseek-r1": {"base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1", "api_key": os.getenv("DASHSCOPE_API_KEY"), "concurrency": 4},
        "glm-4-plus": {"base_url": "https://open.bigmodel.cn/api/paas/v4", "api_key": os.getenv("ZHIPU_API_KEY"), "concurrency": 3},
        "glm-4-air": {"base_url": "https://open.bigmodel.cn/api/paas/v4", "api_key": os.getenv("ZHIPU_API_KEY"), "concurrency": 4},
        "glm-4-flash": {"base_url": "https://open.bigmodel.cn/api/paas/v4", "api_key": os.getenv("ZHIPU_API_KEY"), "concurrency": 2},
        "kimi-k2.6": {"base_url": "https://api.moonshot.cn/v1", "api_key": os.getenv("MOONSHOT_API_KEY"), "concurrency": 4}
    }

    SYSTEM_PROMPT = """你是一个顶级的临床医学信息抽取架构师。请严格按照要求抽取，只输出JSON，严禁输出任何分析过程。

【核心稀疏输出原则】（至关重要）
如果病历文本中没有提及某个字段、模块或嵌套数组，请直接省略该键值对。
绝对不要输出空数组 `[]`、空对象 `{}`、空字符串 `""`、`0` 或 `null`。

【字段语义边界】（务必遵守，避免混淆）
- 用药动作 intent 只能是：新增/停药/换药/改量/维持。
- 用药目的（镇痛、抑酸、抗凝、控制心室率等）放进 indication，不要放进 intent。
- 给药途径（雾化吸入、静推、口服、泵入等）放进 route 或 infusion_method，不要放进 intent。
- 诊断依据放进 diagnoses 的 diagnostic_basis，不要放进 medication_orders。

【支持的完整结构字典】（尖括号 <...> 内为提取说明，请替换为真实文本/数值）：
{
  "metadata": {"record_time": "<提取查房绝对时间，如 2026-09-01 12:00>"},
  "patient_profile": {
    "age": "<数值>", "gender": "<男/女/未知>", "admission_days": "<数值>",
    "post_op_days": "<数值>", "past_medical_history": ["<既往史1>"]
  },
  "physical_examination": {
    "vital_signs": {"temperature": "<数值>", "pulse": "<数值>", "respiration": "<数值>", "systolic_bp": "<数值>", "diastolic_bp": "<数值>", "spo2": "<数值>"},
    "abnormal_signs": ["<阳性体征1>"]
  },
  "clinical_scores": [{"score_name": "<量表名>", "score_value": "<数值或等级>", "interpretation": "<含义>"}],
  "diagnoses": [{
    "disease_name": "<疾病名称>",
    "diagnosis_type": "<枚举：主要诊断/次要诊断/并发症/既往疾病/未指定>",
    "grading": "<分级、分期或急性/慢性状态>",
    "status": "<枚举：确诊/疑似/排除/待查>",
    "diagnostic_basis": ["<依据1>"]
  }],
  "medication_orders": [{
    "drug_name": "<药名>",
    "intent": ["<枚举：新增/停药/换药/改量/维持>"],
    "indication": "<用药目的，如 控制心室率>",
    "loading_dose": "<如 300mg>", "dosage": "<如 100mg>",
    "frequency": "<如 qd>", "route": "<如 静推>",
    "course_duration": "<如 吃三天>", "infusion_method": "<如 持续泵入>",
    "timing_relation": "<如 餐前>", "application_site": "<如 创口处>",
    "monitoring_target": "<监测目标或控制目标值，如 目标静息心率<80次/分>",
    "cautions": "<注意事项>", "adjusted_from_drug": "<替换前药名>",
    "adjusted_from_dosage": "<修改前剂量>",
    "conditional_actions": "<条件干预，如 若心率<60则停药>",
    "skin_test_required": false, "combination_group": "<联合用药分组>"
  }],
  "fluid_management": [{"fluid_type": "<种类>", "total_volume": "<总量>", "infusion_rate": "<速度>", "urine_output_target": "<目标尿量>", "io_balance_goal": "<出入量目标>"}],
  "non_pharm_interventions": [{"intervention_type": "<手术/护理等>", "intervention_name": "<干预项目>", "intent": "<拟做/已做/维持>", "details": "<执行细节>"}],
  "exam_management": {
    "results": [{"exam_name": "<检查名>", "result_value": "<数值或描述>", "unit": "<单位>", "abnormality_flag": "<↑或异常>", "collection_time": "<采集时间>"}],
    "orders": [{"exam_name": "<检查名>", "urgency": "<急查/平诊>", "specimen_type": "<标本类型>", "conditional_actions": "<前置条件>", "exclusion_conditions": "<免做条件>"}]
  },
  "medical_alerts": {"allergies": [{"allergen": "<过敏原>", "reaction_type": "<反应>", "severity": "<严重度>"}], "contraindications": ["<禁忌症>"]},
  "patient_flow_plans": [{"flow_type": "<转科/会诊等>", "target_department": "<目标科室>", "purpose": "<目的>", "status_or_timing": "<时机>"}]
}
"""

    asyncio.run(run_phase_1(
        str(DATA_FILE), MODEL_CONFIGS, SYSTEM_PROMPT, str(OUTPUT_DIR)))
