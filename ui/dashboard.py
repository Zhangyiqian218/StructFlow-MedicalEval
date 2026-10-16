# -*- coding: utf-8 -*-
import sys
import json
from pathlib import Path

# 无论从哪个工作目录启动 streamlit，都把项目根（ui/ 的上级）加入导入路径
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import streamlit as st
import pandas as pd

st.set_page_config(page_title="StructFlow 观测舱", layout="wide")
st.title("🩺 StructFlow 结构化抽取评测台")

BASE_DIR = _PROJECT_ROOT
OUTPUT_DIR = BASE_DIR / "output_eval"

tab1, tab2, tab3, tab4 = st.tabs([
    "Phase 1: 矩阵与日志",
    "Phase 2: 判官共识",
    "Phase 3: 审计舱",
    "Phase 4: F1 排行榜"
])

# ==========================================================
# Phase 1
# ==========================================================
with tab1:
    st.header("L0 零样本抽取矩阵")
    matrix_path = OUTPUT_DIR / "Phase1_01_Matrix.csv"
    if matrix_path.exists():
        df_matrix = pd.read_csv(matrix_path)
        st.caption(f"当前进度：{len(df_matrix)} / 50 条")
        num_cols = [c for c in df_matrix.columns if c != "record_id"]
        st.dataframe(
            df_matrix.style.background_gradient(cmap="Greens", axis=None, subset=num_cols),
            use_container_width=True)
        st.subheader("各模型当前通过率")
        st.bar_chart(df_matrix[num_cols].mean(numeric_only=True).sort_values(ascending=False))
    else:
        st.info("等待 Phase 1 数据落盘...")

    st.divider()
    st.header("详细日志（错误类型 / 耗时 / 原始输出）")
    logs_path = OUTPUT_DIR / "Phase1_Detailed_Logs.csv"
    if logs_path.exists():
        logs = pd.read_csv(logs_path)
        models = sorted(logs["model_name"].unique())
        sel = st.multiselect("筛选模型", models, default=models, key="p1_models")
        err_only = st.checkbox("只看失败/错误 (score=0)", key="p1_err")
        lf = logs[logs["model_name"].isin(sel)]
        if err_only:
            lf = lf[lf["score"] == 0]
        st.dataframe(lf[["record_id", "model_name", "score", "latency_sec", "error_type"]],
                     use_container_width=True)
        if not lf.empty:
            st.subheader("查看某条原始输出")
            lf_reset = lf.reset_index(drop=True)
            j = st.selectbox("选择日志条目", range(len(lf_reset)),
                             format_func=lambda i: f"{lf_reset.loc[i,'record_id']} / {lf_reset.loc[i,'model_name']}",
                             key="p1_pick")
            r = lf_reset.loc[j]
            a, b = st.columns(2)
            with a:
                st.caption("模型压缩后 JSON")
                st.code(r.get("cleaned_json", ""), language="json")
            with b:
                st.caption("模型原始返回")
                st.code(r.get("raw_response", ""))
    else:
        st.info("等待 Phase 1 详细日志...")

# ==========================================================
# Phase 2
# ==========================================================
with tab2:
    st.header("伪金标准生成状态")
    pseudo_path = OUTPUT_DIR / "Phase2_Pseudo_Gold_Full.json"
    if pseudo_path.exists():
        pseudo_data = json.load(open(pseudo_path, encoding="utf-8"))
        n = len(pseudo_data)
        needs = sum(1 for x in pseudo_data if x.get("needs_human_review"))
        c1, c2, c3 = st.columns(3)
        c1.metric("总样本", n)
        c2.metric("需人工介入 (Bad Case)", needs, delta_color="inverse")
        c3.metric("高共识直接采纳", n - needs)
        st.subheader("IRT 难度分布（pass_count / 9）")
        pc = pd.Series([x.get("pass_count", 0) for x in pseudo_data])
        st.bar_chart(pc.value_counts().sort_index())
        st.subheader("查看伪金样本")
        ids = [str(x["record_id"]) for x in pseudo_data]
        picked = st.selectbox("选择病例", ids, key="p2_pick")
        for x in pseudo_data:
            if str(x["record_id"]) == picked:
                tag = "⚠️ 需人工介入" if x.get("needs_human_review") else "✅ 高共识"
                st.caption(f"难度 {x.get('pass_count')}/9 | {tag}")
                a, b = st.columns(2)
                with a:
                    st.caption("原始口述文本")
                    st.write(x.get("original_text", ""))
                with b:
                    st.caption("判官 proposed_json")
                    st.json(x.get("proposed_json", {}))
                if x.get("judge_error"):
                    st.error(x["judge_error"])
                break
    else:
        st.info("等待 Phase 2 判官裁决...")

