# EllipsoDesk — 椭偏拟合工作台

## §0 项目定位
EllipsoDesk 是通用光谱椭偏拟合工具：读取 Horiba .spe 光谱 → 传输矩阵法多层拟合 → 材料库 / HTML 报告。三种形态共用同一拟合引擎：Streamlit 交互工作台（GUI）、CLI 批量管线、打包桌面应用。仓库：github.com/zhenyuwei99/EllipsoDesk。

## §1 目录结构
```
ellipsodesk/
  scripts/   拟合引擎 + CLI：spe_parser tmm dispersion fit stack_fit
             material_lib si_data psi_display run_analysis make_report calibrate_single
  gui/       fit_app.py + file_explorer.py（CCv2 双面板浏览器）
  stacks/    堆叠定义 YAML（如 sio2_on_si.yaml）
packaging/         桌面打包三件套：launcher.py（入口）/ launcher.spec（PyInstaller）/ requirements-pack.txt
.github/workflows/ CI build.yml（macOS arm64 / Windows x64 双平台）
```
`data/`（原始 spe）与 `analysis/`（materials/、results/ 本地材料与历史结果）不入库。材料库自阶段 2 落 platformdirs 用户数据目录（macOS `~/Library/Application Support/EllipsoDesk/materials`、Windows `%APPDATA%\EllipsoDesk\materials`，单一真源 `material_lib.MATERIALS_DIR`）；代码包内 `ellipsodesk/materials/` 仅作一次性迁移源（用户材料=非 base-*.json 首次导入时拷入新目录，base 基料由 ensure_base_materials() 自动重建）——原双路径断链（R1 BLOCKING-1）已消除。CLI 输出基准 = CWD 相对 `./results/`（--out 可覆盖，R1 ADV-2）；GUI Explorer 浏览根 = 用户主目录，上次目录持久化于用户数据目录 last_dir.json（R1 ADV-5）。

## §2 运行与分发
- 开发：GUI `streamlit run ellipsodesk/gui/fit_app.py`；批量 `python ellipsodesk/scripts/run_analysis.py` → `python ellipsodesk/scripts/make_report.py`（输入默认 CWD `./data/RAW-DATA`、输出 → CWD `./results/`，`--spe`/`--out` 可覆盖）
- 窗口启动（开发态也可）：`python packaging/launcher.py` —— 自动选空闲端口在 daemon 线程拉起 Streamlit，就绪后开 pywebview 窗口；无 pywebview 时退化为系统浏览器 + 阻塞陪跑。**macOS 上 pywebview 必须主线程 start()**（打包契约，实现细节见 packaging/launcher.py 头注）；环境变量 `ELLIPSODESK_NO_WINDOW=1` = 无头冒烟模式（CI 专用：不开窗口，stdout 打 `PORT {port}` 与 `SMOKE_OK …` 两行后 os._exit(0)）
- 桌面分发：packaging/ 三件套 + CI 构建；用户数据目录（platformdirs）：macOS `~/Library/Application Support/EllipsoDesk`、Windows `%APPDATA%\EllipsoDesk`
- CI（`.github/workflows/build.yml`）：push main / `workflow_dispatch` / push tag `v*` 触发，三 job——`test`（ubuntu py3.12：py_compile 全量 + 引擎模块 import + AppTest 无异常）→ `build`（macOS + Windows py3.12：PyInstaller 冻结 + 冒烟门槛 = 冻结产物 stdout 出现 `SMOKE_OK`（动态端口先打 `PORT {port}` 行供脚本解析）；产物 zip artifact 保留 14 天，失败时附 smoke.log）→ `release`（仅 tag `v*`：双平台 zip 挂 GitHub Release + 自动 release notes）
- 无签名首次运行：macOS 右键打开或 `xattr -cr EllipsoDesk.app`；Windows SmartScreen「仍要运行」

