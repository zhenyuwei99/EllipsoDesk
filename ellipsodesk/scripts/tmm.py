import numpy as np

from si_data import si_nk

EV_NM = 1239.841984

def _q(N, s):
    q = np.sqrt(N**2 - s**2 + 0j)
    flip = (q.real < 0) | ((q.real == 0) & (q.imag > 0))
    q = np.where(flip, -q, q)
    return q

def stack_rho(eV, layer_ns, layer_ds, substrate_fn=si_nk, angle_deg=70.0):
    eV = np.atleast_1d(np.asarray(eV, float))
    Ns = np.asarray(substrate_fn(eV), complex)
    N0 = np.ones_like(Ns)
    Ns_all = [N0] + [np.asarray(n, complex) for n in layer_ns] + [Ns]
    ds = list(layer_ds)
    th0 = np.deg2rad(angle_deg)
    s = np.sin(th0)
    qs = [_q(N, s) for N in Ns_all]

    def r_s(i, j):
        return (qs[i] - qs[j]) / (qs[i] + qs[j])

    def r_p(i, j):
        Ni, Nj = Ns_all[i], Ns_all[j]
        return (Nj**2 * qs[i] - Ni**2 * qs[j]) / (Nj**2 * qs[i] + Ni**2 * qs[j])

    m = len(ds)
    Rs, Rp = r_s(m, m + 1), r_p(m, m + 1)
    lam_nm = EV_NM / eV
    for j in range(m, 0, -1):
        rsl, rpl = r_s(j - 1, j), r_p(j - 1, j)
        delta = 2 * np.pi * ds[j - 1] * qs[j] / lam_nm
        ph = np.exp(-2j * delta)
        Rs = (rsl + Rs * ph) / (1 + rsl * Rs * ph)
        Rp = (rpl + Rp * ph) / (1 + rpl * Rp * ph)
    return Rp / Rs

def psi_delta(eV, layer_ns, layer_ds, substrate_fn=si_nk, angle_deg=70.0):
    rho = stack_rho(eV, layer_ns, layer_ds, substrate_fn, angle_deg)
    psi = np.rad2deg(np.arctan(np.abs(rho)))
    delta = np.rad2deg(np.angle(rho))
    return psi, delta

def model_is_ic(eV, layer_ns, layer_ds, substrate_fn=si_nk, angle_deg=70.0):
    psi, delta = psi_delta(eV, layer_ns, layer_ds, substrate_fn, angle_deg)
    p = np.deg2rad(2 * psi)
    d = np.deg2rad(delta)
    return np.sin(p) * np.sin(d), np.sin(p) * np.cos(d)

if __name__ == "__main__":
    e = np.array([1239.841984 / 632.8])
    psi, delta = psi_delta(e, [], [])
    print(f"bare Si 632.8nm 70deg: Psi={psi[0]:.3f} Delta={delta[0]:.3f}  (hand calc 10.58 / 178.7)")

    eV = np.arange(0.6, 6.0001, 0.02)
    n_sio2 = np.full(eV.shape, 1.46 + 0j)
    psi2, delta2 = psi_delta(eV, [n_sio2], [1000.0])
    m = (eV > 1.5) & (eV < 4.5)
    from scipy.signal import find_peaks
    pk, _ = find_peaks(psi2[m])
    spacing = np.mean(np.diff(eV[m][pk])) if len(pk) > 1 else float("nan")
    period_pred = EV_NM / (2 * 1000.0 * np.sqrt(1.46**2 - np.sin(np.deg2rad(70)) ** 2))
    print(f"1000nm SiO2 Psi peaks near 2.5eV: n={len(pk)}, mean spacing={spacing:.3f} eV (theory ~{period_pred:.3f})")
    print(f"Psi range: {psi2.min():.2f}..{psi2.max():.2f} deg (interference oscillation present: {psi2.max()-psi2.min():.2f})")
