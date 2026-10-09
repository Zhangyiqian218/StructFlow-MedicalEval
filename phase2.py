import os
import json
import time
import pandas as pd
from pathlib import Path
from typing import Tuple, Dict, Any, Optional
from openai import OpenAI, APITimeoutError, APIConnectionError, RateLimitError
from dotenv import load_dotenv

# 直接复用 SDK 包的脱水装甲
from structflow.schemas import MedicalRecordSchema

class StructFlowJudge:
    def __init__(self, api_key: str, base_url: str, model_name: str):
        self.client = OpenAI(api_key=api_key, base_url=base_url, timeout=60.0)
        self.model_name = model_name

    def evaluate_judge(self, system_prompt: str, user_text: str) -> Tuple[str, str, Optional[Dict[str, Any]], Optional[str]]:
        raw_response = ""  # 【修复 2】：前置初始化，防止非网络异常时抛出 UnboundLocalError
        max_attempts = 3 
        
        for attempt in range(max_attempts):
            try:
                response = self.client.chat.completions.create(
                    model=self.model_name,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_text}
                    ],
                    temperature=0.1,
                    max_tokens=3000
                )
                raw_response = response.choices[0].message.content or ""
                
                start_idx, end_idx = raw_response.find('{'), raw_response.rfind('}')
                if start_idx != -1 and end_idx != -1 and end_idx >= start_idx:
                    cleaned_text = raw_response[start_idx:end_idx+1]
                else:
                    cleaned_text = raw_response.strip()
                    
                parsed_json = json.loads(cleaned_text)
                
                # 终极压缩术
                validated_model = MedicalRecordSchema(**parsed_json) 
                concise_dict = validated_model.model_dump(exclude_none=True, exclude_defaults=True, exclude_unset=True)
                
                if not concise_dict:
                    raise ValueError("无有效业务内容")
                    
                return raw_response, json.dumps(concise_dict, ensure_ascii=False), concise_dict, None
                
            except (RateLimitError, APITimeoutError, APIConnectionError) as e:
                if attempt < max_attempts - 1:
                    time.sleep(2 ** attempt)
                    continue
                return raw_response, "", None, f"网关崩溃: {str(e)}"
            except Exception as e:
                return raw_response, "", None, f"校验失败: {str(e)}"

def run_phase_2_consensus(matrix_path: str, logs_path: str, raw_data_path: str, output_dir: str, judge_config: dict):
    os.makedirs(output_dir, exist_ok=True)
    
    df_matrix = pd.read_csv(matrix_path)
    df_logs = pd.read_csv(logs_path)
    df_raw = pd.read_excel(raw_data_path)
    
    df_raw['记录编号'] = df_raw['记录编号'].astype(str)
    df_matrix['record_id'] = df_matrix['record_id'].astype(str)
    df_logs['record_id'] = df_logs['record_id'].astype(str)

    judge_engine = StructFlowJudge(**judge_config)
    all_pseudo_gold = []
    
    # 【修复 3】：定义第一梯队模型优先级列表（越靠前，越优先作为参考专家）
    PRIORITY_MODELS = ["deepseek-v3", "qwen-turbo", "kimi-k2.6", "deepseek-r1", "qwen-plus", "glm-4-air", "glm-4-plus", "glm-4-flash"]
    
    total_records = len(df_matrix)
    print(f"⚖️ 启动 Phase 2 判官仲裁引擎 | 判官模型: {judge_engine.model_name}")
    print("=" * 60)

    for idx, matrix_row in df_matrix.iterrows():
        record_id = matrix_row['record_id']
        raw_text_row = df_raw[df_raw['记录编号'] == record_id]
        if raw_text_row.empty:
            continue
            
        original_text = raw_text_row.iloc[0]['口述全文']
        
        # 1. 计算 IRT 难度系数
        model_scores = matrix_row.drop('record_id')
        pass_count = int(model_scores.sum())  # 转原生 int，避免 numpy.int64 无法 JSON 序列化
        
        # 2. 裁判避嫌机制与专家质量排序
        successful_models = model_scores[model_scores == 1].index.tolist()
        candidate_models = [m for m in successful_models if m != judge_engine.model_name]
        
        # 按 PRIORITY_MODELS 的索引位置升序排序，未知模型排在最后
        candidate_models = sorted(candidate_models, key=lambda x: PRIORITY_MODELS.index(x) if x in PRIORITY_MODELS else 999)
        
        expert_opinions = ""
        selected_models = []
        if candidate_models:
            selected_models = candidate_models[:3]  # 优先取排在前面的高质量模型
            opinions_df = df_logs[(df_logs['record_id'] == record_id) & (df_logs['model_name'].isin(selected_models))]
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

        print(f"▶ [{idx+1}/{total_records}] 仲裁病例: {record_id} (难度系数: {pass_count}/9) ", end="")
        _, _, parsed_dict, error_msg = judge_engine.evaluate_judge(
            system_prompt="你是一个权威的医疗数据仲裁者，严格执行JSON校验。", 
            user_text=judge_prompt
        )

        # 判官输出崩溃时，用最高优先级成功专家结果兜底，给人工一个可用起点而非空白
        fallback_model = None
        fallback_dict = None
        if parsed_dict is None and selected_models:
            fb = df_logs[(df_logs['record_id'] == record_id) &
                         (df_logs['model_name'] == selected_models[0])]
            if not fb.empty:
                try:
                    fallback_dict = json.loads(fb.iloc[0]['cleaned_json'])
                    fallback_model = selected_models[0]
                except Exception:
                    fallback_dict = None

        # 3. 全量兜底与人类审查标记
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

    # 4. 全量固化落盘
    output_file = Path(output_dir) / "Phase2_Pseudo_Gold_Full.json"
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(all_pseudo_gold, f, ensure_ascii=False, indent=2)
    
    print("\n" + "=" * 60)
    print(f"🎉 Phase 2 全量预标注完成！")
    print(f"👉 包含完整 50 条数据的文件已保存至: {output_file}")
    print(f"🚨 请在生成的 JSON 中搜索 \"needs_human_review\": true，完成最后的人工精修。")

if __name__ == "__main__":
    load_dotenv()
    BASE_DIR = Path(__file__).resolve().parent
    
    JUDGE_CONFIG = {
        "api_key": os.getenv("DASHSCOPE_API_KEY"),
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "model_name": "qwen-max" 
    }
    
    try:
        run_phase_2_consensus(
            matrix_path=str(BASE_DIR / "output_eval" / "Phase1_01_Matrix.csv"), # 【修复 1】：对齐 Phase 1 新版输出的文件名
            logs_path=str(BASE_DIR / "output_eval" / "Phase1_Detailed_Logs.csv"),
            raw_data_path=str(BASE_DIR / "data" / "医生口述查房记录_50条.xlsx"),
            output_dir=str(BASE_DIR / "output_eval"),
            judge_config=JUDGE_CONFIG
        )
    except Exception as e:
        print(f"\n❌ 程序遭遇异常: {str(e)}")