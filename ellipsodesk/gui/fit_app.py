import json
import shutil
import sys
from contextlib import nullcontext
from datetime import datetime
from pathlib import Path

import numpy as np
import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
ROOT = Path.home()  # Explorer 浏览根 = 用户主目录（阶段 2 / ADV-5；containment 见 _in_root）
sys.path.insert(0, str(SCRIPTS))

from dispersion import MODELS
from fit import FIT_EV_MAX, FIT_EV_MIN, fringe_thickness_guess
from material_lib import (DATA_DIR, MATERIALS_DIR, ensure_base_materials,
                          list_materials_meta, load_material, save_material)
from spe_parser import parse_spe
from stack_fit import fit_stack, profile_thickness
from psi_display import unfold_psi
from file_explorer import dual_explorer

EV_NM = 1239.841984

MODEL_LABELS = {"cauchy": "Cauchy", "sellmeier": "Sellmeier", "tauc_lorentz": "Tauc-Lorentz"}

st.set_page_config(page_title="EllipsoDesk", page_icon="🔬", layout="wide")

# 侧栏收起箭头与「🗂 Explorer」标题同行：头部横条脱离文档流（不再独占 ~60px 一整行）。
# 注意：data-testid 选择器依赖 Streamlit DOM，版本升级失效时表现为横条回归，按新版 testid 更新。
st.markdown("""
<style>
[data-testid="stSidebarHeader"] {
  position: absolute;
  top: 4px;
  left: 0;
  height: 36px;
  min-height: 36px;
  padding: 0 8px;
  z-index: 50;
  background: transparent;
  align-items: center;
}
section[data-testid="stSidebar"] [data-testid="stSidebarContent"] {
  padding-top: 0;
}
section[data-testid="stSidebar"] h1,
section[data-testid="stSidebar"] h2,
section[data-testid="stSidebar"] h3 {
  padding-right: 44px;
}
/* 「保存为材料」按钮加高舒展：col3 内放不可见锚点，:has() 定位其所在列（2026 主流浏览器均支持） */
.save-mat-anchor { display: none; }
section[data-testid="stColumn"]:has(.save-mat-anchor) .stButton > button {
  min-height: 3.4rem;
  font-size: 1.02rem;
  border-radius: 0.6rem;
  padding: 0.5rem 1rem;
}
/* metric 卡片：±误差与数值同一行。注意 1.66 的 delta 药丸(stMetricDelta)外还有一层
   无 testid 的匿名 flex 包裹（emotion display:flex、块级）——必须把该包裹层与数值
   一起行内化才会并排（只改 stMetricDelta 无效，实测踩坑）。 */
div[data-testid="stMetric"] [data-testid="stMetricValue"] {
  display: inline-flex;
  align-items: baseline;
}
div[data-testid="stMetric"] [data-testid="stMetricValue"] ~ div {
  display: inline-flex;
  align-items: center;
  vertical-align: baseline;
  margin-left: 10px;
}
/* 标题上方大空行分析：Streamlit 默认给主容器(stMainBlockContainer/旧 .block-container)
   padding-top≈6rem，预留对象是 fixed 定位顶栏 stHeader(高≈5.9rem，只含右上角 running 状态
   与菜单、背景透明)——压缩后顶栏仍悬浮右上，与左对齐标题无重叠 */
.block-container,
[data-testid="stMainBlockContainer"] {
  padding-top: 1.5rem !important;
}
/* 药丸字号对齐数值大字（默认主题 metricValueFontSize=2.25rem / weight 400，bundle 实测；
   换自定义主题时需同步） */
div[data-testid="stMetric"] [data-testid="stMetricDelta"] {
  font-size: 2.25rem;
  font-weight: 400;
}
</style>
""", unsafe_allow_html=True)


# ---------------- 层状态（稳定唯一 ID 做 widget key） ----------------

def next_layer_id():
    seq = st.session_state.get("layer_seq", 0) + 1
    st.session_state.layer_seq = seq
    return f"L{seq}"


def make_fit_layer(name, model="cauchy", thickness=1000.0):
    spec = MODELS[model]
    return {"id": next_layer_id(), "name": name, "model": model,
            "thickness": float(thickness), "fit_thickness": True,
            "params": {p: {"value": float(v), "fit": True} for p, v in spec["init"].items()},
            "_last_model": model}


def make_preset_layer(mat_name):
    """从材料库构造预设层：该材料的模型 + 参数（全部 fixed），厚度=标定值（默认拟合）。"""
    doc = json.loads((MATERIALS_DIR / f"{mat_name}.json")
                     .read_text(encoding="utf-8"))
    model = doc.get("model", "cauchy")
    spec = MODELS[model]
    saved = doc.get("params", {})
    return {"id": next_layer_id(), "name": mat_name, "model": model,
            "thickness": float(doc.get("fitted_thickness_nm") or 100.0),
            "fit_thickness": True,
            "params": {p: {"value": float(saved.get(p, spec["init"][p])), "fit": False}
                       for p in spec["params"]},
            "_last_model": model}


def _read_material_doc(mat_name):
    return json.loads((MATERIALS_DIR / f"{mat_name}.json")
                      .read_text(encoding="utf-8"))


def _fft_d_for_n(n589):
    """把 n_guess=1.46 的 FFT 厚度猜测换算到折射率 n 的膜：
    d ∝ 1/q(n)，q=√(n²−sin²θ)。SiN 实测：不换算偏差 +56%，换算后 ~±1%。"""
    dg = st.session_state.get("fft_guess")
    if not dg:
        return None
    q146 = np.sqrt(1.46**2 - np.sin(np.deg2rad(70.0)) ** 2)
    qn = np.sqrt(n589**2 - np.sin(np.deg2rad(70.0)) ** 2)
    return float(dg) * q146 / qn


