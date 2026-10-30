"""Ψ 显示工具：仪器折叠 Ψ 的展开与桥接。

物理背景：仪器由 Is²+Ic²=sin²(2Ψ) 反解 Ψ，sin(2Ψ) 对 Ψ 与 90°−Ψ 简并，
报告值折叠于 [0°,45°]。展开 = 用拟合模型的 45° 穿越分段翻转数据；
实测折叠峰只到 38–44°（振幅压制），穿越处直接翻转会产生 90−2Ψ≈10–25° 假跳变，
故每个穿越处用 ±bridge_pts 点线性桥接（桥接段非实测，仅用于显示）。
拟合目标 Is/Ic 在两分支下严格相同，不受任何影响。
"""
import numpy as np


def unfold_psi(psi_exp, psi_model, bridge_pts=3):
    """按模型 45° 穿越翻转实验 Ψ，穿越处线性桥接。

    Returns
    -------
    unfolded : ndarray
    n_bridges : int  桥接的穿越处个数
    """
    psi_exp = np.asarray(psi_exp, float)
    psi_model = np.asarray(psi_model, float)
    n = len(psi_exp)
    flip = psi_model > 45.0
    u = np.where(flip, 90.0 - psi_exp, psi_exp)
    n_bridges = 0
    for i in np.where(flip[:-1] != flip[1:])[0]:
        a = max(0, int(i) - bridge_pts)
        b = min(n - 1, int(i) + 1 + bridge_pts)
        if b > a + 1:
            u[a:b + 1] = np.linspace(u[a], u[b], b - a + 1)
            n_bridges += 1
    return u, n_bridges
