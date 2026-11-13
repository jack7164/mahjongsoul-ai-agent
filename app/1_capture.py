# -*- coding: utf-8 -*-
"""1. 畫面擷取 —— capture.py 的功能測試與基準量測。

這一支確認「程式看到的畫面」跟「螢幕上的畫面」是同一回事,並且量出整條
管線的**上游預算**。它錯了後面全錯,而且錯得很安靜。

================================================================================
一、要檢查的七件事
================================================================================

1. 找得到那個視窗
   ⚠️ 用標題找有陷阱:執行中的指令列會被 Windows 設成終端機視窗的標題,
      所以只要指令裡打了遊戲視窗的名字,終端機自己也符合條件,而且按下
      Enter 的當下它就在最上層。多個符合時要停下來列候選,不猜。
      建議一律用 --hwnd。

2. DPI 感知生效了嗎 —— ✅ 這個可以修
   螢幕縮放不是 100% 時(筆電常見 125%、150%),沒宣告 DPI 感知的程式拿到的
   是「縮小過的假座標」:實際 1920x1080 的視窗,GetWindowRect 回報 1536x864
   (= 1920/1.25)。但 PrintWindow 畫出來的是真實像素,兩者對不上的結果是
   點陣圖只開 1536x864 -> **只抓到畫面左上 80%**,而且所有固定座標整體偏掉。
   修法是在碰任何視窗 API **之前**宣告 DPI 感知(capture.py 在 import 時做)。
   ★ 判斷方法:看「用戶端」那一行。1536x864、1280x720 這種剛好是
     1920/1.25、1920/1.5 的數字就是中獎了。

3. 寬高比是不是 16:9 —— ❌ 這個不能修
   所有固定座標都是照 1920x1080 量的。寬高比不對的話,硬縮成 1920x1080 會讓
   畫面變形,**那次跑出來的座標全部不能用**。這不是「修正」得了的事,只能
   偵測出來、講清楚,請人把視窗調好再重跑。

4. 裁掉標題列和邊框
   PrintWindow 畫的是**整個視窗**,含標題列。要用 ClientToScreen 算偏移量把
   外框裁掉。沒裁或裁錯 = 畫面偏一塊,所有座標整體平移。

5. 全黑圖
   PrintWindow 對某些 GPU 合成/分層繪製的視窗會回**一張全黑圖而且不報錯**。
   靠 PW_RENDERFULLCONTENT 這個 flag,但它不是萬靈丹,所以要檢查亮度。

6. 最小化
   最小化的視窗沒有繪製內容,抓不到。要明確講出來,不能回黑圖裝沒事。
   ★ 但**不需要在最上層** —— 被終端機或編輯器蓋住照樣抓得到。

7. 來源尺寸要記下來
   只要寬高比是 16:9,不同解析度收的座標縮放後就通用;寬高比不對的那批只能
   整批作廢。當下沒記,事後完全無從判斷一份舊資料能不能信。

================================================================================
二、★ 速率量測(這一支最重要的產出)
================================================================================

要回答的是:「一個只出現 D 秒的東西(例如鳴牌宣告橫幅),我們會不會整個
漏掉?」決定它的是**我們多久看一次**:

    擷取速率 R  ->  取樣間隔 T = 1/R
        D >= T  ->  不可能漏(一定至少取樣到一次)
        D <  T  ->  漏掉的機率 = 1 - D/T

    而且取樣次數跟信心度門檻是相乘的:
        抓到的機率 ~ 1 - (1-p)^n    n = D x 迴圈速率
                                    p = 單幀爬過門檻的機率
    p=0.3 的話:n=3 -> 66%,n=5 -> 83%,n=8 -> 94%。
    **提高幀率跟降低門檻是同一條曲線上的兩個旋鈕,不要只調一個。**

★ 「某個東西只出現 N 幀」這種說法**沒有意義**,除非同時知道當時的迴圈速率。
  幀是我們的性質,秒才是遊戲的性質。一律換算:D = N / 迴圈速率。

--------------------------------------------------------------------------
怎麼量「遊戲自己重繪多快」
--------------------------------------------------------------------------
雀魂就算沒人動作,牌面的閃爍特效也一直在跑。所以 **遊戲只要重繪一次,畫面
就一定會變**。反過來說:

    這一幀跟上一幀完全相同  =  遊戲根本沒重繪  =  我們抓得比它快

於是「完全相同的比例」直接量出遊戲的重繪速率,而且**靜止畫面反而是最好的
量測時機** —— 特效會連續跑,不需要有人出牌。等你按播放之前的那段空轉不是
浪費,它就是量測本身。

⚠️ 之前用過「跟前一幀不一樣的幀佔幾成」當指標,那是錯的:麻將大部分時間
   畫面本來就是靜止的,那個數字量到的是「這段時間發生了多少事」,不是取樣
   能力。實機量到 3.3 fps 並不代表遊戲每秒只畫 3.3 次。

--------------------------------------------------------------------------
比對的方式:數「變了幾個點」,不是「平均差多少」
--------------------------------------------------------------------------
每隔 SIG_STRIDE 個像素取一點(240x135),不做平均,數有多少取樣點真的變了。

  縮圖取平均不行:一張牌只佔畫面 0.27%,就算整塊從 60 變成 255,全畫面平均
  也只動 0.5 左右 —— 跟一層很淡但很大片的特效完全分不開。

這樣會得到三層:

    完全相同        遊戲沒重繪           -> 我們有餘裕
    有變,但很小    只有特效在動(閒置)
    變很多          實質變化(出牌、鳴牌動畫)

★ 第 2/3 層的門檻**這一輪不猜**。一張牌跟一層特效哪個訊號比較強是經驗問題,
  所以先把分佈跟直方圖印出來,看得出兩群再把門檻放在谷底(--big)。
  看不出兩群的話代表這個判準分不開 —— 那也是結論。

================================================================================
三、自動找視窗
================================================================================
capture.py 本來就會用標題找,但那條路有個缺口,它自己的註解寫得很白:

    console_hwnd():「在 Windows Terminal 底下,這個 API 回傳的是隱藏的
    ConPTY 視窗,不是你看得見的那個終端機視窗」

你看到的終端機屬於 WindowsTerminal.exe,GetConsoleWindow() 給的卻是 python
自己那個隱藏的 ConPTY。排除了看不見的,看得見的還在候選裡 —— 而它的標題剛好
含「雀魂」(因為指令裡打了),又剛好在最上層。所以只能停下來叫人選。

這裡改用別的屬性,都不需要新套件:

    視窗類別    終端機的類別是固定那幾個,直接排除(見 TERMINAL_CLASSES)
    行程名稱    分得出遊戲本體 / 瀏覽器分頁 / 編輯器 / 聊天視窗
    用戶端尺寸  小於 640x360 一律排除
    寬高比      只剩一個 16:9 的時候拿它當決勝點

★ 第一次指定之後會把**類別 + 行程名稱**記進 window.json。hwnd 每次開都會變,
  類別跟 exe 不會,所以之後直接認得出來 —— 選一次就不用再選。

★ 保留一條規則:**候選超過一個就停下來列出來,不自己挑。**
  不是謹慎,是代價不對稱 —— 貼一次號碼花五秒,挑錯視窗然後跑二十分鐘的辨識
  回不來,而且未必一眼看得出來。

================================================================================
四、用法
================================================================================
    python 1_capture.py                      自動找(找不到或太多會列出來)
    python 1_capture.py --list
    python 1_capture.py --hwnd 12345                   檢查 + 量 20 秒
    python 1_capture.py --hwnd 12345 --no-preview      量真正的上限(建議)
    python 1_capture.py --hwnd 12345 --seconds 30
    python 1_capture.py --hwnd 12345 --save check.png  存程式看到的那一幀
    python 1_capture.py --hwnd 12345 --save-raw raw.png  未裁切未縮放的原圖
    python 1_capture.py --selftest                     不用遊戲,驗純函式
    python 1_capture.py --forget                       忘掉記住的視窗

    ⚠️ 速率要在**對局進行中**量。等待畫面是靜止的,重複幀會灌到 95% 以上,
       那個數字沒有意義。

    量到的結果會附加到 capture_baseline.txt(--no-record 可關)。
    那份是後面每一支的預算來源,不要只看終端機就關掉。
"""
import argparse
import json
import os
import sys
import time
from datetime import datetime