def make_base_layer(mat_name):
    """基料（kind="base"）→ 起点层：该材料的模型 + 参数（全部 fit，只是初值），
    厚度 = FFT 猜测按基料 n₅₈₉ 换算（无数据时 100 nm），默认拟合。"""
    doc = _read_material_doc(mat_name)
    model = doc.get("model", "cauchy")
    spec = MODELS[model]
    saved = doc.get("params", {})
    fn = MODELS[model]["fn"]
    try:
        n589 = float(np.real(fn(np.array([EV_NM / 589.0]), **saved))[0])
    except Exception:
        n589 = None
    d0 = _fft_d_for_n(n589) if n589 else None
    return {"id": next_layer_id(), "name": mat_name.replace("base-", ""), "model": model,
            "thickness": float(d0 or 100.0),
            "fit_thickness": True,
            "params": {p: {"value": float(saved.get(p, spec["init"][p])), "fit": True}
                       for p in spec["params"]},
            "_last_model": model}


def _find_layer(lid):
    return next((l for l in st.session_state.layers if l["id"] == lid), None)


def layer_summary(lay):
    src = MODEL_LABELS.get(lay["model"], lay["model"])
    nfit = sum(1 for pd in lay["params"].values() if pd.get("fit"))
    nfix = len(lay["params"]) - nfit
    dpart = f"d≈{lay['thickness']:.0f} nm" if lay["fit_thickness"] else f"d={lay['thickness']:.0f} nm"
    if lay["fit_thickness"] and nfit:
        fits = f"●拟合 d+{nfit}参"
    elif lay["fit_thickness"]:
        fits = f"●拟合 d（{nfix}参固定）" if nfix else "●拟合 d"
    elif nfit:
        fits = f"●拟合 {nfit}参"
    else:
        fits = "○全固定"
    return f"{lay['name']} · {src} · {dpart} · {fits}"


def stack_from_layers(layers, angle, ev_min, ev_max):
    out_layers = []
    for lay in layers:
        fit = []
        if lay["fit_thickness"]:
            fit.append("thickness")
        fit += [p for p, d in lay["params"].items() if d["fit"]]
        entry = {"name": lay["name"], "model": lay["model"],
                 "init": {"thickness": float(lay["thickness"])}, "fit": fit}
        for p, d in lay["params"].items():
            entry["init"][p] = float(d["value"])
        if lay.get("rough"):
            entry["roughness"] = {"thickness": float(lay["rough"]["d"]),
                                  "fit": bool(lay["rough"]["fit"])}
        out_layers.append(entry)
    return {"name": "gui_stack", "angle_deg": float(angle),
            "eV_min": float(ev_min), "eV_max": float(ev_max),
            "layers": out_layers, "substrate": "si"}


def wrap360(a):
    return np.asarray(a) % 360.0


# ---------------- 回调（打开弹窗前把默认值种进 session_state） ----------------

def _open_add():
    st.session_state.dialog = "add"
    st.session_state["dlg_name"] = f"film{len(st.session_state.get('layers', [])) + 1}"
    st.session_state["dlg_model"] = "cauchy"
    st.session_state["dlg_d"] = float(st.session_state.get("fft_guess") or 1000.0)
    st.session_state["dlg_fitd"] = True


def _open_edit(lid):
    lay = _find_layer(lid)
    if lay is None:
        return
    st.session_state.dialog = ("edit", lid)
    st.session_state["dlg_e_name"] = lay["name"]
    st.session_state["dlg_e_d"] = float(lay["thickness"])
    st.session_state["dlg_e_fitd"] = bool(lay["fit_thickness"])
    st.session_state["dlg_e_model"] = lay.get("model") if lay.get("model") in MODELS else "cauchy"


def _del_layer(lid):
    layers = st.session_state.get("layers", [])
    st.session_state.layers = [l for l in layers if l["id"] != lid]
    _enforce_top_rough()


def _move_layer(lid, dz):
    layers = st.session_state.layers
    i = next(idx for idx, l in enumerate(layers) if l["id"] == lid)
    j = i + dz
    if 0 <= j < len(layers):
        layers[i], layers[j] = layers[j], layers[i]
        _enforce_top_rough()


def _enforce_top_rough():
    """v1 顶层守则：粗糙层只允许挂在 layers[0]。列表变动后摘除非顶层的 rough（提示）。"""
    layers = st.session_state.get("layers", [])
    for l in layers[1:]:
        if l.pop("rough", None) is not None:
            st.session_state["rough_stripped"] = l["name"]


def _add_rough(lid):
    """⊕ 静默添加：仅顶层可用，默认 d=2.0 nm、参与拟合。"""
    layers = st.session_state.get("layers", [])
    if not layers or layers[0]["id"] != lid:
        return
    layers[0].setdefault("rough", {"d": 2.0, "fit": True})


def _del_rough(lid):
    lay = _find_layer(lid)
    if lay:
        lay.pop("rough", None)


def _open_rough_edit(lid):
    lay = _find_layer(lid)
    if lay is None or not lay.get("rough"):
        return
    st.session_state.dialog = ("rough", lid)
    st.session_state["dlg_r_d"] = float(lay["rough"]["d"])
    st.session_state["dlg_r_fit"] = bool(lay["rough"]["fit"])


def _open_save_mat():
    lf = st.session_state.get("last_fit")
    if not lf:
        return
    fittable = [l["name"] for l in st.session_state.layers]
    if not fittable:
        return
    st.session_state.dialog = "save_mat"
    st.session_state["sm_layer"] = fittable[0]
    st.session_state["sm_name"] = fittable[0]


def _in_root(p):
    try:
        Path(p).resolve().relative_to(ROOT.resolve())
        return True
    except ValueError:
        return False


def _on_fx_event(ev):
    """浏览器组件事件分发。单击 select 仅高亮；双击 open/mat_insert 才引入。"""
    t = ev.get("type")
    p = ev.get("path", "")
    if t == "select":
        st.session_state["fx_sel"] = p
    elif t == "open":
        _sel_spe(p)
    elif t == "enter":
        _nav_to(p)
    elif t == "up":
        _nav_up()
    elif t == "copy":
        _copy_item(p)
    elif t == "paste":
        _paste_clip()
    elif t == "rename":
        _begin_rename(p)
    elif t == "delete":
        _begin_delete(p)
    elif t == "mat_insert":
        _insert_material(p)
    elif t == "mat_view":
        st.session_state.dialog = ("mat_view", p)
    elif t == "mat_rename":
        _begin_mat_rename(p)
    elif t == "mat_delete":
        _begin_mat_delete(p)


