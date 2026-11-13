"""用錄影(frames.mp4)重跑局勢判斷模型,換掉觀測檔裡的 region_detections。
比較不同版本的局勢判斷模型用:其他模型的輸出(牌河、手牌、鳴牌、場況)不動。

    python tools\\reread_region.py <錄影資料夾> --model <權重> [--imgsz 960] --tag 新640
    python tools\\reread_region.py <錄影資料夾> --dora --tag dora     (只重讀寶牌面板)
    python tools\\reread_region.py <錄影資料夾> --river <牌河權重> --tag 新牌河
    python tools\\reread_region.py <錄影資料夾> --hand <手牌權重> --tag 新手牌
    python tools\\reread_region.py <錄影資料夾> --meld <鳴牌權重> --tag 新鳴牌
    (--model / --dora / --river / --hand / --meld 可以一起給)
    --hand 只重讀錄製當下有跑手牌模型的那幾幀;塗黑用這一幀的局勢偵測(有給 --model
           就是新的),摸入牌樣板比對(drawn_lib)也一起重做。
    --meld 只重讀錄製當下有跑鳴牌模型的那幾幀。權重旁邊要有 <權重名>.crop_meta.json
           (新訓練的:把 meld\\dataset_ready\\crop_meta.json 複製成 best.crop_meta.json)。
    --river 沿用錄製當下每一幀的牌河框(含自動擴張),只換模型。

    輸入:<錄影資料夾>\\observations_reui.ndjson(沒有就用 observations.ndjson)
    輸出:<錄影資料夾>\\observations_<tag>.ndjson
    之後:python live_v7\\replay.py <錄影資料夾>\\observations_<tag>.ndjson --out <資料夾>

⚠️ frames.mp4 是壓縮過的,比實戰抓到的畫面模糊一點 —— 適合「同一份錄影、
   不同模型」互相比較;絕對數字跟實戰不完全一樣。
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

import _paths  # noqa: F401
from obs import det_region


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("run_dir")
    ap.add_argument("--model", default=None)
    ap.add_argument("--imgsz", type=int, default=None)
    ap.add_argument("--tag", required=True)
    ap.add_argument("--floor", type=float, default=0.10,
                    help="同錄製端 --region-floor")
    ap.add_argument("--river", default=None, help="牌河模型權重")
    ap.add_argument("--hand", default=None, help="手牌模型權重")
    ap.add_argument("--meld", default=None, help="鳴牌模型權重")
    ap.add_argument("--dora", action="store_true",
                    help="用 dora_reader(手牌模型讀面板)重讀寶牌")
    a = ap.parse_args(argv)
    if not (a.model or a.dora or a.river or a.hand or a.meld):
        ap.error("--model / --dora / --river / --hand / --meld 至少要給一個")
    import cv2
    from region_model import RegionModel
    src = os.path.join(a.run_dir, "observations_reui.ndjson")
    if not os.path.isfile(src):
        src = os.path.join(a.run_dir, "observations.ndjson")
    vid = os.path.join(a.run_dir, "frames.mp4")
    vix = os.path.join(a.run_dir, "video_index.csv")
    for p in (src, vid, vix):
        if not os.path.isfile(p):
            print(f"❌ 找不到 {p}")
            return 2
    with open(vix, encoding="utf-8") as fh:
        idx = {int(r["frame_index"]): int(r["video_frame"])
               for r in csv.DictReader(fh)}
    region = RegionModel(model_path=a.model, default_conf=a.floor,
                         imgsz=a.imgsz) if a.model else None
    from obs import det_tile
    river_model = None
    if a.river:
        from river_model import RiverModel
        river_model = RiverModel(model_path=a.river)
    hand_model = meld_model = tile_lib = None
    if a.hand:
        import drawn_lib
        from hand_model import HandModel
        # 塗黑用觀測檔裡這一幀的局勢偵測,不需要手牌模型自己再載一顆局勢模型
        hand_model = HandModel(model_path=a.hand,
                               region=region or type("NoRegion", (), {})())
        tile_lib = drawn_lib.DrawnLibrary(drawn_lib.DEFAULT_LIB)
    if a.meld:
        from meld_model import MeldModel
        meld_model = MeldModel(model_path=a.meld)
    dora_reader = None
    if a.dora:
        from dora_reader import DoraReader
        from kyoku_info import DORA_ZONE
        dora_reader = DoraReader()
    cap = cv2.VideoCapture(vid)
    out = os.path.join(a.run_dir, f"observations_{a.tag}.ndjson")
    pos, n = 0, 0
    print(f"來源 {src}\n輸出 {out}")
    with open(src, encoding="utf-8") as fin, \
            open(out, "w", encoding="utf-8") as fout:
        for line in fin:
            if not line.strip():
                continue
            o = json.loads(line)
            v = idx.get(o["frame_index"])
            want_r = region is not None \
                and o.get("region_detections") is not None
            want_d = dora_reader is not None and \
                ((o.get("models") or {}).get("dora") or {}).get("ran")
            want_v = river_model is not None and isinstance(
                o.get("river"), dict) and o["river"]
            ran = o.get("models") or {}
            want_h = hand_model is not None and \
                (ran.get("hand") or {}).get("ran") and o.get("p0_hand")
            want_m = meld_model is not None and \
                (ran.get("meld") or {}).get("ran")
            if v is not None and (want_r or want_d or want_v or want_h
                                  or want_m):
                if v != pos + 1:
                    cap.set(cv2.CAP_PROP_POS_FRAMES, v - 1)
                ok, im = cap.read()
                pos = v
                if ok and im.shape[:2] != (1080, 1920):
                    im = cv2.resize(im, (1920, 1080))
                if ok and want_r:
                    o["region_detections"] = [det_region(d)
                                              for d in region.detect(im)]
                if ok and want_v:
                    zones = {s: tuple(z["zone"]) for s, z in o["river"].items()
                             if z and z.get("zone")}
                    tiles = river_model.detect(im, zones=zones)
                    for s in zones:
                        o["river"][s]["tiles"] = [det_tile(d) for d in tiles
                                                  if d["seat"] == s]
                if ok and want_h:
                    rdets = o.get("region_detections") or []
                    hd = hand_model.detect(
                        im, region_dets=[d for d in rdets
                                         if d["cls"] == "meld_area"
                                         and d["conf"] >= 0.25])
                    tiles = [det_tile(d) for d in hd]
                    o["p0_hand"]["tiles"] = tiles
                    o["p0_hand"]["drawn_lib"] = drawn_lib.observe(
                        tile_lib, im, tiles, rdets)
                    o["p0_hand"]["hand_fix"] = drawn_lib.observe_hand(
                        tile_lib, im, tiles, rdets)
                if ok and want_m:
                    md = meld_model.detect(im)
                    o["meld"] = {s: {"tiles": [det_tile(d) for d in md
                                               if d["seat"] == s]}
                                 for s in ("P0", "P1", "P2", "P3")}
                if ok and want_d:
                    o["dora_observation"] = {
                        "zone": list(DORA_ZONE),
                        "tiles": [{"pai": d["pai"], "conf": d["conf"],
                                   "box": list(d["box"]),
                                   "center": list(d["center"])}
                                  for d in dora_reader.detect(im)]}
                if ok:
                    n += 1
                    if n % 500 == 0:
                        print(f"  {n} 幀")
            fout.write(json.dumps(o, ensure_ascii=False) + "\n")
    print(f"✅ 重跑 {n} 幀 -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