import cv2
import numpy as np

from capture import (DPI_MODE, WindowCapture, resolve_target_window,
                     window_dpi_scale)

TARGET_W, TARGET_H = 1920, 1080

# 判斷「這一幀跟上一幀一樣」用的縮圖尺寸。縮到這麼小是為了便宜:全尺寸比對
# 一次要好幾毫秒,會把要量的東西本身弄髒。
# 比對相鄰兩幀用的取樣間隔。每隔 8 個像素取一點 -> 240x135,不做平均。
#
# ★ 為什麼不縮圖:縮圖是「平均」,一張牌只佔畫面 0.27%,平均下去幾乎看不見。
#   取樣是「抽點」,牌上面的取樣點是真的變了,訊號不會被整張畫面稀釋。
SIG_STRIDE = 8
# 單一取樣點的三個通道裡,最大變化超過這個才算「這一點變了」。
# 12 是為了跳過編碼雜訊,不是為了分辨特效跟出牌 —— 那個靠下面的分佈。
PIXEL_DELTA = 12

# 「有實質變化」的門檻:變了的取樣點佔多少比例。
#
# ★ 2026-10-20 實機量出來的。897 幀的分佈換算成對數密度之後是兩群:
#       0.05 ~ 0.10%  密度 246   <- 第一群的峰(只有閃爍特效)
#       0.20 ~ 0.50%  密度 106   <- 谷底
#       1.00 ~ 5.00%  密度 245   <- 第二群的峰(出牌 / 鳴牌動畫)
#   (直接比計數會被騙 —— 那幾格的寬度差 100 倍。)
#
#   0.3% 放在谷底,而且剛好有物理意義:--selftest 裡「一張牌(60x95)整塊
#   變成全白」算出來是 0.296%。所以這個門檻讀作:
#       **變動量小於一張牌 = 只有特效;大於一張牌 = 有東西真的動了。**
BIG_CHANGE = 0.003

# 畫面平均亮度低於這個就當成全黑
DARK_MEAN = 2.0

DEFAULT_SECONDS = 20.0
RECORD_FILE = "capture_baseline.txt"
REMEMBER_FILE = "window.json"

# 終端機的視窗類別。排除這些就不會再被「指令列把自己的標題設成遊戲名稱」
# 那個陷阱咬到 —— 那是 capture.py 的 console_hwnd() 擋不住的那一個缺口。
TERMINAL_CLASSES = {
    "ConsoleWindowClass",             # 傳統 conhost
    "CASCADIA_HOSTING_WINDOW_CLASS",  # Windows Terminal
    "PseudoConsoleWindow",            # ConPTY
    "Windows.UI.Core.CoreWindow",     # UWP 殼
}
# 用戶端比這個小的一律不是遊戲視窗(終端機、小工具、提示框)
MIN_CLIENT_W, MIN_CLIENT_H = 640, 360
# 寬高比離 16:9 多近才算「像遊戲視窗」。跟 capture.py 的 0.03 一致。
ASPECT_SLACK = 0.03


# ---------------------------------------------------------------------------
# 純函式(--selftest 驗這幾個,不需要遊戲也不需要 Windows)
# ---------------------------------------------------------------------------

def signature(img):
    """一幀的取樣指紋:每隔 SIG_STRIDE 個像素抽一點,不做平均。"""
    return img[::SIG_STRIDE, ::SIG_STRIDE].astype(np.int16)