## §3 .spe 格式（解析契约）
- ISO-8859 文本 + CRLF：必须 `open(..., encoding='latin-1')`（° 字符 UTF-8 报错）
- 结构：`## KEY:` 文件级头（NUMBER OF SPECTRUM、UNIT）→ 每光谱段 `# KEY:` 头（START TIME、INCIDENCE ANGLE=70°、FIRST/LAST POINT、INCREMENT、NUMBER OF POINTS；0.02 步长 296 点）→ `# DATA:` 数据块
- `# KEY:` 的值常在下一行，不能只读同行；`# DATA:` 后首行是列名表头，跳过；解析=逐行扫描，遇 `# DATA:` 按空格 split 数值行至非数值行
- 数据 8 列：eV Hv SlitWidth S0 Psi Delta Is Ic；Ψ/Δ 单位度；范围 0.6–6.5 eV
- 样本标识只用文件名 stem，禁止按命名规则推断 chip/position 等业务含义
- 自检恒等式 Is=sin(2Ψ)sin(Δ)、Ic=sin(2Ψ)cos(Δ) 解析后必须验证；孤立毛刺点（约每谱 1 个）以恒等式残差 >5e-3 剔除（spe_parser.quality_mask）

## §4 光学模型与拟合引擎
- 堆叠：空气 / 层1..n / c-Si 衬底；TMM 递归 Fresnel，N=n−ik 约定（裸 Si 文献值 + 干涉周期双验证）；入射角 70°
- c-Si n/k：si_data.py 内置文献表（Aspnes-Studna 1.5–6.0 eV + Green 2008 0.83–1.45 µm + Salzberg-Villa IR Sellmeier），离线
- 色散模型选型：透明膜（SiO₂ 等）Cauchy/Sellmeier；吸收膜（富硅 SiN、a-Si、poly-Si）Tauc-Lorentz（数值 KK）；多孔/混合膜 Bruggeman EMA
- 参数边界（勿收窄）：Cauchy A∈[1.30,3.00]；TL E0∈[3.0,12]
- 初值策略：Δ 条纹 FFT 估 d（fit.fringe_thickness_guess）+ 文献参数初值；禁止随机初值；多起点只对 d 撒种子
- 目标函数：Is/Ic 最小二乘（避开 Δ 360° 卷绕；Ψ/Δ 由拟合结果直接计算，无目标切换参数）；scipy.optimize.least_squares，无 lmfit
- 返回值契约：fit_stack / fit_single_sio2 返回 seeds_info + seed_consistency（±0.5 nm 一致）+ eV_fine/Psi_fit_fine/Delta_fit_fine/layer_nk_fine（2N−1 细网格，供模型线绘图）；多起点局部优化不保证全局最优
- 全局最优验证：stack_fit.profile_thickness 做 χ²(d) 剖面，每点双起点 = 全局最优参数 + 延续（单一热启动会在真谷陷入固定 d 局部极小）
- EMA 取根：本库 N=n−ik 要求 Im(ε)≤0，非无源根罚 1e6 后再取 |Im| 最小（纯 min|Im| 在吸收宿主下选非无源分支）
- 拟合窗 fit.FIT_EV_MIN/MAX = 1.2–5.5 eV（原因见 §5）

## §5 已知物理边界与限制
- 残差地板 mse≈0.005（rms≈0.08）是数据侧系统误差墙（与 DeltaPsi2 相当）；禁止靠堆模型复杂度压残差
- E<1.12 eV 时 Si 衬底透明、半无限近似失效；E>5.5 eV 信噪/标定差 → 拟合窗取 1.2–5.5 eV
- 仪器 Δ 约定与 TMM 直接一致（ρ=rₚ/r_s，不取负/共轭）；Ψ 折叠于 [0°,45°]（sin(2Ψ) 对 Ψ/90°−Ψ 简并，相调制仪器固有），对 Is/Ic 拟合零影响
- Ψ 展开（psi_display.unfold_psi）：按模型 45° 穿越翻转数据段 + 穿越处 ±3 点线性桥接；禁止改回逐点翻转（数据折叠峰只到 38–44°，逐点规则在每次穿越制造 10–25° 假断点；数据侧分段检测不可行）
- 高能区（>4 eV）振幅塌缩是真实物理：高能 Si 类金属致 Vp 物理上限 ±5–8°；数据条纹振幅超模型上限 ~2.6 倍为深紫外标定系统误差，振幅比系统性 3.4–3.8——勿再重查，勿为压残差改模型
- 窗口截断致 d 漂 ~9 nm（单角度 n–d 相关）；拟合 n₅₈₉≈1.448 低于热氧化典型 1.458–1.462 属该相关正常偏移
- 已知限制：n₅₈₉ 偏低、>5.5 eV 区间未用、多角度/多入射数据可解耦 n–d
- 表面粗糙层对透明氧化膜无效：热氧化 SiO₂（粗糙 <0.5 nm）信号在残差墙下 ~7 倍，自由 d_rough 只吃系统误差——功能保留，poly-Si/a-Si 片再启用

