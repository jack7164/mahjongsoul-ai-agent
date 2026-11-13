r"""用 Chrome 玩網頁版時的準備 / 收尾(雀魂AI助手.exe 選「網頁版＋Chrome」時呼叫這支)。

    python app\chrome_mode.py prepare    開始前:原則寫進登錄檔 -> 用特殊參數開 Chrome 進雀魂
    python app\chrome_mode.py restore    結束後:登錄檔還原成開始前的樣子
    python app\chrome_mode.py prepare --no-prompt   Chrome 開著就直接失敗(主程式用)
    python app\chrome_mode.py apply      只寫原則、不開 Chrome(主程式「再開一場」用)
    python app\chrome_mode.py status     印出目前的狀態(除錯用)

為什麼要這樣做(2026-11-05):Chrome 的視窗被別的視窗整個蓋住時,會暫停更新畫面、
放慢網頁 -> 程式抓到的是停住的畫面,辨識跟不上。分兩部分處理:

1) 啟動參數(只對這次開的 Chrome 有效,不留痕跡):
     --disable-backgrounding-occluded-windows        被蓋住也當作前景
     --disable-features=CalculateNativeWinOcclusion  不偵測「視窗被蓋住」
     --disable-background-timer-throttling           背景計時器不降速
     --disable-renderer-backgrounding                背景分頁不降優先權
   ★ Chrome 要「完全關掉」再用這裡開,參數才會生效(已經在跑的話,
     新開的只是同一個 Chrome 的新視窗)。

2) Chrome 原則(登錄檔 HKEY_CURRENT_USER\Software\Policies\Google\Chrome,
   不需要系統管理員權限):
     TabDiscardingExceptions     雀魂的網址永遠不會被「記憶體節省模式」卸載
     BatterySaverModeAvailability = 0   關掉「節能模式」(降低畫面更新率)
   原則會讓 Chrome 設定頁顯示「由貴機構管理」-> 所以只在玩的時候寫,
   結束(按字幕列 [停止])就還原。
   ★ 開始前的狀態整個備份在 chrome_policy_backup.json;還原是「照備份寫回去」,
     原本就有的原則(例如公司設的)不會被刪掉。
   ★ 程式沒有正常結束的話還原不會執行 -> 下次開啟雀魂AI助手.exe 時會先自動還原。
"""
import json
import os
import subprocess
import sys
import time

APP_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(APP_DIR)
BACKUP = os.path.join(APP_DIR, "chrome_policy_backup.json")
CONFIG = os.path.join(ROOT_DIR, "config.json")

POLICY_KEY = r"Software\Policies\Google\Chrome"
LIST_NAME = "TabDiscardingExceptions"
BATTERY_NAME = "BatterySaverModeAvailability"
# 雀魂各伺服器的網域(這個格式會連子網域一起比對)
GAME_DOMAINS = ["maj-soul.com", "mahjongsoul.com", "mahjongsoul.game.yo-star.com"]
DEFAULT_URL = "https://game.maj-soul.com/1/"
CHROME_FLAGS = [
    "--disable-backgrounding-occluded-windows",
    "--disable-features=CalculateNativeWinOcclusion",
    "--disable-background-timer-throttling",
    "--disable-renderer-backgrounding",
]


# ---------------------------------------------------------------------------
# 登錄檔(包一層:測試時換成假的)
# ---------------------------------------------------------------------------
class WinReg:
    """只用得到的幾個操作,全部在 HKEY_CURRENT_USER 底下。"""

    def __init__(self):
        import winreg
        self.w = winreg

    def key_exists(self, path):
        try:
            self.w.CloseKey(self.w.OpenKey(self.w.HKEY_CURRENT_USER, path))
            return True
        except OSError:
            return False

    def values(self, path):
        """{名稱: (資料, 型別)};key 不存在回 None。"""
        try:
            k = self.w.OpenKey(self.w.HKEY_CURRENT_USER, path)
        except OSError:
            return None
        out, i = {}, 0
        try:
            while True:
                try:
                    n, d, t = self.w.EnumValue(k, i)
                except OSError:
                    break
                out[n] = (d, t)
                i += 1
        finally:
            self.w.CloseKey(k)
        return out

    def has_subkeys(self, path):
        try:
            k = self.w.OpenKey(self.w.HKEY_CURRENT_USER, path)
        except OSError:
            return False
        try:
            self.w.EnumKey(k, 0)
            return True
        except OSError:
            return False
        finally:
            self.w.CloseKey(k)

    def create(self, path):
        self.w.CloseKey(self.w.CreateKey(self.w.HKEY_CURRENT_USER, path))

    def set(self, path, name, data, typ):
        k = self.w.CreateKey(self.w.HKEY_CURRENT_USER, path)
        try:
            self.w.SetValueEx(k, name, 0, typ, data)
        finally:
            self.w.CloseKey(k)

    def delete_value(self, path, name):
        try:
            k = self.w.OpenKey(self.w.HKEY_CURRENT_USER, path, 0,
                               self.w.KEY_SET_VALUE)
        except OSError:
            return
        try:
            self.w.DeleteValue(k, name)
        except OSError:
            pass
        finally:
            self.w.CloseKey(k)

    def delete_key(self, path):
        try:
            self.w.DeleteKey(self.w.HKEY_CURRENT_USER, path)
        except OSError:
            pass

    REG_SZ = 1
    REG_DWORD = 4


