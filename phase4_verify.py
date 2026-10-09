"""Phase4 结果独立核验：复算 Micro/Macro、留底逐记录 F1、量化主键重复记录影响。"""
import json
import pandas as pd
from pathlib import Path
from phase4 import flatten_to_facts, calculate_metrics

OUT = Path(__file__).resolve().parent / "output_eval"
SPECIAL = {"DICT-014", "DICT-024", "DICT-032", "DICT-043", "DICT-049"}

gold = {str(r["record_id"]): flatten_to_facts(r.get("gold_json", {}))
        for r in json.load(open(OUT / "Phase3_True_Gold_Final.json", encoding="utf-8"))}
df = pd.read_csv(OUT / "Phase1_Detailed_Logs.csv")
df["record_id"] = df["record_id"].astype(str)

rows = []
for _, x in df.iterrows():
    pf = set()
    cj = x["cleaned_json"]
    if pd.notna(cj) and str(cj).strip():
        try:
            pf = flatten_to_facts(json.loads(cj))
        except Exception:
            pf = set()
    tp, fp, fn, f1 = calculate_metrics(gold[x["record_id"]], pf)
    rows.append({"record_id": x["record_id"], "model_name": x["model_name"],
                 "tp": tp, "fp": fp, "fn": fn, "record_f1": f1,
                 "gold_facts": len(gold[x["record_id"]]), "pred_facts": len(pf),
                 "parsed": int(x["score"] == 1),
                 "group": "主键重复5条" if x["record_id"] in SPECIAL else "其余45条"})

pr = pd.DataFrame(rows)
pr.to_csv(OUT / "Phase4_Per_Record_F1.csv", index=False, encoding="utf-8-sig")

# ---- 1. 复算排行榜，与落盘 CSV 比对 ----
board = pd.read_csv(OUT / "Phase4_F1_Leaderboard.csv")
print("===== 1. 独立复算 vs 落盘 Leaderboard =====")
ok = True
for m, g in pr.groupby("model_name"):
    tp, fp, fn = g.tp.sum(), g.fp.sum(), g.fn.sum()
    mp = tp/(tp+fp) if tp+fp else 0
    mr = tp/(tp+fn) if tp+fn else 0
    mf1 = 2*mp*mr/(mp+mr) if mp+mr else 0
    mac = g.record_f1.mean()
    ref = board[board.model_name == m].iloc[0]
    match = (abs(mf1-ref.Micro_F1) < 1e-9 and abs(mac-ref.Macro_F1) < 1e-9
             and abs(mp-ref.Micro_P) < 1e-9 and abs(mr-ref.Micro_R) < 1e-9)
    ok &= match
    print(f"{m:<12} P={mp:.4f} R={mr:.4f} MicroF1={mf1:.4f} MacroF1={mac:.4f} "
          f"TP={tp} FP={fp} FN={fn} {'✓' if match else '✗ 不一致!'}")
print("复算结论:", "✅ 全部一致" if ok else "❌ 存在偏差")

# ---- 2. 主键重复 5 条 vs 其余 45 条 ----
print("\n===== 2. 主键重复记录影响（各组 Macro F1）=====")
piv = pr.groupby(["model_name", "group"]).record_f1.mean().unstack()
piv["差值(特殊-常规)"] = piv["主键重复5条"] - piv["其余45条"]
print(piv.round(4).to_string())

# ---- 3. 解析失败记录 ----
print("\n===== 3. 未成功解析（按0分计，拉低 Recall）=====")
fail = pr[pr.parsed == 0].groupby("model_name").record_id.apply(list)
for m, ids in fail.items():
    print(f"{m}: {len(ids)} 条 {ids}")
if fail.empty:
    print("无")

# ---- 4. 极端记录：F1=0 与最高/最低 ----
print("\n===== 4. 各模型 F1=0 的记录 =====")
z = pr[pr.record_f1 == 0].groupby("model_name").record_id.apply(list)
for m, ids in z.items():
    print(f"{m}: {ids}")
if z.empty:
    print("无")