def _load_nav_default():
    """上次浏览目录（持久化于用户数据目录 last_dir.json）；无记录/目录已消失则回退用户主目录。"""
    try:
        doc = json.loads((DATA_DIR / "last_dir.json").read_text(encoding="utf-8"))
        d = Path(doc.get("dir") or Path.home())
        return str(d) if d.is_dir() else str(Path.home())
    except Exception:
        return str(Path.home())


def _save_nav_dir(path):
    """目录切换时持久化上次目录；写入失败静默忽略（只读介质/权限等，不影响浏览）。"""
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        (DATA_DIR / "last_dir.json").write_text(
            json.dumps({"dir": str(path)}, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


def _nav_to(path):
    st.session_state["nav_dir"] = str(path)
    _save_nav_dir(path)


def _nav_up():
    cur = Path(st.session_state.nav_dir)
    if cur.parent != cur:
        st.session_state["nav_dir"] = str(cur.parent)
        _save_nav_dir(cur.parent)


def _sel_spe(path):
    st.session_state["spe_selected"] = str(path)


def _copy_item(path):
    st.session_state["clip"] = str(path)


def _paste_clip():
    src = Path(st.session_state.get("clip", ""))
    dst_dir = Path(st.session_state.nav_dir)
    if not src.exists() or not dst_dir.is_dir() or not _in_root(dst_dir):
        return
    target = dst_dir / src.name
    k = 1
    while target.exists():
        target = dst_dir / f"{src.stem}_{k}{src.suffix}"
        k += 1
    if src.is_dir():
        shutil.copytree(src, target)
    else:
        shutil.copy2(src, target)


def _to_trash(p):
    if not _in_root(p):  # 守卫:仅移动 Explorer 根(用户主目录)内路径;材料目录亦在其下
        raise ValueError(f"拒绝移动根目录外路径: {p}")
    trash = DATA_DIR / ".trash"
    trash.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    target = trash / f"{ts}_{p.name}"
    k = 1
    while target.exists():
        target = trash / f"{ts}_{k}_{p.name}"
        k += 1
    shutil.move(str(p), str(target))
    return target


def _begin_rename(path):
    st.session_state.dialog = ("rename", str(path))
    st.session_state["rn_name"] = Path(path).name


def _begin_delete(path):
    st.session_state.dialog = ("delete", str(path))


def _begin_mat_rename(name):
    st.session_state.dialog = ("mat_rename", name)
    st.session_state["mr_name"] = name


def _begin_mat_delete(name):
    st.session_state.dialog = ("mat_delete", name)


def _clear_dialog():
    st.session_state.dialog = None


def _insert_material(mat_name):
    """Explorer 双击材料 → 插入预设层（该材料的模型，参数全部 fixed，厚度=标定值默认拟合）。"""
    if "layers" not in st.session_state:
        st.session_state.layers = []
    try:
        doc = _read_material_doc(mat_name)
    except FileNotFoundError:
        st.toast(f"材料 {mat_name}.json 不在材料库目录（{MATERIALS_DIR}），插入失败",
                 icon=":material/error:")
        return
    except (OSError, json.JSONDecodeError) as exc:
        st.toast(f"材料 {mat_name}.json 读取失败：{exc}", icon=":material/error:")
        return
    lay = make_base_layer(mat_name) if doc.get("kind") == "base" else make_preset_layer(mat_name)
    st.session_state.layers.append(lay)
    name, k = lay["name"], 2
    while any(l["name"] == name for l in st.session_state.layers[:-1]):
        name = f"{lay['name']}{k}"
        k += 1
    st.session_state.layers[-1]["name"] = name
    st.session_state["mat_added"] = name
    _enforce_top_rough()


# ---------------- 弹窗 ----------------

@st.dialog("新增膜层", width="medium", on_dismiss=_clear_dialog)
def add_layer_dialog():
    name = st.text_input("层名", key="dlg_name")
    model = st.segmented_control("色散模型", list(MODELS.keys()), key="dlg_model",
                                 format_func=lambda m: MODEL_LABELS.get(m, m))
    if model is None:
        model = "cauchy"
    spec = MODELS[model]
    st.caption("参数初值（全部参与拟合）：" +
               "，".join(f"{p}={v:g}" for p, v in spec["init"].items()) +
               "；材料预设请到侧栏材料面板双击插入")
    d = st.number_input("厚度初值 (nm)", 1.0, 100000.0, step=1.0, key="dlg_d")
    fitd = st.checkbox("拟合该层厚度", key="dlg_fitd")
    if st.button("插入", icon=":material/add:", type="primary", key="dlg_add_ok"):
        lay = make_fit_layer(name or "layer", model, d)
        lay["fit_thickness"] = fitd
        st.session_state.layers.append(lay)
        _enforce_top_rough()
        st.session_state.dialog = None
        st.rerun()


@st.dialog("编辑膜层", width="medium", on_dismiss=_clear_dialog)
def edit_layer_dialog(lid):
    lay = _find_layer(lid)
    if lay is None:
        st.session_state.dialog = None
        st.rerun()
        return
    name = st.text_input("层名", key="dlg_e_name")
    model = st.segmented_control("色散模型", list(MODELS.keys()), key="dlg_e_model",
                                 format_func=lambda m: MODEL_LABELS.get(m, m))
    if model is None:
        model = lay.get("model") if lay.get("model") in MODELS else "cauchy"
    spec = MODELS[model]
    param_vals = {}
    cols = st.columns(len(spec["params"]))
    for c, p in zip(cols, spec["params"]):
        lo, hi = spec["bounds"][p]
        cur = lay["params"].get(p, {}) if lay.get("model") == model else {}
        v = float(cur.get("value", spec["init"][p]))
        f = bool(cur.get("fit", True))
        param_vals[p] = (c.number_input(p, float(lo), float(hi), v, 0.0001, format="%.5g",
                                        key=f"dlg_pv_{model}_{p}"),
                         c.checkbox("fit", f, key=f"dlg_pf_{model}_{p}"))
    d = st.number_input("厚度 (nm)", 1.0, 100000.0, step=1.0, key="dlg_e_d")
    fitd = st.checkbox("拟合该层厚度", key="dlg_e_fitd")
    # 无「保存」按钮：控件值在每次渲染时直接写回层，改动即时生效
    lay["name"] = name or lay["name"]
    lay["thickness"] = float(d) if d is not None else lay["thickness"]
    lay["fit_thickness"] = bool(fitd)
    lay.update(model=model,
               params={p: {"value": float(v), "fit": bool(f)}
                       for p, (v, f) in param_vals.items() if v is not None},
               _last_model=model)


@st.dialog("表面粗糙层", width="small", on_dismiss=_clear_dialog)
def rough_edit_dialog(lid):
    """零按钮即时生效：EMA(50% void + 本层材料) 的厚度/拟合开关直接写回层。"""
    lay = _find_layer(lid)
    if lay is None or not lay.get("rough"):
        st.session_state.dialog = None
        st.rerun()
        return
    d = st.number_input("粗糙层厚度 (nm)", 0.0, 10.0, step=0.1, key="dlg_r_d")
    f = st.checkbox("拟合粗糙层厚度", key="dlg_r_fit")
    if d is not None:
        lay["rough"] = {"d": float(d), "fit": bool(f)}


@st.dialog("保存为材料", width="medium", on_dismiss=_clear_dialog)
def save_material_dialog():
    lf = st.session_state.get("last_fit")
    if lf is None:
        st.warning("先完成一次拟合", title="无法保存")
        return
    r = lf["result"]
    fittable = [l["name"] for l in st.session_state.layers]
    if not fittable:
        st.warning("当前无色散拟合层（材料库层 nk 已固定，无需保存）", title="无法保存")
        return
    lname = st.selectbox("层", fittable, key="sm_layer")
    name = st.text_input("材料名（保存为 materials/<名称>.json）", key="sm_name")
    lay = next((l for l in st.session_state.layers if l["name"] == lname), None)
    if lay is None:
        st.warning(f"层 {lname!r} 不在当前堆叠中")
        return
    spec = MODELS[lay["model"]]
    params, errs = {}, {}
    for p in spec["params"]:
        key = f"{lname}.{p}"
        if key in r["params"]:
            params[p], errs[p] = r["params"][key]
        else:
            params[p], errs[p] = float(lay["params"][p]["value"]), float("nan")
    n589 = float(np.real(spec["fn"](np.array([EV_NM / 589.0]), **params))[0])
    d_fit = r["thickness_nm"].get(lname)
    d_err = r["thickness_err_nm"].get(f"{lname}.thickness", float("nan"))
    prov = {"spe_file": lf["file"], "thickness_nm": d_fit, "thickness_err_nm": d_err,
            "chi2_red": r["chi2_red"], "fit_window_eV": [lf["ev_min"], lf["ev_max"]],
            "gui": True}
    st.markdown("**预览**")
    st.markdown(
        f"- 模型：**{MODEL_LABELS.get(lay['model'], lay['model'])}**\n"
        + "- " + "；".join(f"`{p}` = {params[p]:.5g} ± {errs[p]:.2g}" for p in spec["params"]) + "\n"
        + f"- n₅₈₉ = **{n589:.4f}**　标定厚度 = {d_fit:.1f} ± {d_err:.1f} nm　χ²ᵣ = {r['chi2_red']:.3g}\n"
        + f"- 来源：`{Path(lf['file']).name}` · 窗口 {lf['ev_min']}–{lf['ev_max']} eV")
    if st.button("保存", icon=":material/save:", type="primary", key="sm_ok"):
        path = save_material(name or lname, lay["model"], params, errs,
                             (lf["ev_min"], lf["ev_max"]), prov)
        st.session_state.dialog = None
        st.session_state["sm_saved"] = str(path)
        st.rerun()


@st.dialog("重命名", width="small", on_dismiss=_clear_dialog)
def rename_dialog(path):
    p = Path(path)
    new = st.text_input("新名称", key="rn_name")
    conflict = (p.parent / new).exists() if new else False
    valid = new and new != p.name and "/" not in new and "\\" not in new and not conflict
    if new and not valid:
        st.warning("名称无效（空/同名/含路径分隔符/已存在）", title="无法重命名")
    if st.button("重命名", icon=":material/edit:", type="primary", key="rn_ok",
                 disabled=not valid):
        p.rename(p.parent / new)
        if st.session_state.get("spe_selected") == str(p):
            st.session_state["spe_selected"] = str(p.parent / new)
        st.session_state.dialog = None
        st.rerun()


@st.dialog("删除", width="small", on_dismiss=_clear_dialog)
def delete_dialog(path):
    p = Path(path)
    st.warning(f"将把 **{p.name}** 移入回收站（用户数据目录 `.trash/`，可手动找回），不会真正删除。")
    if st.button("确认移入回收站", icon=":material/delete:", type="primary", key="del_ok"):
        _to_trash(p)
        if st.session_state.get("spe_selected") == str(p):
            st.session_state["spe_selected"] = None
        st.session_state.dialog = None
        st.rerun()


@st.dialog("重命名材料", width="small", on_dismiss=_clear_dialog)
def mat_rename_dialog(name):
    new = st.text_input("新材料名", key="mr_name")
    mats_dir = MATERIALS_DIR
    dst = mats_dir / f"{new}.json"
    valid = new and new != name and new.isidentifier() and not dst.exists()
    if new and not valid:
        st.warning("名称无效或已存在（建议用字母/数字/下划线）", title="无法重命名")
    if st.button("重命名", icon=":material/edit:", type="primary", key="mr_ok",
                 disabled=not valid):
        src = mats_dir / f"{name}.json"
        doc = json.loads(src.read_text(encoding="utf-8"))
        doc["name"] = new
        dst.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
        src.unlink()
        st.session_state.dialog = None
        st.rerun()


@st.dialog("删除材料", width="small", on_dismiss=_clear_dialog)
def mat_delete_dialog(name):
    try:
        is_base = _read_material_doc(name).get("kind") == "base"
    except FileNotFoundError:
        is_base = False
    if is_base:
        st.caption("内置基料（文献典型初值）——删除后下次启动自动重建。")
    st.warning(f"将把材料 **{name}** 移入回收站（用户数据目录 `.trash/`，可手动找回）。")
    if st.button("确认移入回收站", icon=":material/delete:", type="primary", key="mdel_ok"):
        _to_trash(MATERIALS_DIR / f"{name}.json")
        st.session_state.dialog = None
        st.rerun()


@st.dialog("材料 n·k", width="medium", on_dismiss=_clear_dialog)
def mat_view_dialog(name):
    try:
        nk = load_material(name)
    except Exception as exc:
        st.error(f"无法加载材料 {name}：{exc}")
        return
    eV = np.linspace(0.6, 6.5, 400)
    lam = EV_NM / eV
    n = np.real(nk(eV))
    k = -np.imag(nk(eV))
    i589 = int(np.argmin(np.abs(lam - 589.0)))
    st.caption(f"**{name}** · n₅₈₉ = {n[i589]:.4f} · k₅₈₉ = {k[i589]:.4g}（由材料参数自动推出）")
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=lam, y=n, mode="lines", name="n",
                             line=dict(width=2, color="#2b6cb0")))
    fig.add_trace(go.Scatter(x=lam, y=k, mode="lines", name="k",
                             line=dict(width=2, color="#e0533d", dash="dash"), yaxis="y2"))
    fig.update_layout(height=340, showlegend=True, margin=dict(l=40, r=40, t=20, b=40),
                      xaxis_title="波长 (nm)", yaxis=dict(title="n"),
                      yaxis2=dict(title="k", overlaying="y", side="right", showgrid=False))
    st.plotly_chart(fig, width="stretch")


