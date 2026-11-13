"""場況(局數/本場/供託/自風/點數)用存下的畫面重讀一次,不用重跑全部模型。

    python tools\\reread_ui.py runs\\20261027_181804
    -> runs\\20261027_181804\\observations_reui.ndjson
    python live_v7\\replay.py runs\\20261027_181804\\observations_reui.ndjson

同時補 P0 摸入牌的樣板比對(drawn_lib.py,寫進 p0_hand.drawn_lib)
和暗牌的樣板修正(寫進 p0_hand.hand_fix);--no-drawn 都不補。

什麼時候用:改了點數/面板辨識(ui\\scores.py、樣板)之後,舊錄影的
ui_context 還是錄製當時讀的。這支只換掉 ui_context,其他模型輸出(牌河、手牌、
鳴牌、寶牌)原封不動。需要錄製時加了 --save-video(frames.mp4 + video_index.csv)。

⚠️ frames.mp4 是壓縮過的,跟錄製當下的畫面不完全一樣。這只能檢查「修正後的
   程式讀不讀得出來」,不能拿來量錄製當下的讀取率。
"""
# --- 除錯工具放在 tools\,主程式在 app\live_v7\:先把那裡加進搜尋路徑 ---
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.join(_os.path.dirname(_os.path.abspath(__file__)),
                                  "..", "app", "live_v7"))
import argparse
import csv
import json
import os
import sys

import _paths


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("run_dir")
    ap.add_argument("--old-dir", default=_paths.DEFAULT_OLD_DIR)
    ap.add_argument("--no-drawn", action="store_true",
                    help="不補 P0 摸入牌的樣板比對(drawn_lib.py)")
    ap.add_argument("--save-lib", help="把這場建出來的摸入牌樣板庫存成檔")
    a = ap.parse_args(argv)
    import cv2
    from kyoku_info import RawUiReader
    obs = os.path.join(a.run_dir, "observations.ndjson")
    vid = os.path.join(a.run_dir, "frames.mp4")
    vix = os.path.join(a.run_dir, "video_index.csv")
    for p in (obs, vid, vix):
        if not os.path.isfile(p):
            print(f"❌ 找不到 {p}(錄製時要加 --save-video)")
            return 2
    with open(vix, encoding="utf-8") as fh:
        idx = {int(r["frame_index"]): int(r["video_frame"])
               for r in csv.DictReader(fh)}
    cap = cv2.VideoCapture(vid)
    rd = RawUiReader(a.old_dir, None)
    lib = None
    if not a.no_drawn:
        import drawn_lib
        lib = drawn_lib.DrawnLibrary(drawn_lib.DEFAULT_LIB)
    out_path = os.path.join(a.run_dir, "observations_reui.ndjson")
    pos = 0
    n = changed = 0
    with open(obs, encoding="utf-8") as fin, \
            open(out_path, "w", encoding="utf-8") as fout:
        for line in fin:
            if not line.strip():
                continue
            o = json.loads(line)
            f = o["frame_index"]
            need_ui = o.get("ui_context") is not None
            need_dr = lib is not None and o.get("p0_hand") is not None
            if (need_ui or need_dr) and f in idx:
                v = idx[f]
                if v != pos + 1:
                    cap.set(cv2.CAP_PROP_POS_FRAMES, v - 1)
                ok, im = cap.read()
                pos = v
                if ok and need_ui:
                    ui, _ = rd.read(im)
                    if ui != o["ui_context"]:
                        changed += 1
                    o["ui_context"] = ui
                    n += 1
                if ok and need_dr:
                    import drawn_lib
                    o["p0_hand"]["drawn_lib"] = drawn_lib.observe(
                        lib, im, o["p0_hand"]["tiles"],
                        o.get("region_detections"))
                    o["p0_hand"]["hand_fix"] = drawn_lib.observe_hand(
                        lib, im, o["p0_hand"]["tiles"],
                        o.get("region_detections"))
            fout.write(json.dumps(o, ensure_ascii=False) + "\n")
    print(f"重讀 {n} 幀場況,{changed} 幀跟原本不同 -> {out_path}")
    if lib is not None and a.save_lib:
        lib.save(a.save_lib)
        print(f"摸入牌樣板庫 {sum(len(v) for v in lib.lib.values())} 張 "
              f"-> {a.save_lib}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