# ---------------------------------------------------------------------------
# 原則:備份 -> 寫入 -> 還原
# ---------------------------------------------------------------------------
def snapshot(reg):
    """開始前的樣子:哪些 key 本來就在、兩個原則本來的值。"""
    parents = [r"Software\Policies\Google", POLICY_KEY,
               POLICY_KEY + "\\" + LIST_NAME]
    pol = reg.values(POLICY_KEY) or {}
    lst = reg.values(POLICY_KEY + "\\" + LIST_NAME)
    return {
        "saved_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "keys_existed": {p: reg.key_exists(p) for p in parents},
        "battery": list(pol[BATTERY_NAME]) if BATTERY_NAME in pol else None,
        "list": {n: list(v) for n, v in lst.items()} if lst is not None else None,
    }


def apply(reg, snap):
    reg.set(POLICY_KEY, BATTERY_NAME, 0, reg.REG_DWORD)
    lk = POLICY_KEY + "\\" + LIST_NAME
    reg.create(lk)
    have = {str(v[0]).lower() for v in (snap["list"] or {}).values()}
    used = {n for n in (snap["list"] or {})}
    i = 1
    for d in GAME_DOMAINS:
        if d.lower() in have:
            continue
        while str(i) in used:
            i += 1
        reg.set(lk, str(i), d, reg.REG_SZ)
        used.add(str(i))


def restore(reg, snap):
    """照備份寫回去:本來沒有的刪掉、本來有的寫回原值、本來沒有的 key 刪掉。"""
    lk = POLICY_KEY + "\\" + LIST_NAME
    # 節能模式
    if snap["battery"] is None:
        reg.delete_value(POLICY_KEY, BATTERY_NAME)
    else:
        reg.set(POLICY_KEY, BATTERY_NAME, snap["battery"][0], snap["battery"][1])
    # 不卸載清單:整個換回原本的
    now = reg.values(lk) or {}
    for n in now:
        reg.delete_value(lk, n)
    if snap["list"] is not None:
        for n, (d, t) in snap["list"].items():
            reg.set(lk, n, d, t)
    # 本來不存在的 key:現在是空的就刪掉(由裡到外)
    ex = snap["keys_existed"]
    for p in (lk, POLICY_KEY, r"Software\Policies\Google"):
        if not ex.get(p) and reg.key_exists(p) and not reg.values(p) \
                and not reg.has_subkeys(p):
            reg.delete_key(p)


def do_restore(reg, quiet=False):
    if not os.path.isfile(BACKUP):
        if not quiet:
            print("Chrome 設定:沒有需要還原的(沒有備份檔)。")
        return True
    try:
        with open(BACKUP, encoding="utf-8") as fh:
            snap = json.load(fh)
        restore(reg, snap)
    except Exception as e:
        print(f"❌ 還原 Chrome 設定失敗:{e}")
        print("   可以再執行一次雀魂AI助手.exe(開啟時會自動還原);還是不行的話,"
              "把 app\\chrome_policy_backup.json 傳給開發者(不要刪)。")
        return False
    os.remove(BACKUP)
    print("✅ Chrome 設定已還原(記憶體節省模式 / 節能模式回到你原本的設定)。")
    return True


def do_apply(reg):
    # 上次沒還原到(直接關了視窗)-> 先還原,才不會把「我們寫的值」當成原本的值備份
    if os.path.isfile(BACKUP):
        print("上次結束時 Chrome 設定沒有還原,先還原…")
        if not do_restore(reg):
            return False
    snap = snapshot(reg)
    with open(BACKUP, "w", encoding="utf-8") as fh:
        json.dump(snap, fh, ensure_ascii=False, indent=2)
    apply(reg, snap)
    print("✅ 已暫時設定:雀魂分頁不會被記憶體節省模式卸載、節能模式關閉。"
          "(按字幕列 [停止] 結束時會自動還原)")
    return True


