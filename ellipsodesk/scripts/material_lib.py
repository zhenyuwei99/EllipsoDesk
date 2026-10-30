import json
import re
from datetime import datetime
from pathlib import Path

import numpy as np

from dispersion import MODELS

MATERIALS_DIR = Path(__file__).resolve().parent.parent / "materials"

def save_material(name, model, params, errs, eV_range, provenance):
    MATERIALS_DIR.mkdir(parents=True, exist_ok=True)
    path = MATERIALS_DIR / f"{name}.json"
    history = []
    if path.exists():
        try:
            old = json.loads(path.read_text(encoding="utf-8"))
            history = old.get("history", [])
            history.append({k: old[k] for k in ("model", "params", "fitted_thickness_nm", "date") if k in old})
        except Exception:
            pass
    doc = {
        "name": name,
        "model": model,
        "params": params,
        "param_errs": errs,
        "eV_range": list(eV_range),
        "fitted_thickness_nm": provenance.get("thickness_nm"),
        "thickness_err_nm": provenance.get("thickness_err_nm"),
        "provenance": provenance,
        "date": datetime.now().isoformat(timespec="seconds"),
        "history": history,
    }
    path.write_text(json.dumps(doc, indent=2, ensure_ascii=False), encoding="utf-8")
    return path

def load_material(name):
    path = MATERIALS_DIR / f"{name}.json"
    doc = json.loads(path.read_text(encoding="utf-8"))
    model = doc["model"]
    if model not in MODELS:
        raise KeyError(f"model {model} of material {name} not in dispersion.MODELS")
    fn = MODELS[model]["fn"]
    params = doc["params"]

    def nk(eV):
        return fn(eV, **params)

    nk.doc = doc
    return nk

def list_materials():
    if not MATERIALS_DIR.exists():
        return []
    return sorted(p.stem for p in MATERIALS_DIR.glob("*.json"))

def list_materials_meta():
    out = []
    if not MATERIALS_DIR.exists():
        return out
    for p in sorted(MATERIALS_DIR.glob("*.json")):
        try:
            doc = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        n589 = None
        model = doc.get("model")
        if model in MODELS:
            try:
                nk = MODELS[model]["fn"](np.array([1239.841984 / 589.0]), **doc.get("params", {}))
                n589 = float(np.real(nk)[0])
            except Exception:
                pass
        out.append({
            "name": doc.get("name", p.stem),
            "kind": doc.get("kind", "calibrated"),
            "model": model,
            "n589": n589,
            "thickness_nm": doc.get("fitted_thickness_nm"),
            "date": (doc.get("date") or "")[:10],
            "file": str(p),
        })
    return out


# ---- 内置基料（kind="base"）：文献典型初值，插入后参数全部参与拟合 ----
# 与标定材料（kind="calibrated"，参数=结果、插入后固定）的区别在 fit_app.make_base_layer。
BASE_MATERIALS = {
    "base-sio2": {"model": "cauchy",
                  "params": {"A": 1.45, "B": 0.004, "C": 0.0},
                  "note": "热氧化/LPCVD SiO₂（n589≈1.46）"},
    "base-si3n4": {"model": "cauchy",
                   "params": {"A": 1.98, "B": 0.015, "C": 0.001},
                   "note": "化学计量比 Si₃N₄（n589≈2.01）"},
    "base-sin-rich": {"model": "cauchy",
                      "params": {"A": 2.40, "B": 0.02, "C": 0.001},
                      "note": "富硅 SiN，透明区（n589≈2.43；k>0 请改用 TL）"},
    "base-a-si": {"model": "tauc_lorentz",
                  "params": {"A": 45.0, "E0": 3.6, "Gam": 2.5, "Eg": 1.7, "eps_inf": 1.5},
                  "note": "非晶硅（吸收膜， Eg≈1.7 eV， n@1.5eV≈3.7）"},
    "base-si-poly": {"model": "tauc_lorentz",
                     "params": {"A": 30.0, "E0": 3.6, "Gam": 1.0, "Eg": 1.2, "eps_inf": 1.5},
                     "note": "多晶硅层（n@1eV≈3.8；衬底 c-Si 无需基料）"},
}

def ensure_base_materials():
    """基料 JSON 缺失则（重新）生成——删除基料后下次启动自动恢复。"""
    MATERIALS_DIR.mkdir(parents=True, exist_ok=True)
    for name, spec in BASE_MATERIALS.items():
        p = MATERIALS_DIR / f"{name}.json"
        if p.exists():
            continue
        doc = {"name": name, "kind": "base", "model": spec["model"],
               "params": spec["params"], "eV_range": None,
               "provenance": f"内置基料（文献典型初值）：{spec['note']}",
               "date": datetime.now().isoformat(timespec="seconds")}
        p.write_text(json.dumps(doc, indent=2, ensure_ascii=False), encoding="utf-8")
    return [n for n in BASE_MATERIALS]
