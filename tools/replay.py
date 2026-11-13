"""離線重播:observations.ndjson -> 本地事件帳 + 報告。

    python tools\\replay.py runs\\20261027_xxxx\\observations.ndjson
    python tools\\replay.py obs.ndjson --print          逐筆印出事件
    python tools\\replay.py obs.ndjson --out 結果資料夾
    python tools\\replay.py obs.ndjson --mortal docker

產物(預設寫在觀測檔旁邊):
    events.ndjson       本地事件帳,照 (局, t_first) 排序,一筆一行
    fsm_trace.ndjson    每一次狀態轉移(從哪到哪、為什麼)
    mjai_preview.ndjson 欄位齊全、照因果順序「能送到哪裡」的 mjai 行
    mjai.jsonl          Mortal 守門器實際會送出的串流(第 4 步)
    delivery.ndjson     每一筆送出/擋下的稽核紀錄(延遲、理由)
    replay_summary.txt  統計與每一局卡在哪裡

不需要模型、不需要 Windows。改了判斷規則,對同一份觀測重跑就好。
"""
# --- 除錯工具放在 tools\,主程式在 app\live_v7\:先把那裡加進搜尋路徑 ---
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.join(_os.path.dirname(_os.path.abspath(__file__)),
                                  "..", "app", "live_v7"))
import argparse
import json
import os
import sys
import time
from collections import Counter

import _paths  # noqa: F401
from mjai_preview import preview
from mortal_guard import FileSink, MortalGuard, MultiSink, ProcessSink
from obs import iter_ndjson, validate
from pipeline import Pipeline


