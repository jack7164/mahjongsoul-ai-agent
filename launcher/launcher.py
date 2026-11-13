r"""雀魂 AI 助手 —— 主程式(啟動器)。打包成「雀魂AI助手.exe」放在專案資料夾最上層。

    python launcher\launcher.py          (開發時直接跑)
    打包主程式.bat                         (打包成 exe)

=== 為什麼 exe 只是「啟動器」 ===
辨識要用的套件(torch、ultralytics…)約 3GB,Mortal 的 Docker 映像約 6GB,不可能
全部包進一個 exe。所以 exe 只負責:檢查/安裝環境 -> 說明與免責聲明 -> 選平台 ->
確認擷取畫面 -> 用「環境裡的 Python」跑 app\live_v7\agent.py(提示列 + 手牌標記)。

★ 這支只用 Python 標準函式庫(tkinter、subprocess、urllib…):它要在「什麼都
  還沒裝」的電腦上就能打開。需要 cv2 / pywin32 的事(列視窗、擷取預覽)交給
  app\launcher_helper.py,用環境裡的 Python 執行。

=== 環境放在哪裡 ===
    runtime\python\      uv 下載的獨立 Python 3.10(不用另外裝 Anaconda)
    runtime\uv\uv.exe    安裝工具 uv(第一次自動下載)
    runtime\env.json     「哪一個 Python 驗證過了」的紀錄,下次開啟直接用
舊電腦已經有 conda 環境 mahjongsoul_agent / mjai 的話,驗證過就直接用,不重裝。

=== 視窗標題不能含「雀魂」 ===
找遊戲視窗時會用到這兩個字(agent.py 的字幕列也是同樣的理由)。
"""
import base64
import ctypes
import glob
import hashlib
import json
import os
import platform
import queue
import shutil
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser
import zipfile

import tkinter as tk
from tkinter import ttk

WIN = sys.platform == "win32"
VERSION = "1.0"
WINDOW_TITLE = "AI 麻將助手"            # ★ 不能含「雀魂」(見模組說明)
PRODUCT = "雀魂 AI 助手"

if getattr(sys, "frozen", False):
    ROOT = os.path.dirname(os.path.abspath(sys.executable))
else:
    ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP = os.path.join(ROOT, "app")
RUNTIME = os.path.join(ROOT, "runtime")
CONFIG = os.path.join(ROOT, "config.json")
WEIGHTS = os.path.join(APP, "Model", "mortal.pth")
MORTAL_SRC = os.path.join(APP, "Mortal")
HELPER = os.path.join(APP, "launcher_helper.py")
AGENT = os.path.join(APP, "live_v7", "agent.py")
CHROME_MODE = os.path.join(APP, "chrome_mode.py")
ENV_MARK = os.path.join(RUNTIME, "env.json")
UV_EXE = os.path.join(RUNTIME, "uv", "uv.exe")
UV_URL = ("https://github.com/astral-sh/uv/releases/latest/download/"
          "uv-x86_64-pc-windows-msvc.zip")
DOCKER_DOC = "https://docs.docker.com/desktop/setup/install/windows-install/"
DOCKER_DESKTOP = r"C:\Program Files\Docker\Docker\Docker Desktop.exe"
DOCKER_BIN = r"C:\Program Files\Docker\Docker\resources\bin"
NO_WINDOW = 0x08000000 if WIN else 0
INTRO_VERSION = 1            # 說明內容大改時 +1,讓使用者重新看一次
NEED_GB = 15

# ---------------------------------------------------------------------------
# 外觀(跟設計稿一致)
# ---------------------------------------------------------------------------
C = {"bg": "#121418", "bar": "#0d0f12", "surf": "#1b1e24", "surf2": "#1f232a",
     "btn": "#232730", "btnh": "#2c313b", "line": "#2e333d", "line2": "#3a404b",
     "text": "#e8e9ec", "sub": "#c9cdd4", "muted": "#9aa0aa", "dis": "#8b919b",
     "acc": "#3ecf9a", "acch": "#5edbae", "accd": "#173a2e", "accbg": "#172520",
     "accline": "#23574a", "warn": "#ffb35c", "warnd": "#3d2a12",
     "warnbg": "#2a1e10", "warnline": "#5a3a16", "err": "#ff6b6b",
     "errd": "#3a1f22", "ink": "#0d0f12"}
FONT = "Microsoft JhengHei UI" if WIN else "Noto Sans CJK TC"
MONO = "Consolas" if WIN else "DejaVu Sans Mono"


def F(size, bold=False):
    return (FONT, size, "bold") if bold else (FONT, size)


# ---------------------------------------------------------------------------
# 設定檔:啟動器自己的東西放在 config.json 的 "launcher" 底下
# ---------------------------------------------------------------------------
def load_cfg():
    try:
        with open(CONFIG, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return {}


def launcher_cfg():
    return load_cfg().get("launcher") or {}


def save_launcher_cfg(**kw):
    cfg = load_cfg()
    lc = cfg.get("launcher") or {}
    lc.update(kw)
    cfg["launcher"] = lc
    tmp = CONFIG + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(cfg, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, CONFIG)


# ---------------------------------------------------------------------------
# 子程式
# ---------------------------------------------------------------------------
def child_env(extra=None):
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    if WIN and os.path.isdir(DOCKER_BIN) and \
            DOCKER_BIN.lower() not in env.get("PATH", "").lower():
        # 剛裝好 Docker 時,這個程式拿到的 PATH 還是舊的
        env["PATH"] = env.get("PATH", "") + os.pathsep + DOCKER_BIN
    env.update(extra or {})
    return env


def python_env(py, extra=None):
    """用某個 Python 跑程式時的環境變數。

    ★ conda 環境沒有 activate 的話,Library\bin 之類的 DLL 資料夾不在 PATH 裡,
      torch / pywin32 有時會載入失敗 —— 所以手動把 activate 會加的幾個資料夾補上。
      (uv 裝的獨立 Python 沒有這些資料夾,不受影響)"""
    env = child_env(extra)
    base = os.path.dirname(os.path.abspath(py))
    if os.path.isdir(os.path.join(base, "Library", "bin")):
        dirs = [base, os.path.join(base, "Library", "mingw-w64", "bin"),
                os.path.join(base, "Library", "usr", "bin"),
                os.path.join(base, "Library", "bin"),
                os.path.join(base, "Scripts")]
        env["PATH"] = os.pathsep.join(dirs + [env.get("PATH", "")])
        env["CONDA_PREFIX"] = base
    return env


def _env_for(cmd, env):
    if env is not None:
        return env
    if cmd and str(cmd[0]).lower().endswith(("python.exe", "python", "python3")):
        return python_env(cmd[0])
    return child_env()


def run(cmd, timeout=60, cwd=None, env=None):
    """跑完回傳 (returncode, 輸出文字)。找不到程式回 (-1, 訊息)。"""
    try:
        r = subprocess.run(cmd, cwd=cwd, env=_env_for(cmd, env),
                           capture_output=True, timeout=timeout,
                           creationflags=NO_WINDOW,
                           stdin=subprocess.DEVNULL)
        out = (r.stdout or b"") + (r.stderr or b"")
        return r.returncode, out.decode("utf-8", "replace")
    except FileNotFoundError as e:
        return -1, str(e)
    except subprocess.TimeoutExpired:
        return -2, "逾時"
    except Exception as e:
        return -3, str(e)


def stream(cmd, on_line, cwd=None, env=None, holder=None):
    """邊跑邊把每一行交給 on_line。回傳 returncode。"""
    try:
        p = subprocess.Popen(cmd, cwd=cwd, env=_env_for(cmd, env),
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                             stdin=subprocess.DEVNULL, creationflags=NO_WINDOW)
    except FileNotFoundError as e:
        on_line(f"找不到程式:{e}")
        return -1
    if holder is not None:
        holder.append(p)
    buf = b""
    while True:
        ch = p.stdout.read(1)
        if not ch:
            break
        if ch in (b"\n", b"\r"):
            if buf.strip():
                on_line(buf.decode("utf-8", "replace").rstrip())
            buf = b""
        else:
            buf += ch
    if buf.strip():
        on_line(buf.decode("utf-8", "replace").rstrip())
    return p.wait()


# ---------------------------------------------------------------------------
# 環境檢查與安裝(全部在背景執行緒呼叫)
# ---------------------------------------------------------------------------
CHECK_CODE = ("import torch, ultralytics, cv2, numpy, win32gui; "
              "print('OK', torch.__version__, "
              "'GPU' if torch.cuda.is_available() else 'CPU')")


def gpu_name():
    rc, out = run(["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
                  timeout=20)
    if rc == 0 and out.strip():
        return out.strip().splitlines()[0].strip()
    return None


def ram_gb():
    if not WIN:
        return None

    class MS(ctypes.Structure):
        _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
    m = MS()
    m.dwLength = ctypes.sizeof(MS)
    if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m)):
        return round(m.ullTotalPhys / 2 ** 30)
    return None


def free_gb():
    try:
        return shutil.disk_usage(ROOT).free // 2 ** 30
    except Exception:
        return None


def windows_name():
    if not WIN:
        return platform.system()
    build = sys.getwindowsversion().build
    return ("Windows 11" if build >= 22000 else "Windows 10") + \
        (" 64 位元" if platform.machine().endswith("64") else " 32 位元")


def req_file(gpu):
    return os.path.join(ROOT, "requirements_gpu.txt" if gpu
                        else "requirements_cpu.txt")


def sha(path):
    try:
        with open(path, "rb") as fh:
            return hashlib.sha1(fh.read()).hexdigest()
    except OSError:
        return ""


def conda_pythons():
    home, local = os.environ.get("USERPROFILE", ""), os.environ.get("LOCALAPPDATA", "")
    prog = os.environ.get("ProgramData", r"C:\ProgramData")
    bases = [os.path.join(home, "anaconda3"), os.path.join(home, "miniconda3"),
             os.path.join(local, "anaconda3"), os.path.join(local, "miniconda3"),
             os.path.join(prog, "anaconda3"), os.path.join(prog, "miniconda3"),
             r"C:\anaconda3", r"C:\miniconda3"]
    out = []
    for b in bases:
        for name in ("mahjongsoul_agent", "mjai"):
            p = os.path.join(b, "envs", name, "python.exe")
            if os.path.isfile(p):
                out.append(p)
    return out


def managed_python():
    hits = sorted(glob.glob(os.path.join(RUNTIME, "python", "cpython-3.10*",
                                         "python.exe")))
    return hits[-1] if hits else None


def verify_python(py):
    rc, out = run([py, "-c", CHECK_CODE], timeout=300)
    line = next((x for x in out.splitlines() if x.startswith("OK ")), None)
    return (rc == 0 and line is not None), (line or out.strip()[-300:])


def write_env_mark(py, detail):
    os.makedirs(RUNTIME, exist_ok=True)
    with open(ENV_MARK, "w", encoding="utf-8") as fh:
        json.dump({"python": py, "detail": detail, "verified": time.time()},
                  fh, ensure_ascii=False, indent=2)


def find_env(force=False):
    """回傳 (python 路徑, 說明) 或 (None, 原因)。有驗證紀錄就不重驗(torch 載入要好幾秒)。"""
    if not force and os.path.isfile(ENV_MARK):
        try:
            with open(ENV_MARK, encoding="utf-8") as fh:
                m = json.load(fh)
            if os.path.isfile(m.get("python", "")):
                return m["python"], m.get("detail", "")
        except Exception:
            pass
    for py in [p for p in [managed_python()] if p] + conda_pythons():
        ok, detail = verify_python(py)
        if ok:
            write_env_mark(py, detail)
            return py, detail
    return None, "還沒安裝"