# ---------------- 页面区块 ----------------

def render_sidebar():
    for k, v in [("angle", 70.0), ("ev_min", FIT_EV_MIN), ("ev_max", FIT_EV_MAX), ("clean", True)]:
        st.session_state.setdefault(k, v)
    st.session_state.setdefault("nav_dir", _load_nav_default())
    ensure_base_materials()   # 基料缺失自动重建（删除后下次启动恢复）
    with st.sidebar:
        st.header("🗂 Explorer")
        nav = Path(st.session_state.nav_dir)
        try:
            ents = sorted(nav.iterdir(), key=lambda q: (q.is_file(), q.name.lower()))
        except (PermissionError, FileNotFoundError):
            ents = []
        ents = [e for e in ents if e.is_dir() or e.suffix.lower() == ".spe"][:100]
        sel = st.session_state.get("spe_selected")
        fxsel = st.session_state.get("fx_sel")
        entries = [{"name": e.name, "path": str(e), "kind": "dir" if e.is_dir() else "spe",
                    "selected": str(e) == fxsel, "loaded": str(e) == sel,
                    "safe": _in_root(e)} for e in ents]
        chain, p = [nav], nav
        while p.parent != p:
            p = p.parent
            chain.append(p)
        crumb = [{"name": (c.name if c.name else str(c)), "path": str(c)}
                 for c in reversed(chain)]
        clip = st.session_state.get("clip")
        materials = []
        for m in list_materials_meta():
            base = m.get("kind") == "base"
            lbl = ("◆ " if base else "") + f"{m['name']} · {MODEL_LABELS.get(m['model'], m['model'] or '?')}"
            if m.get("n589"):
                lbl += f" · {m['n589']:.4f}"
            lbl += " · 基料" if base else ""
            materials.append({"name": m["name"], "label": lbl})

        dual_explorer(
            crumb=crumb, entries=entries, materials=materials,
            selected_name=Path(fxsel).name if fxsel else "",
            loaded_name=Path(sel).name if sel else "",
            clip_name=Path(clip).name if clip else "",
            on_event=_on_fx_event, key="fx")

        if sel and Path(sel).exists() and Path(sel).suffix.lower() == ".spe":
            spe_path = Path(sel)
        else:
            spe_path = None
    return spe_path


