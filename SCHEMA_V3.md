# 医疗口述查房记录 — L0 Schema 规范 (V3.1 完整版)

`StructFlow` 医疗查房与诊疗指令 **L0 输出**规范。本规范全面覆盖了绝对时间锚点、结构化与定性体征解耦、复杂用药逻辑（适应症/负荷与维持量/原值追溯/条件分支）、液体管理及临床流转，旨在将重症、急诊及病房等高复杂度查房文本转化为下游 HIS/CDSS 系统可直接消费的深层嵌套 JSON 字典。

---

## 目录

1. [顶层结构设计](#1-顶层结构设计)
2. [核心嵌套字段定义](#2-核心嵌套字段定义)
3. [核心业务规则](#3-核心业务规则)
4. [语料解析全景示例](#4-语料解析全景示例)

---

## 1. 顶层结构设计

解析结果的顶层包含 11 大模块（采用稀疏输出：若文本中未提及某模块/字段，直接省略该键，不输出空字典 `{}`、空数组 `[]` 或 null）：

| 模块名称 | JSON 键名 | 类型 | 对应业务解决痛点 |
|------|------|------|------|
| **基础元数据** | `metadata` | dict | 记录查房绝对时间锚点 |
| **患者画像与时态** | `patient_profile` | dict | 提取年龄/性别、入院/术后天数、既往史 |
| **体格检查** | `physical_examination` | dict | 结构化生命体征与定性异常体征严格分离 |
| **临床评分量表** | `clinical_scores` | list[dict] | 包含评分名称、数值与临床解释，支撑下游判定 |
| **诊断信息** | `diagnoses` | list[dict] | 涵盖主次诊断、疾病分级、确诊/疑似状态及依据 |
| **用药医嘱** | `medication_orders` | list[dict] | 解决适应症、负荷/维持量、原值追溯、用药条件的提取 |
| **液体管理** | `fluid_management` | list[dict] | 解决重症/急诊查房中补液方案、尿量目标、出入量的痛点 |
| **非药物干预** | `non_pharm_interventions` | list[dict] | 收纳手术、护理、饮食干预，并附带执行意图 |
| **检查检验管理** | `exam_management` | dict | 拆分已回报结果（带采集时间）与新开立医嘱（带排除条件） |
| **临床警告** | `medical_alerts` | dict | 过敏史（细化反应类型与严重度）、诊疗禁忌症 |
| **诊疗流向规划** | `patient_flow_plans` | list[dict] | 会诊科室、转科目标、目的与时机 |

---

## 2. 核心嵌套字段定义

### 2.1 `metadata` (基础元数据)
| 字段 | 类型 | 说明 |
|------|------|------|
| `record_time` | str | 本次查房的记录或发生时间（如 `"2026-09-01 12:00"`） |

### 2.2 `patient_profile` (患者画像与时态)
| 字段 | 类型 | 说明 |
|------|------|------|
| `age` | int | 年龄数值 |
| `gender` | str | 男/女/未知 |
| `admission_days` | int | 入院天数或患病时长 |
| `post_op_days` | int | 术后天数 |
| `past_medical_history`| list[str]| 既往疾病史、手术史、个人史（如吸烟、饮酒史） |

### 2.3 `physical_examination` (体格检查)
定量体征剥离单位（默认 T:℃, P:次/分, R:次/分, BP:mmHg, SpO2:%），定性体征独立成组。
```json
{
  "vital_signs": {
    "temperature": 37.2,
    "pulse": 110,
    "respiration": 26,
    "systolic_bp": 100,
    "diastolic_bp": 65,
    "spo2": 95
  },
  "abnormal_signs": [
    "口唇轻度发绀", 
    "颈静脉怒张", 
    "双肺底细湿啰音", 
    "心尖部2/6级收缩期吹风样杂音"
  ]
}
```

### 2.4 `clinical_scores` (临床评分)
| 字段 | 类型 | 说明 |
|------|------|------|
| `score_name` | str | 评分量表名称（如“NYHA分级”、“GCS评分”） |
| `score_value` | str | 评分结果（如“IV级”、“8分”） |
| `interpretation` | str | 临床含义解释（如“重度心衰”、“重型颅脑损伤”），便于下游反推 |

### 2.5 `diagnoses` (诊断列表)
| 字段 | 类型 | 说明 |
|------|------|------|
| `disease_name` | str | 疾病名称 |
| `diagnosis_type` | str | 枚举：`主要诊断`、`次要诊断`、`并发症`、`既往疾病`、`未指定` |
| `grading` | str | 疾病分级、分期或危险度（如“3级很高危”、“T2N1M0”） |
| `status` | str | 枚举：`确诊`、`疑似`、`排除` |
| `diagnostic_basis`| list[str]| 支撑该诊断的核心临床依据（如“心电图ST段抬高”） |

### 2.6 `medication_orders` (用药医嘱)
支持同组用药、负荷量及疗程提取，解决修改追溯及条件干预。
| 字段 | 类型 | 说明 |
|------|------|------|
| `drug_name` | str | 药物名称 |
| `intent` | list[str] | 枚举：`新增`、`停药`、`换药`、`改量`、`维持` |
| `indication` | str | 用药适应症（如“控制心室率”、“抗感染”），支撑合理性审核 |
| `loading_dose` | str | 首次负荷剂量（如“300mg”），无则省略该字段 |
| `dosage` | str | 维持剂量或普通单次剂量（如“100mg”、“半片”） |
| `frequency` | str | 给药频率（如“qd”、“st”） |
| `route` | str | 给药途径（如“静推”、“口服”） |
| `course_duration` | str | 用药疗程（如“吃三天”、“术前”） |
| `infusion_method`| str | 输注方式（如“10%静推，剩余1小时泵入”） |
| `timing_relation`| str | 用药时机（如“餐前15-30分钟”“餐后1小时”） |
| `application_site`| str | 用药部位（如“疱疹破溃处涂抹”） |
| `monitoring_target`| str | 监测目标/阈值（如“静息心率<80次/分”） |
| `cautions`| str | 注意事项（如“评估出血风险”“避免吗啡”） |
| `adjusted_from_drug`| str | 替换前的药物名（仅当 `intent` 包含“换药”时触发） |
| `adjusted_from_dosage`| str| 修改前的原剂量（仅当 `intent` 包含“改量”时触发，如“原为一片”） |
| `conditional_actions` | str | 条件逻辑（如“若心率<60则停药”、“根据尿量调整”） |
| `skin_test_required`| bool| 是否需要皮试 |
| `combination_group`| str | 联合用药分组标记（如“抗血小板组”、“静脉输液组A”） |
| `other_notes`| list[str] | 兜底：原文真实存在但无法归类的信息，避免误判幻觉 |

### 2.7 `fluid_management` (液体管理)
| 字段 | 类型 | 说明 |
|------|------|------|
| `fluid_type` | str | 补液种类（如“0.9%生理盐水”、“胶体液”） |
| `total_volume` | str | 目标总量（如“500ml”） |
| `infusion_rate` | str | 输液速度（如“20ml/h”） |
| `urine_output_target` | str | 目标尿量（如“>1ml/kg/h”） |
| `io_balance_goal` | str | 出入量平衡目标（如“负平衡500ml”） |

### 2.8 `non_pharm_interventions` (非药物干预)
| 字段 | 类型 | 说明 |
|------|------|------|
| `intervention_type`| str | 枚举：`手术`、`介入`、`护理`、`饮食`、`康复`、`其他` |
| `intervention_name`| str | 干预项目（如“急诊冠脉造影”、“I级护理”、“低脂饮食”） |
| `intent` | str | 状态与意图（枚举：`拟做`、`已做`、`维持`、`调整`、`取消`） |
| `details` | str | 执行细节或前置条件 |

### 2.9 `exam_management` (检查检验管理)
分为 `results`（已出结果的事实）与 `orders`（新开立的医嘱）。
```json
{
  "results": [
    {
      "exam_name": "肌钙蛋白I",
      "result_value": "8.6",
      "unit": "ng/mL",
      "abnormality_flag": "↑",
      "collection_time": "2026-09-01 08:00" 
    }
  ],
  "orders": [
    {
      "exam_name": "痰培养及药敏",
      "urgency": "急查",
      "specimen_type": "痰液",
      "conditional_actions": "使用抗生素前留取",
      "exclusion_conditions": "若昨日已查则免查"
    }
  ]
}
```

### 2.10 `medical_alerts` (临床警告)
```json
{
  "allergies": [
    {
      "allergen": "青霉素",
      "reaction_type": "皮疹",
      "severity": "中度"
    }
  ],
  "contraindications": ["禁用β受体阻滞剂"]
}
```

### 2.11 `patient_flow_plans` (诊疗流向规划)
| 字段 | 类型 | 说明 |
|------|------|------|
| `flow_type` | str | 枚举：`会诊`、`转科`、`出院`、`手术室流转` |
| `target_department`| str | 目标科室或团队（如“心衰中心”） |
| `purpose` | str | 流向目的（如“评估是否安装ICD”、“带药出院”） |
| `status_or_timing` | str | 执行时机或紧急状态（如“病情稳定后”、“立即启动”） |

---

## 3. 核心业务规则

1. **意图与修改解耦 (Adjustments)**：禁止使用布尔值表达修改状态。口述中要求的更改动作抽取为 `intent`，目标生效值写入当前字段（如 `dosage`），原信息写入专用的 `adjusted_from_*` 字段以实现追溯。
2. **条件触发与排除限制 (Conditions & Exclusions)**：带有前提触发条件的诊疗计划（如“若收缩压<130则减量”）整体提取至 `conditional_actions`；临床禁忌或“免做”等否定语义提取至 `exclusion_conditions` 或 `medical_alerts`，保证临床语义不丢失。
3. **状态诊断识别 (Status Normalization)**：具备推测、鉴别语义的表达（如“排除左房血栓”、“高度怀疑哮喘”）需准确拆解至 `diagnoses` 列表中的 `status` 字段（疑似/排除），拦截大模型幻觉。

---

## 4. 语料解析全景示例 

> **口述原文**：“记录时间2026-09-01 12:00。患者75岁女，5年前确诊扩张型心肌病。1周前受凉后气促加重。T37.2℃ P110次/分 R26次/分 BP100/65mmHg。口唇轻度发绀，颈静脉怒张，双肺底细湿啰音，心尖部2/6级杂音，双下肢中度凹陷性水肿。BNP结果：2850pg/mL（↑↑）。诊断：慢性心力衰竭急性失代偿（NYHA IV级），扩张型心肌病。计划：半卧位，吸氧，心电监护。静推呋塞米20mg利尿，根据尿量调整，目标负平衡500ml。小剂量多巴酚丁胺持续泵入改善心输出量。病情稳定后复查BNP，请心衰中心会诊评估后续方案。注意该患者对青霉素过敏（曾起严重皮疹）。”

**JSON 输出 (Gold Standard)**:
```json
{
  "metadata": {
    "record_time": "2026-09-01 12:00"
  },
  "patient_profile": {
    "age": 75,
    "gender": "女",
    "past_medical_history": ["扩张型心肌病病史5年"]
  },
  "physical_examination": {
    "vital_signs": {
      "temperature": 37.2,
      "pulse": 110,
      "respiration": 26,
      "systolic_bp": 100,
      "diastolic_bp": 65
    },
    "abnormal_signs": [
      "口唇轻度发绀",
      "颈静脉怒张",
      "双肺底细湿啰音",
      "心尖部2/6级杂音",
      "双下肢中度凹陷性水肿"
    ]
  },
  "clinical_scores": [
    {
      "score_name": "NYHA分级",
      "score_value": "IV级",
      "interpretation": "重度心衰，静息状态下有症状"
    }
  ],
  "diagnoses": [
    {
      "disease_name": "慢性心力衰竭急性失代偿",
      "diagnosis_type": "主要诊断",
      "grading": "NYHA IV级",
      "status": "确诊",
      "diagnostic_basis": ["气促加重", "肺底细湿啰音", "双下肢水肿", "BNP显著升高"]
    },
    {
      "disease_name": "扩张型心肌病",
      "diagnosis_type": "既往疾病",
      "status": "确诊",
      "diagnostic_basis": ["既往确诊史"]
    }
  ],
  "medication_orders": [
    {
      "drug_name": "呋塞米",
      "intent": ["新增"],
      "indication": "利尿",
      "dosage": "20mg",
      "frequency": "st",
      "route": "静推",
      "conditional_actions": "根据尿量调整"
    },
    {
      "drug_name": "多巴酚丁胺",
      "intent": ["新增"],
      "indication": "改善心输出量",
      "dosage": "小剂量",
      "frequency": "持续",
      "route": "持续泵入"
    }
  ],
  "fluid_management": [
    {
      "io_balance_goal": "负平衡500ml"
    }
  ],
  "non_pharm_interventions": [
    {
      "intervention_type": "护理",
      "intervention_name": "半卧位",
      "intent": "拟做"
    },
    {
      "intervention_type": "护理",
      "intervention_name": "吸氧",
      "intent": "拟做"
    },
    {
      "intervention_type": "护理",
      "intervention_name": "心电监护",
      "intent": "拟做"
    }
  ],
  "exam_management": {
    "results": [
      {
        "exam_name": "BNP",
        "result_value": "2850",
        "unit": "pg/mL",
        "abnormality_flag": "↑↑"
      }
    ],
    "orders": [
      {
        "exam_name": "BNP",
        "urgency": "平诊",
        "specimen_type": "血液",
        "conditional_actions": "病情稳定后复查"
      }
    ]
  },
  "medical_alerts": {
    "allergies": [
      {
        "allergen": "青霉素",
        "reaction_type": "皮疹",
        "severity": "严重"
      }
    ]
  },
  "patient_flow_plans": [
    {
      "flow_type": "会诊",
      "target_department": "心衰中心",
      "purpose": "评估后续方案",
      "status_or_timing": "病情稳定后"
    }
  ]
}
```