def uv_env():
    return child_env({
        "UV_PYTHON_INSTALL_DIR": os.path.join(RUNTIME, "python"),
        "UV_CACHE_DIR": os.path.join(RUNTIME, "uv-cache"),
        "UV_LINK_MODE": "copy",
        "UV_NO_PROGRESS": "1",
    })


def download(url, dest, on_progress):
    tmp = dest + ".part"
    with urllib.request.urlopen(url, timeout=60) as r, open(tmp, "wb") as fh:
        total = int(r.headers.get("Content-Length") or 0)
        got = 0
        while True:
            chunk = r.read(1 << 16)
            if not chunk:
                break
            fh.write(chunk)
            got += len(chunk)
            on_progress(got, total)
    os.replace(tmp, dest)


def install_env(gpu, report, holder):
    """report(百分比 或 None, 一行說明)。成功回 (python, 說明),失敗丟 RuntimeError。"""
    os.makedirs(os.path.dirname(UV_EXE), exist_ok=True)
    if not os.path.isfile(UV_EXE):
        report(1, "下載安裝工具 uv…")
        z = os.path.join(RUNTIME, "uv", "uv.zip")
        download(UV_URL, z, lambda g, t: report(
            1 + (4 * g / t if t else 0), f"下載安裝工具 uv… {g / 2 ** 20:.1f} MB"))
        with zipfile.ZipFile(z) as zf:
            for n in zf.namelist():
                if n.lower().endswith(("uv.exe", "uvx.exe")):
                    with zf.open(n) as src, open(os.path.join(
                            os.path.dirname(UV_EXE), os.path.basename(n)), "wb") as dst:
                        shutil.copyfileobj(src, dst)
        os.remove(z)
    env = uv_env()
    report(6, "下載 Python 3.10…")
    if managed_python() is None:
        rc = stream([UV_EXE, "python", "install", "3.10"],
                    lambda s: report(None, s), env=env, holder=holder)
        if rc != 0 or managed_python() is None:
            raise RuntimeError("Python 下載失敗,請確認網路後重試")
    py = managed_python()
    req = req_file(gpu)
    state = {"resolved": 0, "done": 0}

    def on_line(s):
        low = s.strip().lower()
        if low.startswith("resolved"):
            try:
                state["resolved"] = int(low.split()[1])
            except (IndexError, ValueError):
                pass
            report(12, s)
        elif low.startswith("downloaded"):
            state["done"] += 1
            report(min(80, 15 + state["done"] * 6), s)
        elif low.startswith("prepared"):
            report(85, s)
        elif low.startswith(("installed", "uninstalled")):
            report(92, s)
        else:
            report(None, s)

    report(10, "安裝辨識套件(第一次要下載約 3GB)…")
    rc = stream([UV_EXE, "pip", "install", "--python", py,
                 "--break-system-packages", "--index-strategy",
                 "unsafe-best-match", "-r", req],
                on_line, env=env, holder=holder)
    if rc != 0:
        raise RuntimeError("套件安裝失敗(詳細紀錄裡有錯誤訊息)")
    report(95, "檢查安裝結果…")
    ok, detail = verify_python(py)
    if not ok:
        raise RuntimeError("裝好了但載入失敗:" + detail[-120:])
    write_env_mark(py, detail)
    run([UV_EXE, "cache", "clean"], timeout=300, env=env)   # 省 3GB 硬碟
    report(100, "完成")
    return py, detail


def docker_exe():
    p = shutil.which("docker")
    if p:
        return p
    q = os.path.join(DOCKER_BIN, "docker.exe")
    return q if os.path.isfile(q) else None


def docker_state():
    """'missing' 沒裝 / 'stopped' 裝了沒開 / 'ok'。"""
    exe = docker_exe()
    if exe is None:
        return "missing"
    rc, _ = run([exe, "info"], timeout=30)
    return "ok" if rc == 0 else "stopped"


def image_ok():
    exe = docker_exe()
    if exe is None:
        return False
    rc, _ = run([exe, "image", "inspect", "mortal"], timeout=30)
    return rc == 0


def build_image(report, holder):
    exe = docker_exe()
    state = {"step": 0, "total": 0}

    def on_line(s):
        # BuildKit plain 輸出:「#7 [3/9] RUN …」
        if "[" in s and "/" in s:
            try:
                inner = s.split("[", 1)[1].split("]", 1)[0].split()[-1]
                k, n = inner.split("/")
                state["step"], state["total"] = int(k), int(n)
            except (ValueError, IndexError):
                pass
        pct = (100 * (state["step"] - 1) / state["total"]) if state["total"] else None
        report(pct, s)

    rc = stream([exe, "build", "--progress=plain", "-t", "mortal:latest", "."],
                on_line, cwd=MORTAL_SRC,
                env=child_env({"DOCKER_BUILDKIT": "1"}), holder=holder)
    if rc != 0:
        raise RuntimeError("映像建立失敗(詳細紀錄裡有錯誤訊息)")
    report(100, "完成")


def chrome_running():
    rc, out = run(["tasklist", "/FI", "IMAGENAME eq chrome.exe", "/NH",
                   "/FO", "CSV"], timeout=15)
    return "chrome.exe" in out.lower()


def open_folder(path):
    try:
        os.startfile(path)            # noqa: Windows only
    except Exception:
        webbrowser.open("file:///" + path.replace("\\", "/"))


# ---------------------------------------------------------------------------
# 小元件
# ---------------------------------------------------------------------------
class Btn(tk.Label):
    """扁平按鈕(tk.Button 在 Windows 上很多顏色改不了)。"""

    STYLES = {
        "primary": ("acc", "ink", "acch"),
        "warn": ("warn", "ink", "#ffc47f"),
        "ghost": ("bg", "text", "btn"),
        "soft": ("btn", "text", "btnh"),
        "danger": ("errd", "err", "#4a272b"),
    }

    def __init__(self, master, text, command=None, kind="ghost", size=11,
                 bold=None, padx=18, pady=7, bg_parent=None):
        self.kind = kind
        self.command = command
        self.enabled = True
        bold = (kind in ("primary", "warn")) if bold is None else bold
        super().__init__(master, text=text, font=F(size, bold), padx=padx,
                         pady=pady, cursor="hand2", bd=0,
                         highlightthickness=1 if kind == "ghost" else 0,
                         highlightbackground=C["line2"])
        self._paint(False)
        self.bind("<Enter>", lambda e: self._paint(True))
        self.bind("<Leave>", lambda e: self._paint(False))
        self.bind("<ButtonRelease-1>", self._click)

    def _paint(self, hover):
        if not self.enabled:
            self.config(bg=C["btn"] if self.kind != "ghost" else C["bg"],
                        fg=C["dis"], cursor="arrow")
            return
        bg, fg, hv = self.STYLES[self.kind]
        bg = C.get(bg, bg)
        self.config(bg=C.get(hv, hv) if hover else bg, fg=C.get(fg, fg),
                    cursor="hand2")

    def _click(self, e):
        if self.enabled and self.command and 0 <= e.x <= self.winfo_width() \
                and 0 <= e.y <= self.winfo_height():
            self.command()

    def set_enabled(self, on):
        self.enabled = bool(on)
        self._paint(False)


