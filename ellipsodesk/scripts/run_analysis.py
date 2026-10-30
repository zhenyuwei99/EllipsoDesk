import json
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from spe_parser import find_spe_files
from stack_fit import fit_stack, load_stack

RESULTS = Path("results")   # CWD 相对输出基准（--out 可覆盖）；不再写进代码包

def main(stack_yaml=None, spe_files=None, out_dir=None):
    stack_yaml = stack_yaml or Path(__file__).resolve().parent.parent / "stacks" / "sio2_on_si.yaml"
    stack = load_stack(stack_yaml)
    files = spe_files or find_spe_files()
    rows, payload = [], []
    for f in files:
        r = fit_stack(f, stack)
        meta, sp = r["meta"], r["sp"]
        lay = list(r["thickness_nm"].keys())[0]
        nk = r["layer_nk"][lay]["nk"]
        n589 = float(np.interp(1239.841984 / 589.0, sp["eV"][::-1], np.real(nk)[::-1]))
        k589 = float(np.interp(1239.841984 / 589.0, sp["eV"][::-1], -np.imag(nk)[::-1]))
        cfg = Path(f).parent.name
        sc = r.get("seed_consistency") or {}
        rows.append({
            "file": Path(f).name,
            "config": cfg,
            "stack": r["stack_name"],
            "d_nm": round(r["thickness_nm"][lay], 2),
            "d_err_nm": round(r["thickness_err_nm"].get(f"{lay}.thickness", np.nan), 2),
            "n_589": round(n589, 4),
            "k_589": round(k589, 4),
            "chi2_red": round(r["chi2_red"], 5),
            "rms": round(r["rms"], 5),
            "n_points_fit": r["n_used"],
            "seed_ok": f"{sc.get('n')}/{sc.get('total')}" if sc else "",
        })
        params_flat = {k: v for k, (v, e) in r["params"].items()}
        errs_flat = {k + "_err": e for k, (v, e) in r["params"].items()}
        payload.append({
            "file": str(f),
            "meta": {k: v for k, v in meta.items() if k != "sp"},
            "summary": rows[-1],
            "params": {**params_flat, **errs_flat},
            "eV": sp["eV"].tolist(),
            "Psi": sp["Psi"].tolist(),
            "Delta": sp["Delta"].tolist(),
            "Is": sp["Is"].tolist(),
            "Ic": sp["Ic"].tolist(),
            "mask": r["mask"].tolist(),
            "Psi_fit": r["Psi_fit"].tolist(),
            "Delta_fit": r["Delta_fit"].tolist(),
            "Is_fit": r["Is_fit"].tolist(),
            "Ic_fit": r["Ic_fit"].tolist(),
            "eV_fine": r["eV_fine"].tolist(),
            "Psi_fit_fine": r["Psi_fit_fine"].tolist(),
            "Delta_fit_fine": r["Delta_fit_fine"].tolist(),
            "layer_nk": {name: {"source": v["source"],
                                "n": np.real(v["nk"]).tolist(),
                                "k": (-np.imag(v["nk"])).tolist()}
                         for name, v in r["layer_nk"].items()},
            "layer_nk_fine": {name: {"source": v["source"],
                                     "n": np.real(v["nk"]).tolist(),
                                     "k": (-np.imag(v["nk"])).tolist()}
                              for name, v in r["layer_nk_fine"].items()},
            "thickness_nm": r["thickness_nm"],
            "seed_consistency": r.get("seed_consistency"),
        })
        print(f"{Path(f).name:<40} d={rows[-1]['d_nm']:8.2f} nm "
              f"+- {rows[-1]['d_err_nm']:5.2f}  n589={n589:.4f}  rms={r['rms']:.4f}")
    results = Path(out_dir) if out_dir else RESULTS
    results.mkdir(parents=True, exist_ok=True)
    df = pd.DataFrame(rows)
    df.to_csv(results / "summary.csv", index=False, encoding="utf-8")
    doc = {
        "generated": datetime.now().isoformat(timespec="seconds"),
        "stack": stack,
        "stack_file": str(stack_yaml),
        "samples": payload,
    }
    (results / "results.json").write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    print(f"\nwrote {(results / 'summary.csv').resolve()} and {(results / 'results.json').resolve()}")

if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(
        description="批量拟合 .spe → ./results/summary.csv + results.json")
    ap.add_argument("--stack", default=None,
                    help="堆叠定义 YAML（默认代码包内 stacks/sio2_on_si.yaml）")
    ap.add_argument("--spe", nargs="*", default=None,
                    help="要拟合的 .spe 文件（默认 CWD 下 data/RAW-DATA 递归扫描）")
    ap.add_argument("--out", default=None,
                    help="输出目录（默认 ./results，CWD 相对）")
    a = ap.parse_args()
    main(a.stack, a.spe, a.out)
