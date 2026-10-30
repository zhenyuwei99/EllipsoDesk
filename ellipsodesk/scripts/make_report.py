import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from psi_display import unfold_psi

RESULTS = Path("results")   # CWD 相对输出基准（--out 可覆盖）；不再写进代码包
EV_NM = 1239.841984

def wrap360(a):
    import numpy as np
    return (np.asarray(a) % 360.0).tolist()

CSS = """
body{font-family:-apple-system,'PingFang SC','Helvetica Neue',sans-serif;margin:0;background:#f7f8fa;color:#1c2733}
.wrap{max-width:1080px;margin:0 auto;padding:28px 20px 60px}
h1{font-size:26px;margin:.2em 0 .3em}
h2{font-size:20px;margin:1.6em 0 .5em;border-left:4px solid #2b6cb0;padding-left:10px}
.card{background:#fff;border:1px solid #e3e8ee;border-radius:10px;padding:18px 20px;margin:14px 0}
table{border-collapse:collapse;width:100%;font-size:14px}
th{background:#2b6cb0;color:#fff;padding:7px 10px;text-align:left;font-weight:600}
td{border-bottom:1px solid #e8edf2;padding:6px 10px}
tr:nth-child(even) td{background:#f4f7fa}
.note{color:#5a6b7b;font-size:13px;line-height:1.7}
.mono{font-family:Menlo,Consolas,monospace;font-size:12.5px;background:#f4f7fa;border-radius:6px;padding:10px 12px;white-space:pre-wrap}
"""

def sample_fig(s):
    name = s.get("meta", {}).get("name") or Path(s["file"]).stem
    fig = make_subplots(rows=2, cols=2,
                        subplot_titles=("Ψ 谱 (实验展开·桥接 vs 拟合)", "Δ 谱 (实验 vs 拟合, 0–360°)",
                                        "拟合残差 (窗口内)", "SiO₂ 层 n(λ)/k(λ)"),
                        horizontal_spacing=0.09, vertical_spacing=0.16,
                        specs=[[{"secondary_y": False}, {"secondary_y": False}],
                               [{"secondary_y": False}, {"secondary_y": True}]])
    eV, mask = s["eV"], s["mask"]
    # 模型线用 2× 密度细网格（旧 results.json 无细网格键时回退采样点）
    eVf = s.get("eV_fine") or eV
    psif = s.get("Psi_fit_fine") or s["Psi_fit"]
    deltaf = s.get("Delta_fit_fine") or s["Delta_fit"]
    nkf = s.get("layer_nk_fine") or s["layer_nk"]
    psi_unfold, _n_bridges = unfold_psi(s["Psi"], s["Psi_fit"])
    fig.add_trace(go.Scatter(x=eV, y=psi_unfold, mode="markers", name="Ψ exp (展开)",
                             marker=dict(size=4, color="#2b6cb0"), opacity=0.75), row=1, col=1)
    fig.add_trace(go.Scatter(x=eVf, y=psif, mode="lines", name="Ψ fit",
                             line=dict(width=1.6, color="#e0533d")), row=1, col=1)
    fig.add_trace(go.Scatter(x=eV, y=wrap360(s["Delta"]), mode="markers", name="Δ exp",
                             marker=dict(size=4, color="#2b6cb0"), opacity=0.75), row=1, col=2)
    fig.add_trace(go.Scatter(x=eVf, y=wrap360(deltaf), mode="lines", name="Δ fit",
                             line=dict(width=1.6, color="#e0533d")), row=1, col=2)
    eVm = [e for e, mk in zip(eV, mask) if mk]
    r_is = [a - b for a, b, mk in zip(s["Is"], s["Is_fit"], mask) if mk]
    r_ic = [a - b for a, b, mk in zip(s["Ic"], s["Ic_fit"], mask) if mk]
    fig.add_trace(go.Scatter(x=eVm, y=r_is, mode="lines", name="ΔIs", line=dict(width=1.2, color="#2b6cb0")), row=2, col=1)
    fig.add_trace(go.Scatter(x=eVm, y=r_ic, mode="lines", name="ΔIc", line=dict(width=1.2, color="#e0533d")), row=2, col=1)
    lam = [EV_NM / e for e in eVf]
    fig.add_trace(go.Scatter(x=lam, y=nkf["sio2"]["n"], mode="lines", name="n",
                             line=dict(width=2, color="#2b6cb0")), row=2, col=2)
    fig.add_trace(go.Scatter(x=lam, y=nkf["sio2"]["k"], mode="lines", name="k",
                             line=dict(width=2, color="#e0533d", dash="dash")),
                  row=2, col=2, secondary_y=True)
    su = s["summary"]
    sc = s.get("seed_consistency") or {}
    seed_txt = f"　起点一致 {sc.get('n')}/{sc.get('total')}" if sc.get("total") else ""
    fig.update_layout(
        height=640, showlegend=False, margin=dict(l=50, r=50, t=60, b=45),
        title=dict(text=(f"{name}　d = {su['d_nm']} ± {su['d_err_nm']} nm　"
                         f"n₅₈₉ = {su['n_589']}　χ²ᵣ = {su['chi2_red']}　rms = {su['rms']}{seed_txt}"),
                   font=dict(size=14)))
    fig.update_xaxes(title_text="光子能量 (eV)", row=1, col=1)
    fig.update_xaxes(title_text="光子能量 (eV)", row=1, col=2)
    fig.update_xaxes(title_text="光子能量 (eV)", row=2, col=1)
    fig.update_xaxes(title_text="波长 (nm)", row=2, col=2)
    fig.update_yaxes(title_text="Ψ (°)", row=1, col=1)
    fig.update_yaxes(title_text="Δ (°)", range=[-5, 365], row=1, col=2)
    fig.update_yaxes(title_text="残差", row=2, col=1)
    fig.update_yaxes(title_text="n", range=[1.35, 1.75], row=2, col=2)
    fig.update_yaxes(title_text="k", showgrid=False, row=2, col=2, secondary_y=True)
    return fig.to_html(full_html=False, include_plotlyjs=False)

