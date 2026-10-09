import os
import json
import re
import time
import asyncio
import difflib
import pandas as pd
from pathlib import Path
from typing import Optional, Tuple, Dict, Any, List, Union, Literal
from pydantic import BaseModel, Field, ValidationError, ConfigDict
from openai import AsyncOpenAI, APITimeoutError, APIConnectionError, RateLimitError
from dotenv import load_dotenv

# ==========================================
# 1. Pydantic V2 强类型防幻觉装甲 (Literal 枚举绝对边界)
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
# 2. 异步提取器：独立信号量 + 全局去重防复读
# ==========================================
class AsyncExtractor:
    def __init__(self, name: str, api_key: str, base_url: str, concurrency: int):
        self.name = name
        _name = name.lower()
        self.is_reasoning = ("r1" in _name or "reasoning" in _name or "kimi" in _name)
        self.timeout = 180.0 if self.is_reasoning else 60.0
        self.max_tokens = 8000 if self.is_reasoning else 2500
        if "kimi" in _name:
            self.temperature = 1.0
        elif self.is_reasoning:
            self.temperature = 0.6
        else:
            self.temperature = 0.1
        self.client = AsyncOpenAI(api_key=api_key, base_url=base_url, timeout=self.timeout)

        self.sem = asyncio.Semaphore(concurrency)
        self.history: List[str] = []
        self.dedup_lock = asyncio.Lock()

    @staticmethod
    def clean_json_string(text: str) -> str:
        if not text:
            return "{}"
        text = re.sub(r'<think>.*?(?:</think>|$)', '', text, flags=re.DOTALL)
        s = text.find('{')
        e = text.rfind('}')
        if s != -1 and e != -1 and e >= s:
            return text[s:e+1]
        return text.strip()

    async def _evaluate(self, system_prompt: str, user_text: str):
        raw_response = ""
        for attempt in range(4):
            try:
                resp = await self.client.chat.completions.create(
                    model=self.name,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_text}
                    ],
                    temperature=self.temperature,
                    max_tokens=self.max_tokens
                )
                raw_response = resp.choices[0].message.content or ""
                cleaned = self.clean_json_string(raw_response)
                parsed = json.loads(cleaned)

                validated = MedicalRecordSchema(**parsed)
                concise = validated.model_dump(
                    exclude_none=True, exclude_defaults=True, exclude_unset=True)
                if not concise:
                    raise ValueError("JSON 结构合规但无有效业务内容 (空提取)")
                cj = json.dumps(concise, ensure_ascii=False)

                # 全局去重（本模型已完成的全部输出，加锁原子化）
                dup_sim = 0.0
                async with self.dedup_lock:
                    for prev in self.history:
                        s = difflib.SequenceMatcher(None, cj, prev).ratio()
                        if s > dup_sim:
                            dup_sim = s
                    if dup_sim > 0.90:
                        duplicate = True
                    else:
                        duplicate = False
                        self.history.append(cj)
                if duplicate:
                    if attempt < 3:
                        await asyncio.sleep(2 + attempt)
                        continue
                    return raw_response, "", None, f"雷同阻断: 与历史输出相似度 {dup_sim:.2%}"

                return raw_response, cj, concise, None

            except (RateLimitError, APITimeoutError, APIConnectionError) as e:
                if attempt < 3:
                    await asyncio.sleep(2 ** attempt)
                    continue
                return raw_response, "", None, f"网关错误({type(e).__name__}): {str(e)[:120]}"
            except json.JSONDecodeError as e:
                if attempt < 3:
                    await asyncio.sleep(1 + attempt)
                    continue
                return raw_response, "", None, f"JSON结构撕裂/截断: {str(e)[:100]}"
            except ValidationError as e:
                return raw_response, "", None, f"Schema幻觉拦截: {str(e)[:200]}"
            except ValueError as e:
                return raw_response, "", None, f"业务校验失败: {str(e)[:120]}"
            except Exception as e:
                if "429" in str(e) and attempt < 3:
                    await asyncio.sleep(2 ** attempt)
                    continue
                return raw_response, "", None, f"底层异常: {str(e)[:150]}"
        return raw_response, "", None, "重试4次仍失败"

    async def run(self, system_prompt: str, user_text: str):
        async with self.sem:
            return await self._evaluate(system_prompt, user_text)

# ==========================================
# 3. 主调度：按记录聚合，完成即落盘
# ==========================================
async def run_phase_1(data_path: str, model_configs: dict, system_prompt: str, output_dir: str):
    os.makedirs(output_dir, exist_ok=True)
    matrix_path = os.path.join(output_dir, "Phase1_01_Matrix.csv")
    logs_path = os.path.join(output_dir, "Phase1_Detailed_Logs.csv")

    df = pd.read_excel(data_path)
    df['记录编号'] = df['记录编号'].astype(str)

    extractors: Dict[str, AsyncExtractor] = {}
    for name, cfg in model_configs.items():
        if cfg.get("api_key"):
            extractors[name] = AsyncExtractor(
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
# 4. 主程序
# ==========================================
if __name__ == "__main__":
    load_dotenv()
    BASE_DIR = Path(__file__).resolve().parent
    DATA_FILE = BASE_DIR / "医生口述查房记录_50条.xlsx"
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