def get_preview_model(spe_path):
    """每文件缓存一次快速参考拟合（单层 Cauchy），用于 Ψ 展开的分支选择 + 参考线。
    返回 {"psi": 采样点模型Ψ, "eV_fine": 细网格, "psi_fine": 细网格模型Ψ}。"""
    key = f"pmodel::{str(spe_path)}"
    if key not in st.session_state:
        lf = st.session_state.get("last_fit")
        if lf and lf["file"] == str(spe_path):
            r = lf["result"]
            st.session_state[key] = {"psi": np.asarray(r["Psi_fit"]),
                                     "eV_fine": np.asarray(r["eV_fine"]),
                                     "psi_fine": np.asarray(r["Psi_fit_fine"])}
        else:
            with st.spinner("参考拟合（用于 Ψ 展开）…"):
                stack = {"name": "preview_ref", "angle_deg": 70.0,
                         "eV_min": 1.2, "eV_max": 5.5,
                         "layers": [{"name": "ref", "model": "cauchy",
                                     "init": {"thickness": float(
                                         st.session_state.get("fft_guess") or 1000.0)},
                                     "fit": ["*"]}],
                         "substrate": "si"}
                rr = fit_stack(str(spe_path), stack)
                st.session_state[key] = {"psi": np.asarray(rr["Psi_fit"]),
                                         "eV_fine": np.asarray(rr["eV_fine"]),
                                         "psi_fine": np.asarray(rr["Psi_fit_fine"])}
    return st.session_state[key]


