r"""快速測試:牌河超過 18 張(第 3 排往外長)時,讀不讀得到框外的牌。

真的打到 19 張以上很少見(一局約 70 次摸牌,一家大約 17~18 張),所以用兩種方式測:

1) 用錄影模擬(隨時可以測)
    python tools\test_overflow.py 紀錄\<資料夾>
    python tools\test_overflow.py 紀錄\<資料夾> --seat P0 --add 4
  每一家找一幀「第 3 排已經有牌」的畫面,把第 3 排的牌圖接著貼在最後一張後面:
  先補滿到 18 張,再多貼 --add 張(= 第 19、20、21 張,超出原本的框),然後跟
  正式程式一樣:框自動擴張 -> 讀牌。第 19 張起每一張讀到沒有、讀成什麼,
  印成一張表;圖存在
  紀錄\<資料夾>\overflow_test\P0.png …
      紅框 = 原本的框      綠框/藍框 = 擴張後實際讀的兩個窗
      黃字 = 讀到的牌      洋紅框 = 貼上去的假牌(答案寫在框上)

2) 實戰當下(真的遇到超過 18 張時)
    python tools\test_overflow.py --live
  抓一張現在的遊戲畫面,四家照正式程式的方式擴框、讀牌,存成
  overflow_live_<時間>.png(放在這支程式旁邊),印出每家讀到幾張。
  ★ 這個模式不需要先停掉主程式,兩個可以同時跑。
"""
# --- 除錯工具放在 tools\,主程式在 app\:先把那裡加進搜尋路徑 ---
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.join(_os.path.dirname(_os.path.abspath(__file__)),
                                  "..", "app", "live_v7"))
import argparse
import csv
import json
import os
import sys
import time

import _paths  # noqa: E402

import cv2  # noqa: E402

from river import (RIVER_ZONES, SEATS, grow_zone, needed_growth,  # noqa: E402
                   rows_of, sort_by_discard_order)

GROW_LOOKAHEAD, GROW_MARGIN, GROW_MAX = 2, 10, 200    # 跟 record_obs.py 一樣
MIN_CONF = 0.5


def load_model():
    from river_model import RiverModel
    return RiverModel(os.path.join(_paths.MODEL_DIR, "river_model_best.pt"))


def grown_read(model, frame, seat, rounds=6):
    """跟正式程式一樣:讀 -> 看要不要擴 -> 擴了再讀(正式程式是一幀擴一次)。"""
    extra = 0
    zone = RIVER_ZONES[seat]
    dets = []
    for _ in range(rounds):
        zone = grow_zone(seat, RIVER_ZONES[seat], extra)
        dets = [d for d in model.detect(frame, zones={seat: zone})
                if d["seat"] == seat]
        good = [d for d in dets if d["conf"] >= MIN_CONF]
        need = needed_growth(seat, sort_by_discard_order(seat, good), zone,
                             GROW_LOOKAHEAD, GROW_MARGIN) if good else 0
        if not need or extra >= GROW_MAX:
            break
        extra = min(GROW_MAX, extra + need)
    return zone, dets


def draw(frame, seat, zone, dets, model, fakes=()):
    img = frame.copy()
    x, y, w, h = RIVER_ZONES[seat]
    cv2.rectangle(img, (x, y), (x + w, y + h), (0, 0, 255), 2)
    for k, (wx, wy, ww, wh) in enumerate(model.windows(seat, zone)):
        col = (0, 200, 0) if k == 0 else (255, 120, 0)
        cv2.rectangle(img, (wx + 3 * k, wy + 3 * k),
                      (wx + ww - 3 * k, wy + wh - 3 * k), col, 2)
    for f in fakes:
        x1, y1, x2, y2 = f["box"]
        cv2.rectangle(img, (x1, y1), (x2, y2), (255, 0, 255), 2)
        cv2.putText(img, f["pai"], (x1 + 3, y1 + 18), 0, 0.55,
                    (255, 0, 255), 2)
    for d in dets:
        if d["conf"] < MIN_CONF:
            continue
        x1, y1, x2, y2 = d["box"]
        cv2.putText(img, f"{d['pai']}", (x1 + 3, y2 - 6), 0, 0.6,
                    (0, 255, 255), 2)
    zx, zy, zw, zh = zone
    pad = 70
    return img[max(0, zy - pad):zy + zh + pad, max(0, zx - pad):zx + zw + pad]


