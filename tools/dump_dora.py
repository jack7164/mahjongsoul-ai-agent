"""把錄影裡的寶牌面板讀一遍,看新的寶牌讀法(dora_reader)讀得對不對。

    python tools\\dump_dora.py ..\\MJAI\\runs\\20261030_183152
    python tools\\dump_dora.py ..\\MJAI\\runs          (整個 runs 資料夾,每場都跑)

每 --every 幀讀一次面板,讀值跟上一次不一樣才記一筆。輸出到每場的 dora\\ 底下:
    dora_log.txt     每次變化:幀號、5 格讀值(## = 牌背、-- = 翻開但沒讀到、.. = 面板變暗不讀)
    p<幀號>.png      那一刻的面板原圖(只有 284x84,很小)
    sheet.png        全部變化的面板疊成一張,旁邊標讀值,給遠端一眼對完
讀值會抖(同一個面板一下 0.69 一下 0.70)不算變化,只看牌名 / 牌背。
"""
# --- 除錯工具放在 tools\,主程式在 app\live_v7\:先把那裡加進搜尋路徑 ---
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.join(_os.path.dirname(_os.path.abspath(__file__)),
                                  "..", "app", "live_v7"))
import argparse
import csv
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import _paths  # noqa: E402,F401


def label(slots, with_conf=True):
    out = []
    for s in slots:
        if s["state"] == "back":
            out.append("##")
        elif s["state"] == "empty":
            out.append("--")
        elif s["state"] == "dim":
            out.append("..")
        else:
            out.append(f'{s["pai"]}{s["conf"]:.2f}' if with_conf else s["pai"])
    return " ".join(out)


def run_one(run_dir, reader, every):
    import cv2
    import numpy as np
    from dora_reader import CROP
    mp4 = os.path.join(run_dir, "frames.mp4")
    if not os.path.isfile(mp4):
        print(f"  (沒有 frames.mp4,跳過 {run_dir})")
        return
    v2f = {}
    vi = os.path.join(run_dir, "video_index.csv")
    if os.path.isfile(vi):
        with open(vi, encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                v2f.setdefault(int(r["video_frame"]), int(r["frame_index"]))
    out = os.path.join(run_dir, "dora")
    os.makedirs(out, exist_ok=True)
    cap = cv2.VideoCapture(mp4)
    v = 0
    prev = None
    rows, pans = [], []
    while True:
        ok = cap.grab()
        if not ok:
            break
        v += 1                          # video_index 的 video_frame 從 1 起算
        if v % every:
            continue
        ok, fr = cap.retrieve()
        if not ok:
            continue
        if fr.shape[:2] != (1080, 1920):
            fr = cv2.resize(fr, (1920, 1080))
        slots = reader.read_slots(fr)
        key = label(slots, with_conf=False)
        if key == prev:
            continue
        prev = key
        f = v2f.get(v, -v)              # 沒有對照表就用負的影片幀號
        txt = label(slots)
        rows.append(f"f{f}\t{txt}\tV=" +
                    ",".join(str(s["hsv"][2]) for s in slots))
        x1, y1, x2, y2 = CROP
        pan = fr[y1:y2, x1:x2].copy()
        cv2.imwrite(os.path.join(out, f"p{f}.png"), pan)
        pans.append((f, txt, pan))
    with open(os.path.join(out, "dora_log.txt"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(rows) + "\n")
    if pans:
        tiles = []
        for f, txt, pan in pans:
            big = cv2.resize(pan, None, fx=2, fy=2)
            band = np.full((big.shape[0], 520, 3), 255, np.uint8)
            cv2.putText(band, f"f{f}", (8, 40), cv2.FONT_HERSHEY_SIMPLEX,
                        0.9, (0, 0, 0), 2)
            cv2.putText(band, txt, (8, 110), cv2.FONT_HERSHEY_SIMPLEX,
                        0.6, (0, 0, 200), 2)
            tiles.append(np.hstack([big, band]))
        # 太長就分好幾張
        for i in range(0, len(tiles), 40):
            name = "sheet.png" if i == 0 else f"sheet_{i // 40 + 1}.png"
            cv2.imwrite(os.path.join(out, name), np.vstack(tiles[i:i + 40]))
    print(f"  {os.path.basename(run_dir)}:{len(rows)} 次變化 -> {out}")


def main(argv=None):
    from dora_reader import DoraReader
    ap = argparse.ArgumentParser()
    ap.add_argument("path", help="一場的資料夾,或整個 runs 資料夾")
    ap.add_argument("--every", type=int, default=5)
    ap.add_argument("--model", default=None)
    a = ap.parse_args(argv)
    if os.path.isfile(os.path.join(a.path, "frames.mp4")):
        runs = [a.path]
    else:
        runs = sorted(os.path.join(a.path, d) for d in os.listdir(a.path)
                      if os.path.isfile(os.path.join(a.path, d, "frames.mp4")))
    reader = DoraReader(a.model)
    for r in runs:
        run_one(r, reader, a.every)


if __name__ == "__main__":
    main()