def render_data_section(sp, meta, d_guess, spe_path):
    mh = (sp["eV"] >= st.session_state.ev_min) & (sp["eV"] <= st.session_state.ev_max)
    pm = get_preview_model(spe_path)
    psi_u, _ = unfold_psi(sp["Psi"], pm["psi"])
    fig = make_subplots(rows=2, cols=1, vertical_spacing=0.12,
                        subplot_titles=("Ψ 谱（展开；分支由参考拟合确定）", "Δ 谱 (0–360°)"))
    fig.add_trace(go.Scatter(x=sp["eV"], y=psi_u, mode="markers+lines", name="Ψ (展开)",
                             marker=dict(size=3), line=dict(width=0.5)), row=1, col=1)
    fig.add_trace(go.Scatter(x=pm["eV_fine"], y=pm["psi_fine"], mode="lines", name="参考拟合",
                             line=dict(width=1.0, color="#9aa5b1")), row=1, col=1)
    fig.add_trace(go.Scatter(x=sp["eV"], y=wrap360(sp["Delta"]), mode="markers+lines", name="Δ",
                             marker=dict(size=3), line=dict(width=0.5)), row=2, col=1)
    for row in (1, 2):
        fig.add_vrect(x0=st.session_state.ev_min, x1=st.session_state.ev_max,
                      fillcolor="#2b6cb0", opacity=0.08, line_width=0, row=row, col=1)
    fig.update_layout(height=480, showlegend=False, margin=dict(l=40, r=20, t=50, b=40))
    fig.update_xaxes(title_text="光子能量 (eV)", row=2, col=1)
    st.plotly_chart(fig, width="stretch")
    st.caption(f"📍 {Path(spe_path).name} · {len(sp['eV'])} 点（窗口内 {mh.sum()}）· "
               f"{(meta.get('incidence_deg') or 70):.0f}° · FFT 初值 ≈{d_guess:.0f} nm")


def render_fit_results(lf):
    r = lf["result"]
    eV = np.asarray(r["sp"]["eV"])
    mask = np.asarray(r["mask"])
    st.divider()
    st.subheader("拟合结果")
    st.success(f"{Path(lf['file']).name} · 窗口 {lf['ev_min']}–{lf['ev_max']} eV · 窗口内 {r['n_used']} 点")
    m1, m2, m3 = st.columns([1.25, 1.6, 0.85], vertical_alignment="center")
    with m1:
        for lname, d in r["thickness_nm"].items():
            e = r["thickness_err_nm"].get(f"{lname}.thickness", float("nan"))
            st.metric(lname, f"{d:.2f} nm", f"± {e:.2f}", delta_arrow="off")
        srow = st.columns(2)
        srow[0].metric("χ²ᵣ", f"{r['chi2_red']:.4g}")
        srow[1].metric("rms", f"{r['rms']:.4g}")
    with m2:
        prow = [{"参数": pname, "值": f"{v:.5g}", "±误差": f"{e:.3g}"}
                for pname, (v, e) in r["params"].items()]
        # st.dataframe 行高精确 35px/行 + 表头 38：多余高度会在底部渲染成空行
        st.dataframe(prow, width="stretch", hide_index=True,
                     height=35 * len(prow) + 38)
    with m3:
        st.markdown('<div class="save-mat-anchor"></div>', unsafe_allow_html=True)
        st.button("保存为材料", icon=":material/save:", on_click=_open_save_mat,
                  key="btn_save_mat", width="stretch",
                  help="把色散拟合层保存进材料库，供多层堆叠以固定 nk 引用")
        if st.session_state.get("sm_saved"):
            st.success(f"已保存 → `{st.session_state.sm_saved}`（侧栏材料库可双击引用）")
            del st.session_state["sm_saved"]

    psi_exp_u, _ = unfold_psi(r["sp"]["Psi"], r["Psi_fit"])
    w = (eV >= lf["ev_min"]) & (eV <= lf["ev_max"])
    xw = eV[w]
    eV_fine = np.asarray(r.get("eV_fine") if r.get("eV_fine") is not None else eV)
    psi_fine = np.asarray(r.get("Psi_fit_fine", r["Psi_fit"]))
    delta_fine = np.asarray(r.get("Delta_fit_fine", r["Delta_fit"]))
    nk_fine = r.get("layer_nk_fine") or r["layer_nk"]
    wf = (eV_fine >= lf["ev_min"]) & (eV_fine <= lf["ev_max"])
    xwf = eV_fine[wf]
    fig = make_subplots(rows=2, cols=1, vertical_spacing=0.10,
                        subplot_titles=(f"Ψ：实验(展开,点) vs 拟合(线)　[{lf['ev_min']}–{lf['ev_max']} eV]",
                                        "Δ：实验(点) vs 拟合(线)"))
    fig.add_trace(go.Scatter(x=xw, y=psi_exp_u[w], mode="markers", name="Ψ exp (展开)",
                             marker=dict(size=4, color="#2b6cb0"), opacity=0.7), row=1, col=1)
    fig.add_trace(go.Scatter(x=xwf, y=psi_fine[wf], mode="lines", name="Ψ fit",
                             line=dict(width=1.6, color="#e0533d")), row=1, col=1)
    fig.add_trace(go.Scatter(x=xw, y=wrap360(r["sp"]["Delta"])[w], mode="markers", name="Δ exp",
                             marker=dict(size=4, color="#2b6cb0"), opacity=0.7), row=2, col=1)
    fig.add_trace(go.Scatter(x=xwf, y=wrap360(delta_fine)[wf], mode="lines", name="Δ fit",
                             line=dict(width=1.6, color="#e0533d")), row=2, col=1)
    fig.update_layout(height=520, showlegend=False, margin=dict(l=40, r=20, t=50, b=40),
                      xaxis=dict(range=[lf["ev_min"], lf["ev_max"]]),
                      xaxis2=dict(range=[lf["ev_min"], lf["ev_max"]]))
    fig.update_xaxes(title_text="光子能量 (eV)", row=2, col=1)
    st.plotly_chart(fig, width="stretch")

    with st.expander("各层 n·k / 残差"):
        st.markdown("**各层色散 n(λ) / k(λ)**（左轴 n，右轴 k；窗口内；模型线 2× 密度）")
        colors = ["#2b6cb0", "#e0533d", "#3d8f5f", "#8a5fb0"]
        fign = go.Figure()
        lam_fine = EV_NM / xwf
        for i, (lname, v) in enumerate(nk_fine.items()):
            color = colors[i % len(colors)]
            fign.add_trace(go.Scatter(x=lam_fine, y=np.real(v["nk"])[wf], mode="lines",
                                      name=f"{lname}·n", line=dict(width=2, color=color)))
            fign.add_trace(go.Scatter(x=lam_fine, y=-np.imag(v["nk"])[wf], mode="lines",
                                      name=f"{lname}·k", line=dict(width=2, color=color, dash="dash"),
                                      yaxis="y2"))
        fign.update_layout(height=300, showlegend=True, margin=dict(l=40, r=40, t=20, b=40),
                           xaxis_title="波长 (nm)",
                           yaxis=dict(title="n"),
                           yaxis2=dict(title="k", overlaying="y", side="right", showgrid=False))
        st.plotly_chart(fign, width="stretch")
        st.markdown("**窗口内残差 (Is/Ic)**")
        eVm = eV[mask]
        figr = go.Figure()
        figr.add_trace(go.Scatter(x=eVm, y=(np.asarray(r["sp"]["Is"]) - np.asarray(r["Is_fit"]))[mask],
                                  mode="lines", name="ΔIs", line=dict(width=1, color="#2b6cb0")))
        figr.add_trace(go.Scatter(x=eVm, y=(np.asarray(r["sp"]["Ic"]) - np.asarray(r["Ic_fit"]))[mask],
                                  mode="lines", name="ΔIc", line=dict(width=1, color="#e0533d")))
        figr.update_layout(height=300, showlegend=True, margin=dict(l=40, r=20, t=20, b=40),
                           xaxis_title="eV")
        st.plotly_chart(figr, width="stretch")

    with st.expander("🔍 全局最优验证（χ²(d) 剖面扫描）"):
        render_profile_scan(lf)


