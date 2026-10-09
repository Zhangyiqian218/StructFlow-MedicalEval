"""Phase 2 命令行入口：LLM-as-a-Judge 裁判共识与难度分流。

裁判类与编排逻辑已沉淀至 structflow 包，本文件仅保留可直接运行的薄入口；
程序化调用请使用 ``structflow.StructFlowEngine``。
"""
import os
from pathlib import Path

from dotenv import load_dotenv

# 兼容历史引用：from phase2 import StructFlowJudge
from structflow.extractors import StructFlowJudge
from structflow.engine import run_phase_2_consensus, MODEL_REGISTRY

if __name__ == "__main__":
    load_dotenv()
    BASE_DIR = Path(__file__).resolve().parent

    JUDGE_MODEL = "qwen-max"
    _meta = MODEL_REGISTRY[JUDGE_MODEL]
    JUDGE_CONFIG = {
        "api_key": os.getenv(_meta["key_env"]),
        "base_url": _meta["base_url"],
        "model_name": JUDGE_MODEL,
    }

    try:
        run_phase_2_consensus(
            matrix_path=str(BASE_DIR / "output_eval" / "Phase1_01_Matrix.csv"),
            logs_path=str(BASE_DIR / "output_eval" / "Phase1_Detailed_Logs.csv"),
            raw_data_path=str(BASE_DIR / "data" / "医生口述查房记录_50条.xlsx"),
            output_dir=str(BASE_DIR / "output_eval"),
            judge_config=JUDGE_CONFIG,
        )
    except Exception as e:
        print(f"\n❌ 程序遭遇异常: {str(e)}")
