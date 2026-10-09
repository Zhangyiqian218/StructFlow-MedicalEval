"""Phase3 True Gold 终检：用户人工复核完成后、跑 Phase4 前的闸门检查。"""
import json
from pathlib import Path
import pandas as pd
from pydantic import ValidationError
from structflow.schemas import MedicalRecordSchema
from phase4 import PRIMARY_KEYS, flatten_to_facts

BASE = Path(__file__).resolve().parent
OUT = BASE / "output_eval"
GOLD = OUT / "Phase3_True_Gold_Final.json"
XLSX = BASE / "data" / "医生口述查房记录_50条.xlsx"
LOGS = OUT / "Phase1_Detailed_Logs.csv"

issues = []   # 阻断性问题
warns = []     # 预警（不阻断）

# ---------- 1. 加载 ----------
try:
    gold = json.load(open(GOLD, encoding="utf-8"))
except Exception as e:
    print(f"❌ True Gold JSON 无法解析: {e}")
    raise SystemExit(1)

df_src = pd.read_excel(XLSX)
df_src["记录编号"] = df_src["记录编号"].astype(str)
src_ids = df_src["记录编号"].tolist()
src_text = dict(zip(df_src["记录编号"], df_src["口述全文"].astype(str)))

# ---------- 2. ID 完整性 ----------
gold_ids = [str(r.get("record_id")) for r in gold]
dup_ids = {x for x in gold_ids if gold_ids.count(x) > 1}
if dup_ids:
    issues.append(f"record_id 重复: {sorted(dup_ids)}")
missing = sorted(set(src_ids) - set(gold_ids))
extra = sorted(set(gold_ids) - set(src_ids))
if missing:
    issues.append(f"缺少 {len(missing)} 条 record_id: {missing}")
if extra:
    issues.append(f"多出 {len(extra)} 条 record_id: {extra}")
print(f"[1] 条数: Gold {len(gold)} / 源表 {len(src_ids)} | 缺失 {len(missing)} | 多余 {len(extra)} | 重复 {len(dup_ids)}")

# ---------- 3. 逐条结构检查 ----------
EMPTY = (None, "", [], {})
def scan_empty(obj, path=""):
    """返回违反稀疏原则的空值路径"""
    hits = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            if v in EMPTY:
                hits.append(f"{path}.{k}={v!r}")
            else:
                hits += scan_empty(v, f"{path}.{k}")
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            if v in EMPTY:
                hits.append(f"{path}[{i}]={v!r}")
            else:
                hits += scan_empty(v, f"{path}[{i}]")
    return hits

schema_fail, sparse_fail, empty_gold, text_mismatch = [], [], [], []
human_modified, auto_modified = 0, 0
pk_dup_warn = []

for r in gold:
    rid = str(r.get("record_id"))
    gj = r.get("gold_json")

    if not isinstance(gj, dict) or not gj:
        empty_gold.append(rid)
        continue

    # 3.1 Schema 强校验（含 extra=forbid 与枚举）
    try:
        MedicalRecordSchema(**gj)
    except ValidationError as e:
        locs = sorted({'.'.join(str(i) for i in err['loc']) + f"({err['type']})"
                       for err in e.errors()})
        schema_fail.append((rid, locs))

    # 3.2 稀疏原则
    eh = scan_empty(gj)
    if eh:
        sparse_fail.append((rid, eh[:5]))

    # 3.3 原文一致性（防串行/错位，归一化空白后比较）
    if rid in src_text:
        norm = lambda s: "".join(str(s).split())
        if norm(r.get("original_text", "")) != norm(src_text[rid]):
            text_mismatch.append(rid)

    # 3.4 人工/自动修改统计
    if r.get("human_overrides"):
        human_modified += 1
    if r.get("auto_changes"):
        auto_modified += 1

    # 3.5 Phase4 主键碰撞预警：同模块内同主键多条目（事实寻址会合并）
    for mod, pk in PRIMARY_KEYS.items():
        containers = []
        if mod in ("results", "orders"):
            em = gj.get("exam_management", {})
            if isinstance(em, dict) and isinstance(em.get(mod), list):
                containers = em[mod]
        elif mod == "allergies":
            ma = gj.get("medical_alerts", {})
            if isinstance(ma, dict) and isinstance(ma.get("allergies"), list):
                containers = ma["allergies"]
        else:
            v = gj.get(mod)
            if isinstance(v, list):
                containers = v
        seen = {}
        for it in containers:
            if isinstance(it, dict) and pk in it:
                key = str(it[pk]).strip().lower()
                seen[key] = seen.get(key, 0) + 1
        dups = {k: c for k, c in seen.items() if c > 1}
        if dups:
            pk_dup_warn.append((rid, mod, dups))

