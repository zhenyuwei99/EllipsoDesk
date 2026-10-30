"""EllipsoDesk 桌面启动器（双平台单文件入口，开发态也可直接跑）。

- 开发态：`python packaging/launcher.py` —— 拉起 pywebview 窗口；无 pywebview 时退化为系统浏览器。
- 打包态：PyInstaller（launcher.spec，onedir）入口；fit_app.py 从 _MEIPASS 旁的代码包定位。

关键契约（防踩坑）：
- macOS 上 pywebview 必须在**主线程** start()，Streamlit 服务器放 daemon 线程；
- 空闲端口用 socket bind(("127.0.0.1", 0)) 取号后关闭（存在极小竞争窗口，可接受）；
- 就绪判定：轮询 /_stcore/health 至返回 ok（超时 60s）；
- 窗口全部关闭后 os._exit(0) 硬杀——tornado 有非守护线程不会自然退出；
- 启动期信息打 stderr；**冻结态**（sys.frozen，console=False 下 stderr 不可见）且非冒烟时
  stderr 重定向到 platformdirs 用户数据目录 logs/launcher.log（append 一行时间戳，不轮转），
  排查冻结产物问题先看它——含 PORT {port} 行；
- 冒烟逃生口：环境变量 ELLIPSODESK_NO_WINDOW=1（CI 无显示环境用）——就绪后不开窗口，
  stdout 打印 "PORT {port}"（脚本解析动态端口）与 "SMOKE_OK http://…/_stcore/health"
  （确定性健康检查信号）后 os._exit(0)；日志行均 flush（os._exit 不冲缓冲）。
"""
import os
import socket
import sys
import threading
import time
import webbrowser
from pathlib import Path
from urllib.request import urlopen

HEALTH_TIMEOUT_S = 60.0
WINDOW_TITLE = "EllipsoDesk · 椭偏拟合工作台"


def _locate_fit_app() -> Path:
    """定位 fit_app.py：frozen 从 _MEIPASS 旁的代码包找；开发态从本文件 ../ellipsodesk/ 找。"""
    base = Path(getattr(sys, "_MEIPASS", "")) if getattr(sys, "frozen", False) \
        else Path(__file__).resolve().parent.parent
    cand = base / "ellipsodesk" / "gui" / "fit_app.py"
    if not cand.exists():
        raise SystemExit(f"[EllipsoDesk] fit_app.py not found under {base}")
    return cand


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _redirect_stderr_to_log() -> Path | None:
    """冻结态 stderr 落盘（R2 ADV-4）：console=False 的 .app/.exe 无控制台，stderr 不可见。

    重定向到 platformdirs 用户数据目录 logs/launcher.log（与 material_lib.DATA_DIR 同根、
    单独计算以免开发态也要摆弄 sys.path）；启动 append 一行时间戳分隔（不轮转）。
    任何异常（目录不可写等）都吞掉、保留原 stderr——日志是锦上添花，不能拖垮启动。
    """
    try:
        import platformdirs
        log_dir = Path(platformdirs.user_data_dir("EllipsoDesk", appauthor=False)) / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        log_path = log_dir / "launcher.log"
        fh = open(log_path, "a", buffering=1)  # 行缓冲：os._exit 不冲缓冲
        fh.write(f"==== {time.strftime('%Y-%m-%d %H:%M:%S')} pid={os.getpid()} ====\n")
        sys.stderr = fh
        return log_path
    except Exception as exc:  # pragma: no cover - 兜底分支
        print(f"[EllipsoDesk] stderr log redirect failed: {exc!r}", file=sys.__stderr__, flush=True)
        return None


def _start_server(script: Path, port: int) -> threading.Thread:
    """Streamlit 服务器线程（daemon：主进程退出即随之结束）。

    streamlit ≥1.4x 的 bootstrap 在协程内 signal.signal(SIGTERM, …)，而 Python 信号只允许
    主线程注册——非主线程直跑 stcli.main() 会 ValueError 直接夭折。此处把 signal.signal
    置为 no-op：stcli.main() 阻塞不返回，下方 finally 的恢复实际不会执行，即该
    monkey-patch 作用域为**进程全程**（桌面单进程 + os._exit 退出模型下无害；进程生命
    周期本就由 os._exit() 兜底管理，无需信号——改架构前先理解这一点）。
    """
    def run():
        import signal
        _orig_signal = signal.signal
        signal.signal = lambda *a, **k: None
        try:
            from streamlit.web import cli as stcli
            sys.argv = ["streamlit", "run", str(script),
                        "--server.headless", "true",
                        "--server.port", str(port),
                        "--global.developmentMode", "false"]
            sys.exit(stcli.main())
        finally:
            signal.signal = _orig_signal

    t = threading.Thread(target=run, name="streamlit-server", daemon=True)
    t.start()
    return t


def _wait_ready(port: int, timeout: float = HEALTH_TIMEOUT_S) -> bool:
    url = f"http://127.0.0.1:{port}/_stcore/health"
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urlopen(url, timeout=2.0) as resp:
                if resp.read().decode().strip() == "ok":
                    return True
        except Exception:
            time.sleep(0.3)
    return False


def main() -> None:
    smoke = os.environ.get("ELLIPSODESK_NO_WINDOW") == "1"
    # 冒烟模式绝不重定向（CI 从 stdout/stderr 读 PORT/SMOKE_OK），保持原样
    if getattr(sys, "frozen", False) and not smoke:
        _redirect_stderr_to_log()
    script = _locate_fit_app()
    port = _free_port()
    url = f"http://127.0.0.1:{port}"
    print(f"[EllipsoDesk] streamlit: {url}  ({script})", file=sys.stderr)
    if smoke:
        print(f"PORT {port}", flush=True)   # 先行端口行：CI 脚本据此 curl 动态端口（stdout 契约）
    else:
        print(f"PORT {port}", file=sys.stderr, flush=True)  # 冻结态随 stderr 落 launcher.log

    server = _start_server(script, port)
    if not _wait_ready(port):
        print(f"[EllipsoDesk] server not ready within {HEALTH_TIMEOUT_S:.0f}s, abort", file=sys.stderr)
        os._exit(1)

    if smoke:  # 无显示环境：确定性健康检查后即退，不开窗口
        print(f"SMOKE_OK {url}/_stcore/health", flush=True)
        os._exit(0)

    try:
        import webview  # noqa: F401
    except ImportError:
        # 开发环境/精简安装无 pywebview：退化系统浏览器，主线程阻塞陪跑服务器线程
        print(f"[EllipsoDesk] pywebview unavailable, opening system browser: {url}", file=sys.stderr)
        if not webbrowser.open(url):
            # R2 ADV-5：open 失败（返回 False）必须明示，不得静默陪跑
            msg = f"[EllipsoDesk] 无法打开浏览器，请手动访问 {url}（Ctrl-C 退出）"
            print(msg, flush=True)                  # stdout：冻结态 console=True 时可见
            print(msg, file=sys.stderr, flush=True)  # stderr：冻结态落 launcher.log
        server.join()
        return

    webview.create_window(WINDOW_TITLE, url, width=1440, height=900)
    webview.start()  # macOS：必须主线程
    # 走到这里 = 窗口全部关闭；tornado 的非守护线程不肯退，硬杀兜底
    os._exit(0)


if __name__ == "__main__":
    main()
