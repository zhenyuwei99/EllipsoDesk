"""EllipsoDesk 桌面启动器（双平台单文件入口，开发态也可直接跑）。

- 开发态：`python packaging/launcher.py` —— 拉起 pywebview 窗口；无 pywebview 时退化为系统浏览器。
- 打包态：PyInstaller（launcher.spec，onedir）入口；fit_app.py 从 _MEIPASS 旁的代码包定位。

关键契约（防踩坑）：
- macOS 上 pywebview 必须在**主线程** start()，Streamlit 服务器放 daemon 线程；
- 空闲端口用 socket bind(("127.0.0.1", 0)) 取号后关闭（存在极小竞争窗口，可接受）；
- 就绪判定：轮询 /_stcore/health 至返回 ok（超时 60s）；
- 窗口全部关闭后 os._exit(0) 硬杀——tornado 有非守护线程不会自然退出；
- 启动期信息打 stderr（窗口模式下不可见；frozen 且无 pywebview 时有用）。
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


def _start_server(script: Path, port: int) -> threading.Thread:
    """Streamlit 服务器线程（daemon：主进程退出即随之结束）。

    streamlit ≥1.4x 的 bootstrap 在协程内 signal.signal(SIGTERM, …)，而 Python 信号只允许
    主线程注册——非主线程直跑 stcli.main() 会 ValueError 直接夭折。服务器线程的生命周期
    本就由本进程 os._exit() 兜底管理，无需信号 → 线程内临时禁用注册。
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
    script = _locate_fit_app()
    port = _free_port()
    url = f"http://127.0.0.1:{port}"
    print(f"[EllipsoDesk] streamlit: {url}  ({script})", file=sys.stderr)

    server = _start_server(script, port)
    if not _wait_ready(port):
        print(f"[EllipsoDesk] server not ready within {HEALTH_TIMEOUT_S:.0f}s, abort", file=sys.stderr)
        os._exit(1)

    try:
        import webview  # noqa: F401
    except ImportError:
        # 开发环境/精简安装无 pywebview：退化系统浏览器，主线程阻塞陪跑服务器线程
        print(f"[EllipsoDesk] pywebview unavailable, opening system browser: {url}", file=sys.stderr)
        webbrowser.open(url)
        server.join()
        return

    webview.create_window(WINDOW_TITLE, url, width=1440, height=900)
    webview.start()  # macOS：必须主线程
    # 走到这里 = 窗口全部关闭；tornado 的非守护线程不肯退，硬杀兜底
    os._exit(0)


if __name__ == "__main__":
    main()
