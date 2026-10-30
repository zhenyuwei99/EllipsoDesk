import numpy as np

EV_UM = 1.239841984

def cauchy(eV, A=1.45, B=0.004, C=0.0):
    w_um = EV_UM / np.asarray(eV, float)
    n = A + B / w_um**2 + C / w_um**4
    return n.astype(complex)

def sellmeier(eV, B1=1.099, C1=0.0095, B2=0.1099, C2=9.996):
    w_um = EV_UM / np.asarray(eV, float)
    n2 = 1.0 + B1 * w_um**2 / (w_um**2 - C1) + B2 * w_um**2 / (w_um**2 - C2)
    return np.sqrt(n2).astype(complex)

def bruggeman(eps_a, eps_b, f_a):
    eps_a = np.asarray(eps_a, complex)
    eps_b = np.asarray(eps_b, complex)
    a = 2.0
    b = -((3 * f_a - 1) * eps_a + (2 - 3 * f_a) * eps_b)
    c = -eps_a * eps_b
    disc = np.sqrt(b**2 - 4 * a * c + 0j)
    r1 = (-b + disc) / (2 * a)
    r2 = (-b - disc) / (2 * a)
    # N=n−ik 约定（ε=ε1−iε2）：无源介质要求 Im(ε)≤0。非无源根罚 1e6 后取 |Im| 最小者。
    # 教训：纯 min|Im| 在吸收宿主下会选错分支（EMA 出 k<0、n≈0 的非物理解）。
    pen1 = np.where(r1.imag > 0, 1e6 + np.abs(r1.imag), np.abs(r1.imag))
    pen2 = np.where(r2.imag > 0, 1e6 + np.abs(r2.imag), np.abs(r2.imag))
    return np.where(pen1 <= pen2, r1, r2)

_TL_EGRID = np.linspace(1e-3, 30.0, 12000)
_TL_DELTA = 0.015

def _tl_eps2(egrid, A, E0, Gam, Eg):
    e = np.asarray(egrid, float)
    eps2 = np.zeros_like(e)
    m = e > Eg
    if np.any(m):
        em, eg, a, e0, g = e[m], Eg, A, E0, Gam
        eps2[m] = a * e0 * g * (em - eg) ** 2 / ((em**2 - e0**2) ** 2 + g**2 * em**2)
    return eps2

def _kk_matrix(e_targets):
    E = _TL_EGRID
    w = np.empty_like(E)
    w[0] = (E[1] - E[0]) / 2
    w[-1] = (E[-1] - E[-2]) / 2
    w[1:-1] = (E[2:] - E[:-2]) / 2
    M = np.zeros((len(e_targets), len(E)))
    for i, et in enumerate(e_targets):
        ok = np.abs(E - et) > _TL_DELTA
        M[i, ok] = (2 / np.pi) * E[ok] / (E[ok] ** 2 - et**2) * w[ok]
    return M

_TL_KK_CACHE = {}

def tauc_lorentz(eV, A, E0, Gam, Eg, eps_inf=1.0):
    eV = np.atleast_1d(np.asarray(eV, float))
    key = (eV.shape, float(eV[0]) if eV.size else 0, float(eV[-1]) if eV.size else 0)
    if key not in _TL_KK_CACHE:
        _TL_KK_CACHE[key] = _kk_matrix(eV)
    eps2_fine = _tl_eps2(_TL_EGRID, A, E0, Gam, Eg)
    eps2_t = _tl_eps2(eV, A, E0, Gam, Eg)
    eps1 = eps_inf + _TL_KK_CACHE[key] @ eps2_fine
    eps = eps1 - 1j * eps2_t
    N = np.sqrt(eps)
    return np.where(N.real < 0, -N, N)

MODELS = {
    "cauchy": {
        "fn": cauchy,
        "params": ["A", "B", "C"],
        "init": {"A": 1.45, "B": 0.004, "C": 0.0},
        # A 上界 3.0：覆盖 Si₃N₄(≈2.0)/富硅SiN(2.2–2.9)/TiO₂(≈2.4) 等透明介质。
        # 实测教训：曾为 1.70 时拟合 SiN 数据会死贴边界给出静默错解（d 错 23%）。
        "bounds": {"A": (1.30, 3.00), "B": (0.0, 0.10), "C": (0.0, 0.02)},
        "absorbing": False,
    },
    "sellmeier": {
        "fn": sellmeier,
        "params": ["B1", "C1", "B2", "C2"],
        "init": {"B1": 1.10, "C1": 0.0095, "B2": 0.11, "C2": 9.996},
        "bounds": {"B1": (0.5, 8.0), "C1": (0.0, 0.1), "B2": (0.0, 2.0), "C2": (1.0, 100.0)},
        "absorbing": False,
    },
    "tauc_lorentz": {
        "fn": tauc_lorentz,
        "params": ["A", "E0", "Gam", "Eg", "eps_inf"],
        "init": {"A": 1.0, "E0": 7.0, "Gam": 1.2, "Eg": 2.7, "eps_inf": 1.9},
        # E0 下界 3.0：Si 类材料振子峰 3.4–4.2 eV（a-Si≈3.6、poly-Si/c-Si≈3.4–4.2）
        "bounds": {"A": (0.02, 200.0), "E0": (3.0, 12.0), "Gam": (0.3, 5.0),
                   "Eg": (1.0, 6.0), "eps_inf": (1.0, 4.0)},
        "absorbing": True,
    },
}

if __name__ == "__main__":
    n589 = cauchy(np.array([EV_UM / 0.589]), 1.45, 0.004, 0.0)
    print(f"Cauchy(1.45,0.004,0) @589nm: n = {n589[0].real:.4f} (expect ~1.4615)")
    print(f"Sellmeier @589nm: n = {sellmeier(np.array([EV_UM / 0.589]))[0].real:.4f} (Malitson fused silica ~1.458)")
    e = np.linspace(0.6, 6.0, 12)
    tl = tauc_lorentz(e, A=60, E0=6.5, Gam=1.2, Eg=2.7, eps_inf=1.9)
    print("TL(A=60,E0=6.5,G=1.2,Eg=2.7,einf=1.9):")
    for ei, ni in zip(e, tl):
        print(f"  {ei:4.2f} eV: n={ni.real:6.3f} k={-ni.imag:7.4f}")
    print("checks: k=0 below Eg; n rises toward E0")
