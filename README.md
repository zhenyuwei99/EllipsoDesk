# EllipsoDesk · 椭偏拟合工作台

[![build](https://github.com/zhenyuwei99/EllipsoDesk/actions/workflows/build.yml/badge.svg)](https://github.com/zhenyuwei99/EllipsoDesk/actions/workflows/build.yml)

EllipsoDesk 是一个光谱椭偏数据多层膜厚拟合工具：读取 Horiba 椭偏仪 .spe 原始光谱，基于传输矩阵法（TMM）与 Cauchy / Sellmeier / Tauc-Lorentz / Bruggeman EMA 色散模型拟合各层厚度与光学常数 n(λ)/k(λ)，配合多起点全局策略与可复用的材料库，提供 Streamlit 交互工作台与 CLI 批量管线，并可打包为 macOS / Windows 双平台桌面应用。

## 功能要点

- **.spe 原始解析**：ISO-8859-1 文本格式直读，Ψ/Δ/Is/Ic 八列数据；内置自检恒等式 `Is = sin(2Ψ)·sin(Δ)`、`Ic = sin(2Ψ)·cos(Δ)` 校验，自动剔除孤立毛刺点
- **多层堆叠拟合**：空气 / 任意层序列 / c-Si 衬底，顶层可挂表面粗糙层（EMA 50% void）；已知层 n/k 固定、未知层色散+厚度联合拟合，避开单角度 n–d 强相关
- **材料库标定复用**：单层标定片拟合结果存为 JSON 材料（含参数±误差与来源溯源），多层分析直接引用；内置 SiO₂/Si₃N₄/富硅 SiN/a-Si/poly-Si 文献基料
- **多起点全局策略**：Δ 条纹 FFT 估厚初值 + 对厚度撒种子的多起点优化；χ²(d) 剖面扫描验证全局最优性，拟合返回起点一致性诊断
- **Ψ 折叠展开显示**：相调制仪器将 Ψ 折叠于 [0°,45°]，展开算法按模型 45° 穿越翻转数据段并线性桥接，数据与模型可直接对比
- **HTML 交互报告**：批量管线输出单文件 plotly 报告（Ψ/Δ 叠加、残差、n/k 双轴、厚度汇总表）与汇总 CSV
- **双形态使用**：Streamlit 交互工作台（选数据→配层→拟合→存材料）与 CLI 批量管线（`run_analysis` → `make_report`）同一拟合引擎

## 下载使用

从 [GitHub Releases](https://github.com/zhenyuwei99/EllipsoDesk/releases) 下载对应平台安装包：

| 平台 | 文件 |
|---|---|
| macOS (Apple Silicon) | `EllipsoDesk-<版本>-arm64.dmg` |
| Windows x64 | `EllipsoDesk-<版本>-setup.exe` / `.zip` |

- **macOS**：应用未做签名公证，首次打开需右键点击 →「打开」，或在终端执行 `xattr -cr /Applications/EllipsoDesk.app` 后再启动
- **Windows**：SmartScreen 拦截时点击「更多信息」→「仍要运行」

## 开发者指南

### 环境要求

- Python ≥ 3.12，依赖：numpy、scipy、pandas、plotly、streamlit、pyyaml、platformdirs（**无需 lmfit**；绘图全部使用 plotly）
- c-Si 衬底光学常数内置文献表（Aspnes-Studna / Green 2008 / Salzberg-Villa），完全离线运行

### 启动 GUI 工作台

```bash
streamlit run ellipsodesk/gui/fit_app.py
```

### 批量管线

```bash
# 批量拟合 data/RAW-DATA 下全部 .spe → 当前目录 results/（summary.csv + results.json）
python ellipsodesk/scripts/run_analysis.py

# 由 results.json 生成单文件交互 HTML 报告（→ ./results/report.html）
python ellipsodesk/scripts/make_report.py

# 可选：--spe <文件…> 显式指定光谱，--out <目录> 覆盖输入/输出基准（均相对 CWD）
```

### 本地打包（桌面应用）

在干净的 Python 3.12 venv 中执行（依赖宽松下限见 `packaging/requirements-pack.txt`）：

```bash
python -m venv .venv-pack && source .venv-pack/bin/activate   # Windows: .venv-pack\Scripts\activate
pip install -r packaging/requirements-pack.txt
pyinstaller packaging/launcher.spec
# 产物：dist/EllipsoDesk/（onedir；launcher 自动选空闲端口拉起内嵌 Streamlit 并开 pywebview 窗口）
# macOS 的 .app/.dmg 与 Windows 安装包封装由 CI 负责
```

开发态也可直接 `python packaging/launcher.py` 拉起窗口（无 pywebview 时自动退化为系统浏览器）。

## 目录结构

```
EllipsoDesk/
├── ellipsodesk/
│   ├── scripts/        # 拟合引擎与 CLI（spe_parser / tmm / dispersion / fit / stack_fit / material_lib …）
│   ├── gui/            # Streamlit 工作台（fit_app.py + 文件浏览器组件）
│   └── stacks/         # 多层堆叠定义 YAML
├── packaging/          # 桌面应用打包：launcher.py（启动入口）/ launcher.spec（PyInstaller）/ requirements-pack.txt
├── .github/workflows/  # CI（build.yml：macOS arm64 / Windows x64 双平台构建 + 冒烟 + tag 发版）
├── AGENTS.md           # 项目契约：格式规格 / 拟合引擎 / GUI 契约 / 已知边界
├── README.md
└── .gitignore
```

> `data/`（原始 .spe 光谱）与 `analysis/`（本地材料库与历史结果）为本地工作数据，不入库。

## 许可

内部研究工具，暂未指定开源许可。