# ==========================================================
# Phase 3：人机协同复核（以自动建议金标准为起点）
# ==========================================================
with tab3:
    from structflow.schemas import MedicalRecordSchema
    st.header("真值对齐与审计舱")
    st.caption("流程：先读原文（背对背）→ 核对自动修正版 → 确认/微调后提交。进度实时保存。")
    pseudo_path = OUTPUT_DIR / "Phase2_Pseudo_Gold_Full.json"
    suggested_path = OUTPUT_DIR / "Phase3_Suggested_Gold.json"
    gold_path = OUTPUT_DIR / "Phase3_True_Gold_Final.json"

    def load_json(p, default):
        return json.load(open(p, encoding="utf-8")) if p.exists() else default

    def save_gold(data):
        json.dump(data, open(gold_path, "w", encoding="utf-8"),
                  ensure_ascii=False, indent=2)

    if pseudo_path.exists():
        pseudo_data = load_json(pseudo_path, [])
        suggested = {str(x["record_id"]): x for x in load_json(suggested_path, [])}
        gold_data = load_json(gold_path, [])
        done_ids = {str(x["record_id"]) for x in gold_data}

        c1, c2, c3 = st.columns(3)
        c1.metric("总病历", len(pseudo_data))
        c2.metric("已复核", len(done_ids))
        c3.metric("待复核", len(pseudo_data) - len(done_ids))

        only_pending = st.checkbox("只看待复核", value=True, key="p3_only_pending")
        only_changed = st.checkbox("只看自动修正过的", value=False, key="p3_only_changed")
        items = [x for x in pseudo_data
                 if (not only_pending or str(x["record_id"]) not in done_ids)
                 and (not only_changed
                      or suggested.get(str(x["record_id"]), {}).get("was_modified"))]
        ids = [str(x["record_id"]) for x in items]

        if ids:
            picked = st.selectbox("选择病历（按顺序逐条复核）", ids, key="p3_pick")
            rec = next(x for x in pseudo_data if str(x["record_id"]) == picked)
            original = rec["original_text"]
            sug = suggested.get(picked, {})
            start_json = sug.get("suggested_json") or rec.get("proposed_json", {})
            auto_changes = sug.get("changes", [])
            st.caption(f"难度 {rec.get('pass_count')}/9 | "
                       + ("✏️ 已自动修正" if sug.get("was_modified") else "未改动"))
            if auto_changes:
                with st.expander(f"自动修正留痕（{len(auto_changes)} 项）", expanded=True):
                    for c in auto_changes:
                        st.write(f"- {c}")

            lc, rc = st.columns(2)
            with lc:
                st.subheader("① 先读原文")
                st.info(original)
            with rc:
                st.subheader("② 核对 / 微调 JSON")
                default_json = json.dumps(start_json, ensure_ascii=False, indent=2)
                edited_text = st.text_area("可直接编辑", value=default_json,
                                           height=420, key=f"p3_edit_{picked}")
                ov_text = st.text_area("你的额外修改留痕（可留空；每行：字段, 动作, 原因）",
                                       value="", height=70, key=f"p3_ov_{picked}")

            with st.expander("事实核对辅助（关键实体是否在原文出现）"):
                def walk(o, path="root"):
                    if isinstance(o, dict):
                        for k, v in o.items():
                            yield from walk(v, f"{path}.{k}")
                    elif isinstance(o, list):
                        for i, v in enumerate(o):
                            yield from walk(v, f"{path}[{i}]")
                    else:
                        yield path, o
                key_fields = ("drug_name", "disease_name", "score_name", "exam_name",
                              "allergen", "intervention_name", "fluid_type")
                try:
                    cand = json.loads(edited_text)
                    flags = []
                    for path, val in walk(cand):
                        leaf = path.rsplit(".", 1)[-1].split("[")[0]
                        if leaf in key_fields and val:
                            sval = str(val).strip()
                            if not (sval in original or sval[:3] in original):
                                flags.append((path, sval))
                    if flags:
                        st.error("以下关键实体在原文未直接命中，请人工确认：")
                        for p, v in flags:
                            st.write(f"- `{p}` = {v}")
                    else:
                        st.success("关键实体均能在原文找到。")
                except json.JSONDecodeError:
                    st.caption("JSON 合法后才显示核对结果。")

            if st.button("③ 校验并确认提交", key="p3_submit", type="primary"):
                try:
                    edited = json.loads(edited_text)
                    model = MedicalRecordSchema(**edited)
                    clean = model.model_dump(exclude_none=True,
                                            exclude_defaults=True, exclude_unset=True)
                    if not clean:
                        raise ValueError("金标准为空：请至少保留诊断/用药等有效信息")
                    human_ov = []
                    for line in ov_text.splitlines():
                        parts = [p.strip() for p in line.split(",")]
                        if len(parts) >= 3:
                            human_ov.append({"field": parts[0], "action": parts[1],
                                             "reason": parts[2]})
                    record = {"record_id": picked, "original_text": original,
                              "gold_json": clean,
                              "human_overrides": human_ov,
                              "auto_changes": auto_changes,
                              "review_status": "modified" if human_ov else "confirmed"}
                    gold_data = [x for x in gold_data if str(x["record_id"]) != picked]
                    gold_data.append(record)
                    save_gold(gold_data)
                    st.success(f"✅ {picked} 已确认落盘！请在上方下拉选择下一条。")
                except json.JSONDecodeError as e:
                    st.error(f"JSON 语法错误：{e}")
                except Exception as e:
                    st.error(f"校验失败：{e}")
        else:
            st.success("当前筛选条件下没有待复核病历了。")
            if len(done_ids) == len(pseudo_data):
                st.balloons()
                st.success("🎉 全部 50 条复核完成，可进入 Phase4！")

        with st.expander("查阅已固化 True Gold"):
            if gold_data:
                gd_ids = [str(x["record_id"]) for x in gold_data]
                gpick = st.selectbox("选择", gd_ids, key="p3_done_pick")
                g = next(x for x in gold_data if str(x["record_id"]) == gpick)
                st.json(g["gold_json"])
            else:
                st.caption("暂无已复核记录。")
    else:
        st.info("等待 Phase 2 判官裁决...")

# ==========================================================
# Phase 4
# ==========================================================
with tab4:
    st.header("实体级 F1 评测指标")
    leaderboard_path = OUTPUT_DIR / "Phase4_F1_Leaderboard.csv"
    if leaderboard_path.exists():
        df_rank = pd.read_csv(leaderboard_path)
        st.dataframe(df_rank.style.format({
            "Micro_P": "{:.1%}", "Micro_R": "{:.1%}",
            "Micro_F1": "{:.1%}", "Macro_F1": "{:.1%}"
        }), use_container_width=True)
        st.subheader("Micro F1 对比")
        st.bar_chart(df_rank.set_index("model_name")["Micro_F1"])
    else:
        st.info("等待 Phase 4 算分完成...")
