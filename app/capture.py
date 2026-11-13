"""視窗擷取:找到雀魂視窗、抓出用戶端畫面、統一成 1920x1080。

所有固定座標都是照 1920x1080 量的,所以這裡一定要縮放成這個尺寸。
視窗寬高比不是 16:9 的話,縮放會讓畫面變形、座標對不準,會印一次警告。
"""
import ctypes

import cv2
import numpy as np
import win32gui
import win32ui


# ---------------------------------------------------------------------------
# ★ DPI 感知:一定要在碰任何視窗 API 之前設好(所以放在 import 的時候就做)
# ---------------------------------------------------------------------------
# Windows 的「縮放與版面配置」不是 100% 的時候(筆電常見 125%、150%),沒有宣告
# DPI 感知的程式拿到的視窗座標是「縮小過的假座標」:
#     實際 1920x1080 的視窗,GetWindowRect 回報 1536x864(= 1920 / 1.25)
# 但 PrintWindow 畫出來的是真實像素。兩者對不上的結果是:
#     點陣圖只開 1536x864 -> 只抓到畫面左上 80%
#     標題列的偏移也用假座標算 -> 裁切位置跑掉
# 看起來就是「畫面歪了、偏一邊、缺一塊」,而且所有固定座標全部對不準。
# 桌機是 100% 所以從來沒出事;換到 125% 的筆電馬上就露出來了。
#
# 修法就是告訴 Windows「這個程式自己處理 DPI」,之後拿到的都是真實像素。
# 由新到舊依序嘗試,舊版 Windows 沒有新 API 就退一步。
def _make_dpi_aware():
    try:                                   # Win10 1703+:Per-Monitor V2
        if ctypes.windll.user32.SetProcessDpiAwarenessContext(
                ctypes.c_void_p(-4)):
            return "per-monitor-v2"
    except (AttributeError, OSError):
        pass
    try:                                   # Win8.1+:Per-Monitor
        if ctypes.windll.shcore.SetProcessDpiAwareness(2) == 0:
            return "per-monitor"
    except (AttributeError, OSError):
        pass
    try:                                   # Vista+:System aware
        if ctypes.windll.user32.SetProcessDPIAware():
            return "system"
    except (AttributeError, OSError):
        pass
    # 已經設過(例如被別的套件先設了)也會走到這裡;不是錯誤。
    return "unknown"


DPI_MODE = _make_dpi_aware()


def window_dpi_scale(hwnd):
    """視窗所在螢幕的縮放比例(1.0 = 100%、1.25 = 125%)。取不到回 None。"""
    try:
        dpi = ctypes.windll.user32.GetDpiForWindow(hwnd)
        return dpi / 96 if dpi else None
    except (AttributeError, OSError):
        return None

# Windows 8.1+ 的 flag。有些用 GPU 合成/分層繪製的視窗(瀏覽器、Electron
# 包的用戶端)不加這個,PrintWindow 只會抓到一片黑。
PW_RENDERFULLCONTENT = 0x00000002

TARGET_W, TARGET_H = 1920, 1080

# ---------------------------------------------------------------------------
# 網頁版(瀏覽器):在擷取到的畫面裡找出 16:9 的遊戲區域
# ---------------------------------------------------------------------------
# ★ 2026-11-05 用使用者的 Chrome 實測(一般最大化 / 視窗化 / 縮放 125% / F11):
#   瀏覽器的用戶端區域包含分頁列、網址列、書籤列;下面的網頁區裡遊戲固定 16:9、
#   置中,多出來的地方補深色花紋(寬了補左右、高了補上下)。網頁縮放不影響。
#   照這個規則切出來、縮到 1920x1080,跟 F11 全螢幕的畫面比,每個像素平均只差
#   2~3(滿分 255)。
#   唯一不知道的是上面那排瀏覽器列有多高(書籤列開關、F11 都會變)-> 每一種
#   高度都試,看遊戲右上角的「設定/離開」兩顆鈕有沒有落在該在的位置(那兩顆鈕
#   對局中一直都在;Steam 版的畫面比對也有 0.79,隨便一塊畫面只有 0.09)。
WEB_CORNER = (1680, 25, 210, 95)       # 右上角兩顆鈕(1920x1080 座標)
WEB_MIN_SCORE = 0.60
WEB_KEEP_SCORE = 0.45
_corner_tpl = None


def _corner_template():
    global _corner_tpl
    if _corner_tpl is None:
        import os
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "assets", "templates_web", "corner.png")
        t = cv2.imread(path, cv2.IMREAD_GRAYSCALE)
        if t is not None and t.ndim == 3:
            t = t[:, :, 0]
        _corner_tpl = t
    return _corner_tpl


