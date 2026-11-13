"""從錄影(frames.mp4)抽出指定的幾幀存成 jpg,給遠端分析用。

整支 mp4 太大傳不過來,只抽有問題的那幾段:

    python tools\\dump_frames.py runs\\20261030_183152 --preset t1
    python tools\\dump_frames.py runs\\XXXX 600-700:5 2440-2480

範圍寫法:  起-迄[:間隔]   (frame_index,跟 check_sheet / replay 輸出的 f 編號一樣)
輸出:      <run_dir>\\snap\\f<frame>.jpg (縮成 1280x720、品質 80,一張約 100KB)
"""
# --- 除錯工具放在 tools\,主程式在 app\live_v7\:先把那裡加進搜尋路徑 ---
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.join(_os.path.dirname(_os.path.abspath(__file__)),
                                  "..", "app", "live_v7"))
import argparse
import csv
import os

PRESETS = {
    # 2026-10-30 實戰 runs\\20261030_183152 還卡住的地方
    "t1": ["600-700:4", "2440-2480:2", "2520-2536:2", "3525-3545:2",
           "3880-3960:4", "5408-5436:2", "5680-5696:2", "7108-7128:2"],
}


def parse(spec):
    step = 1
    if ":" in spec:
        spec, s = spec.split(":")
        step = int(s)
    a, _, b = spec.partition("-")
    a = int(a)
    b = int(b) if b else a
    return range(a, b + 1, step)


def main(argv=None):
    import cv2
    ap = argparse.ArgumentParser()
    ap.add_argument("run_dir")
    ap.add_argument("ranges", nargs="*")
    ap.add_argument("--preset", choices=sorted(PRESETS))
    ap.add_argument("--width", type=int, default=1280)
    a = ap.parse_args(argv)
    specs = list(a.ranges) + (PRESETS[a.preset] if a.preset else [])
    if not specs:
        ap.error("要給範圍或 --preset")
    want = sorted({f for s in specs for f in parse(s)})

    idx = {}
    with open(os.path.join(a.run_dir, "video_index.csv"), encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            idx[int(r["frame_index"])] = int(r["video_frame"])
    out = os.path.join(a.run_dir, "snap")
    os.makedirs(out, exist_ok=True)
    cap = cv2.VideoCapture(os.path.join(a.run_dir, "frames.mp4"))
    n = 0
    pos = None
    for f in want:
        v = idx.get(f)
        if v is None:
            continue
        if pos is None or not (pos < v <= pos + 30):
            cap.set(cv2.CAP_PROP_POS_FRAMES, v - 1)
        else:
            while pos + 1 < v:         # 近的就往前讀,不跳(跳轉慢又不準)
                cap.grab()
                pos += 1
        ok, im = cap.read()
        pos = v
        if not ok:
            continue
        h, w = im.shape[:2]
        if a.width and w > a.width:
            im = cv2.resize(im, (a.width, int(h * a.width / w)))
        cv2.imwrite(os.path.join(out, f"f{f}.jpg"), im,
                    [cv2.IMWRITE_JPEG_QUALITY, 80])
        n += 1
    print(f"存了 {n} 張到 {out}")


if __name__ == "__main__":
    main()
