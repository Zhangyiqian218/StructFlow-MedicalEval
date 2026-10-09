"""Phase 4 命令行入口：主键寻址实体级 Micro/Macro F1 评测。

F1 工具与编排逻辑已沉淀至 structflow.engine，本文件 re-export 以兼容
``from phase4 import flatten_to_facts, calculate_metrics, PRIMARY_KEYS`` 等历史引用，
并保留可直接运行的薄入口；程序化调用请使用 ``structflow.StructFlowEngine``。
"""
from pathlib import Path

from structflow.engine import (
    PRIMARY_KEYS,
    normalize_medical_value,
    flatten_to_facts,
    calculate_metrics,
    run_f1_alignment,
)

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
        judge_model_name=JUDGE_MODEL,
    )

    # 组2：非思考组（消融，可选；仅在已生成对应日志时运行）
    nothink_logs = OUTPUT_DIR_NOTHINK / "Phase1_Detailed_Logs.csv"
    if nothink_logs.exists():
        run_f1_alignment(
            gold_path=GOLD,
            logs_path=str(nothink_logs),
            output_dir=str(OUTPUT_DIR_NOTHINK),
            judge_model_name=JUDGE_MODEL,
        )
    else:
        print("（非思考组消融未运行：output_eval_nothink 日志不存在，已跳过。）")