def frame_diff(a, b):
    """相鄰兩幀的差。回傳「有多少比例的取樣點真的變了」(0.0 ~ 1.0)。

    === 為什麼用「變了的點佔幾成」而不是「平均差多少」 ===
    平均差會被整張畫面稀釋:一張牌只佔畫面 0.27%,就算那塊從 60 變成 255,
    全畫面平均也只動 0.5 左右 —— 跟一層很淡但很大片的特效分不開。
    改成數「有幾個點變了」之後,一張牌是一個面積明確的訊號。

    === 完全相同(回傳 0.0)代表什麼 ===
    雀魂就算沒人動作,牌面的閃爍特效也一直在跑。所以**遊戲只要重繪一次,
    畫面就一定會變**。反過來說:
        這一幀跟上一幀完全相同  =  遊戲根本沒重繪  =  我們抓得比它快
    這就是量「遊戲重繪速率」的判準,而且靜止畫面反而是最好的量測時機 ——
    特效會連續跑,不需要有人出牌。
    """
    if a is None or b is None:
        return 1.0
    d = np.abs(a - b).max(axis=2)
    return float((d > PIXEL_DELTA).mean())


def pct(values, p):
    """第 p 百分位(0~100)。沒有 numpy 也要能算。"""
    if not values:
        return 0.0
    v = sorted(values)
    i = min(len(v) - 1, max(0, int(round((p / 100.0) * (len(v) - 1)))))
    return v[i]


def is_16_9(info):
    h = info.get("client", (0, 0))[1]
    return bool(h) and abs(info["client"][0] / h - 16 / 9) <= ASPECT_SLACK


def plausible(info, self_pid=None, keyword=None):
    """這個視窗有沒有可能是遊戲視窗。四條硬篩,全部要過。

    刻意不看標題以外的「像不像」—— 標題是唯一不可靠的那一個欄位,
    它會被執行中的指令列污染(見模組說明三)。keyword 只在使用者明確
    給了 --window 的時候才加進來縮小範圍。
    """
    if info["cls"] in TERMINAL_CLASSES:
        return False
    if self_pid is not None and info.get("pid") == self_pid:
        return False
    w, h = info.get("client", (0, 0))
    if w < MIN_CLIENT_W or h < MIN_CLIENT_H:
        return False
    if keyword and keyword.lower() not in info["title"].lower():
        return False
    return True


def pick_window(infos, remembered=None, keyword=None, self_pid=None):
    """從視窗清單挑一個。回傳 (選中的 info 或 None, 原因, 剩下的候選)。

    ★ 純函式,不碰 Windows API —— 所以 --selftest 驗得到,而這段邏輯正是
      最容易悄悄挑錯東西的地方。

    順序:
      1. 四條硬篩(類別 / 行程 / 尺寸 / 可選的關鍵字)
      2. 照 window.json 記住的「類別 + 行程」對 —— 只剩一個就用它
      3. 還是只剩一個 -> 用它
      4. 剩下的裡面只有一個是 16:9 -> 用它(終端機已經被類別擋掉了,
         而最大化的瀏覽器扣掉分頁列也不會剛好是 16:9)
      5. 其餘情況一律回 None,讓呼叫端列出來給人選。**不猜。**
    """
    cands = [i for i in infos if plausible(i, self_pid, keyword)]
    if not cands:
        return None, "沒有任何視窗通過篩選", []

    if remembered:
        same = [i for i in cands
                if i["cls"] == remembered.get("cls")
                and i["exe"] == remembered.get("exe")]
        if len(same) == 1:
            return same[0], "照 window.json 記住的類別 + 行程找到的", cands
        if len(same) > 1:
            cands = same          # 記住的那一類有好幾個,往下繼續縮

    if len(cands) == 1:
        return cands[0], "通過篩選的只有這一個", cands

    wide = [i for i in cands if is_16_9(i)]
    if len(wide) == 1:
        return wide[0], "通過篩選的裡面只有這一個是 16:9", cands

    return None, f"有 {len(cands)} 個候選,不自己挑", cands


def miss_odds(duration_s, update_fps):
    """橫幅存在 duration_s 秒、取樣率 update_fps,整個漏掉的機率。"""
    if update_fps <= 0:
        return 1.0
    t = 1.0 / update_fps
    if duration_s >= t:
        return 0.0
    return 1.0 - duration_s / t


def hist_lines(diffs, big):
    """差異量的分佈。★ 這一輪的主要產出 —— 門檻要從這裡挑,不是用猜的。"""
    if not diffs:
        return ["  (沒有資料)"]
    zero = sum(1 for d in diffs if d == 0.0)
    nz = sorted(d for d in diffs if d > 0.0)
    n = len(diffs)
    lines = [
        f"  完全相同      {zero:5d} 幀  {100 * zero / n:5.1f}%"
        f"   <- 遊戲沒有重繪這一幀",
        f"  有變化        {len(nz):5d} 幀  {100 * len(nz) / n:5.1f}%",
    ]
    if nz:
        lines.append(f"     變化量(變了的取樣點佔比)"
                     f"  中位 {pct(nz, 50) * 100:.3f}%"
                     f"  / 90% {pct(nz, 90) * 100:.3f}%"
                     f"  / 99% {pct(nz, 99) * 100:.3f}%"
                     f"  / 最大 {nz[-1] * 100:.3f}%")
        lines.append("     直方圖(非零的部分):")
        edges = [0.0, 0.0001, 0.0005, 0.001, 0.002, 0.005,
                 0.01, 0.05, 1.01]
        for lo, hi in zip(edges, edges[1:]):
            c = sum(1 for d in nz if lo < d <= hi)
            if not c:
                continue
            bar = "#" * min(40, max(1, round(40 * c / len(nz))))
            lines.append(f"       {lo * 100:7.3f}% ~ {hi * 100:7.3f}%  "
                         f"{c:5d}  {bar}")
        big_n = sum(1 for d in nz if d >= big)
        lines.append(f"     以門檻 {big * 100:.3f}% 算:"
                     f"「實質變化」{big_n} 幀、「只有特效」{len(nz) - big_n} 幀")
        lines.append(f"     ★ 門檻 {big * 100:.1f}% 是 2026-10-20 從分佈的谷底"
                     f"量出來的,而且剛好等於一張牌的面積(60x95 = 0.296%)——")
        lines.append("       讀作「變動量小於一張牌 = 只有特效」。")
        lines.append("     ★ 這次的分佈如果看不到兩群(特效一群在 0.05~0.1%、"
                     "出牌一群在 1~5%),")
        lines.append("       代表畫面狀況跟當時不一樣,門檻要重挑 —— "
                     "用 --big 設。比高度會被騙,")
        lines.append("       那幾格的寬度差 100 倍,要先換算成每單位寬度的密度。")
    return lines