## §6 GUI 结构契约（fit_app.py）
- 单页布局 = 工作流顺序五区：① 原始数据（Ψ 展开 + Δ 两行 + caption 测点信息）② 拟合模型（层构建器）③ 拟合设置 ④ 拟合结果 ⑤ 全局最优验证 expander 固定最底
- 结果区：三列 vertical_alignment="center"——st.metric 厚度卡片（用户明确偏好：±err 与数值同行、药丸 delta_arrow="off"、药丸字号=数值 2.25rem）、参数 dataframe（行高精确 35px/行 + 表头 38px）、保存材料按钮；Ψ·Δ 叠加只画 [E_min,E_max] 窗口内、模型线用 2× 细网格；χ²ᵣ/rms 只在 col1 显示
- 弹窗规则：开关由 session_state.dialog 驱动，按钮 on_click 先种默认值；所有弹窗必须 on_dismiss=_clear_dialog（✕/ESC/点遮罩不通知 Python）；控件值必须 None 兜底；参数控件 key 含模型名（dlg_pv_{model}_{p}，切模型自动重置）；层用稳定唯一 ID 做 key（禁止列表索引）；编辑弹窗零按钮即时生效；确认按钮 = 改状态 → dialog=None → st.rerun()
- 层初始化用 `if "layers" not in session_state`（禁止 `if not layers`，删空会被误判未初始化而自动补层）；空堆叠拟合按钮禁用 + main 级守卫；列表变动后 _enforce_top_rough() 摘除非顶层 rough 并 toast（同帧消费）
- 侧栏 = CCv2 compact_browser 单挂载 dual_explorer：高度禁止 height="stretch" 与整型挂载——JS 给根元素设固定像素 root.style.height=data.height_px，height_px=视口高−64、下限 400；双击时序：单击延迟 260ms 发 select（可取消）、400ms 内第二次点击发 open，点击状态存 root._clicks；事件经 _on_fx_event 分发；文件/材料删除统一走 DATA_DIR/.trash 回收站（platformdirs 用户数据目录，不污染主目录；_to_trash 有 _in_root 守卫，仅允许移动 Explorer 根内路径）
- 材料插入按 JSON kind 分流：base→make_base_layer（参数全 fit + FFT d 换算），calibrated→make_preset_layer（参数全 fixed、fit 仅 ["thickness"]）；保存成功提示同帧消费（AppTest 断言查文件非 session_state）
- 侧栏 CSS：stSidebarHeader 绝对定位使收起箭头与标题同行、h1–h3 加 padding-right；主容器 padding-top 压缩（依赖 data-testid，Streamlit 升级失效按新版更新）
- 数据预览 Ψ 展开用每文件缓存参考拟合（get_preview_model），不画折叠 Ψ 原始值

## §7 材料库
- 标定流程：单层标定片拟合 → materials/<name>.json：{model, params±err, eV_range, provenance{spe 文件, 日期, 拟合厚度}}
- 两类（kind 字段）：calibrated=拟合结果，插入后参数全 fixed、仅拟合厚度；base=内置文献初值（base-sio2/si3n4/sin-rich/a-si/si-poly 共 5 条），插入后参数全 fit、FFT d 按基料 n₅₈₉ 换算（_fft_d_for_n，不换算 SiN 偏差 +56%）；ensure_base_materials() 启动自动重建
- 多层 YAML：已知层引用材料库且 n/k 固定（不引入拟合参数，避免 n–d 强相关不收敛），只拟合未知层厚度或新层色散+厚度
- 表面粗糙层 v1：仅顶层、EMA(50% void + 本层材料)、d∈[0,10] nm、f 固定 0.5（与 d_rough 强简并勿放开）；保存材料自动忽略 rough（粗糙度是样品属性）

## §8 Python 环境与测试策略
- 开发：conda env experiment（Python 3.14；numpy/scipy/pandas/matplotlib/plotly/streamlit/pyyaml/platformdirs 齐备，无 lmfit）；打包：干净 venv Python 3.12（依赖见 packaging/requirements-pack.txt）
- 测试：streamlit.testing.v1.AppTest 无浏览器跑通全流程（选文件→弹窗加层→弹窗编辑→拟合）；弹窗内控件用 at.get_by_key 寻址（segmented_control 可 set_value）
- 测试边界：组件 JS 交互 AppTest 驱动不了——事件分发/操作函数直调单测 + 整页无异常冒烟，浏览器行为靠手测
