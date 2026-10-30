import sys
from pathlib import Path

import numpy as np
import yaml
from scipy.optimize import least_squares

sys.path.insert(0, str(Path(__file__).resolve().parent))
from dispersion import MODELS, bruggeman
from fit import fringe_thickness_guess
from material_lib import load_material
from spe_parser import parse_spe, quality_mask
from tmm import model_is_ic, psi_delta

SEED_CONSIST_TOL_NM = 0.5

def load_stack(yaml_path):
    return yaml.safe_load(Path(yaml_path).read_text(encoding="utf-8"))


def _cfg_nk(lc, pd, eV):
    """层 cfg → 复折射率数组（material: 材料库固定 nk；model: 当前参数下的色散）。"""
    if lc["kind"] == "material":
        return lc["nk"](eV)
    pr = {k: pd.get(f"{lc['name']}.{k}", v) for k, v in lc["params"].items()}
    return lc["spec"]["fn"](eV, **pr)

def _prepare(spe_path, stack, eV_min=None, eV_max=None, clean=True):
    """把 stack dict 组装成数值优化问题（fit_stack 与 profile_thickness 共用）。"""
    meta, sps = parse_spe(spe_path)
    sp = sps[0]
    eV_min = eV_min if eV_min is not None else stack.get("eV_min", 1.2)
    eV_max = eV_max if eV_max is not None else stack.get("eV_max", 5.5)
    angle = stack.get("angle_deg", 70.0)
    m = (sp["eV"] >= eV_min) & (sp["eV"] <= eV_max)
    if clean:
        m = m & quality_mask(sp)
    x = sp["eV"][m]
    ise, ice = sp["Is"][m], sp["Ic"][m]

    layers = stack["layers"]
    pnames, x0, lo, hi, layers_cfg = [], [], [], [], []
    for lay in layers:
        lname = lay.get("name", f"L{len(layers_cfg)+1}")
        init = dict(lay.get("init", {}))
        fit = list(lay.get("fit", []))
        if "material" in lay and lay["material"]:
            nk = load_material(lay["material"])
            default_t = init.get("thickness", 100.0)
            if "thickness" in fit or fit == ["*"]:
                pnames.append(f"{lname}.thickness")
                x0.append(default_t)
                lo.append(1.0)
                hi.append(100000.0)
                layers_cfg.append({"name": lname, "kind": "material", "nk": nk,
                                   "fit_thickness": True, "fixed_t": default_t})
            else:
                layers_cfg.append({"name": lname, "kind": "material", "nk": nk,
                                   "fit_thickness": False, "fixed_t": default_t})
        else:
            model = lay.get("model", "cauchy")
            spec = MODELS[model]
            fit_all = fit == ["*"] or not fit
            for p in spec["params"]:
                if fit_all or p in fit:
                    pnames.append(f"{lname}.{p}")
                    x0.append(init.get(p, spec["init"][p]))
                    lo.append(spec["bounds"][p][0])
                    hi.append(spec["bounds"][p][1])
            if fit_all or "thickness" in fit:
                pnames.append(f"{lname}.thickness")
                x0.append(init.get("thickness", 100.0))
                lo.append(1.0)
                hi.append(100000.0)
                fit_th = True
            else:
                fit_th = False
            layers_cfg.append({"name": lname, "kind": "model", "spec": spec,
                               "params": {p: init.get(p, spec["init"][p]) for p in spec["params"]},
                               "fit_thickness": fit_th, "fixed_t": init.get("thickness", 100.0)})
        # 表面粗糙层（v1 仅允许顶层）：Bruggeman EMA(50% void + 宿主材料)，插在宿主之上。
        # 参数名 {层名}·粗糙.thickness 以复用 endswith(".thickness") 机制（误差/诊断/metric）。
        rough = lay.get("roughness")
        if isinstance(rough, dict):
            if len(layers_cfg) != 1:
                raise ValueError("粗糙层 v1 仅支持堆叠最顶层（界面 ⊕ 也只在顶层出现）")
            rname = f"{lname}·粗糙"
            rd = float(rough.get("thickness", 2.0))
            rfit = bool(rough.get("fit", True))
            if rfit:
                pnames.append(f"{rname}.thickness")
                x0.append(rd)
                lo.append(0.0)
                hi.append(10.0)
            layers_cfg.insert(0, {"name": rname, "kind": "roughness", "host_idx": 1,
                                  "fit_thickness": rfit, "fixed_t": rd, "f_void": 0.5})

    def assemble(pars, eV=x):
        pd = dict(zip(pnames, pars))
        ns, ds = [], []
        for lc in layers_cfg:
            if lc["kind"] == "roughness":
                nk_host = _cfg_nk(layers_cfg[lc["host_idx"]], pd, eV)
                ns.append(np.sqrt(bruggeman(1.0 + 0j, nk_host ** 2, lc["f_void"])))
            else:
                ns.append(_cfg_nk(lc, pd, eV))
            ds.append(pd.get(f"{lc['name']}.thickness", lc["fixed_t"]))
        return ns, ds

    def residual(pars):
        ns, ds = assemble(pars)
        ism, icm = model_is_ic(x, ns, ds, angle_deg=angle)
        return np.concatenate([ism - ise, icm - ice])

    return {"meta": meta, "sp": sp, "mask": m, "x": x, "angle": angle,
            "pnames": list(pnames), "x0": np.array(x0, float),
            "lo": np.array(lo, float), "hi": np.array(hi, float),
            "layers_cfg": layers_cfg, "residual": residual, "assemble": assemble,
            "eV_window": (eV_min, eV_max)}