def rate_lines(grabs, fails, diffs, times, change_ts, elapsed, big):
    """三段報告 + 回傳要記進檔案的數字。"""
    lines = [f"── 2. 擷取({elapsed:.1f} 秒,呼叫 grab {grabs + fails} 次)──"]
    if not grabs:
        return lines + ["  ❌ 一幀都沒抓到"], {}

    cap_fps = grabs / elapsed
    sample_ms = 1000.0 / cap_fps
    grab_ms = pct(times, 50) * 1000
    ceiling = 1000.0 / grab_ms if grab_ms else 0.0
    lines.append(f"  grab 成功 {grabs} 次,失敗 {fails} 次"
                 f"(最小化 / PrintWindow 回 0)")
    lines.append(f"  擷取速率    {cap_fps:6.1f} fps   "
                 f"-> 取樣間隔 {sample_ms:.0f} ms")
    lines.append(f"  單次 grab 耗時  最小 {pct(times, 0) * 1000:.1f}"
                 f" / 中位 {grab_ms:.1f}"
                 f" / 95% {pct(times, 95) * 1000:.1f} ms"
                 f"   -> 純 grab 的上限約 {ceiling:.1f} fps")
    lines.append("")

    hist = hist_lines(diffs, big)
    lines.append("── 3. 畫面變化(相鄰兩幀)──")
    lines.extend(hist)
    lines.append("")

    redraw_fps = sum(1 for d in diffs if d > 0.0) / elapsed
    zero_ratio = (sum(1 for d in diffs if d == 0.0) / len(diffs)
                  if diffs else 0.0)
    lines.append("── 4. 遊戲重繪速率 ──")
    if zero_ratio <= 0.001:
        lines.append(f"  ⚠️ 幾乎沒有「完全相同」的幀。代表遊戲重繪得比我們抓得快"
                     f"(或 PrintWindow 每次輸出都有微小差異)。")
        lines.append(f"     這種情況下量不到遊戲的重繪率,只知道它 "
                     f">= {cap_fps:.1f} fps。**我們是上限。**")
    else:
        lines.append(f"  遊戲重繪速率 ≈ {redraw_fps:.1f} fps"
                     f"(非「完全相同」的幀 / 秒)")
        lines.append(f"  完全相同佔 {zero_ratio * 100:.1f}% —— "
                     f"我們抓得比遊戲快,有餘裕 ✅")
        lines.append("  ★ 這個數字不需要有人出牌也量得到 —— 牌面的閃爍特效"
                     "一直在跑,遊戲每重繪一次畫面就一定會變。")

    # 動畫期間的更新間隔:相鄰兩次「有變化」之間隔多久。靜止期間的長間隔
    # 會落在百分位的尾巴,不會污染前段。
    gaps = [b - a for a, b in zip(change_ts, change_ts[1:])]
    if len(gaps) >= 5:
        lines.append(f"  相鄰兩次變化的間隔  最短 {pct(gaps, 0) * 1000:.0f}"
                     f" / 10% {pct(gaps, 10) * 1000:.0f}"
                     f" / 中位 {pct(gaps, 50) * 1000:.0f} ms"
                     f"   (取樣間隔是 {sample_ms:.0f} ms)")

    stats = {"擷取速率": cap_fps, "純grab上限": ceiling,
             "遊戲重繪速率": redraw_fps, "完全相同比例": zero_ratio,
             "grab中位耗時ms": grab_ms}
    return lines, stats, hist


def budget_lines(cap_fps):
    """把量到的擷取速率翻成後面每一支要用的預算。

    ★ 用的是**擷取速率**,不是「畫面變化的速率」。會不會漏掉一個持續 D 秒的
      東西,取決於「我們多久看一次」,跟畫面有多忙無關。
    """
    if cap_fps <= 0:
        return ["── 5. 預算 ──", "  擷取速率是 0,算不出預算。"]
    t_ms = 1000.0 / cap_fps
    lines = [
        "── 5. 預算(抄下來,後面每一支都要用)──",
        f"  擷取速率 R = {cap_fps:.1f} fps,取樣間隔 T = {t_ms:.0f} ms",
        "",
        "  ★ 之後任何一支量到「某個東西只出現 N 幀」,先換算成秒再討論:",
        "        D = N / 當時的迴圈速率   秒",
        "     幀是我們的性質,秒才是遊戲的性質。不換算的話同一個 N 在不同",
        "     速度下差好幾倍,結論完全不同。",
        "",
        "  ★ 一個持續 D 秒的東西,整個被漏掉的機率:",
        f"        D >= T  ->  不可能漏(一定至少取樣到一次)",
        f"        D <  T  ->  漏掉的機率 = 1 - D/T",
        "",
        "  ★ 取樣次數跟信心度門檻是相乘的,不要只調一個:",
        "        抓到的機率 ~ 1 - (1-p)^n     n = D x 迴圈速率",
        "                                      p = 單幀爬過門檻的機率",
        "",
        f"  以這次的擷取速率 {cap_fps:.1f} fps 來看:",
    ]
    for d in (0.15, 0.30, 0.60):
        n = d * cap_fps
        odds = miss_odds(d, cap_fps)
        tail = "不可能漏" if odds == 0 else f"整個漏掉的機率 {odds * 100:.0f}%"
        lines.append(f"      {d * 1000:4.0f} ms 的東西 -> 約 {n:4.1f} 次取樣,{tail}")
    lines.append("")
    lines.append("  ⚠️ 這是**擷取層**的上限。接上模型之後迴圈會慢很多"
                 "(之前整條管線是 5~6 fps),")
    lines.append(f"     也就是 {cap_fps:.1f} 裡有一大半是被推論吃掉的。"
                 f"那是下一層要處理的事,但先知道上限在哪。")
    return lines


# ---------------------------------------------------------------------------
# 視窗檢查
# ---------------------------------------------------------------------------

