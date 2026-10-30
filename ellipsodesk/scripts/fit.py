import numpy as np
from scipy.optimize import least_squares

from dispersion import MODELS
from spe_parser import parse_spe, quality_mask
from tmm import model_is_ic, psi_delta

FIT_EV_MIN = 1.2
FIT_EV_MAX = 5.5

def fringe_thickness_guess(sp, n_guess=1.46, angle_deg=70.0):
    from numpy.fft import rfft, rfftfreq
    z = sp["Delta"] - np.median(sp["Delta"])
    z = z - np.mean(z)
    f = rfftfreq(len(z), d=float(np.median(np.diff(sp["eV"]))))
    spec = np.abs(rfft(z))
    k = int(np.argmax(spec[1:])) + 1
    span = float(sp["eV"][-1] - sp["eV"][0])
    ncyc = f[k] * span
    q = np.sqrt(n_guess**2 - np.sin(np.deg2rad(angle_deg)) ** 2)
    lam1 = 1239.841984 / sp["eV"][0]
    lam2 = 1239.841984 / sp["eV"][-1]
    return ncyc / (2 * q * abs(1 / lam2 - 1 / lam1))

def fit_single_sio2(spe_path, model="cauchy", eV_min=FIT_EV_MIN, eV_max=FIT_EV_MAX,
                    angle_deg=70.0, extra_seeds=None):
    meta, sps = parse_spe(spe_path)
    sp = sps[0]
    m = quality_mask(sp) & (sp["eV"] >= eV_min) & (sp["eV"] <= eV_max)
    x = sp["eV"][m]
    ise, ice = sp["Is"][m], sp["Ic"][m]
    spec = MODELS[model]
    pnames = spec["params"]
    b_lo = np.array([20.0] + [spec["bounds"][p][0] for p in pnames])
    b_hi = np.array([4000.0] + [spec["bounds"][p][1] for p in pnames])
    init = np.array([spec["init"][p] for p in pnames])

    def residual(p):
        n_layer = spec["fn"](x, *p[1:])
        ism, icm = model_is_ic(x, [n_layer], [p[0]], angle_deg=angle_deg)
        return np.concatenate([ism - ise, icm - ice])

    d0 = fringe_thickness_guess(sp)
    seeds = list(np.geomspace(max(30, d0 * 0.6), d0 * 1.6, 10)) + [d0 * 0.85, d0, d0 * 1.15]
    seeds += list(np.geomspace(40, 2500, 12))
    if extra_seeds:
        seeds += list(extra_seeds)
    x0_list = [np.clip(np.array([s] + list(init)), b_lo + 1e-12, b_hi - 1e-12) for s in seeds]
    seed_pairs = []
    for x0 in x0_list:
        try:
            r = least_squares(residual, x0, bounds=(b_lo, b_hi), method="trf",
                              x_scale="jac", max_nfev=3000)
            seed_pairs.append((x0, r))
        except Exception:
            continue
    best = min(seed_pairs, key=lambda pr: pr[1].cost)[1]
    best = least_squares(residual, best.x, bounds=(b_lo, b_hi), method="trf", x_scale="jac",
                         xtol=1e-14, ftol=1e-14, gtol=1e-14, max_nfev=8000)
    seeds_info = [{"seed_d": float(s[0]), "d": float(r.x[0]),
                   "mse": float(2 * r.cost / r.fun.size), "ok": bool(r.success)}
                  for s, r in seed_pairs]
    best_d = float(best.x[0])
    seed_consistency = {
        "n": sum(1 for si in seeds_info if si["ok"] and abs(si["d"] - best_d) <= 0.5),
        "total": len(seeds_info), "failed": int(len(x0_list) - len(seed_pairs)),
        "tol_nm": 0.5, "best_d": {"thickness": best_d},
    }
    dof = residual(best.x).size - len(best.x)
    s2 = 2 * best.cost / dof if dof > 0 else np.nan
    try:
        perr = np.sqrt(np.clip(np.diag(s2 * np.linalg.inv(best.jac.T @ best.jac)), 0, None))
    except np.linalg.LinAlgError:
        perr = np.full(len(best.x), np.nan)

    eV_all = sp["eV"]
    n_full = spec["fn"](eV_all, *best.x[1:])
    is_fit, ic_fit = model_is_ic(eV_all, [n_full], [best.x[0]], angle_deg=angle_deg)
    psi_fit, delta_fit = psi_delta(eV_all, [n_full], [best.x[0]], angle_deg=angle_deg)
    fitmask = m
    return {
        "meta": meta,
        "sp": sp,
        "mask": fitmask,
        "model": model,
        "x": best.x,
        "perr": perr,
        "chi2_red": s2,
        "rms": np.sqrt(2 * best.cost / residual(best.x).size),
        "d_nm": float(best.x[0]),
        "d_err": float(perr[0]),
        "model_params": {p: (float(v), float(e)) for p, v, e in zip(pnames, best.x[1:], perr[1:])},
        "d_guess_fft": float(d0),
        "Is_fit": is_fit,
        "Ic_fit": ic_fit,
        "Psi_fit": psi_fit,
        "Delta_fit": delta_fit,
        "n_layer": n_full,
        "n_used": int(fitmask.sum()),
        "eV_window": (eV_min, eV_max),
        "seeds_info": seeds_info,
        "seed_consistency": seed_consistency,
    }

if __name__ == "__main__":
    import sys
    spe = sys.argv[1] if len(sys.argv) > 1 else "data/RAW-DATA/0.6 - 6.5 eV 0.02/SIO2-LEFT-CHIP-RIGHT-SIDE.IGO-2.14h 46mn 22s.spe"
    r = fit_single_sio2(spe)
    m = r["meta"]
    print(f"{Path(spe).name}  (FFT d-guess {r['d_guess_fft']:.0f} nm)")
    print(f"d = {r['d_nm']:.2f} +- {r['d_err']:.2f} nm   chi2_red = {r['chi2_red']:.4g}  rms = {r['rms']:.4g}")
    for p, (v, e) in r["model_params"].items():
        print(f"  {p} = {v:.5g} +- {e:.3g}")
    n589 = np.interp(1239.841984 / 589.0, r["sp"]["eV"][::-1], r["n_layer"].real[::-1])
    print(f"  n(589nm) = {n589:.4f}")