def _seed_diagnostics(pnames, seed_pairs, best_x, n_attempted):
    """多起点一致性：多少个成功起点收敛到与最优解相同的厚度（±tol）。"""
    th_names = [n for n in pnames if n.endswith(".thickness")]
    best_th = {n: float(best_x[pnames.index(n)]) for n in th_names}
    seeds_info = []
    for s, r in seed_pairs:
        seeds_info.append({
            "seed": {n: float(s[pnames.index(n)]) for n in th_names},
            "d": {n: float(r.x[pnames.index(n)]) for n in th_names},
            "mse": float(2 * r.cost / r.fun.size),
            "ok": bool(r.success),
        })
    n_same = sum(1 for si in seeds_info
                 if si["ok"] and all(abs(si["d"][n] - best_th[n]) <= SEED_CONSIST_TOL_NM
                                     for n in th_names))
    return seeds_info, {"n": n_same, "total": len(seeds_info),
                        "failed": int(n_attempted - len(seed_pairs)),
                        "tol_nm": SEED_CONSIST_TOL_NM, "best_d": best_th}

def fit_stack(spe_path, stack, eV_min=None, eV_max=None, extra_d_seeds=None, clean=True):
    P = _prepare(spe_path, stack, eV_min, eV_max, clean)
    meta, sp, m, angle = P["meta"], P["sp"], P["mask"], P["angle"]
    pnames = P["pnames"]
    lo, hi, residual, assemble = P["lo"], P["hi"], P["residual"], P["assemble"]
    x0 = P["x0"]

    seeds = [x0]
    th_idx = [i for i, n in enumerate(pnames)
              if n.endswith(".thickness") and "·粗糙" not in n]
    rough_idx = [i for i, n in enumerate(pnames)
                 if n.endswith(".thickness") and "·粗糙" in n]
    d_guess = fringe_thickness_guess(sp)
    for i in th_idx:
        for f in (0.5, 0.75, 0.9, 1.1, 1.25, 1.5):
            s = x0.copy()
            s[i] = x0[i] * f if x0[i] > 50 else d_guess * f
            seeds.append(np.clip(s, lo + 1e-9, hi - 1e-9))
    for i in rough_idx:   # 粗糙层纳米级小范围，显式撒点（大厚度因子会被界 [0,10] 钳成同一点）
        for v in (0.5, 1.0, 3.0, 5.0):
            s = x0.copy()
            s[i] = v
            seeds.append(np.clip(s, lo + 1e-9, hi - 1e-9))
    if extra_d_seeds:
        for dg in extra_d_seeds:
            for i in th_idx:
                s = x0.copy()
                s[i] = dg
                seeds.append(np.clip(s, lo + 1e-9, hi - 1e-9))
    seed_pairs = []
    for s in seeds:
        try:
            r = least_squares(residual, np.clip(s, lo + 1e-12, hi - 1e-12), bounds=(lo, hi),
                              method="trf", x_scale="jac", max_nfev=3000)
            seed_pairs.append((s, r))
        except Exception:
            continue
    best = min(seed_pairs, key=lambda pr: pr[1].cost)[1]
    best = least_squares(residual, best.x, bounds=(lo, hi), method="trf", x_scale="jac",
                         xtol=1e-14, ftol=1e-14, gtol=1e-14, max_nfev=8000)
    dof = residual(best.x).size - len(best.x)
    s2 = 2 * best.cost / dof if dof > 0 else np.nan
    try:
        perr = np.sqrt(np.clip(np.diag(s2 * np.linalg.inv(best.jac.T @ best.jac)), 0, None))
    except np.linalg.LinAlgError:
        perr = np.full(len(best.x), np.nan)
    pd_best = {n: (float(v), float(e)) for n, v, e in zip(pnames, best.x, perr)}
    seeds_info, seed_consistency = _seed_diagnostics(pnames, seed_pairs, best.x, len(seeds))

    eV_all = sp["eV"]
    pd_all = dict(zip(pnames, best.x))
    ns_all, ds_all = [], []
    layer_nk_full = {}
    # 细网格（2× 采样密度）：模型线画图用，数据散点/残差仍在采样点上
    eV_fine = np.linspace(float(eV_all[0]), float(eV_all[-1]), 2 * len(eV_all) - 1)
    ns_fine, layer_nk_fine = [], {}
    for lc in P["layers_cfg"]:
        if lc["kind"] == "roughness":
            host = P["layers_cfg"][lc["host_idx"]]
            nkf = np.sqrt(bruggeman(1.0 + 0j, _cfg_nk(host, pd_all, eV_all) ** 2, lc["f_void"]))
            nkf_f = np.sqrt(bruggeman(1.0 + 0j, _cfg_nk(host, pd_all, eV_fine) ** 2, lc["f_void"]))
            ds_all.append(pd_all.get(f"{lc['name']}.thickness", lc["fixed_t"]))
            src = f"ema:{int(round(lc['f_void'] * 100))}%void+" + str(host["name"])
            layer_nk_full[lc["name"]] = {"source": src, "nk": nkf}
            layer_nk_fine[lc["name"]] = {"source": src, "nk": nkf_f}
        elif lc["kind"] == "material":
            nkf = lc["nk"](eV_all)
            nkf_f = lc["nk"](eV_fine)
            ds_all.append(pd_all.get(f"{lc['name']}.thickness", lc["fixed_t"]))
            layer_nk_full[lc["name"]] = {"source": "material:" + str(lc.get("_matname", lc["name"])), "nk": nkf}
            layer_nk_fine[lc["name"]] = {"source": layer_nk_full[lc["name"]]["source"], "nk": nkf_f}
        else:
            pr = {k: pd_all.get(f"{lc['name']}.{k}", v) for k, v in lc["params"].items()}
            nkf = lc["spec"]["fn"](eV_all, **pr)
            nkf_f = lc["spec"]["fn"](eV_fine, **pr)
            ds_all.append(pd_all.get(f"{lc['name']}.thickness", lc["fixed_t"]))
            layer_nk_full[lc["name"]] = {"source": "fit:" + lc["spec"]["fn"].__name__, "nk": nkf}
            layer_nk_fine[lc["name"]] = {"source": layer_nk_full[lc["name"]]["source"], "nk": nkf_f}
        ns_all.append(nkf)
        ns_fine.append(nkf_f)
    is_fit, ic_fit = model_is_ic(eV_all, ns_all, ds_all, angle_deg=angle)
    psi_fit, delta_fit = psi_delta(eV_all, ns_all, ds_all, angle_deg=angle)
    psi_fine, delta_fine = psi_delta(eV_fine, ns_fine, ds_all, angle_deg=angle)
    return {
        "meta": meta, "sp": sp, "mask": m,
        "stack_name": stack.get("name"),
        "params": pd_best,
        "thickness_nm": {lc["name"]: float(pd_all.get(f"{lc['name']}.thickness", lc["fixed_t"]))
                         for lc in P["layers_cfg"]},
        "thickness_err_nm": {n: pd_best[n][1] for n in pnames if n.endswith(".thickness")},
        "chi2_red": s2,
        "rms": float(np.sqrt(2 * best.cost / residual(best.x).size)),
        "Is_fit": is_fit, "Ic_fit": ic_fit,
        "Psi_fit": psi_fit, "Delta_fit": delta_fit,
        "eV_fine": eV_fine,
        "Psi_fit_fine": psi_fine, "Delta_fit_fine": delta_fine,
        "layer_nk": layer_nk_full,
        "layer_nk_fine": layer_nk_fine,
        "layer_ds": ds_all,
        "eV_window": P["eV_window"],
        "n_used": int(m.sum()),
        "seeds_info": seeds_info,
        "seed_consistency": seed_consistency,
    }