def make_fakes(frame, seat, dets, add):
    """第 3 排最後一張後面接著貼牌(用第 3 排前面幾張的圖):先補滿到 18 張,
    再多貼 add 張 = 第 19、20… 張(真正超出原本框的那幾張)。"""
    good = [d for d in dets if d["conf"] >= MIN_CONF]
    order = sort_by_discard_order(seat, good)
    rows = rows_of(seat, order)
    if not order or max(rows) < 2:
        return None, None
    row3 = [d for d, r in zip(order, rows) if r == max(rows)]
    if len(row3) < 2:
        return None, None
    # 一張牌往前一格的位移:每一排相鄰兩張的位移取中位數(只看最後兩張會
    # 被中間漏讀/被鳴走的空格騙,間距變兩倍)
    steps = []
    for r in set(rows):
        rr = [d for d, k in zip(order, rows) if k == r]
        steps += [(q["center"][0] - p_["center"][0],
                   q["center"][1] - p_["center"][1])
                  for p_, q in zip(rr, rr[1:])]
    steps.sort(key=lambda v: abs(v[0]) + abs(v[1]))
    dx, dy = steps[len(steps) // 2]
    img = frame.copy()
    fakes = []
    H, W = img.shape[:2]
    fill = max(0, 6 - len(row3))           # 第 3 排一排 6 張:先補到 18 張
    # 拿來貼的牌要完整(被框邊切到的那張只有半截,貼上去不像一張牌)
    srcs = [d for d in order if not d.get("clipped")] or row3
    for k in range(fill + add):
        src = srcs[k % len(srcs)]
        x1, y1, x2, y2 = src["box"]
        last = row3[-1]["box"]
        nx = last[0] + dx * (k + 1)
        ny = last[1] + dy * (k + 1)
        w, h = x2 - x1, y2 - y1
        if nx < 0 or ny < 0 or nx + w >= W or ny + h >= H:
            break
        img[ny:ny + h, nx:nx + w] = frame[y1:y2, x1:x2]
        fakes.append({"pai": src["pai"], "box": (nx, ny, nx + w, ny + h),
                      "center": (nx + w // 2, ny + h // 2),
                      "no": 12 + len(row3) + k + 1})       # 第幾張
    return img, fakes


def pick_frames(run_dir, seats, stable=20):
    """每一家找「第 3 排已經有好幾張(14~18 張)、而且穩定一陣子」的一幀。

    取第一段連續 stable 幀張數不變的中間那一幀 —— 不要拿最後幾幀:錄影結尾
    常常是結算畫面,牌河上蓋著名次橫幅。"""
    want, run = {}, {s: (None, 0, None) for s in seats}
    with open(os.path.join(run_dir, "observations.ndjson"),
              encoding="utf-8") as fh:
        for line in fh:
            o = json.loads(line)
            for s in seats:
                if s in want:
                    continue
                p = (o.get("river") or {}).get(s) or {}
                n = len([x for x in p.get("tiles") or []
                         if x["conf"] >= MIN_CONF])
                cnt, length, first = run[s]
                if 14 <= n <= 18 and n == cnt:
                    length += 1
                else:
                    cnt, length, first = n, 1, o["frame_index"]
                run[s] = (cnt, length, first)
                if 14 <= cnt <= 18 and length >= stable:
                    want[s] = o["frame_index"] - stable // 2
    return want


def read_frames(run_dir, frame_ids):
    idx = {}
    with open(os.path.join(run_dir, "video_index.csv"),
              encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            idx[int(r["video_frame"])] = int(r["frame_index"])
    need = set(frame_ids)
    out = {}
    cap = cv2.VideoCapture(os.path.join(run_dir, "frames.mp4"))
    i = 0
    while need:
        ok, f = cap.read()
        i += 1
        if not ok:
            break
        fi = idx.get(i)
        if fi in need:
            out[fi] = cv2.resize(f, (1920, 1080))
            need.discard(fi)
    return out


def run_recording(a):
    if not os.path.isfile(os.path.join(a.run, "frames.mp4")):
        sys.exit("這個資料夾沒有 frames.mp4(錄影)。config.json 的 save_video "
                 "要是 true 才有錄影。")
    seats = [a.seat] if a.seat else SEATS
    picks = pick_frames(a.run, seats)
    if not picks:
        sys.exit("這場錄影裡沒有任何一家打到第 3 排(13 張以上),換一場試試。")
    frames = read_frames(a.run, picks.values())
    model = load_model()
    out_dir = os.path.join(a.run, "overflow_test")
    os.makedirs(out_dir, exist_ok=True)
    print(f"\n{'家':<4}{'幀':>7}  {'模擬':<6}{'結果'}")
    total = ok_n = 0
    for s in seats:
        f = frames.get(picks.get(s))
        if f is None:
            print(f"{s:<4}{'-':>7}  這一家沒打到第 3 排,略過")
            continue
        _z, dets = grown_read(model, f, s)
        img, fakes = make_fakes(f, s, dets, a.add)
        if not fakes:
            print(f"{s:<4}{picks[s]:>7}  第 3 排讀不到兩張以上,無法模擬")
            continue
        zone, got = grown_read(model, img, s)
        res = []
        for fk in [f_ for f_ in fakes if f_["no"] > 18]:
            cx, cy = fk["center"]
            m = [d for d in got if abs(d["center"][0] - cx) < 25
                 and abs(d["center"][1] - cy) < 25]
            m.sort(key=lambda d: -d["conf"])
            total += 1
            tag = f"#{fk['no']} {fk['pai']}"
            if m and m[0]["pai"] == fk["pai"]:
                ok_n += 1
                res.append(f"{tag}✔({m[0]['conf']:.2f})")
            elif m:
                res.append(f"{tag}✘讀成{m[0]['pai']}")
            else:
                res.append(f"{tag}✘沒讀到")
        win = model.windows(s, zone)
        print(f"{s:<4}{picks[s]:>7}  補到18張+{len(res)}  " + "  ".join(res)
              + f"   (框擴 {max(zone[2] - RIVER_ZONES[s][2], zone[3] - RIVER_ZONES[s][3])}px,"
              f"{len(win)} 個窗)")
        cv2.imwrite(os.path.join(out_dir, f"{s}.png"),
                    draw(img, s, zone, got, model, fakes))
    print(f"\n超出 18 張的假牌 {total} 張,讀對 {ok_n} 張。圖在 {out_dir}")


def run_live(a):
    os.chdir(_paths.PROJECT_DIR)            # window.json 用相對路徑
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "_cap1", os.path.join(_paths.PROJECT_DIR, "1_capture.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    hwnd = m.find_window(a.window)
    if hwnd is None:
        sys.exit("找不到雀魂視窗")
    from capture import WindowCapture
    frame = WindowCapture(hwnd).grab()
    if frame is None:
        sys.exit("抓不到遊戲畫面(視窗是不是最小化了?)")
    model = load_model()
    here = os.path.dirname(os.path.abspath(__file__))
    stamp = time.strftime("%Y%m%d_%H%M%S")
    print()
    for s in SEATS:
        zone, dets = grown_read(model, frame, s)
        good = sort_by_discard_order(
            s, [d for d in dets if d["conf"] >= MIN_CONF])
        grew = max(zone[2] - RIVER_ZONES[s][2], zone[3] - RIVER_ZONES[s][3])
        print(f"{s}  讀到 {len(good):>2} 張  框擴 {grew:>3}px  "
              + " ".join(d["pai"] for d in good))
        cv2.imwrite(os.path.join(here, f"overflow_live_{stamp}_{s}.png"),
                    draw(frame, s, zone, dets, model))
    print(f"\n圖存在 {here}\\overflow_live_{stamp}_P*.png")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("run", nargs="?", help="紀錄資料夾(有 frames.mp4 的)")
    ap.add_argument("--seat", choices=SEATS, help="只測這一家")
    ap.add_argument("--add", type=int, default=3,
                    help="超出 18 張之後再多打幾張(預設 3 = 第 19~21 張)")
    ap.add_argument("--live", action="store_true", help="抓現在的遊戲畫面")
    ap.add_argument("--window", default="雀魂")
    a = ap.parse_args()
    if a.live:
        run_live(a)
    elif a.run:
        run_recording(a)
    else:
        ap.print_help()


if __name__ == "__main__":
    main()
