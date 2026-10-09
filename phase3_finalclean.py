"""终检清洗：删除 True Gold 中残留的空字符串键（稀疏原则），带备份与变更留痕。"""
import json
import shutil
from pathlib import Path

OUT = Path(__file__).resolve().parent / "output_eval"
GOLD = OUT / "Phase3_True_Gold_Final.json"
BAK = OUT / "Phase3_True_Gold_Final.bak.json"

data = json.load(open(GOLD, encoding="utf-8"))

if not BAK.exists():
    shutil.copy2(GOLD, BAK)
    print(f"已备份原文件 -> {BAK.name}")
else:
    print("备份已存在，跳过备份")

def strip_empty(obj, path, changes):
    """递归删除值为 None / '' / [] / {} 的键，返回清洗后的对象"""
    if isinstance(obj, dict):
        new = {}
        for k, v in obj.items():
            p = f"{path}.{k}" if path else k
            if v is None or v == "" or v == [] or v == {}:
                changes.append(f"删除空值字段 {p}={v!r}")
                continue
            new[k] = strip_empty(v, p, changes)
        return new
    if isinstance(obj, list):
        return [strip_empty(x, f"{path}[{i}]", changes) for i, x in enumerate(obj)]
    return obj

total = 0
for rec in data:
    changes = []
    rec["gold_json"] = strip_empty(rec["gold_json"], "", changes)
    if changes:
        rec.setdefault("auto_changes", [])
        rec["auto_changes"] = list(rec["auto_changes"]) + [
            f"[phase3终检清洗] {c}" for c in changes]
        total += len(changes)
        print(f"{rec['record_id']}: {len(changes)} 处 -> {[c.split(' ',1)[1] for c in changes]}")

json.dump(data, open(GOLD, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
print(f"\n清洗完成，共删除 {total} 个空值字段。")