def run(path, out_dir=None, verbose=False, limit=None, mortal=None):
    out_dir = out_dir or os.path.dirname(os.path.abspath(path))
    os.makedirs(out_dir, exist_ok=True)
    psink = (ProcessSink(mortal, os.path.join(out_dir, "mortal_replies.jsonl"),
                         sync=True) if mortal else None)
    sink = MultiSink(FileSink(os.path.join(out_dir, "mjai.jsonl")), psink)
    guard = MortalGuard(sink, log=print if verbose else None)
    first = None
    pipe = None
    bad = 0
    t0 = time.time()
    for i, obs in enumerate(iter_ndjson(path)):
        if limit and i >= limit:
            break
        if first is None:
            first = obs
            pipe = Pipeline(obs.get("run_id", "RUN"), verbose=verbose,
                            guard=guard)
        if validate(obs):
            bad += 1
            continue
        pipe.feed(obs)
    if pipe is None:
        print("❌ 觀測檔是空的")
        sink.close()
        return None
    pipe.finish()
    sink.close()
    dt = time.time() - t0
    with open(os.path.join(out_dir, "delivery.ndjson"), "w",
              encoding="utf-8") as fh:
        for r in guard.delivery:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")

    led = pipe.ledger
    with open(os.path.join(out_dir, "events.ndjson"), "w",
              encoding="utf-8") as fh:
        for e in led.ordered():
            fh.write(json.dumps(e, ensure_ascii=False) + "\n")
        for e in led.events:
            if e["type"] == "correction":
                fh.write(json.dumps(e, ensure_ascii=False) + "\n")
    with open(os.path.join(out_dir, "fsm_trace.ndjson"), "w",
              encoding="utf-8") as fh:
        for r in pipe.fsm.trace:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    pv = preview(led)
    with open(os.path.join(out_dir, "mjai_preview.ndjson"), "w",
              encoding="utf-8") as fh:
        for h, r in pv.items():
            for m in r["sent"]:
                fh.write(json.dumps(m, ensure_ascii=False) + "\n")
            if r["blocked"]:
                fh.write(json.dumps({"_hand": h, "_blocked": r["blocked"]},
                                    ensure_ascii=False) + "\n")

    lines = summary(pipe, pv, bad, dt)
    if psink is not None and psink.pid is not None:
        from mortal_view import review
        rl, ok, n = review(psink.stream, psink.pairs, psink.pid)
        with open(os.path.join(out_dir, "mortal_review.txt"), "w",
                  encoding="utf-8") as fh:
            fh.write("Mortal 建議 vs 你實際的選擇(✓ 一樣、✗ 不一樣)\n\n")
            fh.write("\n".join(rl) + "\n")
        lines.append(f"\n[9] Mortal:回覆 {n} 次,跟你實際的選擇一樣 {ok} 次"
                     f"({ok / max(1, n):.0%})-> mortal_review.txt")
    with open(os.path.join(out_dir, "replay_summary.txt"), "w",
              encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    print("\n".join(lines))
    print(f"\n產物寫在 {out_dir}")
    return pipe


def summary(pipe, pv, bad, dt):
    led = pipe.ledger
    evs = [e for e in led.events if e["type"] != "correction"]
    L = [f"=== 重播報告 run={pipe.run_id} ===",
         f"幀數 {pipe.frames}(格式不合跳過 {bad}),局數 {pipe.hand_no},"
         f"處理 {dt:.1f}s"]
    by = Counter((e["type"], e["status"]) for e in evs)
    L.append("\n[1] 事件數(類型/狀態)")
    for (t, s), n in sorted(by.items()):
        L.append(f"    {t:<16} {s:<10} {n}")
    dahai = [e for e in evs if e["type"] == "dahai"]
    if dahai:
        nd = len(dahai)
        has_pai = sum(1 for e in dahai if e.get("pai"))
        has_cut = sum(1 for e in dahai if e.get("tsumogiri") is not None)
        inf_cut = sum(1 for e in dahai if e.get("tsumogiri_inferred"))
        tg = sum(1 for e in dahai if e.get("tsumogiri") is True)
        L.append("\n[2] 打牌")
        L.append(f"    共 {nd} 筆;有牌面 {has_pai} ({has_pai/nd:.0%});"
                 f"有切法 {has_cut} ({has_cut/nd:.0%})")
        L.append(f"    摸切 {tg} / 有切法 {has_cut}"
                 f"({tg/max(1,has_cut):.0%})—— 逼近 100% 代表摸入牌看不到"
                 f"(drawn.judge 的已知代價)")
        L.append(f"    切法沒有直接證據、依規則推的 {inf_cut} 筆")
        meth = Counter((e.get("evidence", {}).get("cut") or {}).get("method")
                       for e in dahai)
        L.append(f"    切法來源 {dict(meth)}")
    L.append("\n[3] 狀態機統計")
    for k, v in sorted(pipe.fsm.stats.items()):
        L.append(f"    {k:<28} {v}")
    L.append("\n[4] 牌河轉接層統計")
    for k, v in sorted(pipe.adapter.stats.items()):
        L.append(f"    {k:<28} {v}")
    for k, v in sorted(pipe.stats.items()):
        L.append(f"    {k:<28} {v}")
    rej = pipe.declare.rejected
    if rej:
        L.append(f"    宣告被擋(最後一局) {dict(rej)}")
    gaps = [e for e in evs if e["type"] == "event_gap"]
    L.append(f"\n[5] EVENT_GAP {len(gaps)} 筆")
    for g in gaps[:30]:
        L.append(f"    {g['event_id']} f{g['frame_first']} 預期 "
                 f"{g['expected']} 看到 {g['observed']}:{g['reason']}")
    L.append("\n[6] 每一局欄位齊全度預覽(不檢查手牌帳與更正)")
    for h, r in pv.items():
        L.append(f"    {h}: 可送 {len(r['sent'])}/{r['total']}"
                 + (f"  ⛔ {r['blocked']}" if r["blocked"] else "  ✅ 全部"))
    starts = [e for e in evs if e["type"] == "start_kyoku"]
    L.append("\n[7] start_kyoku(第 0 步)")
    for s in starts:
        L.append(f"    {s['hand_id']}: "
                 + ("✅" if s.get("export_eligible") else
                    "缺 " + ", ".join(s.get("missing", [])))
                 + f"  kyoku={s.get('bakaze')}{s.get('kyoku')} "
                   f"honba={s.get('honba')} kyotaku={s.get('kyotaku')} "
                   f"seat_map={s.get('seat_map')} scores={s.get('scores')} "
                   f"dora={s.get('dora_marker')}"
                 + (f"  ({s['tehais_note']})" if s.get("tehais_note")
                    else ""))
    g = pipe.guard
    if g is not None:
        L.append("\n[8] Mortal 守門器(實際送出的串流 mjai.jsonl)")
        L += g.report()
    return L


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("observations")
    ap.add_argument("--out", help="產物資料夾(預設:觀測檔旁邊)")
    ap.add_argument("--print", dest="verbose", action="store_true",
                    help="逐筆印出事件")
    ap.add_argument("--limit", type=int, help="只跑前 N 幀")
    ap.add_argument("--mortal", help="同時餵給 Mortal 程序。寫 docker 就是預設指令"
                    "(掛載 專案\\Model);也可以給完整指令,{player_id}、"
                    "{model_dir} 會被替換")
    a = ap.parse_args(argv)
    if not os.path.isfile(a.observations):
        print(f"❌ 找不到 {a.observations}")
        return 2
    return 0 if run(a.observations, a.out, a.verbose, a.limit,
                       a.mortal) else 1


if __name__ == "__main__":
    sys.exit(main())
