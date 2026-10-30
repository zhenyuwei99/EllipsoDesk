import argparse
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fit import fit_single_sio2
from material_lib import save_material

if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Calibrate a single-layer material from one .spe and save to library")
    ap.add_argument("--spe", required=True)
    ap.add_argument("--model", default="cauchy")
    ap.add_argument("--name", required=True, help="material name, e.g. sio2_thermal")
    ap.add_argument("--ev-min", type=float, default=1.2)
    ap.add_argument("--ev-max", type=float, default=5.5)
    args = ap.parse_args()

    r = fit_single_sio2(args.spe, model=args.model, eV_min=args.ev_min, eV_max=args.ev_max)
    params = {k: v for k, (v, e) in r["model_params"].items()}
    errs = {k: e for k, (v, e) in r["model_params"].items()}
    eV = r["sp"]["eV"][r["mask"]]
    prov = {
        "spe_file": str(Path(args.spe)),
        "thickness_nm": r["d_nm"],
        "thickness_err_nm": r["d_err"],
        "chi2_red": r["chi2_red"],
        "fit_window_eV": [args.ev_min, args.ev_max],
    }
    path = save_material(args.name, args.model, params, errs,
                         (float(eV.min()), float(eV.max())), prov)
    print(f"saved {path}")
    print(f"  d = {r['d_nm']:.2f} +- {r['d_err']:.2f} nm   chi2_red = {r['chi2_red']:.4g}")
    for k, v in params.items():
        print(f"  {k} = {v:.6g} +- {errs[k]:.3g}")
    n589 = np.interp(1239.841984 / 589.0, r["sp"]["eV"][::-1], r["n_layer"].real[::-1])
    print(f"  n(589nm) = {n589:.4f}")
