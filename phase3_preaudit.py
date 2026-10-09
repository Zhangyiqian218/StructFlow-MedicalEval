import os
import json
import asyncio
import pandas as pd
from pathlib import Path
from openai import AsyncOpenAI
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = BASE_DIR / "output_eval"

pseudo = json.load(open(OUTPUT_DIR / "Phase2_Pseudo_Gold_Full.json", encoding="utf-8"))

client = AsyncOpenAI(
    api_key=os.getenv("DASHSCOPE_API_KEY"),
    base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
    timeout=90)
sem = asyncio.Semaphore(5)

SYS = """你是独立的医疗数据事实核查员。给定【原始病历口述】和【待审JSON】，严格对照原文核查，只输出JSON：
{
  "hallucinations": ["JSON中存在、但原文完全未提及的关键信息，注明字段"],
  "missing": ["原文明确提及、但JSON漏掉的关键信息（药物/诊断/剂量/关键处理/关键数值），注明原文依据"],
  "verdict": "pass 或 review"
}
判定原则：
- 只有JSON关键信息能在原文找到依据才不算幻觉；同义改写、合理概括不算幻觉。
- 原文的核心药物、诊断、重要剂量、关键处理必须在JSON中体现，否则列入missing。
- 两者都为空则verdict=pass；任一项非空则verdict=review。"""

async def check(rec):
    rid = rec["record_id"]
    pj = rec.get("proposed_json", {})
    user = f"【原始病历口述】\n{rec['original_text']}\n\n【待审JSON】\n{json.dumps(pj, ensure_ascii=False, indent=2)}"
    async with sem:
        try:
            r = await client.chat.completions.create(
                model="deepseek-v3",
                messages=[{"role": "system", "content": SYS},
                          {"role": "user", "content": user}],
                temperature=0.0, max_tokens=1500)
            txt = r.choices[0].message.content or "{}"
            s, e = txt.find("{"), txt.rfind("}")
            out = json.loads(txt[s:e+1])
            out["record_id"] = rid
            out["pre_flagged"] = rec.get("needs_human_review", False)
            return out
        except Exception as ex:
            return {"record_id": rid, "verdict": "error", "hallucinations": [],
                    "missing": [], "error": str(ex)[:100]}

async def main():
    res = await asyncio.gather(*[check(r) for r in pseudo])
    json.dump(res, open(OUTPUT_DIR / "Phase3_Audit_Checks.json", "w", encoding="utf-8"),
              ensure_ascii=False, indent=2)
    review = [x for x in res if x.get("verdict") == "review"]
    err = [x for x in res if x.get("verdict") == "error"]
    print(f"核查完成：{len(res)} 条 | 需重点复核 {len(review)} | 核查出错 {len(err)}")
    print("=" * 60)
    for x in review:
        print(f"\n⚠️ {x['record_id']}" + ("（判官原已标记）" if x.get("pre_flagged") else ""))
        for h in x.get("hallucinations", []):
            print(f"   幻觉: {h}")
        for m in x.get("missing", []):
            print(f"   遗漏: {m}")

asyncio.run(main())
