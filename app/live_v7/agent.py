"""雀魂 AI 助手:長條字幕列 + 畫面辨識 + Mortal 建議。雀魂AI助手.exe 執行這支。

    python live_v7\\agent.py                 (設定讀 ..\\..\\config.json,沒有就用預設值)

=== 畫面 ===
一條細長、永遠在最上層、可以用滑鼠拖動的字幕列:
    左邊   GPU/CPU、每秒幾幀、目前第幾局
    中間   字幕(就緒 / 開始辨識 / Mortal 建議 / 錯誤)
    右邊   [新的一場]  [停止]
不用鍵盤快捷鍵:打牌時焦點在遊戲視窗上,按鍵傳不到字幕列。

=== 流程 ===
1. 檢查:顯示卡 -> Docker 有沒有開 -> mortal 映像在不在 -> 找雀魂視窗(找不到就每 3 秒再找)
2. 載入模型 -> 就緒(字幕一直顯示「就緒,等待對局開始」)
3. 第一局開始(守門器把 start_game 送給 Mortal)-> 「開始辨識」3 秒
4. Mortal 有建議就顯示「建議:… 次選:…」;P0 行動後 / 換局時清掉
5. 守門器擋下一局(辨識有矛盾)-> 「辨識有誤,本局暫停建議」,下一局開始時恢復
6. [停止]:存檔、關 Mortal、結束。[新的一場]:存檔、開新的紀錄資料夾、重新載入

=== 紀錄 ===
每一次(每一場)一個資料夾 <config.record_dir>\\<日期_時間>\\,內容同 record_obs.py,
另外有 events_live.ndjson(即時寫入的局中事件)、events.ndjson(結束時的最終版)、
advice.ndjson(字幕列顯示過的每一筆建議)、console.log(主控台輸出)。
"""
import json
import os
import queue
import subprocess
import sys
import threading
import time
import traceback
from datetime import datetime

import _paths  # noqa: F401

APP_DIR = _paths.PROJECT_DIR                     # ...\app
ROOT_DIR = os.path.dirname(APP_DIR)              # ...\mahjongsoul ai agent
CONFIG_PATH = os.path.join(ROOT_DIR, "config.json")
DEFAULTS = {
    "save_video": True,        # 存 frames.mp4(一場約 250MB;查錯、重讀新模型要用)
    "show_preview": False,     # 另外開一個縮小的遊戲畫面視窗(除錯用)
    "mortal": "docker",
    "window_title": "雀魂",
    "record_dir": "紀錄",      # 相對於 mahjongsoul ai agent 資料夾
    "bar_width": 1000,
    "bar_alpha": 0.88,
    "show_dots": True,         # 手牌上方的數字徽章(1 主選 / 2 次選 / 鳴牌寫動作)
    "show_second": True,       # 次選(提示列的「次選 …」和「2」徽章)
    # 全域快捷鍵(遊戲視窗在前面也有效)。可以寫 F1~F12,前面可加 Ctrl+ / Alt+ / Shift+
    "hotkeys": {"toggle_bar": "F8", "toggle_dots": "F9", "toggle_second": "F7"},
}
# 你做了這些動作 -> 建議清掉。立直不在裡面:宣告立直之後才要選宣言牌,
# 「立直,打 X」要留著到你打出去為止。
P0_ACTIONS = ("dahai", "chi", "pon", "daiminkan", "ankan", "kakan", "hora")
START_FLASH_S = 3.0
NONE_ADVICE_S = 4.0            # 「不鳴」這種建議顯示幾秒就收
WINDS_ZH = {"E": "東", "S": "南", "W": "西", "N": "北"}
KYOKU_FLASH_S = 2.0
# 手牌徽章(2026-11-11 設計稿方案 C):(底色, 外框, 字色, 字)
#   主選  綠底黑字「1」   次選  深底淺綠框「2」   鳴牌  金底寫動作(吃/碰/槓)
# 三種用「形狀 + 明暗」區分,不靠紅/藍色相,色弱也分得清。
BADGES = {"main": ("#3ecf9a", "#0d0f12", "#0d0f12", "1"),
          "second": ("#1b1e24", "#bff0dd", "#bff0dd", "2"),
          "call": ("#f2c14e", "#0d0f12", "#0d0f12", None)}


def _rel(actor, pid):
    if actor is None or pid is None:
        return "?"
    return {0: "你", 1: "下家", 2: "對家", 3: "上家"}[(actor - pid) % 4]


def load_config():
    cfg = dict(DEFAULTS)
    if os.path.isfile(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, encoding="utf-8") as fh:
                cfg.update(json.load(fh))
        except Exception as e:
            print(f"⚠️ config.json 讀不了,用預設值:{e}")
    return cfg


class Tee:
    """主控台輸出同時寫進 console.log(換電腦出問題時要看)。"""

    def __init__(self, stream, path):
        self.stream = stream
        self.fh = open(path, "a", encoding="utf-8")

    def write(self, s):
        try:
            self.stream.write(s)
        except Exception:
            pass
        self.fh.write(s)
        self.fh.flush()

    def flush(self):
        try:
            self.stream.flush()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# 辨識執行緒 <-> 字幕列:只透過 queue 傳訊息
