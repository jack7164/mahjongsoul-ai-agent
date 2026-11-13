"""對照表:把 replay 的結果印成「跟雀魂牌譜逐局對照」用的文字。

    python tools\\check_sheet.py runs\\X\\events.ndjson
    -> 印在終端,同時寫 runs\\X\\check_sheet.txt

每一局:場況、P0 起手、P0 摸牌、四家牌河(手=手切、摸=摸切,[ ] 是鳴牌/立直/槓)、
結果。打開那一局的牌譜一家一家對,錯的地方記下「第幾局、誰、第幾張」。
標 ? 的是程式不知道的(對手的摸牌本來就看不到)。
標 ⛔ 的局是守門器沒送完的,原因寫在旁邊。
"""
# --- 除錯工具放在 tools\,主程式在 app\live_v7\:先把那裡加進搜尋路徑 ---
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.join(_os.path.dirname(_os.path.abspath(__file__)),
                                  "..", "app", "live_v7"))
import json
import os
import sys

SEATS = ("P0", "P1", "P2", "P3")
WIND = {"E": "東", "S": "南", "W": "西", "N": "北"}


def load(path):
    evs = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                evs.append(json.loads(line))
    return [e for e in evs if e["type"] != "correction"]


def sheet(evs, delivery=None):
    hands = []
    for e in evs:
        if e["hand_id"] not in hands:
            hands.append(e["hand_id"])
    blocked = {}
    for r in delivery or []:
        if r.get("blocked_reason"):
            blocked.setdefault(r["run_hand"], r["blocked_reason"])
    out = []
    for h in hands:
        H = [e for e in evs if e["hand_id"] == h]
        st = next((e for e in H if e["type"] == "start_kyoku"), None)
        sm = (st or {}).get("seat_map") or {}
        n_dahai = sum(1 for e in H if e["type"] == "dahai")
        if (not st or not st.get("export_eligible")) and n_dahai <= 2 and \
                not (st or {}).get("dora_marker"):
            # 錄影開頭的大廳/載入畫面讀到一兩張假牌(2026-10-30 實戰 H001)
            out.append(f"=== {h} (不是真的一局:錄影開頭的大廳/載入畫面,"
                       f"讀到 {n_dahai} 張假牌,沒有送給 Mortal)")
            out.append("")
            continue
        head = f"=== {h} "
        if st and st.get("bakaze"):
            head += (f"{WIND.get(st['bakaze'], st['bakaze'])}{st.get('kyoku')}局 "
                     f"{st.get('honba')}本場 供託{st.get('kyotaku')}")
        if st and st.get("scores") and sm:
            sc = {s: st["scores"][a] for s, a in sm.items()}
            head += "  點數 " + " ".join(f"{s}={sc[s]}" for s in SEATS)
        if st and st.get("dora_marker"):
            head += f"  寶牌指示 {st['dora_marker']}"
        out.append(head + (f"  ⛔ {blocked[h]}" if h in blocked else ""))
        if st and st.get("tehais") and sm:
            out.append("P0 起手:" + " ".join(st["tehais"][sm["P0"]])
                       + (f"  ({st['tehais_note']})" if st.get("tehais_note")
                          else ""))
        out.append("P0 摸牌:" + " ".join(
            e.get("pai") or "?" for e in H
            if e["type"] == "tsumo" and e.get("actor") == "P0"))
        for s in SEATS:
            row = []
            for e in H:
                t = e["type"]
                if e.get("actor") != s:
                    continue
                if t == "dahai":
                    cut = {True: "摸", False: "手"}.get(e.get("tsumogiri"), "?")
                    row.append(f"{e.get('pai') or '?'}{cut}")
                elif t in ("chi", "pon", "daiminkan"):
                    zh = {"chi": "吃", "pon": "碰", "daiminkan": "大明槓"}[t]
                    row.append(f"[{zh} {e.get('pai')} 用{'+'.join(e.get('consumed') or ['?'])}"
                               f" 自{e.get('target')}]")
                elif t in ("ankan", "kakan", "kan_self"):
                    zh = {"ankan": "暗槓", "kakan": "加槓",
                          "kan_self": "槓?"}[t]
                    row.append(f"[{zh} {'+'.join(e.get('consumed') or ['?'])}]")
                elif t == "reach":
                    row.append("[立直]")
            out.append(f"{s} 牌河:" + " ".join(row))
        for e in H:
            if e["type"] == "dora":
                out.append(f"槓寶牌指示:{e.get('dora_marker')}")
            if e["type"] == "hora":
                how = "自摸" if e.get("target") == e.get("actor") else \
                    f"榮和 {e.get('target')}"
                out.append(f"結果:{e.get('actor')} {how}")
            if e["type"] == "ryukyoku":
                out.append("結果:流局")
            if e["type"] == "event_gap":
                out.append(f"⚠️ 漏了東西:預期 {e.get('expected')},看到 "
                           f"{e.get('observed')}")
        out.append("")
    return out


def main(argv=None):
    argv = argv if argv is not None else sys.argv[1:]
    if not argv:
        print(__doc__)
        return 2
    path = argv[0]
    evs = load(path)
    dpath = os.path.join(os.path.dirname(path), "delivery.ndjson")
    delivery = load_plain(dpath) if os.path.isfile(dpath) else None
    lines = sheet(evs, delivery)
    out = os.path.join(os.path.dirname(path), "check_sheet.txt")
    with open(out, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    print("\n".join(lines))
    print(f"寫在 {out}")
    return 0


def load_plain(path):
    with open(path, encoding="utf-8") as fh:
        return [json.loads(x) for x in fh if x.strip()]


if __name__ == "__main__":
    sys.exit(main())