def main(out_dir=None):
    results = Path(out_dir) if out_dir else RESULTS
    doc = json.loads((results / "results.json").read_text(encoding="utf-8"))
    samples = doc["samples"]
    stack = doc["stack"]

    html = [f"<!DOCTYPE html><html><head><meta charset='utf-8'>"
            f"<title>椭偏拟合报告 — 热氧化 SiO₂</title><style>{CSS}</style></head><body><div class='wrap'>"]
    html.append(f"<h1>热氧化 SiO₂ 晶圆椭偏拟合报告</h1>")
    html.append(f"<div class='note'>生成时间 {doc['generated']}　|　堆叠模型 <b>{stack['name']}</b> "
                f"(air / SiO₂ / c-Si, {stack['angle_deg']}°)　|　拟合窗口 {stack['eV_min']}–{stack['eV_max']} eV "
                f"（Is/Ic 最小二乘）</div>")

    rows = "".join(
        f"<tr><td>{s['summary']['d_nm']}</td><td>{s['summary']['d_err_nm']}</td>"
        f"<td>{s['summary']['n_589']}</td><td>{s['summary']['chi2_red']}</td>"
        f"<td>{s['summary']['rms']}</td><td>{s['summary'].get('seed_ok') or '-'}</td>"
        f"<td>{s['summary']['config']}</td><td class='mono' style='font-size:11px'>{s['file'].split('/')[-1]}</td></tr>"
        for s in samples)
    html.append("<h2>厚度汇总</h2><div class='card'><table>"
                "<tr><th>厚度 (nm)</th><th>±err (nm)</th>"
                "<th>n @589nm</th><th>χ²ᵣ</th><th>rms</th><th>起点一致</th><th>采集配置</th><th>文件</th></tr>"
                + rows + "</table></div>")

    fig = go.Figure()
    for s in samples:
        lbl = s.get("meta", {}).get("name") or Path(s["file"]).stem
        fig.add_trace(go.Bar(x=[lbl], y=[s["summary"]["d_nm"]],
                             error_y=dict(type="data", array=[s["summary"]["d_err_nm"]], visible=True),
                             name=lbl, marker_color="#2b6cb0", opacity=0.85,
                             text=[f"{s['summary']['d_nm']}"], textposition="outside"))
    fig.update_layout(height=420, showlegend=False, margin=dict(l=50, r=20, t=30, b=60),
                      yaxis_title="SiO₂ 厚度 (nm)")
    html.append("<h2>各测点厚度对比</h2><div class='card'>"
                + fig.to_html(full_html=False, include_plotlyjs="inline") + "</div>")

    html.append("<h2>各测点拟合详情</h2>")
    for s in samples:
        html.append(f"<div class='card'>{sample_fig(s)}</div>")

    html.append("<h2>方法说明</h2><div class='card note'>"
                "<p><b>全流程自研</b>：.spe 解析（latin-1）→ 传输矩阵法计算 70° 反射椭偏 ρ=r<sub>p</sub>/r<sub>s</sub> → "
                "在 Is=sin2ΨsinΔ / Ic=sin2ΨcosΔ 上 scipy 最小二乘（多起点），不依赖 DeltaPsi2。</p>"
                "<p><b>Ψ 展开显示</b>：仪器由 Is²+Ic²=sin²(2Ψ) 反解 Ψ，而 sin(2Ψ) 对 Ψ 与 90°−Ψ 简并，"
                "故报告的 Ψ 折叠于 [0°,45°]，真实 &gt;45° 的干涉尖峰被镜像到低区且物理上不可直接观测。"
                "图中实验 Ψ 按拟合模型的 45° 穿越分段翻转，穿越处 ±3 点线性桥接（桥接段非实测）；"
                "实测折叠峰只到 38–44°（振幅压制系统误差），不桥接则每次穿越出现 ~10–25° 假断点。"
                "展开仅影响 Ψ 显示，拟合目标 Is/Ic 在两分支下严格相同，结果不受影响。</p>"
                "<p><b>全局最优性</b>：拟合为多起点局部优化（TRF），不具全局最优数学保证。"
                "汇总表「起点一致」列 = 收敛到最优解的起点数/成功起点总数（±0.5 nm）；"
                "χ²(d) 剖面扫描（固定 d 网格逐点拟合其余参数）可进一步验证谷形唯一性"
                "（工作台「全局最优验证」面板，实测本批数据在 ±15% 范围内单谷且谷底=拟合值）。</p>"
                "<p><b>高能区（&gt;4 eV）残差较大</b>：Is/Ic 残差 rms 从低能区 ~0.026 增至 5.0–5.5 eV 的 ~0.13；"
                "数据在 4.5–5.5 eV 仍保有 ±0.2 的条纹振幅，而任何 (n,d) 的物理模型只能产生 ±0.05–0.08 —— "
                "属仪器深紫外（225–275 nm）标定系统误差（灯源强度坍缩、调制器/检偏器标定漂移、光栅偏振响应），"
                "DeltaPsi2 拟合同批数据 χ²≈18 同量级可佐证；Cauchy 外推与 c-Si 文献表在 E₂ 带间跃迁（~4.3 eV）"
                "附近的近似亦集中于此区间。窗口截断实验显示 d 随窗口选择漂移约 ±9 nm（单角度 n–d 相关的体现），"
                "多角度数据方可解耦。</p>"
                "<p><b>色散模型</b>：SiO₂ 用 Cauchy (n = A + B/λ² + C/λ⁴, k=0)；"
                "c-Si 衬底常数内置 Aspnes-Studna 1983 (1.5–6.0 eV) + Green 2008 + Salzberg-Villa 1957 文献表。</p>"
                "<p><b>拟合窗口 1.2–5.5 eV 的原因</b>：E &lt; 1.12 eV 时 Si 衬底透明，半无限衬底近似失效；"
                "E &gt; 5.5 eV 信噪与能量标定变差。全谱均绘出以便检查。</p>"
                "<p><b>已知限制</b>：单角度 70° 拟合中 n 与 d 存在相关性，n589 结果略低于热氧化 SiO₂ 典型值 1.458–1.462；"
                "残差水平 (rms ≈ 0.08) 与 DeltaPsi2 自身拟合水平相当（其 χ²≈18, σ≈0.017），"
                "为仪器/样品侧系统误差（厚膜条纹对光斑内厚度不均与调制器标定敏感）。</p>"
                "<p><b>自动数据清洗</b>：Is/Ic-Ψ/Δ 恒等式校验剔除孤立仪器毛刺点（每谱 ≤1 点，~1.43 eV）。</p>"
                "</div>")

    html.append("</div></body></html>")
    out = results / "report.html"
    out.write_text("".join(html), encoding="utf-8")
    print(f"wrote {out.resolve()} ({out.stat().st_size/1e6:.2f} MB)")

if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(
        description="由 ./results/results.json 生成单文件交互 HTML 报告")
    ap.add_argument("--out", default=None,
                    help="结果目录（默认 ./results，CWD 相对；report.html 也写在此）")
    a = ap.parse_args()
    main(a.out)