# ---------------------------------------------------------------------------
class Hooks:
    def __init__(self, q, stop_ev, cfg, out_dir):
        self.q = q
        self.stop_ev = stop_ev
        self.cfg = cfg
        self.started = False
        self.blocked = set()
        self.desync = set()
        self.resyncs = 0
        self.ends = []                # 這一局的結束事件(和了/流局),end_kyoku 時一起顯示
        self.hwnd = None              # 遊戲視窗(手牌徽章要畫在它上面)
        self.rect_fn = None
        self.hand_state = None        # (手牌偵測, 摸入牌框):辨識執行緒寫、字幕列讀
        self.last_reach = None        # 上一筆「建議立直」的回覆(次選要用)
        self.last_hand = None
        self.adv_fh = open(os.path.join(out_dir, "advice.ndjson"), "a",
                           encoding="utf-8")
        self._cv2 = None

    # --- record_obs.main 會呼叫的 ---------------------------------------
    def status(self, text, kind="info"):
        self.q.put(("status", text, kind))

    def window(self, hwnd, rect_fn=None):
        self.hwnd = hwnd
        self.rect_fn = rect_fn        # () -> 遊戲畫面在螢幕上的 (x, y, w, h)

    def hand(self, tiles, drawn_box):
        self.hand_state = (list(tiles), drawn_box)   # 整個換掉:另一條執行緒讀也安全

    def reply(self, trigger, reply):
        from mortal_view import advice_marks, advice_text, riichi_advice
        if (trigger or {}).get("type") == "reach_spec":
            # 「立直的話打哪張」(mortal_guard 預先問的),跟上一筆「建議立直」合併
            text, marks = riichi_advice(self.last_reach, reply)
            self.adv_fh.write(json.dumps({"t": time.time(), "trigger": trigger,
                                          "reply": reply, "text": text},
                                         ensure_ascii=False) + "\n")
            self.adv_fh.flush()
            if text:
                self.q.put(("advice", text, False, marks))
            return
        if reply.get("type") == "reach":
            self.last_reach = reply
        text = advice_text(reply)
        self.adv_fh.write(json.dumps({"t": time.time(), "trigger": trigger,
                                      "reply": reply, "text": text},
                                     ensure_ascii=False) + "\n")
        self.adv_fh.flush()
        if text:
            self.q.put(("advice", text, reply.get("type") == "none",
                        advice_marks(reply)))

    def log(self, text):
        if text.startswith("⚠️ Mortal"):
            self.q.put(("alert", "Mortal 停了,下一局開始時會自動重開"))
        elif text.startswith("🔄 重開 Mortal"):
            # ★ 只認這一句:「🔄 H001 已從畫面重新同步」也是 🔄 開頭,
            #   2026-11-04 實戰被當成 Mortal 重啟,字幕一直顯示「Mortal 重新啟動」
            self.q.put(("note", "Mortal 重新啟動"))

    def fps(self, fps):
        self.q.put(("fps", fps))

    def small(self, width):
        """遊戲畫面太小(width 像素寬)/ None = 恢復正常。"""
        self.q.put(("small", width))

    def frame(self, img):
        if not self.cfg.get("show_preview"):
            return
        if self._cv2 is None:
            import cv2
            self._cv2 = cv2
        cv2 = self._cv2
        cv2.imshow("preview", cv2.resize(img, (640, 360)))
        cv2.waitKey(1)

    def should_stop(self):
        return self.stop_ev.is_set()

    def events(self, evs, pipe):
        g = pipe.guard
        if g is None:
            return
        if g.game_started and not self.started:
            self.started = True
            self.q.put(("start",))
        pid = g.self_actor
        for m in pipe.sent:
            t = m.get("type")
            replay = m.get("can_act") is False      # 重新同步時重送的歷史
            if t == "start_kyoku":
                kyoku = m.get("kyoku")
                label = (f"{WINDS_ZH.get(m.get('bakaze'), '?')}{kyoku}局"
                         f" {m.get('honba', 0)}本場")
                self.ends = []
                self.q.put(("kyoku", label, not replay))
            elif replay:
                continue
            elif t == "hora" and pid is not None:
                who, loser = _rel(m.get("actor"), pid), _rel(m.get("target"), pid)
                self.ends.append(f"{who}自摸" if m.get("actor") == m.get("target")
                                 else f"{who}榮和({loser}放銃)")
            elif t == "ryukyoku":
                self.ends.append("流局")
            elif t in ("end_kyoku", "end_game"):
                self.q.put(("hand_end", "、".join(self.ends)))
                self.ends = []
            elif t in P0_ACTIONS and pid is not None and \
                    m.get("actor") == pid:
                self.q.put(("clear_advice",))
        for h, hs in g.hands.items():
            if hs.state.startswith("BLOCKED") and h not in self.blocked:
                self.blocked.add(h)
                self.q.put(("alert", "辨識有誤,本局暫停建議(下一局自動恢復)"))
            elif hs.state == "DESYNC" and h not in self.desync:
                self.desync.add(h)
                self.q.put(("alert", "辨識有誤,下次輪到你摸牌時自動重新同步"))
            elif hs.state != "DESYNC":
                self.desync.discard(h)
        if g.resync_count != self.resyncs:
            self.resyncs = g.resync_count
            self.q.put(("resynced",))

    def close(self):
        try:
            self.adv_fh.close()
        except Exception:
            pass
        if self._cv2 is not None:
            try:
                self._cv2.destroyAllWindows()
            except Exception:
                pass


def _docker_ok():
    try:
        r = subprocess.run("docker info", shell=True, capture_output=True,
                           timeout=30)
        return r.returncode == 0
    except Exception:
        return False