def game_rect_for(w, h, top):
    """網頁區從 top 開始到底:16:9 的遊戲會在哪裡。回傳 (x, y, w, h)。"""
    vh = h - top
    if vh <= 0:
        return None
    if w / vh > TARGET_W / TARGET_H:              # 太寬:左右補邊
        gh = vh
        gw = int(round(gh * TARGET_W / TARGET_H))
        return ((w - gw) // 2, top, gw, gh)
    gw = w                                         # 太高:上下補邊
    gh = int(round(gw * TARGET_H / TARGET_W))
    return (0, top + (vh - gh) // 2, gw, gh)


def corner_score(gray, rect):
    """這個遊戲區域的右上角,像不像遊戲的設定/離開鈕(0~1)。"""
    tpl = _corner_template()
    if tpl is None or rect is None:
        return 0.0
    gx, gy, gw, gh = rect
    s = gw / TARGET_W
    x, y, w, h = WEB_CORNER
    rx, ry = int(gx + x * s), int(gy + y * s)
    rw, rh = max(1, int(w * s)), max(1, int(h * s))
    patch = gray[ry:ry + rh, rx:rx + rw]
    if patch.shape[0] < rh or patch.shape[1] < rw or rw < 20:
        return 0.0
    patch = cv2.resize(patch, (tpl.shape[1], tpl.shape[0]),
                       interpolation=cv2.INTER_AREA)
    return float(cv2.matchTemplate(patch, tpl, cv2.TM_CCOEFF_NORMED)[0, 0])


def locate_game(img, max_top=320):
    """在不是 16:9 的擷取畫面裡找遊戲區域。回傳 ((x, y, w, h), 分數)。

    純函式(不碰 Windows API),可以拿存下來的圖直接測。"""
    h, w = img.shape[:2]
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if img.ndim == 3 else img
    hi = min(max_top, h // 3)

    def scan(tops):
        b, br, bt = 0.0, None, 0
        for top in tops:
            rect = game_rect_for(w, h, top)
            sc = corner_score(gray, rect)
            if sc > b:
                b, br, bt = sc, rect, top
        return b, br, bt
    # 先每 4px 粗找,再在最好的附近逐 px 細找(整段逐 px 要 ~150ms)
    best, best_rect, top0 = scan(range(0, hi + 1, 4))
    b2, r2, _t = scan(range(max(0, top0 - 4), min(hi, top0 + 4) + 1))
    if b2 >= best:
        best, best_rect = b2, r2
    return best_rect, best


def list_windows():
    """回傳 [(hwnd, title), ...]:所有看得見、而且有標題的視窗。"""
    out = []

    def handler(hwnd, _):
        if win32gui.IsWindowVisible(hwnd):
            title = win32gui.GetWindowText(hwnd)
            if title.strip():
                out.append((hwnd, title))

    win32gui.EnumWindows(handler, None)
    return out


def console_hwnd():
    """回傳本程式所在主控台視窗的 hwnd,取不到回傳 0。

    注意:在 Windows Terminal 底下,這個 API 回傳的是隱藏的 ConPTY 視窗,
    不是你看得見的那個終端機視窗——所以「排除主控台」不能當成唯一的防線,
    真正可靠的做法是候選超過一個就停下來讓使用者指定(見 find_windows())。
    """
    try:
        return int(ctypes.windll.kernel32.GetConsoleWindow())
    except Exception:
        return 0


def find_windows(title_substring, exclude=()):
    """回傳所有標題「包含」這段文字的視窗 [(hwnd, title)],不分大小寫。

    順序就是 EnumWindows 的順序,也就是 z-order:最上層的排在最前面。
    正因為如此,絕對不要直接取第一個當答案——執行中的指令列會被設成終端機
    視窗的標題,所以只要指令裡打了遊戲視窗的名字,終端機自己就會符合條件,
    而且按下 Enter 的當下它就在最上層,一定被選中。
    """
    sub = title_substring.lower()
    return [(h, t) for h, t in list_windows()
            if sub in t.lower() and h not in exclude]


def resolve_target_window(hwnd=None, title=None):
    """決定要抓哪個視窗,決不了就印出原因跟候選清單、回傳 None。

    hwnd 優先。只給 title 而且有多個視窗符合時,不會擅自挑一個——見
    find_windows() 說明的終端機標題陷阱。
    """
    if hwnd:
        if not win32gui.IsWindow(hwnd):
            print(f"❌ hwnd={hwnd} 不是有效的視窗,重跑 --list 拿最新的。")
            return None
        print(f"✅ 使用視窗 hwnd={hwnd}:{win32gui.GetWindowText(hwnd)}")
        return hwnd

    if not title:
        print("請用 --hwnd 指定視窗(建議),或用 --window 給標題的一段文字。"
              "先跑 --list 看有哪些視窗。")
        return None

    matches = find_windows(title, exclude=(console_hwnd(),))
    if not matches:
        print(f"❌ 找不到標題包含「{title}」的視窗。先跑 --list 確認。")
        return None

    if len(matches) > 1:
        print(f"⚠️ 有 {len(matches)} 個視窗的標題都包含「{title}」,"
              f"無法確定要抓哪一個:")
        for h, t in matches:
            print(f"    {h}  -  {t}")
        print("\n最常見的原因:執行中的指令列會變成終端機視窗的標題,"
              "所以你打的關鍵字讓終端機自己也符合條件。")
        print("請直接指定要抓的那個:  --hwnd <上面的數字>")
        return None

    chosen, chosen_title = matches[0]
    print(f"✅ 使用視窗 hwnd={chosen}:{chosen_title}")
    return chosen


def print_window_list():
    """列出所有視窗,標出本程式的主控台。"""
    me = console_hwnd()
    for hwnd, title in list_windows():
        tag = "  <- 這是本程式的主控台" if hwnd == me else ""
        print(f"  {hwnd}  -  {title}{tag}")
    print("\n找到遊戲視窗那一行,用最左邊的數字當 --hwnd 的參數。")


class WindowCapture:
    """綁定一個視窗,重複呼叫 grab() 取得畫面。

    PrintWindow 只能畫「整個視窗」(含標題列/邊框),所以這裡抓完之後再用
    ClientToScreen 算出用戶端區域的偏移量把外框裁掉。
    """

    def __init__(self, hwnd):
        self.hwnd = hwnd
        self._aspect_warned = False
        self._minimized_warned = False
        # 縮放成 1920x1080 之前,視窗用戶端區域的真實尺寸。所有收集到的
        # 座標都必須連同這個一起記錄——只要寬高比是 16:9,縮放後的座標就
        # 一致、資料可以互相通用;寬高比不對的話畫面會變形,那批資料只能
        # 作廢。沒記下來的話,事後完全無從判斷一份舊資料能不能信。
        self.client_size = None
        self.aspect_ok = None
        # 螢幕縮放比例。只是拿來印給人看的——DPI 感知設好之後,不管幾 %
        # 抓到的都是真實像素,程式不需要自己換算。
        self.dpi_scale = window_dpi_scale(hwnd)
        # 網頁版:遊戲在用戶端區域裡的位置(用戶端本身就是 16:9 時是 None)
        self.game_rect = None
        self._rect_for = None           # 找到 game_rect 時的用戶端尺寸
        self._rect_tried = 0.0
        self._rect_checked = 0.0
        self._rect_warned = False

    def grab(self):
        """回傳 1920x1080 的 BGR numpy 陣列,失敗回傳 None。"""
        if win32gui.IsIconic(self.hwnd):
            if not self._minimized_warned:
                print("⚠️ 視窗被最小化了,PrintWindow 抓不到內容。"
                      "還原視窗即可(不需要切到最上層)。")
                self._minimized_warned = True
            return None
        self._minimized_warned = False

        raw = self._print_window()
        if raw is None:
            return None
        img = self._crop_client_area(raw)
        img = self._game_area(img)
        return self._normalize_size(img)

    def _game_area(self, img):
        """用戶端不是 16:9(網頁版的分頁列/網址列/書籤列、或 16:10 螢幕的
        黑邊)-> 切出裡面 16:9 的遊戲區域。找不到就原樣回傳(舊行為)。"""
        import time
        h, w = img.shape[:2]
        if h == 0 or w == 0 or abs(w / h - TARGET_W / TARGET_H) <= 0.03:
            self.game_rect = None
            return img
        now = time.time()
        size = (w, h)
        if self.game_rect is not None and self._rect_for == size:
            if now - self._rect_checked > 3.0:
                self._rect_checked = now
                gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
                if corner_score(gray, self.game_rect) < WEB_KEEP_SCORE:
                    # 角落對不上了(書籤列開關?或只是結算畫面蓋住)-> 重找,
                    # 找到有把握的才換
                    rect, sc = locate_game(img)
                    if rect and sc >= WEB_MIN_SCORE and rect != self.game_rect:
                        self.game_rect = rect
                        print(f"🌐 遊戲區域移動了 -> {rect}(相似度 {sc:.2f})")
        elif self._rect_for != size or now - self._rect_tried > 1.0:
            self._rect_tried = now
            rect, sc = locate_game(img)
            if rect and sc >= WEB_MIN_SCORE:
                self.game_rect, self._rect_for = rect, size
                self._rect_checked = now
                print(f"🌐 視窗 {w}x{h} 不是 16:9,在裡面找到遊戲畫面 {rect}"
                      f"(相似度 {sc:.2f})")
            else:
                self.game_rect, self._rect_for = None, size
                if not self._rect_warned:
                    self._rect_warned = True
                    print(f"⚠️ 視窗 {w}x{h} 不是 16:9,還找不到裡面的遊戲畫面"
                          f"(網頁版要先進到牌桌畫面;進去之後會自動找到)")
        if self.game_rect is not None and self._rect_for == size:
            x, y, gw, gh = self.game_rect
            return img[y:y + gh, x:x + gw]
        return img

    def game_screen_rect(self):
        """遊戲畫面在螢幕上的位置 (x, y, w, h)(字幕列畫紅點用)。
        網頁版是瀏覽器裡的那一塊;一般是整個用戶端區域。"""
        _, _, cw, ch = win32gui.GetClientRect(self.hwnd)
        ox, oy = win32gui.ClientToScreen(self.hwnd, (0, 0))
        r = self.game_rect
        if r is not None and self._rect_for == (cw, ch):
            return (ox + r[0], oy + r[1], r[2], r[3])
        return (ox, oy, cw, ch)

    def _print_window(self):
        """把整個視窗畫進點陣圖,回傳 BGR 陣列(含標題列/邊框)。"""
        left, top, right, bottom = win32gui.GetWindowRect(self.hwnd)
        w, h = right - left, bottom - top
        if w <= 0 or h <= 0:
            return None

        hwnd_dc = win32gui.GetWindowDC(self.hwnd)
        mfc_dc = save_dc = bitmap = None
        try:
            mfc_dc = win32ui.CreateDCFromHandle(hwnd_dc)
            save_dc = mfc_dc.CreateCompatibleDC()
            bitmap = win32ui.CreateBitmap()
            bitmap.CreateCompatibleBitmap(mfc_dc, w, h)
            save_dc.SelectObject(bitmap)

            ok = ctypes.windll.user32.PrintWindow(
                self.hwnd, save_dc.GetSafeHdc(), PW_RENDERFULLCONTENT)
            if ok != 1:
                return None

            info = bitmap.GetInfo()
            bits = bitmap.GetBitmapBits(True)
            arr = np.frombuffer(bits, dtype=np.uint8).reshape(
                (info["bmHeight"], info["bmWidth"], 4))
            # GDI 給的是 BGRA,丟掉 alpha 之後的順序就是 cv2 慣用的 BGR
            return arr[:, :, :3].copy()
        finally:
            # GDI object 不釋放會慢慢用完系統配額——長時間跑的即時迴圈
            # 一定要放在 finally,中途丟例外也不能漏掉。
            if bitmap is not None:
                win32gui.DeleteObject(bitmap.GetHandle())
            if save_dc is not None:
                save_dc.DeleteDC()
            if mfc_dc is not None:
                mfc_dc.DeleteDC()
            win32gui.ReleaseDC(self.hwnd, hwnd_dc)

    def _crop_client_area(self, img):
        """裁掉標題列/邊框,只留用戶端內容。算出來的範圍不合理就整張退回。"""
        win_left, win_top, _, _ = win32gui.GetWindowRect(self.hwnd)
        _, _, client_w, client_h = win32gui.GetClientRect(self.hwnd)
        screen_x, screen_y = win32gui.ClientToScreen(self.hwnd, (0, 0))
        ox, oy = screen_x - win_left, screen_y - win_top

        if (ox < 0 or oy < 0 or client_w <= 0 or client_h <= 0
                or oy + client_h > img.shape[0] or ox + client_w > img.shape[1]):
            return img
        return img[oy:oy + client_h, ox:ox + client_w]

    def _normalize_size(self, img):
        h, w = img.shape[:2]
        if h == 0 or w == 0:
            return None
        aspect = w / h
        self.client_size = (w, h)
        self.aspect_ok = abs(aspect - 16 / 9) <= 0.03
        if not self.aspect_ok and not self._aspect_warned:
            print(f"❌ 用戶端區域是 {w}x{h}(寬高比 {aspect:.3f}),不是 16:9。"
                  f"縮放成 {TARGET_W}x{TARGET_H} 會讓畫面變形,這次跑出來的"
                  f"座標全部不能用,請先把視窗調成 16:9"
                  f"(1920x1080 最保險,1600x900、1280x720 也可以)。")
            self._aspect_warned = True
        if (w, h) != (TARGET_W, TARGET_H):
            img = cv2.resize(img, (TARGET_W, TARGET_H),
                             interpolation=cv2.INTER_AREA)
        return img