def window_lines(cap, img):
    """視窗本身的七項檢查。回傳 (報告行, 有沒有致命問題)。"""
    import win32gui

    lines, fatal = ["── 1. 視窗 ──"], False
    left, top, right, bottom = win32gui.GetWindowRect(cap.hwnd)
    _, _, cw, ch = win32gui.GetClientRect(cap.hwnd)
    sx, sy = win32gui.ClientToScreen(cap.hwnd, (0, 0))

    lines.append(f"  hwnd {cap.hwnd}  {win32gui.GetWindowText(cap.hwnd)}")
    lines.append(f"  視窗外框 {right - left}x{bottom - top}"
                 f"   用戶端 {cw}x{ch}"
                 f"   標題列/邊框偏移 ({sx - left}, {sy - top})")
    scale = cap.dpi_scale or window_dpi_scale(cap.hwnd)
    lines.append(f"  螢幕縮放 {scale * 100:.0f}%   DPI 模式 {DPI_MODE}"
                 if scale else f"  螢幕縮放 (取不到)   DPI 模式 {DPI_MODE}")

    # DPI 沒生效的特徵:用戶端尺寸剛好是真實解析度除以縮放比例
    if scale and scale > 1.01:
        lines.append(f"  ⚠️ 縮放 {scale * 100:.0f}%。DPI 沒生效的話用戶端會是"
                     f"假座標 —— 真實像素大約會是 "
                     f"{round(cw * scale)}x{round(ch * scale)}。"
                     f"對照遊戲的畫面設定確認用戶端那個數字對不對。")
    elif scale:
        lines.append("  ✅ 螢幕縮放 100%,沒有假座標的風險")

    w, h = cap.client_size or (cw, ch)
    aspect = w / h if h else 0
    if cap.aspect_ok:
        if (w, h) == (TARGET_W, TARGET_H):
            lines.append(f"  ✅ 寬高比 {aspect:.3f} = 16:9,而且就是 "
                         f"{TARGET_W}x{TARGET_H},不需要縮放(零失真)")
        else:
            lines.append(f"  ✅ 寬高比 {aspect:.3f} = 16:9,{w}x{h} -> "
                         f"{TARGET_W}x{TARGET_H} 等比縮放,座標跟其他解析度通用")
    else:
        fatal = True
        lines.append(f"  ❌ 寬高比 {aspect:.3f} 不是 16:9。縮放會讓畫面變形,"
                     f"**這次跑出來的座標全部不能用**。這不是修得了的事 ——")
        lines.append("     先把視窗調成 16:9(1920x1080 最保險,1600x900、"
                     "1280x720 也可以)再重跑。")

    # PrintWindow 對某些 GPU 合成的視窗會回一張全黑圖,而且不報錯
    mean = float(img.mean())
    if mean < DARK_MEAN:
        fatal = True
        lines.append(f"  ❌ 畫面幾乎全黑(平均亮度 {mean:.1f})。PrintWindow 對"
                     f"這個視窗抓不到內容 —— PW_RENDERFULLCONTENT 沒生效,"
                     f"或視窗被最小化了。")
    else:
        lines.append(f"  ✅ 畫面有內容(平均亮度 {mean:.1f})")
    lines.append("  ✅ 擷取不需要視窗在最上層,被蓋住照樣抓得到(最小化才不行)")
    return lines, fatal


# ---------------------------------------------------------------------------
# Windows 那一半:把視窗列成 info,以及記住 / 忘掉
# ---------------------------------------------------------------------------

def _exe_of(pid):
    """行程的執行檔名。取不到就回空字串 —— 取不到不是錯誤,只是少一個判準。"""
    try:
        import win32api
        import win32con
        import win32process
    except ImportError:
        return ""
    for flags in (getattr(win32con, "PROCESS_QUERY_LIMITED_INFORMATION",
                          0x1000),
                  win32con.PROCESS_QUERY_INFORMATION | win32con.PROCESS_VM_READ):
        try:
            h = win32api.OpenProcess(flags, False, pid)
        except Exception:
            continue
        try:
            return os.path.basename(win32process.GetModuleFileNameEx(h, 0))
        except Exception:
            continue
        finally:
            try:
                win32api.CloseHandle(h)
            except Exception:
                pass
    return ""


def enum_windows():
    """所有看得見、有標題的視窗,附上判斷要用的屬性。"""
    import win32gui
    try:
        import win32process
    except ImportError:
        win32process = None

    out = []

    def handler(hwnd, _):
        if not win32gui.IsWindowVisible(hwnd):
            return
        title = win32gui.GetWindowText(hwnd)
        if not title.strip():
            return
        try:
            _, _, cw, ch = win32gui.GetClientRect(hwnd)
        except Exception:
            cw = ch = 0
        pid = 0
        if win32process is not None:
            try:
                _, pid = win32process.GetWindowThreadProcessId(hwnd)
            except Exception:
                pid = 0
        try:
            cls = win32gui.GetClassName(hwnd)
        except Exception:
            cls = ""
        out.append({"hwnd": hwnd, "title": title, "cls": cls,
                    "pid": pid, "exe": _exe_of(pid) if pid else "",
                    "client": (cw, ch)})

    win32gui.EnumWindows(handler, None)
    return out


def info_line(info, mark=""):
    w, h = info["client"]
    ratio = f"{w / h:.2f}" if h else "?"
    return (f"  {info['hwnd']:<9} {info['title'][:34]:<34} "
            f"{info['cls'][:28]:<28} {info['exe'][:24]:<24} "
            f"{w}x{h} ({ratio}){mark}")


def list_rich():
    """比 capture.print_window_list() 多印類別、行程、用戶端尺寸。

    多印這三欄是有用的:標題不可靠,而這三欄就是 pick_window 拿來判斷的東西
    —— 自動找不出來的時候,看這張表一眼就知道哪一欄可以當判準。
    """
    infos = enum_windows()
    me = os.getpid()
    print(f"  {'hwnd':<9} {'標題':<34} {'類別':<28} {'行程':<24} 用戶端")
    print("  " + "─" * 104)
    for i in sorted(infos, key=lambda x: -x["client"][0] * x["client"][1]):
        why = []
        if i["cls"] in TERMINAL_CLASSES:
            why.append("終端機")
        if i["pid"] == me:
            why.append("本程式")
        w, h = i["client"]
        if w < MIN_CLIENT_W or h < MIN_CLIENT_H:
            why.append("太小")
        print(info_line(i, f"   ← 排除({'/'.join(why)})" if why else
                        ("   ← 候選,16:9" if is_16_9(i) else "   ← 候選")))
    print("\n找到遊戲視窗那一行,用最左邊的數字當 --hwnd 的參數。"
          "指定一次之後會記住,下次就不用了。")