class StatusIcon(tk.Canvas):
    """28px 的狀態圓圈:ok / run / warn / err / wait。"""

    def __init__(self, master, size, bg):
        super().__init__(master, width=size, height=size, bg=bg,
                         highlightthickness=0, bd=0)
        self.s = size
        self.state = None
        self.angle = 0
        self._job = None

    def set(self, state):
        if state == self.state:
            return
        self.state = state
        if self._job:
            self.after_cancel(self._job)
            self._job = None
        self._draw()

    def _draw(self):
        s, d = self.s, self.s * 0.12
        self.delete("all")
        st = self.state
        if st == "ok":
            self.create_oval(1, 1, s - 1, s - 1, fill=C["accd"], outline="")
            self.create_line(s * .28, s * .52, s * .45, s * .68, s * .74, s * .36,
                             fill=C["acc"], width=max(2, s // 9),
                             capstyle="round", joinstyle="round")
        elif st == "warn":
            self.create_oval(1, 1, s - 1, s - 1, fill=C["warnd"], outline="")
            self.create_text(s / 2, s / 2, text="!", fill=C["warn"],
                             font=F(max(9, int(s / 2.6)), True))
        elif st == "err":
            self.create_oval(1, 1, s - 1, s - 1, fill=C["errd"], outline="")
            w = max(2, s // 9)
            self.create_line(s * .34, s * .34, s * .66, s * .66, fill=C["err"], width=w,
                             capstyle="round")
            self.create_line(s * .66, s * .34, s * .34, s * .66, fill=C["err"], width=w,
                             capstyle="round")
        elif st == "run":
            w = max(3, s // 8)
            self.create_oval(d, d, s - d, s - d, outline=C["line"], width=w)
            self.create_arc(d, d, s - d, s - d, start=self.angle, extent=100,
                            style="arc", outline=C["acc"], width=w)
            self.angle = (self.angle - 24) % 360
            self._job = self.after(60, self._draw)
        else:   # wait
            self.create_oval(d, d, s - d, s - d, outline=C["line2"], width=2,
                             dash=(3, 3))


def sep(master, bg=None):
    return tk.Frame(master, height=1, bg=bg or C["line"])


# ---------------------------------------------------------------------------
# 主視窗
# ---------------------------------------------------------------------------
STEPS = ("1 安裝", "2 說明", "3 平台", "4 畫面確認")


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(WINDOW_TITLE)
        self.S = max(1.0, self.winfo_fpixels("1i") / 96.0)
        w, h = self.px(960), self.px(660)
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        self.geometry(f"{w}x{h}+{(sw - w) // 2}+{max(0, (sh - h) // 3)}")
        self.minsize(self.px(900), self.px(600))
        self.configure(bg=C["bg"])
        self.q = queue.Queue()
        self.python = None              # 驗證過的環境 Python
        self.platform = None            # "steam" / "web"
        self.browser = "chrome"
        self.chrome_mode = False        # Chrome 原則現在有沒有套用(結束時要還原)
        self.chrome_session = False     # 這次開啟後有沒有用特殊模式開過 Chrome
        self.hwnd = None
        self.window_info = None
        self.agent = None
        self.style = ttk.Style(self)
        try:
            self.style.theme_use("clam")
        except tk.TclError:
            pass
        self.style.configure("G.Horizontal.TProgressbar", troughcolor=C["line"],
                             background=C["acc"], bordercolor=C["line"],
                             lightcolor=C["acc"], darkcolor=C["acc"],
                             thickness=self.px(6))
        self.style.configure("D.Vertical.TScrollbar", troughcolor=C["bg"],
                             background=C["btn"], bordercolor=C["bg"],
                             arrowcolor=C["muted"], lightcolor=C["btn"],
                             darkcolor=C["btn"])
        self._build_header()
        self.body = tk.Frame(self, bg=C["bg"])
        self.body.pack(fill="both", expand=True)
        self.page = None
        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self.after(50, self._pump)
        self.show(SetupPage)

    def px(self, n):
        return int(round(n * getattr(self, "S", 1.0)))

    # --- 執行緒 -> 畫面 -----------------------------------------------------
    def post(self, fn, *a):
        self.q.put((fn, a))

    def _pump(self):
        try:
            while True:
                fn, a = self.q.get_nowait()
                try:
                    fn(*a)
                except Exception as e:      # 一個錯誤不能讓整個畫面停止更新
                    print("UI 錯誤:", e)
        except queue.Empty:
            pass
        self.after(50, self._pump)

    def bg(self, fn, *a):
        threading.Thread(target=fn, args=a, daemon=True).start()

    # --- 上方步驟列 --------------------------------------------------------
    def _build_header(self):
        bar = tk.Frame(self, bg=C["bar"], height=self.px(46))
        bar.pack(fill="x")
        bar.pack_propagate(False)
        logo = tk.Canvas(bar, width=self.px(20), height=self.px(22), bg=C["bar"],
                         highlightthickness=0)
        s = self.px
        logo.create_rectangle(s(3), s(2), s(17), s(20), outline=C["acc"], width=2)
        logo.create_line(s(7), s(8), s(13), s(8), fill=C["acc"], width=2)
        logo.create_line(s(10), s(8), s(10), s(15), fill=C["acc"], width=2)
        logo.pack(side="left", padx=(s(16), s(8)))
        tk.Label(bar, text=PRODUCT, font=F(11, True), fg=C["text"],
                 bg=C["bar"]).pack(side="left")
        tk.Label(bar, text=f"v{VERSION}", font=F(9), fg=C["muted"],
                 bg=C["bar"]).pack(side="left", padx=(s(8), 0))
        self.steps = tk.Frame(bar, bg=C["bar"])
        self.steps.pack(side="right", padx=s(16))
        self.pills = []
        for i, name in enumerate(STEPS):
            if i:
                tk.Frame(self.steps, width=s(14), height=1,
                         bg=C["line2"]).pack(side="left", padx=s(3))
            lab = tk.Label(self.steps, text=name, font=F(9), padx=s(10),
                           pady=s(3), bg=C["bar"], fg=C["muted"])
            lab.pack(side="left")
            self.pills.append(lab)
        sep(self).pack(fill="x")

    def set_step(self, k):
        if k is None:
            self.steps.pack_forget()
            return
        self.steps.pack(side="right", padx=self.px(16))
        for i, lab in enumerate(self.pills):
            if i == k:
                lab.config(bg=C["acc"], fg=C["ink"], font=F(9, True))
            elif i < k:
                lab.config(bg=C["bar"], fg=C["acc"], font=F(9))
            else:
                lab.config(bg=C["bar"], fg=C["muted"], font=F(9))

    # --- 換頁 ---------------------------------------------------------------
    def show(self, cls, **kw):
        if self.page is not None:
            self.page.leave()
            self.page.destroy()
        self.page = cls(self, **kw)
        self.page.pack(fill="both", expand=True)
        self.page.enter()

    def after_setup(self):
        lc = launcher_cfg()
        if lc.get("agreed_intro", 0) >= INTRO_VERSION:
            self.after_intro()
        else:
            self.show(IntroPage)

    def after_intro(self):
        lc = launcher_cfg()
        if lc.get("remember_platform") and lc.get("platform") in ("steam", "web"):
            self.platform = lc["platform"]
            self.browser = lc.get("browser", "chrome")
            self.show(CapturePage)
        else:
            self.show(PlatformPage)

    def on_close(self):
        if self.agent is not None and self.agent.poll() is None:
            if not ask(self, "提示列還在執行", "關掉這個視窗不會停止提示列。\n"
                       "要停止請按提示列右邊的 [停止]。\n\n仍要關閉這個視窗嗎?",
                       "關閉視窗", "取消"):
                return
        if self.page is not None:
            self.page.leave()
        self.destroy()


def ask(app, title, msg, yes, no):
    """深色的是/否對話框。回傳 True / False。"""
    d = tk.Toplevel(app)
    d.title(title)
    d.configure(bg=C["surf"])
    d.transient(app)
    d.resizable(False, False)
    res = {"v": False}
    tk.Label(d, text=title, font=F(13, True), fg=C["text"], bg=C["surf"],
             anchor="w").pack(fill="x", padx=app.px(24), pady=(app.px(20), app.px(6)))
    tk.Label(d, text=msg, font=F(10), fg=C["sub"], bg=C["surf"], justify="left",
             anchor="w", wraplength=app.px(420)).pack(fill="x", padx=app.px(24))
    row = tk.Frame(d, bg=C["surf"])
    row.pack(fill="x", padx=app.px(24), pady=app.px(20))

    def done(v):
        res["v"] = v
        d.destroy()
    Btn(row, yes, lambda: done(True), "primary").pack(side="right")
    Btn(row, no, lambda: done(False), "ghost").pack(side="right", padx=app.px(8))
    d.update_idletasks()
    x = app.winfo_rootx() + (app.winfo_width() - d.winfo_width()) // 2
    y = app.winfo_rooty() + (app.winfo_height() - d.winfo_height()) // 3
    d.geometry(f"+{x}+{y}")
    d.grab_set()
    app.wait_window(d)
    return res["v"]


class Page(tk.Frame):
    step = None

    def __init__(self, app):
        super().__init__(app.body, bg=C["bg"])
        self.app = app
        self.px = app.px
        self.alive = True

    def enter(self):
        self.app.set_step(self.step)

    def leave(self):
        self.alive = False

    def post(self, fn, *a):
        """背景執行緒用:頁面已經換掉的話就丟掉。"""
        self.app.post(lambda: self.alive and fn(*a))

    def title_block(self, parent, title, sub):
        tk.Label(parent, text=title, font=F(18, True), fg=C["text"], bg=C["bg"],
                 anchor="w").pack(fill="x")
        tk.Label(parent, text=sub, font=F(10), fg=C["muted"], bg=C["bg"],
                 anchor="w", justify="left", wraplength=self.px(860)
                 ).pack(fill="x", pady=(self.px(4), 0))

    def footer(self):
        f = tk.Frame(self, bg=C["bg"], height=self.px(70))
        f.pack(side="bottom", fill="x")
        f.pack_propagate(False)
        sep(self).pack(side="bottom", fill="x")
        inner = tk.Frame(f, bg=C["bg"])
        inner.pack(fill="both", expand=True, padx=self.px(32))
        return inner


# ---------------------------------------------------------------------------
# 1. 安裝
# ---------------------------------------------------------------------------
class Row:
    """安裝清單的一列。"""

    def __init__(self, page, parent, title, last=False):
        px = page.px
        self.page = page
        self.frame = tk.Frame(parent, bg=C["surf"])
        self.frame.pack(fill="x")
        top = tk.Frame(self.frame, bg=C["surf"])
        top.pack(fill="x", padx=px(18), pady=(px(12), px(12)))
        self.icon = StatusIcon(top, px(28), C["surf"])
        self.icon.pack(side="left")
        mid = tk.Frame(top, bg=C["surf"])
        mid.pack(side="left", fill="x", expand=True, padx=(px(16), px(10)))
        self.title = tk.Label(mid, text=title, font=F(11, True), fg=C["text"],
                              bg=C["surf"], anchor="w")
        self.title.pack(fill="x")
        self.detail = tk.Label(mid, text="", font=F(9), fg=C["muted"],
                               bg=C["surf"], anchor="w", justify="left",
                               wraplength=px(560))
        self.detail.pack(fill="x")
        self.right = tk.Frame(top, bg=C["surf"])
        self.right.pack(side="right")
        self.status = tk.Label(self.right, text="", font=F(10), bg=C["surf"],
                               fg=C["muted"])
        self.status.pack(side="right")
        self.prog_box = tk.Frame(self.frame, bg=C["surf"])
        self.prog = ttk.Progressbar(self.prog_box, style="G.Horizontal.TProgressbar",
                                    maximum=100)
        self.prog.pack(fill="x")
        self.prog_text = tk.Label(self.prog_box, text="", font=F(9), fg=C["muted"],
                                  bg=C["surf"], anchor="w")
        self.prog_text.pack(fill="x", pady=(px(4), 0))
        self.buttons = []
        if not last:
            sep(self.frame).pack(fill="x", side="bottom")

    def set(self, state, detail=None, status=None, color=None):
        self.icon.set(state)
        if detail is not None:
            self.detail.config(text=detail)
        if status is not None:
            col = color or {"ok": C["acc"], "warn": C["warn"], "err": C["err"],
                            "run": C["text"]}.get(state, C["muted"])
            self.status.config(text=status, fg=col)
        self.title.config(fg=C["sub"] if state == "wait" else C["text"])

    def progress(self, pct, text=None):
        if not self.prog_box.winfo_ismapped():
            self.prog_box.pack(fill="x", padx=(self.page.px(62), self.page.px(18)),
                               pady=(0, self.page.px(12)))
        if pct is None:
            if self.prog["mode"] != "indeterminate":
                self.prog.config(mode="indeterminate")
                self.prog.start(15)
        else:
            if self.prog["mode"] != "determinate":
                self.prog.stop()
                self.prog.config(mode="determinate")
            self.prog["value"] = pct
        if text is not None:
            self.prog_text.config(text=text[:110])

    def hide_progress(self):
        self.prog.stop()
        self.prog_box.pack_forget()

    def set_buttons(self, specs):
        for b in self.buttons:
            b.destroy()
        self.buttons = []
        if specs:
            self.status.pack_forget()
        else:
            self.status.pack(side="right")
        for text, cmd, kind in reversed(specs or []):
            b = Btn(self.right, text, cmd, kind, size=10, padx=12, pady=5)
            b.pack(side="right", padx=(self.page.px(8), 0))
            self.buttons.append(b)


class SetupPage(Page):
    step = 0

    def __init__(self, app):
        super().__init__(app)
        px = self.px
        self.log_lines = []
        self.procs = []
        self.busy = False
        self.ok = {"sys": False, "env": False, "docker": False, "weights": False,
                   "image": False}
        self.first_pass = True
        content = tk.Frame(self, bg=C["bg"])
        content.pack(fill="both", expand=True, padx=px(36), pady=(px(26), 0))
        self.title_block(content, "第一次使用:準備執行環境",
                         "程式會檢查缺少的項目並自動安裝。全部完成後,之後開啟會直接跳過這一頁。")
        box = tk.Frame(content, bg=C["line"], padx=1, pady=1)
        box.pack(fill="x", pady=(px(18), 0))
        inner = tk.Frame(box, bg=C["surf"])
        inner.pack(fill="x")
        self.r_sys = Row(self, inner, "系統檢查")
        self.r_env = Row(self, inner, "Python 環境與辨識套件")
        self.r_docker = Row(self, inner, "Docker Desktop")
        self.r_weights = Row(self, inner, "Mortal 權重檔")
        self.r_image = Row(self, inner, "建立 Mortal 映像", last=True)
        for r in (self.r_sys, self.r_env, self.r_docker, self.r_weights, self.r_image):
            r.set("wait", "", "等待中")
        self.log = tk.Text(content, height=7, bg=C["bar"], fg=C["muted"],
                           font=(MONO, 9), bd=0, highlightthickness=1,
                           highlightbackground=C["line"], wrap="none")
        foot = self.footer()
        self.log_btn = Btn(foot, "顯示詳細紀錄", self.toggle_log, "ghost", size=10,
                           padx=10)
        self.log_btn.config(highlightthickness=0, fg=C["muted"])
        self.log_btn.pack(side="left")
        self.next = Btn(foot, "下一步", self.app.after_setup, "primary")
        self.next.set_enabled(False)
        self.next.pack(side="right")
        Btn(foot, "稍後再說", self.app.on_close, "ghost").pack(side="right",
                                                          padx=px(10))
        self.hint = tk.Label(foot, text="安裝期間可以關掉視窗,下次開啟會接著做",
                             font=F(9), fg=C["muted"], bg=C["bg"])
        self.hint.pack(side="right", padx=px(10))

    def enter(self):
        super().enter()
        self.app.bg(self.check_all)

    def leave(self):
        super().leave()
        for p in self.procs:
            try:
                p.terminate()
            except Exception:
                pass

    def toggle_log(self):
        if self.log.winfo_ismapped():
            self.log.pack_forget()
            self.log_btn.config(text="顯示詳細紀錄")
        else:
            self.log.pack(fill="both", expand=True, pady=(self.px(12), self.px(8)))
            self.log_btn.config(text="隱藏詳細紀錄")

    def add_log(self, s):
        self.log.insert("end", s + "\n")
        if int(self.log.index("end-1c").split(".")[0]) > 600:
            self.log.delete("1.0", "200.0")
        self.log.see("end")

    def L(self, s):        # 背景執行緒寫紀錄
        self.post(self.add_log, s)

    # --- 檢查(背景) --------------------------------------------------------
    def check_all(self):
        P = self.post
        P(self.r_sys.set, "run", "檢查中…", "")
        gpu = gpu_name()
        self.gpu = gpu
        free, ram = free_gb(), ram_gb()
        parts = [windows_name(),
                 f"NVIDIA {gpu}" if gpu else "沒有偵測到 NVIDIA 顯示卡(CPU 版會跟不上牌局)"]
        if ram:
            parts.append(f"記憶體 {ram} GB")
        if free is not None:
            parts.append(f"剩餘空間 {free} GB(需要約 {NEED_GB} GB)")
        low_disk = free is not None and free < NEED_GB and not self.ok["env"]
        state = "warn" if (low_disk or not gpu) else "ok"
        self.ok["sys"] = True
        P(self.r_sys.set, state, " · ".join(parts),
          "空間不足" if low_disk else ("可以使用" if not gpu else "完成"))
        self.L("系統:" + " · ".join(parts))

        P(self.r_env.set, "run", "檢查已安裝的環境…(第一次約需 10 秒)", "檢查中")
        py, detail = find_env()
        if py:
            self.app.python = py
            self.ok["env"] = True
            P(self.r_env.set, "ok", self.env_detail(py, detail), "完成")
            self.L(f"環境:{py}  {detail}")
            if os.path.isfile(os.path.join(APP, "chrome_policy_backup.json")):
                # 上次用 Chrome 模式沒正常結束:先把 Chrome 設定還原
                run([py, CHROME_MODE, "restore", "--quiet"], timeout=60, cwd=APP)
        else:
            self.start_env_install()      # 先佔 busy,映像要等它裝完才開始
        self.check_docker_side()
        self.after_checks()

    def env_detail(self, py, detail):
        where = "本程式資料夾" if py.lower().startswith(ROOT.lower()) else "conda 環境"
        bits = detail.split()
        ver = f"torch {bits[1]} · {bits[2]}" if len(bits) >= 3 else detail
        return f"{where} · {ver}"

    def check_docker_side(self):
        P = self.post
        P(self.r_weights.set, "run", "", "檢查中")
        if os.path.isfile(WEIGHTS):
            mb = os.path.getsize(WEIGHTS) / 2 ** 20
            self.ok["weights"] = True
            P(self.r_weights.set, "ok", f"app\\Model\\mortal.pth · {mb:.0f} MB",
              "已找到")
            P(self.r_weights.set_buttons, None)
        else:
            self.ok["weights"] = False
            url = str(load_cfg().get("mortal_weights_url") or "").strip()
            if url:
                # 從專案的下載點(例如 GitHub Release)自動取得
                self.start_weights_download(url)
            else:
                P(self.r_weights.set, "err",
                  "找不到 app\\Model\\mortal.pth(約 130 MB)。請從 GitHub 專案的 Releases 頁下載 mortal.pth,放到這個資料夾後按「重新偵測」。",
                  "缺少")
                P(self.r_weights.set_buttons, [
                    ("開啟資料夾", lambda: open_folder(os.path.join(APP, "Model")), "soft"),
                    ("重新偵測", self.recheck, "warn")])

        P(self.r_docker.set, "run", "", "檢查中")
        st = docker_state()
        self.ok["docker"] = st == "ok"
        if st == "ok":
            P(self.r_docker.set, "ok", "Mortal 在 Docker 裡執行(本機,不上傳資料)",
              "執行中")
            P(self.r_docker.set_buttons, None)
        elif st == "stopped":
            P(self.r_docker.set, "warn",
              "已安裝但沒有開。開啟後等左下角出現「Engine running」(綠色)。", "沒有開")
            P(self.r_docker.set_buttons, [
                ("開啟 Docker", self.open_docker, "soft"),
                ("重新偵測", self.recheck, "warn")])
        else:
            P(self.r_docker.set, "warn",
              "Mortal 在 Docker 裡執行。Docker 需要系統管理員權限與重新開機,無法自動完成。",
              "需要你處理")
            P(self.r_docker.set_buttons, [
                ("安裝步驟", self.docker_steps, "soft"),
                ("重新偵測", self.recheck, "warn")])
        self.L(f"Docker:{st}")

        if not (self.ok["docker"] and self.ok["weights"]):
            self.ok["image"] = False
            P(self.r_image.set, "wait",
              "Docker 與權重檔準備好後自動開始 · 約 10–30 分鐘,需要網路", "等待中")
            return
        P(self.r_image.set, "run", "", "檢查中")
        if image_ok():
            self.ok["image"] = True
            P(self.r_image.set, "ok", "映像 mortal 已存在", "完成")
        else:
            self.ok["image"] = False
            self.start_image_build()

    def after_checks(self):
        def ui():
            done = all(self.ok.values())
            self.next.set_enabled(done)
            if done:
                self.hint.config(text="全部準備好了")
                if self.first_pass and not self.busy:
                    self.app.after(500, lambda: self.alive and self.app.after_setup())
            self.first_pass = False
        self.post(ui)

    def recheck(self):
        if self.busy:
            return
        self.first_pass = False

        def job():
            self.check_docker_side()
            if not self.ok["env"] and not self.busy:
                self.start_env_install()
            self.after_checks()
        self.app.bg(job)

    # --- 安裝(背景) --------------------------------------------------------
    def start_env_install(self):
        if self.busy:
            return
        self.busy = True
        gpu = bool(getattr(self, "gpu", None))
        what = ("偵測到 NVIDIA 顯示卡,安裝 GPU 版" if gpu else "沒有 NVIDIA 顯示卡,安裝 CPU 版") \
            + "(torch 2.5.1 · ultralytics 8.4.127,版本鎖定)"
        self.post(self.r_env.set, "run", what, "")
        self.post(self.r_env.set_buttons, None)

        def job():
            def report(pct, text):
                self.L(text)
                self.post(self.r_env.progress, pct, text)
                if pct is not None:
                    self.post(self.r_env.set, "run", None, f"{int(pct)}%",
                              C["text"])
            try:
                py, detail = install_env(gpu, report, self.procs)
                self.app.python = py
                self.ok["env"] = True
                self.post(self.r_env.hide_progress)
                self.post(self.r_env.set, "ok", self.env_detail(py, detail), "完成")
            except Exception as e:
                self.L(f"[錯誤] {e}")
                self.post(self.r_env.set, "err", str(e), "")
                self.post(self.r_env.set_buttons, [("重試", self.retry_env, "warn")])
            finally:
                self.busy = False
            if self.ok["env"] and self.ok["docker"] and self.ok["weights"] \
                    and not self.ok["image"]:
                self.start_image_build()
            self.after_checks()
        self.app.bg(job)

    def start_weights_download(self, url):
        if getattr(self, "dl_weights", False):
            return
        self.dl_weights = True
        self.post(self.r_weights.set, "run", "下載 mortal.pth(約 130 MB)…", "")
        self.post(self.r_weights.set_buttons, None)

        def job():
            try:
                os.makedirs(os.path.dirname(WEIGHTS), exist_ok=True)

                def prog(got, total):
                    pct = 100.0 * got / total if total else None
                    self.post(self.r_weights.progress, pct,
                              f"下載 mortal.pth… {got / 2 ** 20:.0f} MB")
                    if pct is not None:
                        self.post(self.r_weights.set, "run", None, f"{int(pct)}%", C["text"])
                self.L(f"下載權重:{url}")
                download(url, WEIGHTS, prog)
                mb = os.path.getsize(WEIGHTS) / 2 ** 20
                if mb < 50:
                    os.remove(WEIGHTS)
                    raise RuntimeError(f"下載到的檔案只有 {mb:.1f} MB,網址可能不對")
                self.ok["weights"] = True
                self.post(self.r_weights.hide_progress)
                self.post(self.r_weights.set, "ok", f"app\\Model\\mortal.pth · {mb:.0f} MB",
                          "完成")
            except Exception as e:
                self.L(f"[錯誤] 權重下載失敗:{e}")
                self.post(self.r_weights.hide_progress)
                self.post(self.r_weights.set, "err",
                          f"下載失敗({e})。請確認網路後重試,或手動放到 app\\Model\\mortal.pth",
                          "")
                self.post(self.r_weights.set_buttons, [
                    ("開啟資料夾", lambda: open_folder(os.path.join(APP, "Model")), "soft"),
                    ("重試", self.recheck, "warn")])
            finally:
                self.dl_weights = False
            if self.ok["weights"]:
                self.check_docker_side()      # 權重到了:接著檢查 Docker、建映像
            self.after_checks()
        self.app.bg(job)

    def retry_env(self):
        self.post(self.r_env.set_buttons, None)
        self.app.bg(self.start_env_install)

    def start_image_build(self):
        if getattr(self, "building", False):
            return
        if self.busy:          # 先讓 Python 環境裝完(兩個一起下載會互搶頻寬)
            self.post(self.r_image.set, "wait", "等 Python 環境裝完後開始", "等待中")
            return
        self.building = True
        self.post(self.r_image.set, "run",
                  "第一次需要下載與編譯,約 10–30 分鐘,需要網路", "")

        def job():
            def report(pct, text):
                self.L(text)
                self.post(self.r_image.progress, pct, text)
                if pct is not None:
                    self.post(self.r_image.set, "run", None, f"{int(pct)}%", C["text"])
            try:
                build_image(report, self.procs)
                self.ok["image"] = True
                self.post(self.r_image.hide_progress)
                self.post(self.r_image.set, "ok", "映像 mortal 建好了", "完成")
            except Exception as e:
                self.L(f"[錯誤] {e}")
                self.post(self.r_image.set, "err", str(e), "")
                self.post(self.r_image.set_buttons,
                          [("重試", self.retry_image, "warn")])
            finally:
                self.building = False
            self.after_checks()
        self.app.bg(job)

    def retry_image(self):
        self.r_image.set_buttons(None)
        self.app.bg(self.start_image_build)

    # --- Docker 按鈕 -------------------------------------------------------
    def open_docker(self):
        try:
            os.startfile(DOCKER_DESKTOP)
        except Exception:
            self.add_log("找不到 Docker Desktop.exe,請從開始功能表開啟")

    def docker_steps(self):
        DockerSteps(self.app, self.recheck)


class DockerSteps(tk.Toplevel):
    def __init__(self, app, on_done):
        super().__init__(app)
        px = app.px
        self.title("安裝 Docker Desktop")
        self.configure(bg=C["surf"])
        self.transient(app)
        self.resizable(False, False)
        pad = dict(padx=px(28))
        tk.Label(self, text="安裝 Docker Desktop(只要做一次)", font=F(14, True),
                 fg=C["text"], bg=C["surf"], anchor="w").pack(fill="x", pady=(px(22), px(12)), **pad)
        steps = [
            ("開啟 WSL2", "在「開始」搜尋 PowerShell → 右鍵「以系統管理員身分執行」→ 輸入\n"
                         "wsl --install    完成後重新開機。"),
            ("安裝 Docker Desktop", "按下面的按鈕下載安裝。安裝時保持勾選「Use WSL 2 instead of Hyper-V」,"
                                    "裝完重新開機。"),
            ("打開 Docker Desktop", "同意條款(可以略過登入),等左下角顯示綠色的「Engine running」。\n"
                                    "建議在 Settings → General 勾選「Start Docker Desktop when you sign in」。"),
            ("回到這裡", "按「重新偵測」,程式會自動建立 Mortal 映像。"),
        ]
        for i, (t, d) in enumerate(steps, 1):
            row = tk.Frame(self, bg=C["surf"])
            row.pack(fill="x", pady=px(6), **pad)
            tk.Label(row, text=str(i), font=F(10, True), fg=C["ink"], bg=C["acc"],
                     width=2).pack(side="left", anchor="n", pady=px(2))
            col = tk.Frame(row, bg=C["surf"])
            col.pack(side="left", fill="x", padx=(px(12), 0))
            tk.Label(col, text=t, font=F(11, True), fg=C["text"], bg=C["surf"],
                     anchor="w").pack(fill="x")
            tk.Label(col, text=d, font=F(10), fg=C["sub"], bg=C["surf"], anchor="w",
                     justify="left", wraplength=px(520)).pack(fill="x")
        btns = tk.Frame(self, bg=C["surf"])
        btns.pack(fill="x", pady=px(22), **pad)

        def done():
            self.destroy()
            on_done()
        Btn(btns, "重新偵測", done, "primary").pack(side="right")
        Btn(btns, "開啟下載頁", lambda: webbrowser.open(DOCKER_DOC), "soft"
            ).pack(side="right", padx=px(8))
        if shutil.which("winget"):
            Btn(btns, "用 winget 安裝", self.winget, "ghost").pack(side="left")
        self.update_idletasks()
        x = app.winfo_rootx() + (app.winfo_width() - self.winfo_width()) // 2
        y = app.winfo_rooty() + px(60)
        self.geometry(f"+{x}+{y}")

    def winget(self):
        # 會跳出系統管理員權限確認;開一個看得見的視窗讓使用者看進度
        subprocess.Popen(["cmd", "/c", "winget install -e --id Docker.DockerDesktop "
                          "--accept-package-agreements --accept-source-agreements "
                          "& echo. & echo 安裝完成後請重新開機 & pause"],
                         creationflags=0x00000010)   # CREATE_NEW_CONSOLE


# ---------------------------------------------------------------------------
# 2. 說明與免責聲明
# ---------------------------------------------------------------------------
INTRO = [
    ("use", "這個工具做什麼", [
        ("p", "讀取雀魂的遊戲畫面、辨識目前牌局,交給麻將 AI「Mortal」推算,再把建議顯示在螢幕上:"
              "最上方的提示列寫出該打哪張、要不要鳴牌,手牌上方用數字徽章指出那張牌。"),
        ("p", "它只看畫面:不會點擊或操作遊戲、不會自動出牌,也不讀取或修改遊戲的記憶體、檔案或網路封包。"),
    ]),
    ("how", "運作方式", [
        ("step", "1 擷取    每秒約 20–30 次擷取你選的遊戲視窗"),
        ("step", "2 辨識    影像模型讀出手牌、牌河、鳴牌、寶牌與點數"),
        ("step", "3 推算    Mortal 在本機 Docker 裡算出建議"),
        ("step", "4 提示    提示列與手牌徽章顯示在最上層"),
        ("muted", "辨識結果前後矛盾時,該局會暫停建議,下一局自動恢復。"),
    ]),
    ("risk", "可能的風險", [
        ("warnhead", "帳號可能被停權"),
        ("warnbody", "遊戲的服務條款通常禁止使用第三方輔助工具。即使本工具不修改遊戲,在對局中使用"
                     "仍可能被視為違規並導致帳號處分。建議只用於練習、友人場或賽後研究。"),
        ("li", "辨識可能讀錯牌,這時建議也會跟著錯。"),
        ("li", "沒有 NVIDIA 顯示卡時速度太慢,可能跟不上牌局。"),
        ("li", "防毒軟體可能把主程式誤判為可疑(未簽章的程式常見)。"),
    ]),
    ("perm", "需要的權限", [
        ("kv", "擷取視窗畫面", "只擷取你在第 4 步選的那一個視窗"),
        ("kv", "最上層顯示", "提示列與手牌徽章蓋在遊戲上方;徽章不攔截滑鼠點擊"),
        ("kv", "全域快捷鍵", "F7 次選 / F8 縮小提示列 / F9 手牌徽章(可在 config.json 更改)"),
        ("kv", "Docker", "在本機執行 Mortal,牌局資料不會上傳"),
        ("kv", "Chrome 設定(網頁版)", "暫時讓雀魂分頁在背景不被暫停,結束時自動還原"),
        ("kv", "寫入紀錄資料夾", "錄影與牌譜存在本機「紀錄」,可在 config.json 關閉錄影"),
    ]),
    ("legal", "免責聲明", [
        ("p", "本工具為個人研究用途,與雀魂官方及其營運商無任何關聯。AI 建議不保證正確,也不保證勝率。"
              "使用本工具所造成的任何帳號處分、遊戲內損失或其他後果,由使用者自行承擔。"),
    ]),
]


class IntroPage(Page):
    step = 1

    def __init__(self, app):
        super().__init__(app)
        px = self.px
        foot = self.footer()
        self.agree = tk.IntVar(value=0)
        cb = tk.Checkbutton(foot, text="  我已閱讀並了解上述風險與免責聲明",
                            variable=self.agree, command=self.on_agree, font=F(10),
                            fg=C["text"], bg=C["bg"], activebackground=C["bg"],
                            activeforeground=C["text"], selectcolor=C["surf"],
                            bd=0, highlightthickness=0, cursor="hand2")
        cb.pack(side="left")
        self.next = Btn(foot, "同意並繼續", self.go, "primary")
        self.next.set_enabled(False)
        self.next.pack(side="right")

        main = tk.Frame(self, bg=C["bg"])
        main.pack(fill="both", expand=True)
        nav = tk.Frame(main, bg=C["bg"], width=px(200))
        nav.pack(side="left", fill="y")
        nav.pack_propagate(False)
        tk.Frame(main, width=1, bg=C["line"]).pack(side="left", fill="y")
        self.navs = {}
        tk.Frame(nav, height=px(20), bg=C["bg"]).pack()
        for key, title, _ in INTRO:
            b = tk.Label(nav, text=title, font=F(10), fg=C["sub"], bg=C["bg"],
                         anchor="w", padx=px(14), pady=px(8), cursor="hand2")
            b.pack(fill="x", padx=px(12), pady=1)
            b.bind("<Button-1>", lambda e, k=key: self.jump(k))
            self.navs[key] = b

        wrap = tk.Frame(main, bg=C["bg"])
        wrap.pack(side="left", fill="both", expand=True)
        t = self.text = tk.Text(wrap, bg=C["bg"], fg=C["sub"], font=F(10), bd=0,
                                highlightthickness=0, wrap="word", padx=px(30),
                                pady=px(20), spacing1=px(2), spacing3=px(6),
                                cursor="arrow")
        sb = ttk.Scrollbar(wrap, orient="vertical", command=t.yview,
                           style="D.Vertical.TScrollbar")
        t.config(yscrollcommand=lambda a, b: (sb.set(a, b), self.on_scroll()))
        sb.pack(side="right", fill="y")
        t.pack(side="left", fill="both", expand=True)
        t.tag_config("h", font=F(14, True), foreground=C["text"],
                     spacing1=px(14), spacing3=px(8))
        t.tag_config("p", foreground=C["sub"])
        t.tag_config("muted", foreground=C["muted"], font=F(9))
        t.tag_config("step", foreground=C["text"], background=C["surf"],
                     lmargin1=px(4), lmargin2=px(4), spacing1=px(4), spacing3=px(4))
        t.tag_config("warnhead", foreground=C["warn"], font=F(10, True),
                     background=C["warnbg"], lmargin1=px(12), lmargin2=px(12),
                     spacing1=px(8))
        t.tag_config("warnbody", foreground="#e4d3bd", background=C["warnbg"],
                     lmargin1=px(12), lmargin2=px(12), rmargin=px(12),
                     spacing3=px(10))
        t.tag_config("li", lmargin1=px(8), lmargin2=px(22))
        t.tag_config("k", font=F(10, True), foreground=C["text"])
        t.tag_config("v", foreground=C["sub"])
        for key, title, items in INTRO:
            t.mark_set(key, "end-1c")
            t.mark_gravity(key, "left")
            t.insert("end", title + "\n", "h")
            for it in items:
                kind = it[0]
                if kind == "li":
                    t.insert("end", "•  " + it[1] + "\n", ("li",))
                elif kind == "kv":
                    t.insert("end", it[1] + "    ", ("k", "li"))
                    t.insert("end", it[2] + "\n", ("v", "li"))
                else:
                    t.insert("end", it[1] + "\n", (kind,))
        t.insert("end", "\n" * 6)
        t.config(state="disabled")
        self.set_nav("use")

    def jump(self, key):
        self.text.yview(key)
        self.set_nav(key)

    def set_nav(self, key):
        for k, b in self.navs.items():
            b.config(bg=C["surf2"] if k == key else C["bg"],
                     fg=C["text"] if k == key else C["sub"])

    def on_scroll(self):
        try:
            top = self.text.index("@0,0")
            cur = INTRO[0][0]
            for key, _, _ in INTRO:
                if self.text.compare(key, "<=", top + "+2l"):
                    cur = key
            self.set_nav(cur)
        except tk.TclError:
            pass

    def on_agree(self):
        self.next.set_enabled(self.agree.get())

    def go(self):
        save_launcher_cfg(agreed_intro=INTRO_VERSION,
                          agreed_at=time.strftime("%Y-%m-%d %H:%M:%S"))
        self.app.after_intro()


# ---------------------------------------------------------------------------
# 3. 選擇平台
# ---------------------------------------------------------------------------
class PlatformPage(Page):
    step = 2

    def __init__(self, app):
        super().__init__(app)
        px = self.px
        lc = launcher_cfg()
        self.pick = app.platform or lc.get("platform") or "steam"
        self.browser = lc.get("browser", "chrome")
        foot = self.footer()
        self.remember = tk.IntVar(value=1 if lc.get("remember_platform", True) else 0)
        tk.Checkbutton(foot, text="  記住我的選擇(下次直接到畫面確認)",
                       variable=self.remember, font=F(10), fg=C["sub"], bg=C["bg"],
                       activebackground=C["bg"], activeforeground=C["text"],
                       selectcolor=C["surf"], bd=0, highlightthickness=0
                       ).pack(side="left")
        self.next = Btn(foot, "下一步", self.go, "primary")
        self.next.pack(side="right")
        Btn(foot, "上一步", lambda: self.app.show(IntroPage), "ghost"
            ).pack(side="right", padx=px(10))

        content = tk.Frame(self, bg=C["bg"])
        content.pack(fill="both", expand=True, padx=px(36), pady=(px(26), 0))
        self.title_block(content, "你在哪裡玩雀魂?",
                         "選好之後,下一步會找到對應的視窗並讓你確認畫面。之後可以再回來更改。")
        grid = tk.Frame(content, bg=C["bg"])
        grid.pack(fill="x", pady=(px(18), 0))
        grid.columnconfigure(0, weight=1, uniform="c")
        grid.columnconfigure(1, weight=1, uniform="c")
        self.cards = {}
        self.cards["steam"] = self.card(grid, 0, "steam", "Steam / 桌面版", "獨立的遊戲視窗",
                                        "自動尋找雀魂的遊戲視窗。最穩定的方式,建議優先使用。",
                                        ["視窗比例 16:9(例如 1920×1080、1600×900)",
                                         "可以被其他視窗蓋住,但不能最小化"])
        self.cards["web"] = self.card(grid, 1, "web", "網頁版", "在瀏覽器裡玩",
                                      "建議用 Chrome。程式會以特殊模式開啟,讓雀魂分頁在背景也持續更新。",
                                      ["遊戲畫面寬度至少 1100 像素(最大化最穩)",
                                       "Chrome 模式需要先關閉所有 Chrome 視窗"])
        # 網頁版的提示
        self.tip = tk.Frame(content, bg=C["accbg"], highlightthickness=1,
                            highlightbackground=C["accline"])
        tl = tk.Frame(self.tip, bg=C["accbg"])
        tl.pack(side="left", fill="both", expand=True, padx=px(16), pady=px(14))
        tk.Label(tl, text="建議:把雀魂分頁拖出來,變成獨立視窗", font=F(11, True),
                 fg=C["text"], bg=C["accbg"], anchor="w").pack(fill="x")
        tk.Label(tl, text="在分頁標籤上按住滑鼠往外拖,放開後就是單獨的視窗。這樣切到其他分頁時"
                          "遊戲畫面不會中斷,程式也比較容易找到它。",
                 font=F(10), fg="#c9e6db", bg=C["accbg"], anchor="w", justify="left",
                 wraplength=px(520)).pack(fill="x", pady=(px(4), 0))
        tr = tk.Frame(self.tip, bg=C["accbg"])
        tr.pack(side="right", padx=px(16), pady=px(14), anchor="n")
        tk.Label(tr, text="瀏覽器", font=F(9), fg=C["muted"], bg=C["accbg"],
                 anchor="w").pack(fill="x")
        self.bbtns = {}
        brow = tk.Frame(tr, bg=C["accbg"])
        brow.pack(pady=(px(4), 0))
        for key, label in (("chrome", "Chrome(建議)"), ("other", "其他瀏覽器")):
            b = tk.Label(brow, text=label, font=F(10), padx=px(12), pady=px(5),
                         cursor="hand2", highlightthickness=1)
            b.pack(side="left", padx=(0, px(6)))
            b.bind("<Button-1>", lambda e, k=key: self.set_browser(k))
            self.bbtns[key] = b
        self.bnote = tk.Label(tr, text="", font=F(9), fg=C["muted"], bg=C["accbg"],
                              justify="left", wraplength=px(230), anchor="w")
        self.bnote.pack(fill="x", pady=(px(6), 0))
        self.refresh()

    def card(self, grid, col, key, title, sub, desc, bullets):
        px = self.px
        f = tk.Frame(grid, bg=C["surf"], highlightthickness=2, cursor="hand2")
        f.grid(row=0, column=col, sticky="nsew", padx=(0 if col == 0 else px(8),
                                                       px(8) if col == 0 else 0))
        top = tk.Frame(f, bg=C["surf"])
        top.pack(fill="x", padx=px(20), pady=(px(18), px(10)))
        ic = tk.Canvas(top, width=px(44), height=px(44), bg=C["btn"],
                       highlightthickness=0)
        s = px
        if key == "steam":
            ic.create_rectangle(s(10), s(11), s(34), s(28), outline=C["text"], width=2)
            ic.create_line(s(16), s(34), s(28), s(34), fill=C["text"], width=2)
            ic.create_line(s(22), s(28), s(22), s(34), fill=C["text"], width=2)
        else:
            ic.create_oval(s(10), s(10), s(34), s(34), outline=C["text"], width=2)
            ic.create_line(s(10), s(22), s(34), s(22), fill=C["text"], width=2)
            ic.create_oval(s(16), s(10), s(28), s(34), outline=C["text"], width=2)
        ic.pack(side="left")
        tt = tk.Frame(top, bg=C["surf"])
        tt.pack(side="left", padx=px(12))
        tk.Label(tt, text=title, font=F(13, True), fg=C["text"], bg=C["surf"],
                 anchor="w").pack(fill="x")
        tk.Label(tt, text=sub, font=F(9), fg=C["muted"], bg=C["surf"],
                 anchor="w").pack(fill="x")
        chk = tk.Label(top, text="✓", font=F(11, True), fg=C["ink"], width=2,
                       bg=C["acc"])
        tk.Label(f, text=desc, font=F(10), fg=C["sub"], bg=C["surf"], anchor="w",
                 justify="left", wraplength=px(390)).pack(fill="x", padx=px(20))
        for b in bullets:
            tk.Label(f, text="·  " + b, font=F(9), fg=C["muted"], bg=C["surf"],
                     anchor="w").pack(fill="x", padx=px(20))
        tk.Frame(f, height=px(16), bg=C["surf"]).pack()

        def click(e):
            self.pick = key
            self.refresh()

        def bind_all(w):
            w.bind("<Button-1>", click)
            for c in w.winfo_children():
                bind_all(c)
        bind_all(f)
        chk.bind("<Button-1>", click)
        return f, chk

    def set_browser(self, k):
        self.browser = k
        self.refresh()

    def refresh(self):
        for key, (f, chk) in self.cards.items():
            on = key == self.pick
            bg = C["accbg"] if on else C["surf"]
            f.config(highlightbackground=C["acc"] if on else C["line"],
                     highlightcolor=C["acc"] if on else C["line"])

            def paint(w):
                if w is not chk and w.winfo_class() in ("Frame", "Label"):
                    w.config(bg=bg)
                for c in w.winfo_children():
                    paint(c)
            paint(f)
            if on:
                chk.pack(side="right")
            else:
                chk.pack_forget()
        for k, b in self.bbtns.items():
            on = k == self.browser
            b.config(bg=C["acc"] if on else C["surf"], fg=C["ink"] if on else C["text"],
                     highlightbackground=C["acc"] if on else C["line2"])
        self.bnote.config(text="下一步會先關閉 Chrome,再用特殊模式開啟雀魂。"
                          if self.browser == "chrome" else
                          "只擷取畫面,不調整瀏覽器設定。視窗被蓋住時畫面可能停住。")
        if self.pick == "web":
            self.tip.pack(fill="x", pady=(self.px(16), 0))
        else:
            self.tip.pack_forget()
        self.next.config(text="下一步:尋找遊戲視窗" if self.pick == "steam" or
                         self.browser != "chrome" else "下一步:開啟 Chrome")

    def go(self):
        self.app.platform = self.pick
        self.app.browser = self.browser
        save_launcher_cfg(platform=self.pick, browser=self.browser,
                          remember_platform=bool(self.remember.get()))
        self.app.show(CapturePage)


# ---------------------------------------------------------------------------
# 4. 擷取權限與畫面確認
# ---------------------------------------------------------------------------
class CapturePage(Page):
    step = 3

    def __init__(self, app):
        super().__init__(app)
        px = self.px
        self.watch = None
        self.watch_dir = os.path.join(RUNTIME, "preview")
        self.img = None
        self.img_mtime = 0
        self.wins = []
        self.show_all = tk.IntVar(value=0)
        self.sel = tk.IntVar(value=0)
        self.status = {}
        self.consent = bool(launcher_cfg().get("capture_consent"))

        foot = self.footer()
        Btn(foot, "上一步", self.back, "ghost").pack(side="left")
        self.go_btn = Btn(foot, "畫面正確,開始使用", self.start, "primary")
        self.go_btn.set_enabled(False)
        self.go_btn.pack(side="right")
        Btn(foot, "畫面不對,重新選擇", self.redo, "ghost").pack(side="right",
                                                          padx=px(10))

        content = tk.Frame(self, bg=C["bg"])
        content.pack(fill="both", expand=True, padx=px(32), pady=(px(22), 0))
        left = tk.Frame(content, bg=C["bg"], width=px(300))
        left.pack(side="left", fill="y")
        left.pack_propagate(False)
        right = tk.Frame(content, bg=C["bg"])
        right.pack(side="left", fill="both", expand=True, padx=(px(24), 0))

        tk.Label(left, text="確認擷取的畫面", font=F(16, True), fg=C["text"],
                 bg=C["bg"], anchor="w").pack(fill="x")
        tk.Label(left, text="右邊是程式實際看到的畫面。要剛好是整個遊戲畫面,"
                            "不能有網址列或黑邊。",
                 font=F(9), fg=C["muted"], bg=C["bg"], anchor="w", justify="left",
                 wraplength=px(290)).pack(fill="x", pady=(px(4), px(12)))
        # 權限卡
        self.perm = tk.Frame(left, highlightthickness=1)
        self.perm.pack(fill="x")
        self.perm_text = tk.Label(self.perm, font=F(10), anchor="w", justify="left",
                                  wraplength=px(200))
        self.perm_text.pack(side="left", fill="x", expand=True, padx=px(12),
                            pady=px(10))
        self.perm_btn = None
        # Chrome 準備
        self.chrome_box = tk.Frame(left, bg=C["warnbg"], highlightthickness=1,
                                   highlightbackground=C["warnline"])
        self.chrome_text = tk.Label(self.chrome_box, font=F(10), fg="#e4d3bd",
                                    bg=C["warnbg"], anchor="w", justify="left",
                                    wraplength=px(290))
        self.chrome_text.pack(fill="x", padx=px(12), pady=(px(10), px(6)))
        self.chrome_btns = tk.Frame(self.chrome_box, bg=C["warnbg"])
        self.chrome_btns.pack(fill="x", padx=px(12), pady=(0, px(10)))
        # 視窗清單
        hdr = tk.Frame(left, bg=C["bg"])
        hdr.pack(fill="x", pady=(px(14), px(4)))
        tk.Label(hdr, text="找到的視窗", font=F(9), fg=C["muted"], bg=C["bg"]
                 ).pack(side="left")
        tk.Checkbutton(hdr, text="顯示全部", variable=self.show_all,
                       command=self.fill_list, font=F(9), fg=C["muted"], bg=C["bg"],
                       activebackground=C["bg"], selectcolor=C["surf"], bd=0,
                       highlightthickness=0).pack(side="right")
        # ★ 視窗多的時候要能捲動(2026-11-12 實測:第 6 個以後看不到、滾輪沒反應)
        # 檢查項目固定在左欄最下面,清單吃掉中間剩下的高度
        self.checks = tk.Frame(left, bg=C["bg"])
        self.checks.pack(side="bottom", fill="x", pady=(px(10), px(4)))
        lb = tk.Frame(left, bg=C["bg"])
        lb.pack(fill="both", expand=True)
        self.list_canvas = tk.Canvas(lb, bg=C["bg"], highlightthickness=0, bd=0)
        self.list_sb = ttk.Scrollbar(lb, orient="vertical",
                                     command=self.list_canvas.yview,
                                     style="D.Vertical.TScrollbar")
        self.list_canvas.config(yscrollcommand=self.list_sb.set)
        self.list_sb.pack(side="right", fill="y")
        self.list_canvas.pack(side="left", fill="both", expand=True)
        self.listbox = tk.Frame(self.list_canvas, bg=C["bg"])
        self._list_win = self.list_canvas.create_window(0, 0, window=self.listbox,
                                                        anchor="nw")
        self.listbox.bind("<Configure>", lambda e: self.list_canvas.config(
            scrollregion=self.list_canvas.bbox("all")))
        self.list_canvas.bind("<Configure>", lambda e: self.list_canvas.itemconfig(
            self._list_win, width=e.width))
        # 滑鼠在清單上才捲清單
        lb.bind("<Enter>", lambda e: self.bind_all("<MouseWheel>", self._wheel))
        lb.bind("<Leave>", lambda e: self.unbind_all("<MouseWheel>"))
        self.check_rows = {}
        for key in ("found", "aspect", "width", "live"):
            r = tk.Frame(self.checks, bg=C["bg"])
            r.pack(fill="x", pady=px(2))
            ic = StatusIcon(r, px(16), C["bg"])
            ic.pack(side="left")
            lab = tk.Label(r, text="", font=F(9), fg=C["sub"], bg=C["bg"],
                           anchor="w", justify="left", wraplength=px(290))
            lab.pack(side="left", padx=(px(8), 0), fill="x")
            self.check_rows[key] = (ic, lab)

        # 預覽
        self.pw, self.ph = px(536), px(302)
        self.canvas = tk.Canvas(right, width=self.pw, height=self.ph, bg="#0a0b0d",
                                highlightthickness=1, highlightbackground=C["line"])
        self.canvas.pack(anchor="nw")
        self.cap_text = tk.Label(right, text="", font=F(9), fg=C["muted"], bg=C["bg"],
                                 anchor="w", justify="left", wraplength=self.pw)
        self.cap_text.pack(fill="x", pady=(px(8), 0))
        self.placeholder("選好視窗並允許擷取後,這裡會顯示即時畫面")

    # --- 狀態 --------------------------------------------------------------
    def enter(self):
        super().enter()
        self.paint_perm()
        self.set_checks()
        if not self.app.python:
            self.app.show(SetupPage)
            return
        if self.app.platform == "web" and self.app.browser == "chrome" \
                and not self.app.chrome_mode:
            if self.app.chrome_session:
                self.reapply_chrome()       # 再開一場:Chrome 還開著特殊模式
            else:
                self.prepare_chrome()
        else:
            self.refresh_windows()

    def leave(self):
        super().leave()
        self.stop_watch()

    def back(self):
        self.app.show(PlatformPage)

    def placeholder(self, text):
        self.canvas.delete("all")
        self.canvas.create_text(self.pw // 2, self.ph // 2, text=text,
                                fill=C["muted"], font=F(10))

    def paint_perm(self):
        px = self.px
        if self.perm_btn is not None:
            self.perm_btn.destroy()
        if self.consent:
            bg, line = C["accbg"], C["accline"]
            self.perm_text.config(text="已允許擷取你選的這一個視窗\n(只擷取畫面,不操作遊戲)",
                                  fg=C["text"])
            self.perm_btn = Btn(self.perm, "撤銷", self.revoke, "soft", size=9,
                                padx=10, pady=4)
        else:
            bg, line = C["warnbg"], C["warnline"]
            self.perm_text.config(text="需要你的允許:\n擷取所選視窗的畫面", fg="#e4d3bd")
            self.perm_btn = Btn(self.perm, "允許擷取", self.allow, "warn", size=10,
                                padx=12, pady=5)
        self.perm.config(bg=bg, highlightbackground=line)
        self.perm_text.config(bg=bg)
        self.perm_btn.pack(side="right", padx=px(10))

    def allow(self):
        self.consent = True
        save_launcher_cfg(capture_consent=True)
        self.paint_perm()
        self.start_watch()

    def revoke(self):
        self.consent = False
        save_launcher_cfg(capture_consent=False)
        self.stop_watch()
        self.paint_perm()
        self.placeholder("已撤銷擷取權限")
        self.status = {}
        self.set_checks()

    # --- Chrome ------------------------------------------------------------
    def prepare_chrome(self, force_close=False):
        px = self.px
        self.chrome_box.pack(fill="x", pady=(px(12), 0), after=self.perm)
        for w in self.chrome_btns.winfo_children():
            w.destroy()
        self.chrome_text.config(text="正在準備 Chrome…")

        def job():
            if force_close:
                run(["taskkill", "/IM", "chrome.exe"], timeout=20)
                for _ in range(20):
                    if not chrome_running():
                        break
                    time.sleep(0.5)
            if chrome_running():
                self.post(self.chrome_ask)
                return
            rc, out = run([self.app.python, CHROME_MODE, "prepare", "--no-prompt"],
                          timeout=60, cwd=APP)
            self.post(self.chrome_done, rc, out)
        self.app.bg(job)

    def reapply_chrome(self):
        def job():
            if chrome_running():
                rc, _ = run([self.app.python, CHROME_MODE, "apply"], timeout=60,
                            cwd=APP)
                if rc == 0:
                    self.app.chrome_mode = True
                    self.post(self.refresh_windows)
                    return
            self.app.chrome_session = False
            self.post(self.prepare_chrome)
        self.app.bg(job)

    def chrome_ask(self):
        px = self.px
        self.chrome_text.config(text="Chrome 現在開著。要用特殊模式重新開啟,必須先把 Chrome "
                                     "全部關掉(分頁會照常保存,下次可以還原)。")
        Btn(self.chrome_btns, "幫我關閉 Chrome", lambda: self.prepare_chrome(True),
            "warn", size=10, padx=12, pady=5).pack(side="left")
        Btn(self.chrome_btns, "我關好了", self.prepare_chrome, "ghost", size=10,
            padx=12, pady=5).pack(side="left", padx=px(8))

    def chrome_done(self, rc, out):
        for w in self.chrome_btns.winfo_children():
            w.destroy()
        if rc == 0:
            self.app.chrome_mode = True
            self.app.chrome_session = True
            self.chrome_box.pack_forget()
            self.cap_text.config(text="已開啟 Chrome。登入雀魂後,建議把分頁拖出成獨立視窗,"
                                      "再按「畫面不對,重新選擇」重新整理清單。")
            self.app.after(2500, lambda: self.alive and self.refresh_windows())
        else:
            last = [x for x in out.splitlines() if x.strip()][-2:]
            self.chrome_text.config(text="沒辦法準備 Chrome:" + " ".join(last))
            Btn(self.chrome_btns, "重試", self.prepare_chrome, "warn", size=10,
                padx=12, pady=5).pack(side="left")

    # --- 視窗清單 -----------------------------------------------------------
    def refresh_windows(self):
        self.status = {}
        self.set_checks()

        def job():
            rc, out = run([self.app.python, HELPER, "windows", "--exclude-pid",
                           str(os.getpid())], timeout=60, cwd=APP)
            try:
                data = json.loads(out.strip().splitlines()[-1])
            except Exception:
                data = {"windows": [], "suggest": None,
                        "why": "列視窗失敗:" + out.strip()[-200:]}
            self.post(self.got_windows, data)
        self.app.bg(job)

    def got_windows(self, data):
        self.wins = data.get("windows") or []
        sug = data.get("suggest")
        cur = self.sel.get()
        if not any(w["hwnd"] == cur for w in self.wins):
            self.sel.set(sug or 0)
        self.why = data.get("why", "")
        if not sug and self.wins:
            self.cap_text.config(text="沒辦法自動判斷是哪一個視窗,請在左邊選擇遊戲視窗。")
        self.fill_list()
        self.on_select()

    def _wheel(self, e):
        if not self.alive:
            return
        top, bottom = self.list_canvas.yview()
        if top <= 0 and bottom >= 1:
            return                      # 全部看得到就不用捲
        self.list_canvas.yview_scroll(-1 if e.delta > 0 else 1, "units")

    # 預設清單不列這些(桌面、檔案總管、系統的殼);勾「顯示全部」才會出現
    NOISE_CLS = {"Progman", "WorkerW", "Shell_TrayWnd", "CabinetWClass",
                 "ApplicationFrameWindow", "Windows.UI.Core.CoreWindow"}
    NOISE_EXE = {"explorer.exe", "textinputhost.exe", "applicationframehost.exe",
                 "systemsettings.exe", "searchhost.exe", "startmenuexperiencehost.exe"}

    def fill_list(self):
        px = self.px
        for w in self.listbox.winfo_children():
            w.destroy()
        if self.show_all.get():
            shown = list(self.wins)
        else:
            shown = [w for w in self.wins if w["plausible"]
                     and w["cls"] not in self.NOISE_CLS
                     and (w["exe"] or "").lower() not in self.NOISE_EXE]
        # 選中的(通常是自動找到的遊戲視窗)排第一個
        web = self.app.platform == "web"
        shown.sort(key=lambda w: (w["hwnd"] != self.sel.get(),
                                  web and not (w["exe"] or "").lower().startswith(
                                      ("chrome", "msedge", "firefox", "opera",
                                       "brave"))))
        if not shown:
            tk.Label(self.listbox, text="沒有找到可能的遊戲視窗。\n請先開啟雀魂(不要最小化),"
                                        "再按「畫面不對,重新選擇」。",
                     font=F(9), fg=C["warn"], bg=C["bg"], justify="left",
                     anchor="w").pack(fill="x")
            return
        box = tk.Frame(self.listbox, bg=C["bg"])
        box.pack(fill="x")
        for w in shown[:40]:
            on = w["hwnd"] == self.sel.get()
            f = tk.Frame(box, bg=C["surf"], highlightthickness=2,
                         highlightbackground=C["acc"] if on else C["line"],
                         cursor="hand2")
            f.pack(fill="x", pady=(0, px(6)))
            rb = tk.Radiobutton(f, variable=self.sel, value=w["hwnd"],
                                command=self.on_select, bg=C["surf"],
                                activebackground=C["surf"], selectcolor=C["surf"],
                                fg=C["acc"], bd=0, highlightthickness=0)
            rb.pack(side="left", padx=(px(6), 0))
            col = tk.Frame(f, bg=C["surf"])
            col.pack(side="left", fill="x", expand=True, pady=px(6))
            title = w["title"] if len(w["title"]) <= 28 else w["title"][:27] + "…"
            tk.Label(col, text=title, font=F(10), fg=C["text"], bg=C["surf"],
                     anchor="w").pack(fill="x")
            cw, ch = w["client"]
            tk.Label(col, text=f"{w['exe'] or '?'} · {cw}×{ch}", font=(MONO, 8),
                     fg=C["muted"], bg=C["surf"], anchor="w").pack(fill="x")
            for x in (f, col) + tuple(col.winfo_children()):
                x.bind("<Button-1>", lambda e, h=w["hwnd"]: (self.sel.set(h),
                                                             self.on_select()))
        self.list_canvas.yview_moveto(0)

    def on_select(self):
        self.fill_list()
        h = self.sel.get()
        self.app.hwnd = h or None
        self.app.window_info = next((w for w in self.wins if w["hwnd"] == h), None)
        self.status = {}
        self.set_checks()
        if self.consent:
            self.start_watch()
        elif h:
            self.placeholder("請先在左邊按「允許擷取」")

    # --- 即時預覽 -----------------------------------------------------------
    def start_watch(self):
        self.stop_watch()
        h = self.app.hwnd
        if not h or not self.consent:
            return
        os.makedirs(self.watch_dir, exist_ok=True)
        for n in ("preview.png", "status.json"):
            try:
                os.remove(os.path.join(self.watch_dir, n))
            except OSError:
                pass
        self.placeholder("擷取中…")
        try:
            self.watch = subprocess.Popen(
                [self.app.python, HELPER, "watch", "--hwnd", str(h), "--out",
                 self.watch_dir, "--width", str(self.pw), "--parent", str(os.getpid())],
                cwd=APP, env=python_env(self.app.python), creationflags=NO_WINDOW,
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                stderr=open(os.path.join(RUNTIME, "helper.log"), "w",
                            encoding="utf-8"))
        except Exception as e:
            self.placeholder(f"沒辦法開始擷取:{e}")
            return
        self.img_mtime = 0
        self.after(300, self.poll_watch)

    def stop_watch(self):
        if self.watch is not None:
            try:
                self.watch.terminate()
            except Exception:
                pass
            self.watch = None

    def poll_watch(self):
        if not self.alive or self.watch is None:
            return
        p = os.path.join(self.watch_dir, "preview.png")
        s = os.path.join(self.watch_dir, "status.json")
        try:
            m = os.path.getmtime(p)
            if m != self.img_mtime:
                self.img_mtime = m
                # 先一次讀進記憶體再解碼:檔案只被開一瞬間,擷取程式換檔時不容易撞到
                with open(p, "rb") as fh:
                    raw = fh.read()
                self.img = tk.PhotoImage(data=base64.b64encode(raw))
                self.canvas.delete("all")
                self.canvas.create_image(0, 0, image=self.img, anchor="nw")
                self.draw_overlay()
        except (OSError, tk.TclError):
            pass
        try:
            with open(s, encoding="utf-8") as fh:
                self.status = json.load(fh)
        except Exception:
            pass
        self.set_checks()
        if self.watch.poll() is not None:
            self.placeholder("擷取程式停止了(詳細原因在 runtime\\helper.log)")
            self.watch = None
            return
        self.after(250, self.poll_watch)

    def draw_overlay(self):
        st = self.status
        good = st.get("ok") and st.get("aspect_ok")
        col = C["acc"] if good else C["warn"]
        self.canvas.create_rectangle(3, 3, self.pw - 3, self.ph - 3, outline=col,
                                     width=2, dash=(6, 4))
        if st.get("game"):
            gw, gh = st["game"]
            label = f"遊戲區域 {gw}×{gh}"
            tid = self.canvas.create_text(14, 14, text=label, anchor="nw",
                                          fill=C["ink"], font=F(9, True))
            x1, y1, x2, y2 = self.canvas.bbox(tid)
            r = self.canvas.create_rectangle(x1 - 6, y1 - 3, x2 + 6, y2 + 3,
                                             fill=col, outline="")
            self.canvas.tag_lower(r, tid)
        if st.get("fps") is not None:
            tid = self.canvas.create_text(self.pw - 14, 14, anchor="ne",
                                          text="即時預覽",
                                          fill=C["text"], font=F(9))
            x1, y1, x2, y2 = self.canvas.bbox(tid)
            r = self.canvas.create_rectangle(x1 - 18, y1 - 3, x2 + 6, y2 + 3,
                                             fill=C["bar"], outline="")
            self.canvas.create_oval(x1 - 13, (y1 + y2) / 2 - 3.5, x1 - 6,
                                    (y1 + y2) / 2 + 3.5, fill=C["err"], outline="")
            self.canvas.tag_lower(r)
            self.canvas.tag_raise(tid)

    def set_checks(self):
        st = self.status
        rows = self.check_rows
        web = self.app.platform == "web"

        def put(key, state, text):
            ic, lab = rows[key]
            ic.set(state)
            lab.config(text=text, fg=C["sub"] if state != "wait" else C["muted"])

        if not st:
            put("found", "wait", "選擇遊戲視窗")
            put("aspect", "wait", "比例 16:9")
            put("width", "wait", "遊戲畫面寬度至少 1100 像素")
            put("live", "wait", "取得即時畫面")
            self.go_btn.set_enabled(False)
            return
        if st.get("minimized"):
            put("found", "err", "視窗被最小化了,請還原(可以被蓋住,但不能最小化)")
        elif st.get("ok"):
            put("found", "ok", "找到視窗")
        else:
            put("found", "err", st.get("error") or "抓不到畫面")
        if web:
            if st.get("web_found"):
                put("aspect", "ok", "在瀏覽器裡找到遊戲區域")
            elif st.get("aspect_ok"):
                put("aspect", "ok", "比例 16:9")
            else:
                put("aspect", "warn", "還找不到遊戲區域 —— 先登入並進到雀魂主畫面")
        else:
            cw, ch = st.get("client") or (0, 0)
            put("aspect", "ok" if st.get("aspect_ok") else "err",
                "比例 16:9" if st.get("aspect_ok") else
                f"比例不是 16:9({cw}×{ch}),請調整視窗大小")
        gw = (st.get("game") or (0, 0))[0]
        if gw:
            put("width", "ok" if gw >= 1100 else "warn",
                f"遊戲畫面寬 {gw} 像素" + ("(至少 1100)" if gw >= 1100 else
                                         " —— 太小,牌容易讀錯,請把視窗拉大"))
        else:
            put("width", "wait", "遊戲畫面寬度至少 1100 像素")
        stale = time.time() - st.get("ts", 0) > 3
        put("live", "ok" if st.get("ok") and not stale else "wait",
            "即時畫面正常,進入對局後會自動開始辨識" if not stale else "等待畫面…")
        self.go_btn.set_enabled(bool(st.get("ok") and not stale
                                     and not st.get("minimized")))
        if not self.cap_text.cget("text") or "示意" in self.cap_text.cget("text"):
            self.cap_text.config(text="如果畫面是黑的、卡住不動,或框到網址列,"
                                      "請按「畫面不對,重新選擇」。")

    def redo(self):
        self.stop_watch()
        self.placeholder("重新尋找視窗…")
        self.refresh_windows()

    # --- 開始 ---------------------------------------------------------------
    def start(self):
        info = self.app.window_info
        if info:
            # 記住類別 + 行程(格式同 1_capture.py 的 remember_window)
            try:
                with open(os.path.join(APP, "window.json"), "w",
                          encoding="utf-8") as fh:
                    json.dump({"cls": info["cls"], "exe": info["exe"],
                               "title_when_saved": info["title"],
                               "saved_at": time.strftime("%Y-%m-%d %H:%M:%S")},
                              fh, ensure_ascii=False, indent=2)
            except OSError:
                pass
        self.stop_watch()
        self.app.show(RunningPage)


# ---------------------------------------------------------------------------
# 執行中 / 結束
# ---------------------------------------------------------------------------
class RunningPage(Page):
    step = None

    def __init__(self, app):
        super().__init__(app)
        px = self.px
        self.t0 = time.time()
        box = tk.Frame(self, bg=C["bg"])
        box.place(relx=0.5, rely=0.42, anchor="center")
        self.icon = StatusIcon(box, px(44), C["bg"])
        self.icon.pack()
        self.head = tk.Label(box, text="啟動中…", font=F(18, True), fg=C["text"],
                             bg=C["bg"])
        self.head.pack(pady=(px(14), px(6)))
        self.sub = tk.Label(box, text="", font=F(10), fg=C["muted"], bg=C["bg"],
                            justify="center", wraplength=px(560))
        self.sub.pack()
        self.btns = tk.Frame(box, bg=C["bg"])
        self.btns.pack(pady=(px(22), 0))

    def enter(self):
        super().enter()
        self.app.set_step(None)
        self.icon.set("run")
        self.sub.config(text="提示列會出現在螢幕最上方(可以用滑鼠拖動)。\n"
                             "要結束請按提示列右邊的 [停止]。這個視窗會自動縮到工作列。")
        self.launch()

    def launch(self):
        app = self.app
        cmd = [app.python, AGENT]
        if app.hwnd:
            cmd += ["--hwnd", str(app.hwnd)]
        os.makedirs(RUNTIME, exist_ok=True)
        self.logf = open(os.path.join(RUNTIME, "agent_last.log"), "w",
                         encoding="utf-8")
        try:
            app.agent = subprocess.Popen(cmd, cwd=APP, env=python_env(app.python),
                                         creationflags=NO_WINDOW,
                                         stdin=subprocess.DEVNULL,
                                         stdout=self.logf, stderr=self.logf)
        except Exception as e:
            self.finished(-1, str(e))
            return
        self.head.config(text="執行中")
        self.after(1500, lambda: self.alive and self.app.iconify())
        self.app.bg(self.wait_agent)
        self.tick()

    def tick(self):
        if not self.alive or self.app.agent is None or \
                self.app.agent.poll() is not None:
            return
        m, s = divmod(int(time.time() - self.t0), 60)
        h, m = divmod(m, 60)
        self.head.config(text=f"執行中  {h:d}:{m:02d}:{s:02d}")
        self.after(1000, self.tick)

    def wait_agent(self):
        rc = self.app.agent.wait()
        if self.app.chrome_mode:
            run([self.app.python, CHROME_MODE, "restore", "--quiet"], timeout=60,
                cwd=APP)
            self.app.chrome_mode = False
        self.post(self.finished, rc, "")

    def finished(self, rc, err):
        try:
            self.logf.close()
        except Exception:
            pass
        app = self.app
        app.deiconify()
        app.lift()
        px = self.px
        if rc == 0:
            self.icon.set("ok")
            self.head.config(text="已結束")
            self.sub.config(text="紀錄(錄影、牌譜、建議)存在「紀錄」資料夾。"
                            + ("\nChrome 設定已還原。" if app.platform == "web"
                               and app.browser == "chrome" else ""))
        else:
            self.icon.set("err")
            self.head.config(text="程式異常結束")
            self.sub.config(text=(err or f"結束代碼 {rc}。") +
                            "\n錯誤訊息在 runtime\\agent_last.log 與「紀錄」資料夾的 console.log。")
        for w in self.btns.winfo_children():
            w.destroy()
        rec = load_cfg().get("record_dir", "紀錄")
        rec = rec if os.path.isabs(rec) else os.path.join(ROOT, rec)
        Btn(self.btns, "開啟紀錄資料夾", lambda: open_folder(rec), "ghost"
            ).pack(side="left")
        Btn(self.btns, "關閉", app.on_close, "ghost").pack(side="left", padx=px(10))
        Btn(self.btns, "再開一場", lambda: app.show(CapturePage), "primary"
            ).pack(side="left")
        app.agent = None


# ---------------------------------------------------------------------------
def main():
    if WIN:
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            try:
                ctypes.windll.user32.SetProcessDPIAware()
            except Exception:
                pass
        # 工作列圖示跟 python.exe 分開
        try:
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
                "mjai.mahjongsoul.agent")
        except Exception:
            pass
    os.makedirs(RUNTIME, exist_ok=True)
    app = App()
    app.mainloop()


if __name__ == "__main__":
    main()
