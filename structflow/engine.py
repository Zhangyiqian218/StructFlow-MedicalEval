"""StructFlow SDK 统一引擎。

把 Phase 1~Phase 4 的编排沉淀为包内高层 API，wheel 安装后即可在任意项目调用；
根目录 phase1.py~phase4.py 是引用本模块的薄 CLI 入口，逻辑以本模块为单一事实源。

人工环节边界（重要）
--------------------
Phase 3（事实核查 / 自动修正 / 归一化 / Streamlit 人工复核）是 human-in-the-loop，
无法全自动。``run_pipeline`` 在产出伪金标准（Phase 2）后会显式停下并给出指引，
待观测舱固化 ``Phase3_True_Gold_Final.json`` 后，再调用 :meth:`StructFlowEngine.score_f1`
得到实体级 F1 排行榜；若已存在 True Gold，``run_pipeline`` 会一路跑完并返回排行榜。
"""
from __future__ import annotations

import os
import re
import json
import time
import asyncio
import difflib
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
from dotenv import load_dotenv

from .schemas import MedicalRecordSchema
from .extractors import StructFlowExtractor, StructFlowJudge

# ======================================================================
# 模型注册表与默认配置
# ======================================================================
MODEL_REGISTRY: Dict[str, Dict[str, Any]] = {
    "qwen-max":     {"base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1", "key_env": "DASHSCOPE_API_KEY", "concurrency": 4},
    "qwen-plus":    {"base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1", "key_env": "DASHSCOPE_API_KEY", "concurrency": 4},
    "qwen-turbo":   {"base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1", "key_env": "DASHSCOPE_API_KEY", "concurrency": 5},
    "deepseek-v3":  {"base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1", "key_env": "DASHSCOPE_API_KEY", "concurrency": 5},
    "deepseek-r1":  {"base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1", "key_env": "DASHSCOPE_API_KEY", "concurrency": 4},
    "glm-4-plus":   {"base_url": "https://open.bigmodel.cn/api/paas/v4", "key_env": "ZHIPU_API_KEY", "concurrency": 3},
    "glm-4-air":    {"base_url": "https://open.bigmodel.cn/api/paas/v4", "key_env": "ZHIPU_API_KEY", "concurrency": 4},
    "glm-4-flash":  {"base_url": "https://open.bigmodel.cn/api/paas/v4", "key_env": "ZHIPU_API_KEY", "concurrency": 2},
    "kimi-k2.6":    {"base_url": "https://api.moonshot.cn/v1", "key_env": "MOONSHOT_API_KEY", "concurrency": 4},
}

DEFAULT_JUDGE_MODEL = "qwen-max"

# 裁判融合时的专家优先级（越靠前越优先作为参考），并用于裁判避嫌后的质量排序
JUDGE_PRIORITY_MODELS = [
    "deepseek-v3", "qwen-turbo", "kimi-k2.6", "deepseek-r1",
    "qwen-plus", "glm-4-air", "glm-4-plus", "glm-4-flash",
]