def profile_thickness(spe_path, stack, layer, d_range=None, n_points=35,
                      eV_min=None, eV_max=None, clean=True, params_init=None):
    """χ²(d) 剖面：d 固定在网格上逐点拟合其余参数（延续法热启动），验证谷形唯一性。"""
    P = _prepare(spe_path, stack, eV_min, eV_max, clean)
    pnames, lo, hi, residual = P["pnames"], P["lo"], P["hi"], P["residual"]
    key = f"{layer}.thickness"
    if key not in pnames:
        raise ValueError(f"层 {layer!r} 没有参与拟合的厚度参数（可拟合厚度层："
                         f"{[n[:-len('.thickness')] for n in pnames if n.endswith('.thickness')]}）")
    idx = pnames.index(key)
    x = P["x0"].copy()
    if params_init:
        for n, v in params_init.items():
            if n in pnames:
                x[pnames.index(n)] = float(v)
    x = np.clip(x, lo + 1e-12, hi - 1e-12)
    d0 = float(x[idx])
    if d_range is None:
        d_range = (d0 * 0.85, d0 * 1.15)
    d_grid = np.linspace(float(d_range[0]), float(d_range[1]), int(n_points))
    free = [i for i in range(len(pnames)) if i != idx]
    mses = []
    xcur = x.copy()          # 延续法热启动
    for d in d_grid:
        xcur[idx] = d

        def resid_sub(q, _d=float(d)):
            xx = np.empty(len(pnames))
            xx[idx] = _d
            xx[free] = q
            return residual(xx)

        if free:
            best_mse = float("nan")
            best_q = None
            for q0 in (x[free], xcur[free]):   # 双起点：全局最优参数 + 延续
                try:
                    r = least_squares(resid_sub, np.clip(q0, lo[free] + 1e-12, hi[free] - 1e-12),
                                      bounds=(lo[free], hi[free]),
                                      method="trf", x_scale="jac", max_nfev=2000)
                    mse = float(2 * r.cost / r.fun.size)
                    if best_q is None or mse < best_mse:
                        best_mse, best_q = mse, r.x
                except Exception:
                    continue
            if best_q is None:
                mses.append(float("nan"))
            else:
                xcur[free] = best_q
                mses.append(best_mse)
        else:
            mses.append(float(np.mean(residual(xcur) ** 2)))
    return {"layer": layer, "d_grid": d_grid.tolist(), "mse": mses,
            "d_range": [float(d_range[0]), float(d_range[1])],
            "n_points": int(n_points), "eV_window": P["eV_window"]}

