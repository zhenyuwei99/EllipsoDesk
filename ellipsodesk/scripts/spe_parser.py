from pathlib import Path

import numpy as np

def parse_spe(path):
    path = Path(path)
    with open(path, encoding="latin-1") as f:
        lines = f.read().splitlines()
    # 标识只用文件名本身——不做任何基于命名规则的位置/芯片推断（分析软件不该解读业务命名）
    meta = {"file": str(path), "name": path.stem}
    spectra = []
    i = 0
    n_spec = None
    while i < len(lines):
        line = lines[i]
        if line.startswith("## NUMBER OF SPECTRUM:"):
            val = line.split(":", 1)[1].strip()
            if not val:
                j = i + 1
                while j < len(lines) and not lines[j].strip():
                    j += 1
                val = lines[j].strip()
            n_spec = int(val)
        elif line.startswith("# "):
            key, _, val = line[2:].partition(":")
            key = key.strip().upper()
            val = val.strip()
            if key == "SPECTRUM":
                if not val:
                    while i + 1 < len(lines) and not lines[i + 1].strip():
                        i += 1
                    i += 1
                    val = lines[i].strip()
                sp = {"index": int(val)}
                i += 1
                rows = []
                while i < len(lines) and not lines[i].startswith("# DATA:"):
                    ln = lines[i]
                    if ln.startswith("# "):
                        k2, _, v2 = ln[2:].partition(":")
                        v2 = v2.strip()
                        if not v2:
                            j = i + 1
                            while j < len(lines) and not lines[j].strip():
                                j += 1
                            if j < len(lines) and not lines[j].startswith("#"):
                                v2 = lines[j].strip()
                        sp[k2.strip().upper()] = v2
                    i += 1
                if i < len(lines) and lines[i].startswith("# DATA:"):
                    i += 1
                    while i < len(lines):
                        parts = lines[i].split()
                        if len(parts) == 8:
                            try:
                                rows.append([float(p) for p in parts])
                            except ValueError:
                                if rows:
                                    break
                        elif len(parts) > 0 and lines[i].strip() != "":
                            break
                        i += 1
                arr = np.array(rows)
                sp["eV"] = arr[:, 0]
                sp["Hv"] = arr[:, 1]
                sp["SlitWidth"] = arr[:, 2]
                sp["S0"] = arr[:, 3]
                sp["Psi"] = arr[:, 4]
                sp["Delta"] = arr[:, 5]
                sp["Is"] = arr[:, 6]
                sp["Ic"] = arr[:, 7]
                spectra.append(sp)
                continue
        i += 1
    meta["n_spectra_declared"] = n_spec
    meta["incidence_deg"] = float(spectra[0].get("INCIDENCE ANGLE", "70").split()[0]) if spectra else None
    return meta, spectra

def check_identity(sp, tol=5e-3):
    psi = np.deg2rad(sp["Psi"])
    dlt = np.deg2rad(sp["Delta"])
    is_calc = np.sin(2 * psi) * np.sin(dlt)
    ic_calc = np.sin(2 * psi) * np.cos(dlt)
    return is_calc, ic_calc

def quality_mask(sp, tol=5e-3):
    is_calc, ic_calc = check_identity(sp)
    good = (np.abs(is_calc - sp["Is"]) < tol) & (np.abs(ic_calc - sp["Ic"]) < tol)
    return good

def find_spe_files(root="data/RAW-DATA"):
    return sorted(Path(root).rglob("*.spe"))

if __name__ == "__main__":
    files = find_spe_files()
    print(f"{'file':<58} {'N':>5} {'E range':>14} {'ang':>5} {'rms':>9} {'nbad':>5}")
    for f in files:
        meta, sps = parse_spe(f)
        for sp in sps:
            n_pts = len(sp["eV"])
            declared = int(sp.get("NUMBER OF POINTS", n_pts))
            ic, isc = check_identity(sp)
            r = np.sqrt((ic - sp["Is"]) ** 2 + (isc - sp["Ic"]) ** 2)
            nbad = int(np.sum(~quality_mask(sp)))
            print(f"{f.name:<58} {n_pts:>5} "
                  f"{sp['eV'][0]:>6.2f}-{sp['eV'][-1]:<6.2f} {meta['incidence_deg']:>5.1f} "
                  f"{np.sqrt(np.mean(r**2)):>9.5f} {nbad:>5}{'  NPTS-MISMATCH' if n_pts != declared else ''}")
