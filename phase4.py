import os
import re
import json
import pandas as pd
from pathlib import Path

# ==========================================
# 1. 核心业务配置：主键定义
# ==========================================
PRIMARY_KEYS = {
    "clinical_scores": "score_name",
    "diagnoses": "disease_name",
    "medication_orders": "drug_name",
    "fluid_management": "fluid_type",
    "non_pharm_interventions": "intervention_name",
    "allergies": "allergen",
    "patient_flow_plans": "target_department",
    "results": "exam_name",
    "orders": "exam_name"
}

def normalize_medical_value(val: str, field_name: str = "") -> str:
    """带字段路由的精准医疗数值归一化 (防止全局误伤)"""
    if not isinstance(val, str):
        return val
        
    val = val.strip().lower()
    
    # 1. 仅针对频次字段 (frequency)：统一为阿拉伯数字，兼容中文"两/次"等写法
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
        
    # 2. 仅针对给药途径字段 (route)
    if field_name == "route":
        route_map = {
            "iv": "静脉注射", "ivgtt": "静脉滴注", "po": "口服", "im": "肌肉注射", "ih": "皮下注射"
        }
        return route_map.get(val, val)
        
    # 3. 仅针对涉及重量和体积的数值字段
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
    """将嵌套 JSON 打平为主键寻址的一维事实集合"""
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
                        # 主键本身不做归一化，依赖 Phase 3 人工统一
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
            # 传入当前的字典键 (k) 作为路由依据
            val = normalize_medical_value(str(v), k) if isinstance(v, (str, int, float)) else v
            facts.add(f"{new_prefix}:{val}")
            
    return facts

# ==========================================
# 2. 评测计算 (TP/FP/FN 留底支撑 Micro 计算)
# ==========================================
def calculate_metrics(gold_facts: set, pred_facts: set) -> tuple:
    if not gold_facts and not pred_facts:
        return 0, 0, 0, 1.0 
    
    tp = len(gold_facts & pred_facts)
    fp = len(pred_facts - gold_facts)
    fn = len(gold_facts - pred_facts)
    
    precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
    recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0.0
    
    return tp, fp, fn, f1

def run_f1_alignment(gold_path: str, logs_path: str, output_dir: str, judge_model_name: str):
    os.makedirs(output_dir, exist_ok=True)
    
    try:
        with open(gold_path, 'r', encoding='utf-8') as f:
            gold_data = json.load(f)
    except FileNotFoundError:
        print(f"❌ 找不到金标准文件: {gold_path}，请确保 Phase 3 审计完成。")
        return

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
        return
        
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
            "record_id": record_id,
            "model_name": model_name,
            "tp": tp,
            "fp": fp,
            "fn": fn,
            "record_f1": record_f1
        })
        
    df_results = pd.DataFrame(results)
    if df_results.empty:
        return
        
    # 汇总计算 Macro 和 Micro
    leaderboard_data = []
    for model_name, group in df_results.groupby('model_name'):
        macro_f1 = group['record_f1'].mean()
        
        sum_tp = group['tp'].sum()
        sum_fp = group['fp'].sum()
        sum_fn = group['fn'].sum()
        
        micro_p = sum_tp / (sum_tp + sum_fp) if (sum_tp + sum_fp) > 0 else 0.0
        micro_r = sum_tp / (sum_tp + sum_fn) if (sum_tp + sum_fn) > 0 else 0.0
        micro_f1 = 2 * micro_p * micro_r / (micro_p + micro_r) if (micro_p + micro_r) > 0 else 0.0
        
        leaderboard_data.append({
            "model_name": model_name,
            "Micro_P": micro_p,
            "Micro_R": micro_r,
            "Micro_F1": micro_f1,
            "Macro_F1": macro_f1
        })
        
    leaderboard = pd.DataFrame(leaderboard_data)
    # 使用 Micro F1 进行排名
    leaderboard = leaderboard.sort_values(by='Micro_F1', ascending=False).reset_index(drop=True)
    
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
        mi_p = f"{row['Micro_P']:.1%}"
        mi_r = f"{row['Micro_R']:.1%}"
        mi_f1 = f"{row['Micro_F1']:.1%}"
        ma_f1 = f"{row['Macro_F1']:.1%}"
        
        # 裁判隔离
        if model_name == judge_model_name:
            print(f"⚖️ {model_name + ' (裁判)':<16} | {mi_p:>8} | {mi_r:>8} | {mi_f1:>12} | {ma_f1:>8} * 不参与排名")
            continue
            
        marker = "🏅" if rank <= 3 else "🔸"
        print(f"{marker} {model_name:<16} | {mi_p:>8} | {mi_r:>8} | {mi_f1:>12} | {ma_f1:>8}")
        rank += 1
        
    print(f"\n✅ 细粒度对齐矩阵已落盘至: {leaderboard_file}")

if __name__ == "__main__":
    BASE_DIR = Path(__file__).resolve().parent
    OUTPUT_DIR = BASE_DIR / "output_eval"
    OUTPUT_DIR_NOTHINK = BASE_DIR / "output_eval_nothink"
    JUDGE_MODEL = "qwen-max"
    GOLD = str(OUTPUT_DIR / "Phase3_True_Gold_Final.json")

    # 组1：默认思考组（主评测）
    run_f1_alignment(
        gold_path=GOLD,
        logs_path=str(OUTPUT_DIR / "Phase1_Detailed_Logs.csv"),
        output_dir=str(OUTPUT_DIR),
        judge_model_name=JUDGE_MODEL
    )
    # 组2：非思考组（消融，可选；仅在已生成对应日志时运行）
    nothink_logs = OUTPUT_DIR_NOTHINK / "Phase1_Detailed_Logs.csv"
    if nothink_logs.exists():
        run_f1_alignment(
            gold_path=GOLD,
            logs_path=str(nothink_logs),
            output_dir=str(OUTPUT_DIR_NOTHINK),
            judge_model_name=JUDGE_MODEL
        )
    else:
        print("（非思考组消融未运行：output_eval_nothink 日志不存在，已跳过。）")