DEFAULT_SYSTEM_PROMPT = """你是一个顶级的临床医学信息抽取架构师。请严格按照要求抽取，只输出JSON，严禁输出任何分析过程。

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


def build_model_configs(model_names: Optional[List[str]] = None) -> Dict[str, dict]:
    """从环境变量读取 Key，组装 Phase 1 需要的 model_configs；缺少 Key 的模型跳过。"""
    load_dotenv()
    selected = list(model_names) if model_names else list(MODEL_REGISTRY.keys())
    configs: Dict[str, dict] = {}
    missing: List[str] = []
    for name in selected:
        if name not in MODEL_REGISTRY:
            raise ValueError(f"未知模型 {name!r}，注册表可选: {', '.join(MODEL_REGISTRY)}")
        meta = MODEL_REGISTRY[name]
        key = os.getenv(meta["key_env"])
        if key:
            configs[name] = {"base_url": meta["base_url"], "api_key": key,
                             "concurrency": meta["concurrency"]}
        else:
            missing.append(name)
    if missing:
        print(f"⚠️ 以下模型缺少 API Key，已跳过: {', '.join(missing)}")
    if not configs:
        raise RuntimeError("没有任何可用模型，请在 .env 配置 DASHSCOPE / ZHIPU / MOONSHOT 的 Key")
    return configs


# ======================================================================
# Phase 1：多模型零样本抽取调度（按记录聚合，完成即落盘，支持断点续跑）
# ======================================================================
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
                "latency_sec": round(time.time() - start, 1),
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
                for j in range(i + 1, len(ks)):
                    if difflib.SequenceMatcher(None, recs[ks[i]], recs[ks[j]]).ratio() > 0.9:
                        print(f"⚠️ 漏网雷同 [{m}] {ks[i]}~{ks[j]}")
                        dup_hits += 1
        print(f"\n🏁 全局雷同检测完成，漏网 {dup_hits} 对")


# ======================================================================
# Phase 2：LLM-as-a-Judge 裁判共识 + 经验难度（pass_count）分流
# ======================================================================
def run_phase_2_consensus(matrix_path: str, logs_path: str, raw_data_path: str,
                          output_dir: str, judge_config: dict):
    os.makedirs(output_dir, exist_ok=True)

    df_matrix = pd.read_csv(matrix_path)
    df_logs = pd.read_csv(logs_path)
    df_raw = pd.read_excel(raw_data_path)

    df_raw['记录编号'] = df_raw['记录编号'].astype(str)
    df_matrix['record_id'] = df_matrix['record_id'].astype(str)
    df_logs['record_id'] = df_logs['record_id'].astype(str)

    judge_engine = StructFlowJudge(**judge_config)
    all_pseudo_gold = []

    total_records = len(df_matrix)
    print(f"⚖️ 启动 Phase 2 判官仲裁引擎 | 判官模型: {judge_engine.model_name}")
    print("=" * 60)

    for idx, matrix_row in df_matrix.iterrows():
        record_id = matrix_row['record_id']
        raw_text_row = df_raw[df_raw['记录编号'] == record_id]
        if raw_text_row.empty:
            continue

        original_text = raw_text_row.iloc[0]['口述全文']

        # 1. 经验难度系数（通过模型数 / 总模型数）
        model_scores = matrix_row.drop('record_id')
        pass_count = int(model_scores.sum())

        # 2. 裁判避嫌机制与专家质量排序
        successful_models = model_scores[model_scores == 1].index.tolist()
        candidate_models = [m for m in successful_models if m != judge_engine.model_name]
        candidate_models = sorted(
            candidate_models,
            key=lambda x: JUDGE_PRIORITY_MODELS.index(x) if x in JUDGE_PRIORITY_MODELS else 999)

        expert_opinions = ""
        selected_models = []
        if candidate_models:
            selected_models = candidate_models[:3]
            opinions_df = df_logs[(df_logs['record_id'] == record_id)
                                  & (df_logs['model_name'].isin(selected_models))]
            for _, op in opinions_df.iterrows():
                expert_opinions += f"\n--- 参考专家 [{op['model_name']}] 的抽取结果 ---\n{op['cleaned_json']}\n"
        else:
            expert_opinions = "其他独立模型均未能提供有效参考格式，请完全基于你的独立判断进行抽取。"

        judge_prompt = f"""你是一个权威的医疗数据仲裁专家。请根据下方的【原始病历口述】，审查并融合【专家初步抽取结果】，输出最终完美定稿的 JSON。

【核心规则】
1. 彻底剔除幻觉：专家结果中若存在原始口述未提及的字段，必须删除。
2. 修复遗漏：若专家漏掉了口述中的重要药物或诊断，请你补齐。
3. 严格遵循稀疏输出原则：不要输出空对象 {{}}、空数组 [] 或 null，只保留有实际值的嵌套。

【原始病历口述】
{original_text}

【专家初步抽取结果】
{expert_opinions}