def _image_ok():
    try:
        r = subprocess.run("docker image inspect mortal", shell=True,
                           capture_output=True, timeout=30)
        return r.returncode == 0
    except Exception:
        return False


def _find_window(title):
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "_cap1", os.path.join(_paths.PROJECT_DIR, "1_capture.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m.find_window(title)


def worker(q, stop_ev, cfg):
    run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    rec = cfg["record_dir"]
    base = rec if os.path.isabs(rec) else os.path.join(ROOT_DIR, rec)
    out = os.path.join(base, run_id)
    os.makedirs(out, exist_ok=True)
    tee = Tee(sys.__stdout__, os.path.join(out, "console.log"))
    sys.stdout = sys.stderr = tee
    hooks = Hooks(q, stop_ev, cfg, out)
    q.put(("hooks", hooks))
    try:
        print(f"=== {run_id} 紀錄資料夾 {out}")
        hk = cfg.get("hotkeys") or {}
        print(f"快捷鍵:{hk.get('toggle_bar', '-')} 字幕列  "
              f"{hk.get('toggle_dots', '-')} 手牌徽章  "
              f"{hk.get('toggle_second', '-')} 次選(開/關)")
        print("★ 這個黑色視窗可以縮小,但不要關掉。要結束請按字幕列的 [停止]。")
        q.put(("record", out))
        # 1. 顯示卡
        try:
            import torch
            gpu = torch.cuda.is_available()
            name = torch.cuda.get_device_name(0) if gpu else ""
        except Exception:
            gpu, name = False, ""
        q.put(("device", "GPU" if gpu else "CPU"))
        print(f"裝置:{'GPU ' + name if gpu else 'CPU(可能跟不上牌局)'}")
        # 2. Docker / Mortal
        if cfg.get("mortal"):
            while not _docker_ok():
                q.put(("status", "Docker Desktop 沒有開,請先開啟 Docker Desktop…",
                       "error"))
                if stop_ev.wait(5):
                    return
            if cfg["mortal"].strip().lower() == "docker" and not _image_ok():
                q.put(("status", "找不到 mortal 映像 —— 請重新開啟雀魂AI助手.exe 完成安裝",
                       "error"))
                return
        # 3. 雀魂視窗
        hwnd = None
        want = cfg.get("hwnd")
        if want:
            # 主程式(雀魂AI助手.exe)已經讓使用者確認過畫面,直接用那個視窗
            try:
                import win32gui
                if win32gui.IsWindow(want):
                    hwnd = want
                    print(f"✅ 使用主程式選好的視窗 hwnd={hwnd}:"
                          f"{win32gui.GetWindowText(hwnd)}")
            except Exception:
                hwnd = None
        while hwnd is None:
            q.put(("status", "尋找雀魂視窗…", "info"))
            hwnd = _find_window(cfg.get("window_title"))
            if hwnd is None:
                q.put(("status", "找不到雀魂視窗,請先開啟遊戲(每 3 秒會再找一次)",
                       "error"))
                if stop_ev.wait(3):
                    return
        # 4. 錄製 + 辨識 + Mortal
        import record_obs
        argv = ["--hwnd", str(hwnd), "--out", out, "--no-preview"]
        if cfg.get("mortal"):
            argv += ["--mortal", cfg["mortal"]]
        else:
            argv += ["--fsm"]
        if cfg.get("save_video"):
            argv += ["--save-video"]
        rc = record_obs.main(argv, hooks=hooks)
        if rc and not stop_ev.is_set():
            q.put(("status", f"辨識程式結束了(代碼 {rc}),細節見 console.log",
                   "error"))
    except Exception as e:
        traceback.print_exc()
        q.put(("status", f"發生錯誤:{e}(細節見 console.log)", "error"))
    finally:
        hooks.close()
        print(f"=== {run_id} 結束,檔案在 {out}")
        sys.stdout, sys.stderr = sys.__stdout__, sys.__stderr__
        tee.fh.close()
        q.put(("worker_done",))


# ---------------------------------------------------------------------------
# 字幕列
# ---------------------------------------------------------------------------
COLORS = {"bg": "#121418", "info": "#c9cdd4", "ready": "#5fd7ff",
          "start": "#3ecf9a", "advice": "#ffffff", "warn": "#ffb35c",
          "error": "#ff6b6b", "alert": "#ffb35c", "dim": "#9aa0aa",
          "call": "#f2c14e", "line": "#2e333d", "btn": "#232730",
          "btnh": "#2f343e", "stop": "#3a1f22", "stoph": "#4a272b",
          "stopfg": "#ff8a8a", "ok": "#3ecf9a"}
# 提示列整條的底色/外框:要按宣告鍵的動作是金色系,辨識有誤是橘色系(設計稿)
TONES = {"call": ("#261e0a", "#6b5212"), "alert": ("#2a1e10", "#5a3a16"),
         "error": ("#2a1416", "#5a2a2e")}
FONT_UI = "Microsoft JhengHei UI"


def split_advice(text):
    """「建議:打 北　次選:打 9s」-> ("打 北", "打 9s")。不是建議的字串原樣回傳。"""
    if not text.startswith("建議:"):
        return text, None
    body = text[len("建議:"):]
    if "　次選:" in body:
        main, second = body.split("　次選:", 1)
        return main, second
    return body, None


class Bar:
    def __init__(self, cfg):
        import tkinter as tk
        self.tk = tk
        self.cfg = cfg
        self.q = queue.Queue()
        self.stop_ev = threading.Event()
        self.thread = None
        self.closing = False
        self.restart = False
        self.device = "…"
        self.fps = None
        self.kyoku = ""
        self.base = ("啟動中…", "info")
        self.flash = None             # (text, kind, until)
        self.advice = None            # (text, until or None)
        self.alert = None
        self.small = None             # 遊戲畫面太小時是它的寬度(像素)
        self.synced = False
        self.error = None             # 紅字錯誤:蓋過建議,下一個正常狀態出現時清掉
        self.hooks = None
        self.marks = None             # 這次建議要在手牌上標的牌(advice_marks)
        self.show_dots = bool(cfg.get("show_dots", True))
        self.show_second = bool(cfg.get("show_second", True))
        self.bar_hidden = False
        self._last_top = 0.0

        self._tone = None
        self._moved = False

        r = self.root = tk.Tk()
        r.title("MahjongSoul AI Agent")     # 標題不要含「雀魂」:找遊戲視窗會用這兩個字
        r.overrideredirect(True)
        r.attributes("-topmost", True)
        try:
            r.attributes("-alpha", float(cfg.get("bar_alpha", 0.92)))
        except Exception:
            pass
        w, h = int(cfg.get("bar_width", 1000)), 58
        sw = r.winfo_screenwidth()
        r.geometry(f"{w}x{h}+{(sw - w) // 2}+0")
        bg = COLORS["bg"]
        r.configure(bg=bg, highlightthickness=1,
                    highlightbackground=COLORS["line"])
        self._bgw = []                 # 換底色(金色/橘色狀態)時要一起換的元件

        def B(wd):
            self._bgw.append(wd)
            return wd
        # 左:裝置 / fps / 局數
        left = B(tk.Frame(r, bg=bg, width=160))
        left.pack(side="left", fill="y", padx=(16, 0))
        left.pack_propagate(False)
        l1 = B(tk.Frame(left, bg=bg))
        l1.pack(fill="x", pady=(9, 0))
        self.led = B(tk.Canvas(l1, width=8, height=8, bg=bg, highlightthickness=0))
        self.led.create_oval(0, 0, 7, 7, fill=COLORS["dim"], outline="",
                             tags="led")
        self.led.pack(side="left", padx=(0, 6))
        self.left1 = B(tk.Label(l1, text="", fg=COLORS["dim"], bg=bg,
                                font=(FONT_UI, 9), anchor="w"))
        self.left1.pack(side="left")
        self.left2 = B(tk.Label(left, text="", fg=COLORS["dim"], bg=bg,
                                font=(FONT_UI, 9), anchor="w"))
        self.left2.pack(fill="x")
        tk.Frame(r, bg=COLORS["line"], width=1).pack(side="left", fill="y",
                                                      pady=11, padx=(4, 0))
        # 右:圖示按鈕(縮小 / 新的一場 / 停止)
        btns = B(tk.Frame(r, bg=bg))
        btns.pack(side="right", padx=(0, 10))
        icons = icon_glyphs(tk)
        for key, tip, cmd in (("min", "縮小(F8)", self.toggle_bar),
                              ("new", "新的一場", self.on_new),
                              ("stop", "停止", self.on_stop)):
            stop = key == "stop"
            b = tk.Label(btns, text=icons[key][0], font=icons[key][1],
                         width=3, pady=6, cursor="hand2",
                         bg=COLORS["stop" if stop else "btn"],
                         fg=COLORS["stopfg" if stop else "info"])
            b.pack(side="left", padx=2)
            b.bind("<Enter>", lambda e, b=b, s=stop: b.config(
                bg=COLORS["stoph" if s else "btnh"]))
            b.bind("<Leave>", lambda e, b=b, s=stop: b.config(
                bg=COLORS["stop" if s else "btn"]))
            b.bind("<ButtonRelease-1>", lambda e, c=cmd: (
                None if self._moved else c()))
        # 中:主文字 + 次選
        mid = B(tk.Frame(r, bg=bg))
        mid.pack(side="left", fill="both", expand=True)
        inner = B(tk.Frame(mid, bg=bg))
        inner.pack(expand=True)
        self.main = B(tk.Label(inner, text="", fg=COLORS["info"], bg=bg,
                               font=(FONT_UI, 17, "bold")))
        self.main.pack(side="left")
        self.sub = B(tk.Label(inner, text="", fg=COLORS["dim"], bg=bg,
                              font=(FONT_UI, 11)))
        self.sub.pack(side="left", padx=(14, 0))
        # 綁在根視窗就好:裡面的元件透過 bindtags 也會觸發。
        # 按鈕是 Label,放開時沒拖動才算按下(self._moved)。
        r.bind("<ButtonPress-1>", self._drag_start)
        r.bind("<B1-Motion>", self._drag)
        r.protocol("WM_DELETE_WINDOW", self.on_stop)
        self.dots = HandDots(r)
        self._make_mini(cfg)
        start_hotkeys(self.q, cfg.get("hotkeys") or {})
        self.start_worker()
        r.after(100, self.poll)

    # --- 拖動 --------------------------------------------------------------
    def _drag_start(self, e):
        self._dx = e.x_root - self.root.winfo_x()
        self._dy = e.y_root - self.root.winfo_y()
        self._press = (e.x_root, e.y_root)
        self._moved = False

    def _drag(self, e):
        if abs(e.x_root - self._press[0]) + abs(e.y_root - self._press[1]) > 4:
            self._moved = True
        if self._moved:
            self.root.geometry(f"+{e.x_root - self._dx}+{e.y_root - self._dy}")

    def _set_tone(self, kind):
        """整條提示列換成金色(宣告)/ 橘色(辨識有誤)/ 一般。"""
        tone = TONES.get(kind)
        if tone == self._tone:
            return
        self._tone = tone
        bg, line = tone or (COLORS["bg"], COLORS["line"])
        self.root.config(bg=bg, highlightbackground=line)
        for wd in self._bgw:
            try:
                wd.config(bg=bg)
            except Exception:
                pass

    # --- 辨識執行緒 ----------------------------------------------------------
    def start_worker(self):
        self.stop_ev = threading.Event()
        self.base = ("啟動中…", "info")
        self.flash = self.advice = self.alert = self.error = None
        self.small = None
        self.synced = False
        self.kyoku, self.fps = "", None
        self.thread = threading.Thread(target=worker, daemon=True,
                                       args=(self.q, self.stop_ev, self.cfg))
        self.thread.start()

    def on_new(self):
        if self.closing:
            return
        self.restart = True
        self.base = ("存檔中,準備新的一場…", "info")
        self.flash = self.advice = self.alert = self.error = None
        self.small = None
        self.synced = False
        self.stop_ev.set()

    def on_stop(self):
        if self.closing:
            return
        self.closing = True
        self.base = ("結束中,正在存檔…(請稍候)", "info")
        self.flash = self.advice = self.alert = self.error = None
        self.small = None
        self.synced = False
        self.stop_ev.set()
        if self.thread is None or not self.thread.is_alive():
            self.root.after(300, self.root.destroy)

    # --- 訊息 ----------------------------------------------------------------
    def poll(self):
        """每 0.1 秒一次。★ 一定要排下一次:2026-11-04 實戰在主控台按了 Ctrl+C,
        KeyboardInterrupt 打斷這裡 -> 沒排下一次 -> 字幕列從此不再更新(看起來
        像消失了),紅點停在舊位置。現在 Ctrl+C 已經忽略,這裡也包起來。"""
        try:
            self._poll()
        except BaseException:
            traceback.print_exc()
        finally:
            if not getattr(self, "_destroyed", False):
                self.root.after(100, self.poll)

    def _poll(self):
        now = time.time()
        try:
            while True:
                m = self.q.get_nowait()
                k = m[0]
                if k == "status":
                    text, kind = m[1], m[2]
                    if kind == "error":
                        if not self.closing:
                            self.error = text
                    else:
                        self.error = None
                        if kind == "ready":
                            self.base = ("就緒,等待對局開始…", "ready")
                        elif not self.closing:
                            self.base = (text, kind)
                elif k == "start":
                    self.flash = ("開始辨識", "start", now + START_FLASH_S)
                    self.base = ("辨識中", "dim")
                elif k == "kyoku":
                    self.kyoku = m[1]
                    self.synced = False
                    self.alert = None
                    self.advice = self.marks = None
                    if m[2]:                      # 真的換局(不是重新同步)
                        self.base = ("辨識中", "dim")
                        if not (self.flash and self.flash[0] == "開始辨識"
                                and now < self.flash[2]):
                            self.flash = (f"{m[1]} 開始", "start",
                                          now + KYOKU_FLASH_S)
                elif k == "hand_end":
                    what = f"本局結束:{m[1]}" if m[1] else "本局結束"
                    self.base = (f"{what}　等待下一局…", "ready")
                    self.advice = self.marks = self.alert = None
                elif k == "hooks":
                    self.hooks = m[1]
                elif k == "advice":
                    self.advice = (m[1], now + NONE_ADVICE_S if m[2] else None)
                    if self.flash and self.flash[0].endswith(" 開始"):
                        self.flash = None         # 建議比「東2局 開始」重要
                    self.marks = m[3] if len(m) > 3 else None
                elif k == "clear_advice":
                    self.advice = self.marks = None
                elif k == "alert":
                    self.alert = m[1]
                elif k == "note":
                    self.flash = (m[1], "info", now + 2.0)
                elif k == "small":
                    self.small = m[1]
                    if m[1]:
                        self.flash = (f"遊戲畫面太小(寬 {m[1]} 像素),牌容易讀錯"
                                      f" —— 請把視窗拉大", "alert", now + 4.0)
                elif k == "hotkey":
                    self.on_hotkey(m[1], now)
                elif k == "hotkey_fail":
                    print(f"⚠️ 快捷鍵 {m[1]} 註冊失敗(可能被別的程式用了),"
                          f"可以在 config.json 的 hotkeys 換一個鍵")
                elif k == "resynced":
                    # 不用大字幕蓋住接著來的建議:左邊小字標一下就好
                    self.alert = None
                    self.synced = True
                elif k == "fps":
                    self.fps = m[1]
                elif k == "device":
                    self.device = m[1]
                elif k == "worker_done":
                    if self.closing:
                        self._destroyed = True
                        self.root.destroy()
                        return
                    if self.restart:
                        self.restart = False
                        self.start_worker()
        except queue.Empty:
            pass
        if now - self._last_top > 2.0:
            # 遊戲視窗切到前面時,偶爾會把字幕列/紅點壓下去 -> 定時再拉到最上層
            self._last_top = now
            for w in [self.root, self.mini] + [d["win"] for d in self.dots.wins]:
                try:
                    w.attributes("-topmost", True)
                except Exception:
                    pass
        self.render(now)

    # --- 縮小成小視窗(F8 或點字幕列左邊) ----------------------------------
    def _make_mini(self, cfg):
        tk = self.tk
        m = self.mini = tk.Toplevel(self.root)
        m.overrideredirect(True)
        m.attributes("-topmost", True)
        try:
            m.attributes("-alpha", float(cfg.get("bar_alpha", 0.88)))
        except Exception:
            pass
        m.configure(bg=COLORS["bg"], highlightthickness=1,
                    highlightbackground=COLORS["line"])
        f = (FONT_UI, 10, "bold")
        self.mini_dot = tk.Label(m, text="●", fg=COLORS["dim"], bg=COLORS["bg"],
                                 font=(FONT_UI, 10))
        self.mini_dot.pack(side="left", padx=(10, 2), pady=4)
        self.mini_lab = tk.Label(m, text="AI ▾", fg=COLORS["info"],
                                 bg=COLORS["bg"], font=f)
        self.mini_lab.pack(side="left", padx=(0, 10), pady=4)
        # ★ 只綁在 Toplevel 上:Tk 的 bindtags 會讓裡面的 Label 也觸發它。
        #   2026-11-04 實測:三個都綁 -> 點一下觸發兩次 -> 展開又馬上縮回(閃一下)
        m.bind("<ButtonPress-1>", self._mini_press)
        m.bind("<B1-Motion>", self._mini_drag)
        m.bind("<ButtonRelease-1>", self._mini_release)
        m.withdraw()

    def _mini_press(self, e):
        self._mp = (e.x_root, e.y_root, self.mini.winfo_x(),
                    self.mini.winfo_y())
        self._mmoved = False

    def _mini_drag(self, e):
        x0, y0, wx, wy = self._mp
        if abs(e.x_root - x0) + abs(e.y_root - y0) > 4:
            self._mmoved = True
        self.mini.geometry(f"+{wx + e.x_root - x0}+{wy + e.y_root - y0}")

    def _mini_release(self, e):
        if not self._mmoved:          # 點一下(沒拖動)= 展開字幕列
            self.toggle_bar()

    def toggle_bar(self):
        r, m = self.root, self.mini
        self.bar_hidden = not self.bar_hidden
        if self.bar_hidden:
            r.update_idletasks()
            m.update_idletasks()
            mw = m.winfo_reqwidth()
            x = r.winfo_x() + (r.winfo_width() - mw) // 2
            m.geometry(f"+{x}+{r.winfo_y()}")
            r.withdraw()
            m.deiconify()
            m.attributes("-topmost", True)
        else:
            mw = m.winfo_width()
            x = m.winfo_x() + mw // 2 - r.winfo_width() // 2
            r.geometry(f"+{max(0, x)}+{m.winfo_y()}")
            m.withdraw()
            r.deiconify()
            r.attributes("-topmost", True)

    def on_hotkey(self, name, now):
        if name == "toggle_bar":
            self.toggle_bar()
            return
        if name == "toggle_dots":
            self.show_dots = not self.show_dots
            msg = "手牌徽章:" + ("開" if self.show_dots else "關")
        elif name == "toggle_second":
            self.show_second = not self.show_second
            msg = "次選:" + ("開" if self.show_second else "關")
        else:
            return
        self.flash = (msg, "info", now + 1.5)

    def render(self, now):
        fps = f" · {self.fps:.1f} fps" if self.fps is not None else ""
        cpu = self.device == "CPU"
        self.left1.config(text=f"{self.device}{fps}" + ("(可能跟不上)" if cpu else ""))
        if self.small:
            # 視窗太小:左邊第二行改成橘色警告,一直顯示到拉大為止
            self.left2.config(text=f"⚠ 畫面太小({self.small}px)",
                              fg=COLORS["alert"])
        else:
            self.left2.config(text=(self.kyoku or "—")
                              + ("(已重新同步)" if self.synced else ""),
                              fg=COLORS["dim"])
        if self.flash and now > self.flash[2]:
            self.flash = None
        if self.advice and self.advice[1] and now > self.advice[1]:
            self.advice = self.marks = None
        h = self.hooks
        marks = self.marks if (self.advice and self.show_dots) else None
        if marks and not self.show_second:
            marks = [x for x in marks if x[1] != "second"]
        self.dots.show(marks, h.hand_state if h else None,
                       h.hwnd if h else None,
                       getattr(h, "rect_fn", None) if h else None)
        sub = ""
        if self.flash:
            text, kind = self.flash[0], self.flash[1]
        elif self.error:
            text, kind = self.error, "error"
        elif self.alert:
            text, kind = self.alert, "alert"
        elif self.advice:
            text, second = split_advice(self.advice[0])
            kind = "advice"
            if second and self.show_second:
                sub = "次選 " + second
            if any(w in text for w in ("吃", "碰", "槓", "立直", "和了")) \
                    and not text.startswith("打"):
                kind = "call"             # 要你按宣告鍵的建議:金色,比較顯眼
        else:
            text, kind = self.base
        self._set_tone(kind)
        self.main.config(text=text, fg=COLORS.get(kind, COLORS["info"]))
        self.sub.config(text=sub)
        if self.error or self.small or cpu:
            led = COLORS["alert"]
        elif self.device in ("GPU", "CPU") and self.fps is not None:
            led = COLORS["ok"]
        else:
            led = COLORS["dim"]
        self.led.itemconfig("led", fill=led)
        # 縮小時的小膠囊:圓點顏色跟提示列一樣;要宣告時直接寫動作(金色)
        col = COLORS.get(kind, COLORS["info"]) if kind != "dim" else COLORS["dim"]
        self.mini_dot.config(fg=col)
        if kind == "call":
            act = next((w for w in ("立直", "和了", "吃", "碰", "槓")
                        if w in text), "")
            self.mini_lab.config(text=f"{act}! ▾", fg=COLORS["call"])
            self.mini.config(highlightbackground=TONES["call"][1])
        else:
            self.mini_lab.config(text="AI ▾", fg=COLORS["info"])
            self.mini.config(highlightbackground=COLORS["line"])


# ---------------------------------------------------------------------------
# 圖示按鈕的字型(Win10/11 內建 Segoe MDL2 Assets;沒有就退回一般字)
# ---------------------------------------------------------------------------
def icon_glyphs(tk):
    try:
        import tkinter.font as tkfont
        fams = set(tkfont.families())
    except Exception:
        fams = set()
    for fam in ("Segoe Fluent Icons", "Segoe MDL2 Assets"):
        if fam in fams:
            f = (fam, 11)
            return {"min": ("\uE921", f), "new": ("\uE72C", f),
                    "stop": ("\uE71A", f)}
    f = (FONT_UI, 11, "bold")
    return {"min": ("－", f), "new": ("↻", f), "stop": ("■", f)}


# ---------------------------------------------------------------------------
# 手牌上方的數字徽章(以前是紅點 / 藍點)
# ---------------------------------------------------------------------------
def pick_tiles(marks, tiles, drawn_box):
    """advice_marks + 這一刻的手牌偵測 -> [(主選/次選, 牌框 1920x1080 座標)]

    摸切(prefer_drawn)就標摸入牌;手切盡量標暗牌裡的那張(同一種牌有好幾張
    時標最左邊那張)。摸入牌手牌模型沒讀到時,用局勢模型的 drawn_tile 框。"""
    from tiles import base_pai
    tiles = sorted(tiles or [], key=lambda t: t["box"][0])
    drawn = None
    if drawn_box:
        x1, _, x2, _ = drawn_box
        drawn = next((t for t in tiles
                      if x1 <= (t["box"][0] + t["box"][2]) / 2 <= x2), None) \
            or {"pai": None, "box": list(drawn_box)}
    elif len(tiles) % 3 == 2 and len(tiles) >= 2:
        a, b = tiles[-2]["box"], tiles[-1]["box"]
        if b[0] - a[2] > 0.25 * (b[2] - b[0]):      # 最右邊那張分開擺 = 摸入牌
            drawn = tiles[-1]
    out, used = [], set()
    for pai, kind, prefer_drawn in marks or ():
        cands = [t for t in tiles if t["pai"] == pai and id(t) not in used] \
            or [t for t in tiles if base_pai(t["pai"]) == base_pai(pai)
                and id(t) not in used]
        pick = None
        if prefer_drawn and drawn is not None and id(drawn) not in used and (
                drawn["pai"] is None or base_pai(drawn["pai"]) == base_pai(pai)):
            pick = drawn
        elif cands:
            rest = [t for t in cands if t is not drawn]
            pick = (rest or cands)[0]
        if pick is not None:
            used.add(id(pick))
            out.append((kind, pick["box"]))
    return out


class HandDots:
    """遊戲視窗上、建議那張牌正上方的數字徽章(1 主選 / 2 次選 / 鳴牌寫動作)。
    每一顆是一個很小的無框視窗:永遠在最上層、背景透明、滑鼠點擊穿透(不會擋到打牌)。"""

    KEY = "#010203"           # 透明色
    N = 4                     # 吃/碰最多標 2 張 + 次選 1 張

    def __init__(self, root):
        import tkinter as tk
        self.tk = tk
        self.wins = []
        self.size = None
        for _ in range(self.N):
            w = tk.Toplevel(root)
            w.overrideredirect(True)
            w.attributes("-topmost", True)
            w.configure(bg=self.KEY)
            try:
                w.attributes("-transparentcolor", self.KEY)
            except Exception:
                pass
            c = tk.Canvas(w, bg=self.KEY, highlightthickness=0, bd=0)
            c.pack(fill="both", expand=True)
            w.withdraw()
            self.wins.append({"win": w, "canvas": c, "shown": False,
                              "kind": None, "click_through": False})

    @staticmethod
    def _client_rect(hwnd):
        import win32gui
        if win32gui.IsIconic(hwnd):
            return None
        _, _, cw, ch = win32gui.GetClientRect(hwnd)
        ox, oy = win32gui.ClientToScreen(hwnd, (0, 0))
        return (ox, oy, cw, ch) if cw > 0 and ch > 0 else None

    def _click_through(self, d):
        if d["click_through"]:
            return
        try:
            import ctypes
            u = ctypes.windll.user32
            hwnd = u.GetParent(d["win"].winfo_id()) or d["win"].winfo_id()
            # WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE
            ex = u.GetWindowLongW(hwnd, -20)
            u.SetWindowLongW(hwnd, -20,
                             ex | 0x80000 | 0x20 | 0x80 | 0x08000000)
        except Exception:
            pass
        d["click_through"] = True

    def _draw(self, d, kind, size):
        if d["kind"] == (kind, size):
            return
        c = d["canvas"]
        c.delete("all")
        if kind.startswith("call"):
            fill, ring, fg, _ = BADGES["call"]
            label = kind.split(":", 1)[1] if ":" in kind else "鳴"
            font = (FONT_UI, -int(size * 0.5), "bold")
        else:
            fill, ring, fg, label = BADGES.get(kind, BADGES["main"])
            font = ("Segoe UI", -int(size * 0.56), "bold")
        m = 1
        w = max(2, size // 12)
        c.create_oval(m, m, size - m, size - m, fill=fill, outline=ring, width=w)
        c.create_text(size / 2, size / 2 + 0.5, text=label, fill=fg, font=font)
        d["kind"] = (kind, size)

    def hide(self):
        for d in self.wins:
            if d["shown"]:
                d["win"].withdraw()
                d["shown"] = False

    def show(self, marks, hand_state, hwnd, rect_fn=None):
        if not marks or not hand_state or not hwnd:
            self.hide()
            return
        try:
            rect = self._client_rect(hwnd)
            if rect is not None and rect_fn is not None:
                rect = rect_fn()          # 網頁版:瀏覽器裡的遊戲區域
        except Exception:
            rect = None
        if rect is None:
            self.hide()
            return
        ox, oy, cw, ch = rect
        sx, sy = cw / 1920.0, ch / 1080.0
        size = max(18, int(round(30 * sx)))
        gap = max(3, int(round(6 * sy)))
        picks = pick_tiles(marks, *hand_state)
        for i, d in enumerate(self.wins):
            if i >= len(picks):
                if d["shown"]:
                    d["win"].withdraw()
                    d["shown"] = False
                continue
            kind, (x1, y1, x2, _y2) = picks[i]
            cx = ox + (x1 + x2) / 2.0 * sx
            top = oy + y1 * sy
            x, y = int(cx - size / 2), int(top - gap - size)
            self._draw(d, kind, size)
            d["win"].geometry(f"{size}x{size}+{x}+{y}")
            if not d["shown"]:
                d["win"].deiconify()
                d["win"].attributes("-topmost", True)
                d["shown"] = True
                d["win"].update_idletasks()
                self._click_through(d)


# ---------------------------------------------------------------------------
# 全域快捷鍵(Windows RegisterHotKey):遊戲視窗在前面也收得到
# ---------------------------------------------------------------------------
VK_F = {f"F{i}": 0x6F + i for i in range(1, 13)}
MODS = {"ALT": 0x1, "CTRL": 0x2, "SHIFT": 0x4}


def parse_hotkey(text):
    """"F8" / "Ctrl+Alt+F9" -> (modifiers, vk);看不懂回 None。"""
    parts = [x.strip().upper() for x in str(text).split("+") if x.strip()]
    if not parts:
        return None
    mod = 0
    for p in parts[:-1]:
        if p not in MODS:
            return None
        mod |= MODS[p]
    key = parts[-1]
    if key in VK_F:
        return mod, VK_F[key]
    if len(key) == 1 and key.isalnum() and mod:      # 字母/數字一定要加修飾鍵
        return mod, ord(key)
    return None


def start_hotkeys(q, keys):
    def run():
        try:
            import ctypes
            import ctypes.wintypes as wt
            u = ctypes.windll.user32
        except Exception:
            return                       # 不是 Windows(測試環境)
        ids = {}
        for i, (name, text) in enumerate(keys.items(), 1):
            hk = parse_hotkey(text)
            if hk is None or not u.RegisterHotKey(None, i, hk[0] | 0x4000,
                                                  hk[1]):
                q.put(("hotkey_fail", f"{name}={text}"))
                continue
            ids[i] = name
        msg = wt.MSG()
        while u.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            if msg.message == 0x0312:            # WM_HOTKEY
                q.put(("hotkey", ids.get(msg.wParam)))
    threading.Thread(target=run, daemon=True).start()


def main():
    # ★ 不在這裡自己設 DPI 感知:用 capture.py 的設定(抓視窗座標靠它),
    #   另外設別的模式,第二次設定會靜靜失敗,抓圖座標就可能偏掉。
    # window.json(記住的雀魂視窗)是用相對路徑讀的 -> 工作目錄固定在 app\
    os.chdir(APP_DIR)
    # ★ 主控台的 Ctrl+C 不要打斷字幕列(2026-11-04 實戰:按了之後字幕列不再更新)。
    #   要結束請按字幕列的 [停止]。
    import signal
    for sig in ("SIGINT", "SIGBREAK"):
        try:
            signal.signal(getattr(signal, sig), signal.SIG_IGN)
        except (AttributeError, ValueError, OSError):
            pass
    # 先載入 capture:它在 import 時把程式設成 DPI 感知。要在建立任何視窗之前,
    # 字幕列/紅點的螢幕座標才會跟抓圖用的真實像素一致(螢幕縮放 125% 時也對得上)。
    try:
        import capture  # noqa: F401
    except Exception:
        pass
    cfg = load_config()
    # 雀魂AI助手.exe 會傳 --hwnd(使用者在畫面確認頁選好的視窗)
    argv = sys.argv[1:]
    if "--hwnd" in argv:
        try:
            cfg["hwnd"] = int(argv[argv.index("--hwnd") + 1])
        except (IndexError, ValueError):
            pass
    Bar(cfg).root.mainloop()


if __name__ == "__main__":
    main()