# ---------------------------------------------------------------------------
# Chrome
# ---------------------------------------------------------------------------
def chrome_running():
    try:
        r = subprocess.run(["tasklist", "/FI", "IMAGENAME eq chrome.exe",
                            "/NH", "/FO", "CSV"], capture_output=True,
                           text=True, timeout=15)
        return "chrome.exe" in r.stdout.lower()
    except Exception:
        return False


def close_chrome():
    """跟按視窗右上角 X 一樣的關法(不是強制結束),Chrome 會照常存好分頁。"""
    subprocess.run(["taskkill", "/IM", "chrome.exe"], capture_output=True)
    for _ in range(20):
        time.sleep(0.5)
        if not chrome_running():
            return True
    return False


def find_chrome():
    cands = []
    try:
        import winreg
        for root in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
            try:
                k = winreg.OpenKey(root, r"SOFTWARE\Microsoft\Windows\CurrentVersion"
                                         r"\App Paths\chrome.exe")
                cands.append(winreg.QueryValue(k, None))
                winreg.CloseKey(k)
            except OSError:
                pass
    except ImportError:
        pass
    for base in (os.environ.get("ProgramFiles"), os.environ.get("ProgramFiles(x86)"),
                 os.environ.get("LOCALAPPDATA")):
        if base:
            cands.append(os.path.join(base, "Google", "Chrome", "Application",
                                      "chrome.exe"))
    return next((c for c in cands if c and os.path.isfile(c)), None)


def game_url():
    try:
        with open(CONFIG, encoding="utf-8") as fh:
            return json.load(fh).get("web_url") or DEFAULT_URL
    except Exception:
        return DEFAULT_URL


def wait_chrome_closed():
    if not chrome_running():
        return True
    print()
    print("Chrome 現在開著。要用特殊參數重新開,必須先把 Chrome「全部」關掉。")
    print("  (只關視窗不夠的話:工作列右下角的 Chrome 圖示也要按右鍵「結束」)")
    while chrome_running():
        ans = input("  關好後按 Enter;輸入 K 再按 Enter = 由程式幫你關閉 Chrome:"
                    ).strip().lower()
        if ans == "k":
            if close_chrome():
                print("  已關閉 Chrome。")
            else:
                print("  還有 Chrome 沒關掉(可能是背景程式),請手動關閉。")
    return True


def do_prepare(reg, prompt=True):
    chrome = find_chrome()
    if chrome is None:
        print("❌ 找不到 Chrome(chrome.exe)。請確認有安裝 Google Chrome。")
        return 1
    if not prompt and chrome_running():
        # 主程式(雀魂AI助手.exe)呼叫時沒有主控台可以問 -> 由它先確認 Chrome 關了
        print("❌ Chrome 還開著,請先全部關閉。")
        return 2
    wait_chrome_closed()
    if not do_apply(reg):
        return 1
    url = game_url()
    # 跟這個主控台脫鉤:黑色視窗關掉 Chrome 也不會跟著被關
    subprocess.Popen([chrome] + CHROME_FLAGS + ["--new-window", url],
                     close_fds=True,
                     creationflags=0x00000008 | 0x00000200)  # DETACHED | 新群組
    print(f"✅ 已開啟 Chrome:{url}")
    print("   進到牌桌畫面後,字幕列就會開始給建議。")
    return 0


def do_status(reg):
    print("備份檔:", "有(設定還沒還原)" if os.path.isfile(BACKUP) else "沒有")
    print("Chrome 執行中:", chrome_running())
    print("Chrome 位置:", find_chrome())
    print(f"{POLICY_KEY}:", reg.values(POLICY_KEY))
    print(f"{POLICY_KEY}\\{LIST_NAME}:", reg.values(POLICY_KEY + "\\" + LIST_NAME))
    print("(也可以在 Chrome 網址列輸入 chrome://policy 查看)")


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    cmd = argv[0] if argv else "status"
    quiet = "--quiet" in argv
    if sys.platform != "win32":
        if not quiet:
            print("這個工具只支援 Windows。")
        return 0
    reg = WinReg()
    if cmd == "prepare":
        return do_prepare(reg, prompt="--no-prompt" not in argv)
    if cmd == "apply":
        # 只寫原則,不動 Chrome(主程式「再開一場」時用:Chrome 已經是特殊模式開的)
        return 0 if do_apply(reg) else 1
    if cmd == "restore":
        return 0 if do_restore(reg, quiet=quiet) else 1
    do_status(reg)
    return 0


if __name__ == "__main__":
    sys.exit(main())