def load_remembered(path=REMEMBER_FILE):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def remember_window(info, path=REMEMBER_FILE):
    """只記類別 + 行程 + 當時的標題。**不記 hwnd** —— 它每次開都會變。"""
    data = {"cls": info["cls"], "exe": info["exe"],
            "title_when_saved": info["title"],
            "saved_at": f"{datetime.now():%Y-%m-%d %H:%M:%S}"}
    try:
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
        return True
    except OSError:
        return False


def find_window(keyword=None):
    """自動找。回傳 hwnd 或 None(None 的時候已經把候選印出來了)。"""
    infos = enum_windows()
    remembered = load_remembered()
    got, why, cands = pick_window(infos, remembered, keyword, os.getpid())
    if got is not None:
        w, h = got["client"]
        print(f"✅ 自動找到:hwnd {got['hwnd']}  {got['title']}")
        print(f"   類別 {got['cls']}  行程 {got['exe'] or '(取不到)'}  "
              f"用戶端 {w}x{h}" + ("  16:9" if is_16_9(got) else ""))
        print(f"   ({why})")
        if not remembered and remember_window(got):
            print(f"   已記進 {REMEMBER_FILE},下次直接用")
        return got["hwnd"]

    print(f"⚠️ 不自動挑 —— {why}。")
    if cands:
        print(f"  {'hwnd':<9} {'標題':<34} {'類別':<28} {'行程':<24} 用戶端")
        print("  " + "─" * 104)
        for i in cands:
            print(info_line(i, "   16:9" if is_16_9(i) else ""))
    print("\n請用 --hwnd <數字> 指定一次,之後會記住。"
          "(完整清單用 --list)")
    return None


def record(path, cap, stats, elapsed, preview_on, extra=()):
    """把這次的基準附加進檔案。終端機會被關掉,檔案不會。

    ★ extra 是分佈/直方圖那幾行。2026-10-20 第一次量的時候只印在終端機,
      結果要討論門檻的時候得請人重貼 —— 跟面板診斷踩過的是同一個錯。
      要拿來做決定的東西就要落檔。
    """
    w, h = cap.client_size or (0, 0)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(f"\n[{datetime.now():%Y-%m-%d %H:%M:%S}] "
                 f"hwnd={cap.hwnd} 用戶端={w}x{h} "
                 f"寬高比{'OK' if cap.aspect_ok else '不合格'} "
                 f"縮放={cap.dpi_scale} DPI={DPI_MODE}\n")
        fh.write(f"  量了 {elapsed:.1f} 秒"
                 f"{'(有開預覽,不是真正的上限)' if preview_on else ''}\n")
        for k, v in stats.items():
            if k.endswith("比例"):
                fh.write(f"  {k} = {v * 100:.1f}%\n")
            elif k.endswith("ms"):
                fh.write(f"  {k} = {v:.1f}\n")
            else:
                fh.write(f"  {k} = {v:.2f} fps\n")
        for line in extra:
            fh.write(f"  {line.rstrip()}\n")


# ---------------------------------------------------------------------------
# --selftest:驗純函式,不需要遊戲
# ---------------------------------------------------------------------------