if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--spe", required=True)
    ap.add_argument("--stack", default=str(Path(__file__).resolve().parent.parent / "stacks" / "sio2_on_si.yaml"))
    ap.add_argument("--profile", default=None, help="做 χ²(d) 剖面扫描的层名")
    ap.add_argument("--points", type=int, default=35)
    a = ap.parse_args()
    st = load_stack(a.stack)
    r = fit_stack(a.spe, st)
    print(f"{r['meta'].get('name', Path(a.spe).stem)}  stack={r['stack_name']}")
    for n, v in r["thickness_nm"].items():
        e = r["thickness_err_nm"].get(f"{n}.thickness", float("nan"))
        print(f"  {n}: {v:.2f} +- {e:.2f} nm")
    for n, (v, e) in r["params"].items():
        if not n.endswith(".thickness"):
            print(f"  {n} = {v:.5g} +- {e:.3g}")
    sc = r["seed_consistency"]
    print(f"  chi2_red={r['chi2_red']:.4g}  rms={r['rms']:.4g}  "
          f"多起点一致性={sc['n']}/{sc['total']} (failed={sc['failed']})")
    if a.profile:
        prof = profile_thickness(a.spe, st, a.profile, n_points=a.points,
                                 params_init={k: v for k, (v, e) in r["params"].items()})
        for d, mse in zip(prof["d_grid"], prof["mse"]):
            print(f"  d={d:9.2f}  mse={mse:.6f}")
