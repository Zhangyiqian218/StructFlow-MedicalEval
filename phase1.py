"""Phase 1 命令行入口：多模型零样本抽取。

编排逻辑已沉淀至 structflow.engine，本文件仅保留可直接运行的薄入口；
程序化调用请使用 ``structflow.StructFlowEngine``。
"""
import asyncio
from pathlib import Path

from dotenv import load_dotenv

from structflow.engine import (
    run_phase_1,
    build_model_configs,
    DEFAULT_SYSTEM_PROMPT,
)

if __name__ == "__main__":
    load_dotenv()
    BASE_DIR = Path(__file__).resolve().parent
    DATA_FILE = BASE_DIR / "data" / "医生口述查房记录_50条.xlsx"
    OUTPUT_DIR = BASE_DIR / "output_eval"

    # 默认调度注册表内全部模型（缺少对应 API Key 的模型自动跳过）
    MODEL_CONFIGS = build_model_configs()

    asyncio.run(run_phase_1(
        str(DATA_FILE), MODEL_CONFIGS, DEFAULT_SYSTEM_PROMPT, str(OUTPUT_DIR)))