def render_profile_scan(lf):
    r = lf["result"]
    fitted_layers = sorted(k[:-len(".thickness")] for k in r["thickness_err_nm"])
    if not fitted_layers:
        st.caption("当前堆叠没有拟合厚度的层，无需扫描")
        return
    layer = st.selectbox("扫描层", fitted_layers, key="prof_layer")
    prof = lf.setdefault("profile", {}).get(layer)
    c1, c2 = st.columns([1, 3])
    if c1.button("开始扫描", icon=":material/query_stats:", key="prof_btn", type="primary"):
        with st.spinner(f"χ²(d) 剖面扫描中（35 点，双起点）…"):
            prof = profile_thickness(
                lf["file"], lf["stack"], layer,
                eV_min=lf["ev_min"], eV_max=lf["ev_max"], clean=lf["clean"],
                params_init={k: v for k, (v, e) in r["params"].items()})
        lf.setdefault("profile", {})[layer] = prof
    if prof is None:
        c2.caption("固定 d 网格逐点拟合其余参数 → mse(d) 曲线；单谷且谷底=拟合值即全局最优强证据")
        return
    dg = np.array(prof["d_grid"])
    mse = np.array(prof["mse"])
    d_fit = float(r["thickness_nm"][prof["layer"]])
    figp = go.Figure()
    figp.add_trace(go.Scatter(x=dg, y=mse, mode="lines+markers", name="mse(d)",
                              line=dict(width=1.8, color="#2b6cb0"), marker=dict(size=5)))
    figp.add_vline(x=d_fit, line_dash="dot", line_color="#e0533d",
                   annotation_text=f"拟合 d = {d_fit:.1f} nm", annotation_position="top")
    figp.update_layout(height=300, showlegend=False, margin=dict(l=40, r=20, t=20, b=40),
                       xaxis_title="d (nm)", yaxis_title="mse (Is/Ic)")
    st.plotly_chart(figp, width="stretch")

    i = int(np.nanargmin(mse))
    step = float(np.median(np.diff(dg)))
    interior = 0 < i < len(dg) - 1
    match = abs(dg[i] - d_fit) <= step
    mins = [j for j in range(1, len(mse) - 1) if mse[j] < mse[j - 1] and mse[j] < mse[j + 1]]
    secondary = [float(dg[j]) for j in mins if j != i and mse[j] < 2 * mse[i]]
    edge_ok = bool(np.nanmax(mse[[0, -1]]) > mse[i] * 1.02)
    if interior and match and not secondary and edge_ok:
        st.success(f"✓ 单一谷：谷底 d = {dg[i]:.1f} nm 与拟合值一致（步长 {step:.1f} nm，"
                   f"范围 {dg[0]:.0f}–{dg[-1]:.0f} nm，无次级谷）—— 全局最优有强证据")
    else:
        parts = []
        if not interior:
            parts.append(f"⚠ 谷底落在网格边缘（d={dg[i]:.1f}），建议扩大扫描范围")
        if not match:
            parts.append(f"⚠ 谷底 d={dg[i]:.1f} 与拟合值 {d_fit:.1f} 不一致")
        if secondary:
            parts.append(f"⚠ 存在次级谷 @ d={[f'{x:.0f}' for x in secondary]} nm")
        st.warning("；".join(parts))