请直接输出仲裁后的最终 JSON 结果："""

        print(f"▶ [{idx + 1}/{total_records}] 仲裁病例: {record_id} (难度系数: {pass_count}/9) ", end="")
        _, _, parsed_dict, error_msg = judge_engine.evaluate_judge(
            system_prompt="你是一个权威的医疗数据仲裁者，严格执行JSON校验。",
            user_text=judge_prompt
        )

        # 判官输出崩溃时，用最高优先级成功专家结果兜底，给人工一个可用起点而非空白
        fallback_model = None
        fallback_dict = None
        if parsed_dict is None and selected_models:
            fb = df_logs[(df_logs['record_id'] == record_id)
                         & (df_logs['model_name'] == selected_models[0])]
            if not fb.empty:
                try:
                    fallback_dict = json.loads(fb.iloc[0]['cleaned_json'])
                    fallback_model = selected_models[0]
                except Exception:
                    fallback_dict = None

        needs_review = (pass_count < 4) or (parsed_dict is None)

        record_data = {
            "record_id": record_id,
            "pass_count": pass_count,
            "needs_human_review": needs_review,
            "judge_error": error_msg if error_msg else None,
            "fallback_model": fallback_model,
            "original_text": original_text,
            "proposed_json": parsed_dict if parsed_dict else (fallback_dict or {})
        }
        all_pseudo_gold.append(record_data)

        if not needs_review:
            print("✅ [高置信度]")
        else:
            print(f"⚠️ [需人工介入] {error_msg or '低共识争议题'}")

        time.sleep(1)

    output_file = Path(output_dir) / "Phase2_Pseudo_Gold_Full.json"
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(all_pseudo_gold, f, ensure_ascii=False, indent=2)

    print("\n" + "=" * 60)
    print("🎉 Phase 2 全量预标注完成！")
    print(f"👉 伪金标准已保存至: {output_file}")
    print('🚨 请在观测舱完成人工精修，或搜索 "needs_human_review": true 逐条复核。')
    return output_file


# ======================================================================
# Phase 4：主键寻址实体级 Micro / Macro F1
# ======================================================================
PRIMARY_KEYS = {
    "clinical_scores": "score_name",
    "diagnoses": "disease_name",
    "medication_orders": "drug_name",
    "fluid_management": "fluid_type",
    "non_pharm_interventions": "intervention_name",
    "allergies": "allergen",
    "patient_flow_plans": "target_department",
    "results": "exam_name",
    "orders": "exam_name",
}


def normalize_medical_value(val: str, field_name: str = ""):
    """带字段路由的医疗数值归一化（防止全局误伤）。"""
    if not isinstance(val, str):
        return val

    val = val.strip().lower()

    if field_name == "frequency":
        freq_map = {
            "qd": "每日1次", "每日一次": "每日1次", "每日1次": "每日1次", "每天1次": "每日1次", "每天一次": "每日1次", "一日1次": "每日1次",
            "bid": "每日2次", "每日两次": "每日2次", "每日2次": "每日2次", "每天2次": "每日2次", "每天两次": "每日2次", "一日2次": "每日2次",
            "tid": "每日3次", "每日三次": "每日3次", "每日3次": "每日3次", "每天3次": "每日3次", "每天三次": "每日3次", "一日3次": "每日3次",
            "qid": "每日4次", "每日四次": "每日4次", "每日4次": "每日4次", "每天4次": "每日4次",
            "qn": "每晚1次", "q8h": "每8小时1次", "q12h": "每12小时1次",
            "prn": "按需", "st": "立即", "st.": "立即"
        }
        return freq_map.get(val, val)

    if field_name == "route":
        route_map = {"iv": "静脉注射", "ivgtt": "静脉滴注", "po": "口服", "im": "肌肉注射", "ih": "皮下注射"}
        return route_map.get(val, val)

    if field_name in ["dosage", "loading_dose", "total_volume"]:
        m_g = re.match(r'^([\d\.]+)\s*g$', val)
        if m_g:
            try:
                num = float(m_g.group(1)) * 1000
                return f"{int(num) if num.is_integer() else num}mg"
            except ValueError:
                pass
        m_l = re.match(r'^([\d\.]+)\s*l$', val)
        if m_l:
            try:
                num = float(m_l.group(1)) * 1000
                return f"{int(num) if num.is_integer() else num}ml"
            except ValueError:
                pass

    return val


def flatten_to_facts(data: dict, prefix: str = "") -> set:
    """将嵌套 JSON 打平为主键寻址的一维事实集合。"""
    facts = set()
    if not isinstance(data, dict):
        return facts

    for k, v in data.items():
        new_prefix = f"{prefix}.{k}" if prefix else k

        if isinstance(v, dict):
            facts.update(flatten_to_facts(v, new_prefix))
        elif isinstance(v, list):
            for item in v:
                if isinstance(item, dict):
                    pk_field = PRIMARY_KEYS.get(k)
                    if pk_field and pk_field in item:
                        pk_value = str(item[pk_field]).strip().lower()
                        item_prefix = f"{new_prefix}.[{pk_value}]"
                        sub_dict = {sk: sv for sk, sv in item.items() if sk != pk_field}
                        facts.add(f"{item_prefix}.{pk_field}:{pk_value}")
                        facts.update(flatten_to_facts(sub_dict, item_prefix))
                    else:
                        canonical_item = json.dumps(item, sort_keys=True, ensure_ascii=False)
                        facts.add(f"{new_prefix}:{canonical_item}")
                elif isinstance(item, list):
                    pass
                else:
                    facts.add(f"{new_prefix}:{normalize_medical_value(str(item), k)}")
        else:
            val = normalize_medical_value(str(v), k) if isinstance(v, (str, int, float)) else v
            facts.add(f"{new_prefix}:{val}")

    return facts


def calculate_metrics(gold_facts: set, pred_facts: set) -> Tuple[int, int, int, float]:
    if not gold_facts and not pred_facts:
        return 0, 0, 0, 1.0

    tp = len(gold_facts & pred_facts)
    fp = len(pred_facts - gold_facts)
    fn = len(gold_facts - pred_facts)

    precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
    recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0.0

    return tp, fp, fn, f1


def run_f1_alignment(gold_path: str, logs_path: str, output_dir: str,
                     judge_model_name: str) -> Optional[pd.DataFrame]:
    os.makedirs(output_dir, exist_ok=True)

    try:
        with open(gold_path, 'r', encoding='utf-8') as f:
            gold_data = json.load(f)
    except FileNotFoundError:
        print(f"❌ 找不到金标准文件: {gold_path}，请确保 Phase 3 审计完成。")
        return None

    gold_registry = {}
    for record in gold_data:
        record_id = str(record['record_id'])
        gold_json = record.get('gold_json', {})
        gold_registry[record_id] = flatten_to_facts(gold_json)

    print(f"✅ 成功加载 {len(gold_registry)} 条经过审计的 True Gold 数据。")

    try:
        df_logs = pd.read_csv(logs_path)
    except FileNotFoundError:
        print(f"❌ 找不到预测日志: {logs_path}")
        return None

    df_logs['record_id'] = df_logs['record_id'].astype(str)
    results = []

    print("-" * 80)
    print("⏳ 正在进行归一化及主键多维碰撞 (Macro & Micro)...")

    for _, row in df_logs.iterrows():
        record_id = row['record_id']
        model_name = row['model_name']
        cleaned_json_str = row['cleaned_json']

        if record_id not in gold_registry:
            continue

        gold_facts = gold_registry[record_id]
        pred_facts = set()

        if pd.notna(cleaned_json_str) and str(cleaned_json_str).strip() != "":
            try:
                pred_json = json.loads(cleaned_json_str)
                pred_facts = flatten_to_facts(pred_json)
            except Exception:
                pass

        tp, fp, fn, record_f1 = calculate_metrics(gold_facts, pred_facts)
        results.append({
            "record_id": record_id, "model_name": model_name,
            "tp": tp, "fp": fp, "fn": fn, "record_f1": record_f1
        })

    df_results = pd.DataFrame(results)
    if df_results.empty:
        return None

    leaderboard_data = []
    for model_name, group in df_results.groupby('model_name'):
        macro_f1 = group['record_f1'].mean()
        sum_tp, sum_fp, sum_fn = group['tp'].sum(), group['fp'].sum(), group['fn'].sum()
        micro_p = sum_tp / (sum_tp + sum_fp) if (sum_tp + sum_fp) > 0 else 0.0
        micro_r = sum_tp / (sum_tp + sum_fn) if (sum_tp + sum_fn) > 0 else 0.0
        micro_f1 = 2 * micro_p * micro_r / (micro_p + micro_r) if (micro_p + micro_r) > 0 else 0.0
        leaderboard_data.append({
            "model_name": model_name, "Micro_P": micro_p, "Micro_R": micro_r,
            "Micro_F1": micro_f1, "Macro_F1": macro_f1
        })

    leaderboard = pd.DataFrame(leaderboard_data).sort_values(
        by='Micro_F1', ascending=False).reset_index(drop=True)

    leaderboard_file = Path(output_dir) / "Phase4_F1_Leaderboard.csv"
    leaderboard.to_csv(leaderboard_file, index=False, encoding='utf-8-sig')

    print("\n" + "=" * 80)
    print("🏆 StructFlow 最终实体抽取排行榜 (双口径评测)")
    print("=" * 80)
    print(f"{'模型名称':<18} | {'Micro P':<8} | {'Micro R':<8} | {'Micro F1 (主)':<12} | {'Macro F1':<8}")
    print("-" * 80)
    rank = 1
    for _, row in leaderboard.iterrows():
        model_name = row['model_name']
        mi_p, mi_r = f"{row['Micro_P']:.1%}", f"{row['Micro_R']:.1%}"
        mi_f1, ma_f1 = f"{row['Micro_F1']:.1%}", f"{row['Macro_F1']:.1%}"
        if model_name == judge_model_name:
            print(f"⚖️ {model_name + ' (裁判)':<16} | {mi_p:>8} | {mi_r:>8} | {mi_f1:>12} | {ma_f1:>8} * 不参与排名")
            continue
        marker = "🏅" if rank <= 3 else "🔸"
        print(f"{marker} {model_name:<16} | {mi_p:>8} | {mi_r:>8} | {mi_f1:>12} | {ma_f1:>8}")
        rank += 1

    print(f"\n✅ 细粒度对齐矩阵已落盘至: {leaderboard_file}")
    return leaderboard


# ======================================================================
# 高层统一引擎
# ======================================================================
class StructFlowEngine:
    """StructFlow 四阶段流水线的高层封装。

    Parameters
    ----------
    models:
        被测模型名列表，需在 :data:`MODEL_REGISTRY` 内；缺省为全部 9 个模型
        （无对应 API Key 的模型自动跳过）。
    concurrency_mode:
        目前仅支持 ``"async"``（每模型独立信号量 + 滑动窗口防复读）。
    judge_model:
        Phase 2 裁判模型，默认 qwen-max，自动对其执行避嫌。
    system_prompt:
        覆盖默认抽取系统提示词，一般无需修改。
    """

    def __init__(self, models: Optional[List[str]] = None,
                 concurrency_mode: str = "async",
                 judge_model: str = DEFAULT_JUDGE_MODEL,
                 system_prompt: Optional[str] = None):
        if concurrency_mode != "async":
            raise NotImplementedError("当前版本仅支持 concurrency_mode='async'")
        if judge_model not in MODEL_REGISTRY:
            raise ValueError(f"未知裁判模型 {judge_model!r}")
        self.concurrency_mode = concurrency_mode
        self.requested_models = list(models) if models else None
        self.judge_model = judge_model
        self.system_prompt = system_prompt or DEFAULT_SYSTEM_PROMPT
        self._model_configs: Optional[Dict[str, dict]] = None

    def _ensure_configs(self) -> Dict[str, dict]:
        if self._model_configs is None:
            self._model_configs = build_model_configs(self.requested_models)
        return self._model_configs

    def _judge_config(self) -> Dict[str, str]:
        load_dotenv()
        meta = MODEL_REGISTRY[self.judge_model]
        api_key = os.getenv(meta["key_env"])
        if not api_key:
            raise RuntimeError(f"裁判模型 {self.judge_model} 缺少环境变量 {meta['key_env']}")
        return {"api_key": api_key, "base_url": meta["base_url"], "model_name": self.judge_model}

    # ----- 分步 API -----
    def run_extraction(self, data_path: str, output_dir: str = "output_eval"):
        """Phase 1：多模型零样本抽取（断点续跑，落盘矩阵与明细日志）。"""
        configs = self._ensure_configs()
        return asyncio.run(run_phase_1(str(data_path), configs, self.system_prompt, str(output_dir)))

    def run_consensus(self, data_path: str, output_dir: str = "output_eval"):
        """Phase 2：裁判共识，产出伪金标准 Phase2_Pseudo_Gold_Full.json。"""
        self._ensure_configs()
        od = Path(output_dir)
        return run_phase_2_consensus(
            matrix_path=str(od / "Phase1_01_Matrix.csv"),
            logs_path=str(od / "Phase1_Detailed_Logs.csv"),
            raw_data_path=str(data_path), output_dir=str(od),
            judge_config=self._judge_config())

    def score_f1(self, output_dir: str = "output_eval", gold_path: Optional[str] = None):
        """Phase 4：对已固化的 True Gold 计算实体级 Micro/Macro F1，返回排行榜 DataFrame。"""
        od = Path(output_dir)
        gold = Path(gold_path) if gold_path else od / "Phase3_True_Gold_Final.json"
        if not gold.exists():
            raise FileNotFoundError(
                f"未找到 True Gold: {gold}。\n"
                "Phase 3 为人工环节，请先启动观测舱完成复核：streamlit run ui/dashboard.py，"
                "固化 True Gold 后再调用 score_f1()。")
        return run_f1_alignment(str(gold), str(od / "Phase1_Detailed_Logs.csv"),
                                str(od), self.judge_model)

    # ----- 一键编排 -----
    def run_pipeline(self, data_path: str,
                     schema: Optional[type] = None,
                     output_dir: str = "output_eval",
                     gold_path: Optional[str] = None):
        """零样本抽取 → 判官共识 →（人工复核）→ F1 评分。

        若已存在 True Gold（``gold_path`` 或 ``output_dir/Phase3_True_Gold_Final.json``），
        一路跑完 Phase 4 并返回排行榜 ``pandas.DataFrame``；否则在 Phase 3 前暂停并返回
        ``None``，同时打印人工复核指引（human-in-the-loop，不伪装全自动）。
        """
        if schema is not None and not (isinstance(schema, type)
                                       and issubclass(schema, MedicalRecordSchema)):
            raise NotImplementedError(
                "当前版本内置 MedicalRecordSchema 装甲；自定义 Schema 属后续能力。")

        data_path = Path(data_path)
        if not data_path.exists():
            raise FileNotFoundError(f"数据文件不存在: {data_path}")
        od = Path(output_dir)

        print("=" * 70)
        print("StructFlow Pipeline ▸ Phase 1/4：多模型零样本抽取")
        print("=" * 70)
        self.run_extraction(data_path, od)

        print("\n" + "=" * 70)
        print("StructFlow Pipeline ▸ Phase 2/4：裁判共识与难度分流")
        print("=" * 70)
        self.run_consensus(data_path, od)

        gold = Path(gold_path) if gold_path else od / "Phase3_True_Gold_Final.json"
        if gold.exists():
            print("\n" + "=" * 70)
            print("StructFlow Pipeline ▸ Phase 4/4：实体级 Micro/Macro F1（检测到 True Gold）")
            print("=" * 70)
            return self.score_f1(od, gold_path)

        print("\n" + "=" * 70)
        print("⛔ Pipeline 在 Phase 3 前暂停（Human-in-the-Loop，无法跳过人工复核）")
        print("-" * 70)
        print(f"  伪金标准：{od / 'Phase2_Pseudo_Gold_Full.json'}")
        print("  请启动观测舱完成事实核查 / 归一化 / 人工复核，固化 True Gold：")
        print("      streamlit run ui/dashboard.py")
        print("  完成后执行：")
        print("      leaderboard = engine.score_f1()")
        print("=" * 70)
        return None