print(f"[2] Schema 校验失败: {len(schema_fail)} 条")
for rid, locs in schema_fail:
    print(f"    ✗ {rid}: {locs[:4]}")
print(f"[3] 稀疏原则违规(空值/null): {len(sparse_fail)} 条")
for rid, eh in sparse_fail[:10]:
    print(f"    ✗ {rid}: {eh}")
print(f"[4] 空 gold_json: {len(empty_gold)} 条 {empty_gold}")
print(f"[5] gold 内原文与源表错位/不一致: {len(text_mismatch)} 条 {text_mismatch}")
print(f"[6] 人工修改 {human_modified} 条 | 自动修正 {auto_modified} 条")
print(f"[7] Phase4 同主键多条目预警: {len(pk_dup_warn)} 处")
for rid, mod, dups in pk_dup_warn[:15]:
    print(f"    ⚠ {rid} {mod}: {dups}")

# ---------- 4. 与 Phase1 日志对齐 ----------
if LOGS.exists():
    df_log = pd.read_csv(LOGS)
    df_log["record_id"] = df_log["record_id"].astype(str)
    log_ids = set(df_log["record_id"])
    models = sorted(df_log["model_name"].unique())
    missing_log = sorted(set(gold_ids) - log_ids)
    # 每条×每模型应有一行
    grid = df_log.groupby("record_id")["model_name"].nunique()
    incomplete = grid[grid != len(models)]
    parsed = df_log[df_log["score"] == 1].groupby("model_name").size()
    print(f"[8] Phase1 日志: {len(log_ids)} 条记录 × {len(models)} 模型 {models}")
    print(f"    Gold 中有但日志缺的记录: {len(missing_log)} {missing_log}")
    if len(incomplete):
        warns.append(f"日志模型覆盖不齐: {incomplete.to_dict()}")
    print("    各模型成功解析条数:")
    for m, c in parsed.items():
        print(f"      {m}: {c}/{len(gold_ids)}")
else:
    warns.append("Phase1 日志不存在，Phase4 无法评测")

# ---------- 5. flatten 冒烟测试 ----------
for r in gold[:3]:
    f = flatten_to_facts(r["gold_json"])
    if len(f) == 0:
        issues.append(f"flatten 后事实为空: {r['record_id']}")
total_facts = sum(len(flatten_to_facts(r["gold_json"])) for r in gold)
print(f"[9] flatten 事实总数: {total_facts} (平均 {total_facts/max(len(gold),1):.1f} 条/记录)")

# ---------- 汇总 ----------
print("\n" + "=" * 70)
if empty_gold or schema_fail or sparse_fail or text_mismatch or missing or extra or dup_ids:
    issues += [f"schema 失败 {len(schema_fail)}", f"稀疏违规 {len(sparse_fail)}",
               f"空gold {len(empty_gold)}", f"原文错位 {len(text_mismatch)}"]
if issues:
    print("❌ 终检未通过，阻断项：")
    for i in issues:
        print(f"   - {i}")
else:
    print("✅ 终检全部通过，可以运行 Phase4。")
if warns:
    print("⚠️ 预警：")
    for w in warns:
        print(f"   - {w}")