def render_layer_builder():
    if st.session_state.get("mat_added"):
        st.info(f"已从材料库插入 `{st.session_state.mat_added}`（nk 固定，厚度初值=标定值，默认参与拟合）；"
                "点击其方框可编辑")
        del st.session_state["mat_added"]
    st.subheader("拟合模型")
    st.caption("自上而下 = 空气 → 膜层 → c-Si 衬底；点击方框编辑该层")
    lcol, rcol = st.columns([1, 3.2], vertical_alignment="top")
    with lcol:
        st.button("新增膜层", icon=":material/add:", type="primary",
                  on_click=_open_add, key="btn_add", width="stretch")
        st.caption(f"FFT 厚度初值建议\n≈ **{st.session_state.get('fft_guess') or 0:.0f} nm**")
    layers = st.session_state.layers
    if st.session_state.get("rough_stripped"):
        st.toast(f"层「{st.session_state.rough_stripped}」不再是顶层，已摘除其粗糙层（v1 仅顶层）")
        del st.session_state["rough_stripped"]
    with rcol:
        for i, lay in enumerate(layers):
            has_rough = bool(lay.get("rough"))
            show_plus = i == 0 and not has_rough
            pair = st.columns([3.2, 1.5]) if has_rough else None
            host_scope = pair[0] if has_rough else nullcontext()
            with host_scope:
                with st.container(border=True):
                    row = st.columns([4.2, 0.9, 0.9, 0.9] + ([0.9] if show_plus else []),
                                     vertical_alignment="center")
                    row[0].button(layer_summary(lay), key=f"box_{lay['id']}",
                                  on_click=_open_edit, args=(lay["id"],),
                                  icon=":material/edit:", width="stretch",
                                  help="点击编辑此层")
                    row[1].button(":material/arrow_upward:", key=f"up_{lay['id']}",
                                  on_click=_move_layer, args=(lay["id"], -1),
                                  disabled=i == 0, help="上移")
                    row[2].button(":material/arrow_downward:", key=f"dn_{lay['id']}",
                                  on_click=_move_layer, args=(lay["id"], 1),
                                  disabled=i == len(layers) - 1, help="下移")
                    row[3].button(":material/delete:", key=f"del_{lay['id']}",
                                  on_click=_del_layer, args=(lay["id"],), help="删除")
                    if show_plus:
                        row[4].button(":material/add:", key=f"plus_{lay['id']}",
                                      on_click=_add_rough, args=(lay["id"],),
                                      help="添加表面粗糙层（EMA 50% void + 本层材料）")
            if has_rough:
                rg = lay["rough"]
                with pair[1]:
                    with st.container(border=True):
                        rr = st.columns([3.4, 1], vertical_alignment="center")
                        rr[0].button(f"粗糙 EMA50% · d≈{rg['d']:.1f} nm · "
                                     f"{'●拟合' if rg['fit'] else '○固定'}",
                                     key=f"rbox_{lay['id']}",
                                     on_click=_open_rough_edit, args=(lay["id"],),
                                     icon=":material/grain:", width="stretch",
                                     help="点击编辑粗糙层")
                        rr[1].button(":material/delete:", key=f"rdel_{lay['id']}",
                                     on_click=_del_rough, args=(lay["id"],),
                                     help="删除粗糙层")
        with st.container(border=True):
            st.caption("c-Si 衬底 · 半无限 · 文献 n/k（Aspnes/Green/Salzberg）固定")


def render_fit_settings(spe_path):
    st.divider()
    st.subheader("拟合设置")
    c = st.columns([1, 1, 1, 1, 1], vertical_alignment="bottom")
    c[0].number_input("入射角 (°)", 30.0, 89.0, step=0.5, key="angle")
    c[1].number_input("E_min (eV)", 0.5, 6.4, step=0.05, key="ev_min")
    c[2].number_input("E_max (eV)", 0.6, 6.5, step=0.05, key="ev_max")
    c[3].checkbox("坏点净化", key="clean",
                  help="Is/Ic-Ψ/Δ 恒等式残差 >5e-3 的孤立仪器毛刺点自动剔除")
    return c[4].button("开始拟合", icon=":material/play_arrow:", type="primary",
                       width="stretch",
                       disabled=spe_path is None or not st.session_state.get("layers"),
                       help="无膜层时不可拟合" if not st.session_state.get("layers") else None)


def main():
    st.title("EllipsoDesk")
    spe_path = render_sidebar()
    if spe_path is None:
        st.info("← 在侧栏 Explorer 中浏览目录并点击 .spe 文件（或上传）开始")
        return

    meta, sps = parse_spe(spe_path)
    sp = sps[0]
    d_guess = fringe_thickness_guess(sp)
    st.session_state.fft_guess = d_guess
    if "layers" not in st.session_state:
        st.session_state.layers = [make_fit_layer("sio2", "cauchy", d_guess or 1000.0)]

    render_data_section(sp, meta, d_guess, spe_path)

    st.divider()
    render_layer_builder()

    run_fit = render_fit_settings(spe_path)

    if "last_fit" in st.session_state:
        render_fit_results(st.session_state.last_fit)

    if run_fit and st.session_state.layers:
        stack = stack_from_layers(st.session_state.layers, st.session_state.angle,
                                  st.session_state.ev_min, st.session_state.ev_max)
        try:
            with st.spinner("多起点拟合中…"):
                r = fit_stack(str(spe_path), stack, clean=st.session_state.clean)
            st.session_state.last_fit = {
                "result": r, "file": str(spe_path), "stack": stack,
                "ev_min": st.session_state.ev_min, "ev_max": st.session_state.ev_max,
                "clean": st.session_state.clean, "angle": st.session_state.angle,
            }
            st.rerun()
        except Exception as exc:
            st.error(f"拟合失败：{exc}")

    dlg = st.session_state.get("dialog")
    if dlg == "add":
        add_layer_dialog()
    elif isinstance(dlg, tuple) and dlg[0] == "edit":
        edit_layer_dialog(dlg[1])
    elif isinstance(dlg, tuple) and dlg[0] == "rough":
        rough_edit_dialog(dlg[1])
    elif dlg == "save_mat":
        save_material_dialog()
    elif isinstance(dlg, tuple) and dlg[0] == "rename":
        rename_dialog(dlg[1])
    elif isinstance(dlg, tuple) and dlg[0] == "delete":
        delete_dialog(dlg[1])
    elif isinstance(dlg, tuple) and dlg[0] == "mat_rename":
        mat_rename_dialog(dlg[1])
    elif isinstance(dlg, tuple) and dlg[0] == "mat_delete":
        mat_delete_dialog(dlg[1])
    elif isinstance(dlg, tuple) and dlg[0] == "mat_view":
        mat_view_dialog(dlg[1])


if __name__ == "__main__":
    main()