def selftest():
    fails = []

    def check(name, ok, extra=""):
        print(f"  {'✅' if ok else '❌'} {name}" + (f"  {extra}" if extra else ""))
        if not ok:
            fails.append(name)

    print("\n[0] 這支程式自己呼叫的函式都還在嗎")
    # ★ 2026-10-20:加這一節是因為真的踩到了 —— 改 budget_lines 的時候用
    #   「從 A 到 B 整段取代」,把夾在中間的 window_lines() 一起刪掉了,
    #   而 py_compile 通過、--selftest 也通過(那條路徑沒被測到),
    #   直到實機跑才 NameError。靜態檢查三秒就能抓到。
    import ast as _ast
    import builtins as _bi
    try:
        with open(__file__, encoding="utf-8") as _fh:
            src = _ast.parse(_fh.read())
    except (OSError, NameError, SyntaxError):
        src = None
    if src is None:
        check("(讀不到自己的原始碼,跳過)", True)
    else:
        # 定義過的名字:module 層與巢狀的 def/class、import、所有被指派的
        # 名字、函式參數。寧可寬鬆 —— 這一節要抓的是「整個函式不見了」,
        # 不是靜態型別檢查,誤報會讓人不想看。
        known = set(dir(_bi))
        for node in _ast.walk(src):
            if isinstance(node, (_ast.FunctionDef, _ast.AsyncFunctionDef,
                                 _ast.ClassDef)):
                known.add(node.name)
                a = getattr(node, "args", None)
                if a is not None:
                    for arg in (list(a.args) + list(a.posonlyargs)
                                + list(a.kwonlyargs)
                                + [x for x in (a.vararg, a.kwarg) if x]):
                        known.add(arg.arg)
            elif isinstance(node, (_ast.Import, _ast.ImportFrom)):
                known |= {(x.asname or x.name).split(".")[0]
                          for x in node.names}
            elif isinstance(node, _ast.Name) and isinstance(node.ctx,
                                                            _ast.Store):
                known.add(node.id)
        missing = {}
        for node in _ast.walk(src):
            if isinstance(node, _ast.Call) and isinstance(node.func, _ast.Name):
                if node.func.id not in known:
                    missing.setdefault(node.func.id, node.lineno)
        check("★ 所有被呼叫的函式都找得到(防「整段取代砍過頭」)",
              not missing,
              "、".join(f"{k} (第 {v} 行)" for k, v in missing.items()))

    print("\n[1] 相鄰兩幀的比對")
    base = np.full((1080, 1920, 3), 60, np.uint8)
    check("★ 一模一樣 -> 0.0(= 遊戲沒有重繪這一幀)",
          frame_diff(signature(base), signature(base.copy())) == 0.0)

    # 閃爍特效:大面積、很淡。要「不是 0」(代表遊戲重繪了),
    # 但變化量要明顯小於一張牌 —— 兩群分得開,門檻才有地方放。
    glow = base.astype(np.int16)
    glow[300:800, 400:1500] += 14          # 比 PIXEL_DELTA 高一點點
    glow = np.clip(glow, 0, 255).astype(np.uint8)
    d_glow = frame_diff(signature(base), signature(glow))
    check("★ 淡而大片的特效 -> 不是 0(遊戲有重繪)", d_glow > 0.0,
          f"{d_glow * 100:.3f}%")

    # 一張牌:小面積、變化大
    tile = base.copy()
    tile[900:995, 800:860] = 255
    d_tile = frame_diff(signature(base), signature(tile))
    check("★ 一張牌(60x95)-> 不是 0", d_tile > 0.0, f"{d_tile * 100:.3f}%")

    # 雜訊:每個點都只差一點點,應該整個被 PIXEL_DELTA 吃掉
    rng = np.random.RandomState(0)
    noisy = np.clip(base.astype(np.int16) + rng.randint(-3, 4, base.shape),
                    0, 255).astype(np.uint8)
    check("★ 小雜訊 -> 0.0(不然「完全相同」永遠不會發生,判準就廢了)",
          frame_diff(signature(base), signature(noisy)) == 0.0)

    print(f"      (參考:特效 {d_glow * 100:.3f}%  一張牌 {d_tile * 100:.3f}%"
          f"  —— 真正的門檻要看實機分佈,這裡只確認訊號出得來)")

    print("\n[2] 百分位")
    check("0 / 50 / 100", (pct([1, 2, 3, 4, 5], 0), pct([1, 2, 3, 4, 5], 50),
                           pct([1, 2, 3, 4, 5], 100)) == (1, 3, 5))
    check("空清單不會爆", pct([], 50) == 0.0)

    print("\n[3] 漏掉的機率")
    check("D >= T -> 不可能漏", miss_odds(0.6, 6.7) == 0.0)
    check("D = 半個 T -> 漏掉一半", abs(miss_odds(0.0746, 6.7) - 0.5) < 0.02,
          f"{miss_odds(0.0746, 6.7):.3f}")
    check("取樣率 0 -> 一定漏", miss_odds(0.6, 0) == 1.0)

    print("\n[4] 報告")
    many_zero = [0.0] * 500 + [0.004] * 100
    text = "\n".join(rate_lines(600, 0, many_zero, [0.03] * 600,
                                 [1.0 * i for i in range(100)],
                                 20.0, BIG_CHANGE)[0])
    check("★ 有「完全相同」的幀 -> 算得出遊戲重繪速率",
          "遊戲重繪速率 ≈" in text and "有餘裕" in text)

    text = "\n".join(rate_lines(600, 0, [0.004] * 600, [0.03] * 600,
                                 [0.03 * i for i in range(600)],
                                 20.0, BIG_CHANGE)[0])
    check("★ 完全沒有「完全相同」-> 講明我們是上限,不要假裝量到了",
          "我們是上限" in text,
          [l.strip() for l in text.splitlines() if "上限" in l][0][:50])

    text = "\n".join(hist_lines([0.0] * 10 + [0.0003] * 50 + [0.01] * 5,
                                 0.002))
    check("直方圖印得出兩群", text.count("#") > 0 and "實質變化" in text)
    check("空資料不會爆", hist_lines([], 0.002) == ["  (沒有資料)"])

    print("\n[5] 自動找視窗(pick_window 是純函式,這裡用假資料驗)")

    def win(hwnd, title, cls, exe, w, h, pid=100):
        return {"hwnd": hwnd, "title": title, "cls": cls, "exe": exe,
                "pid": pid, "client": (w, h)}

    GAME = win(1, "雀魂麻將", "UnityWndClass", "Jantama.exe", 1920, 1080)
    # ★ 這一個就是 capture.py 的 console_hwnd() 擋不住的那個:
    #   指令列把終端機的標題設成了遊戲名稱,而且它在最上層
    TERM = win(2, "python 1_capture.py --window 雀魂",
               "CASCADIA_HOSTING_WINDOW_CLASS", "WindowsTerminal.exe",
               1200, 800, pid=999)
    CHROME = win(3, "雀魂麻將 - Google Chrome", "Chrome_WidgetWin_1",
                 "chrome.exe", 1920, 960)
    TINY = win(4, "雀魂 小工具", "SomeClass", "x.exe", 300, 200)
    CHAT = win(5, "跟 Claude 討論雀魂辨識", "Chrome_WidgetWin_1",
               "Claude.exe", 1400, 900)

    got, why, _c = pick_window([GAME, TERM], self_pid=None)
    check("★ 終端機標題含遊戲名稱也不會被選中(靠類別,不靠 console_hwnd)",
          got is GAME, why)

    got, why, _c = pick_window([GAME, TINY], self_pid=None)
    check("太小的視窗被排除", got is GAME, why)

    got, why, _c = pick_window([GAME, CHROME, CHAT], self_pid=None)
    check("★ 只有一個是 16:9 -> 用它當決勝點", got is GAME, why)

    got, why, cands = pick_window([GAME, win(6, "另一個", "A", "a.exe",
                                             1920, 1080)], self_pid=None)
    check("★ 兩個都 16:9 -> 不猜,回 None 讓人選",
          got is None and len(cands) == 2, why)

    remembered = {"cls": "UnityWndClass", "exe": "Jantama.exe"}
    moved = win(77, "雀魂麻將", "UnityWndClass", "Jantama.exe", 1920, 1080)
    got, why, _c = pick_window([moved, CHROME, CHAT], remembered=remembered)
    check("★ hwnd 變了,靠記住的類別+行程還是找得到",
          got is moved and got["hwnd"] == 77, why)

    got, why, _c = pick_window([GAME, CHROME], remembered={"cls": "Nope",
                                                           "exe": "no.exe"})
    check("記住的那個不在了 -> 退回一般篩選,不是直接放棄", got is GAME, why)

    got, why, _c = pick_window([GAME, CHROME, CHAT], keyword="Chrome")
    check("--window 關鍵字會縮小範圍", got is CHROME, why)

    got, why, _c = pick_window([GAME], self_pid=100)
    check("★ 自己的行程被排除(pid 相同)", got is None, why)

    check("16:9 判斷:1920x1080 是", is_16_9(GAME))
    check("16:9 判斷:1920x960 不是(最大化瀏覽器扣掉分頁列)", not is_16_9(CHROME))
    check("16:9 判斷:高度 0 不會除零", not is_16_9(win(9, "x", "c", "e", 100, 0)))

    print("\n" + "─" * 50)
    if fails:
        print(f"❌ {len(fails)} 項沒過")
        return 1
    print("全部通過 ✅")
    return 0


# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", action="store_true", help="列出所有視窗")
    ap.add_argument("--hwnd", type=int, help="要抓的視窗(建議用這個)")
    ap.add_argument("--window", help="用標題的一段文字找(有陷阱,見模組說明)")
    ap.add_argument("--seconds", type=float, default=DEFAULT_SECONDS)
    ap.add_argument("--save", metavar="PNG",
                    help="存下程式實際看到的那一幀(已裁切已縮放)後結束")
    ap.add_argument("--save-raw", metavar="PNG",
                    help="存下未裁切未縮放的原圖後結束(核對裁切用)")
    ap.add_argument("--no-preview", action="store_true",
                    help="不開預覽視窗。量真正的上限要加這個")
    ap.add_argument("--no-record", action="store_true",
                    help=f"不要把結果附加進 {RECORD_FILE}")
    ap.add_argument("--big", type=float, default=BIG_CHANGE,
                    help=f"「實質變化」的暫定門檻(變了的取樣點佔比,"
                         f"預設 {BIG_CHANGE})")
    ap.add_argument("--selftest", action="store_true",
                    help="驗純函式,不需要遊戲")
    ap.add_argument("--forget", action="store_true",
                    help=f"忘掉 {REMEMBER_FILE} 記住的視窗")
    args = ap.parse_args()

    if args.selftest:
        return selftest()
    if args.forget:
        try:
            os.remove(REMEMBER_FILE)
            print(f"已刪掉 {REMEMBER_FILE},下次會重新找。")
        except OSError:
            print(f"{REMEMBER_FILE} 本來就不存在。")
        return 0
    if args.list:
        list_rich()
        return 0

    if args.hwnd:
        # 明確指定優先。指定成功就記住,下次不用再指定。
        hwnd = resolve_target_window(hwnd=args.hwnd)
        if hwnd is None:
            return 2
        got = next((i for i in enum_windows() if i["hwnd"] == hwnd), None)
        if got and remember_window(got):
            print(f"   已記進 {REMEMBER_FILE}(類別 {got['cls']} / "
                  f"行程 {got['exe'] or '取不到'}),下次直接用")
    else:
        hwnd = find_window(args.window)
        if hwnd is None:
            return 2

    cap = WindowCapture(hwnd)
    first = cap.grab()
    if first is None:
        print("❌ 第一幀就抓不到。視窗是不是最小化了?"
              "(被其他視窗蓋住沒關係,最小化才不行)")
        return 2

    print()
    lines, fatal = window_lines(cap, first)
    for line in lines:
        print(line)
    print()

    if args.save_raw:
        raw = cap._print_window()
        if raw is None:
            print("❌ 抓不到原圖")
            return 2
        cv2.imwrite(args.save_raw, raw)
        print(f"未裁切未縮放的原圖寫進 {args.save_raw}"
              f"({raw.shape[1]}x{raw.shape[0]})")
        print("  核對用:標題列應該還在,用戶端內容的左上角就是上面那個偏移量")
        return 0

    if args.save:
        cv2.imwrite(args.save, first)
        print(f"程式實際看到的那一幀寫進 {args.save}"
              f"({first.shape[1]}x{first.shape[0]})")
        print("  ★ 核對固定座標請用這張,不要用 Print Screen —— 那截到的含"
              "標題列,又會被顯示縮放影響,座標跟程式看到的對不起來")
        return 0

    if fatal:
        print("⚠️ 上面有 ❌,先解決再量速率,不然量到的數字沒有意義。\n")

    print(f"🎬 量 {args.seconds:.0f} 秒"
          f"{'' if args.no_preview else '(有開預覽,按 q/ESC 可提早結束)'}")
    print("   ★ 不用配合程式的時機 —— 牌面的閃爍特效一直在跑,所以就算還沒")
    print("     按播放,遊戲的重繪速率照樣量得到。空轉那段不會害到數字。")
    print("   ★ 但這 30 秒裡最好有牌在飛,不然看不到「實質變化」那一群。")
    if not args.no_preview:
        print("   ★ 預覽視窗自己會吃時間。要量真正的上限請加 --no-preview")
    print()

    grabs = fails = 0
    times, diffs, change_ts = [], [], []
    prev = None
    t0 = time.time()
    try:
        while time.time() - t0 < args.seconds:
            t = time.time()
            frame = cap.grab()
            times.append(time.time() - t)
            if frame is None:
                fails += 1
                continue
            grabs += 1
            sig = signature(frame)
            if prev is not None:
                d = frame_diff(sig, prev)
                diffs.append(d)
                if d > 0.0:
                    change_ts.append(time.time())
            prev = sig
            if not args.no_preview:
                cv2.imshow("1_capture", cv2.resize(frame, (1280, 720)))
                if (cv2.waitKey(1) & 0xFF) in (ord("q"), 27):
                    break
    except KeyboardInterrupt:
        print("\n收到 Ctrl+C,提早結束。")
    finally:
        cv2.destroyAllWindows()
        elapsed = max(1e-6, time.time() - t0)
        print()
        lines, stats, hist = rate_lines(grabs, fails, diffs, times,
                                        change_ts, elapsed, args.big)
        for line in lines:
            print(line)
        print()
        if stats:
            for line in budget_lines(stats["擷取速率"]):
                print(line)
            if not args.no_record:
                record(RECORD_FILE, cap, stats, elapsed,
                       not args.no_preview, hist)
                print(f"\n  這次的基準已附加進 {RECORD_FILE}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
