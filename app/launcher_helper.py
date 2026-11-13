r"""主程式(雀魂AI助手.exe)的小幫手:需要 pywin32 / cv2 的事情在這裡做。

主程式本身只用標準函式庫(要在什麼都還沒裝的電腦上就能打開),所以「列出視窗」
「擷取預覽畫面」這兩件事用環境裡的 Python 跑這支:

    python app\launcher_helper.py windows [--exclude-pid N]
        印一行 JSON:{"windows": [...], "suggest": hwnd 或 null, "why": "..."}
        挑選規則跟 agent.py 自動找視窗用的完全一樣(1_capture.pick_window)

    python app\launcher_helper.py watch --hwnd N --out 資料夾 [--width 560] [--parent PID]
        一直擷取那個視窗,把「程式實際看到的畫面」(已切出遊戲區域、統一成 16:9)
        存成 資料夾\preview.png,狀態存成 資料夾\status.json。
        主程式關掉(--parent 那個行程不見了)就自己結束。

★ 工作目錄要是 app\(window.json 是用相對路徑讀的,跟 agent.py 一樣)。
"""
import argparse
import importlib.util
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)


def _cap1():
    spec = importlib.util.spec_from_file_location(
        "_cap1", os.path.join(HERE, "1_capture.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def cmd_windows(a):
    m = _cap1()                       # 會順便 import capture(設定 DPI 感知)
    infos = m.enum_windows()
    skip = {os.getpid()} | ({a.exclude_pid} if a.exclude_pid else set())
    infos = [i for i in infos if i.get("pid") not in skip]
    got, why, _c = m.pick_window(infos, m.load_remembered(), None, os.getpid())
    out = [{"hwnd": i["hwnd"], "title": i["title"], "cls": i["cls"],
            "exe": i["exe"] or "", "client": list(i["client"]),
            "plausible": bool(m.plausible(i, os.getpid())),
            "is169": bool(m.is_16_9(i))} for i in infos]
    # 可能的排前面、16:9 的再前面
    out.sort(key=lambda w: (not w["plausible"], not w["is169"]))
    print(json.dumps({"windows": out, "suggest": got["hwnd"] if got else None,
                      "why": why}, ensure_ascii=False))
    return 0


def _replace(tmp, path):
    """換上新檔。主程式剛好在讀舊檔時 Windows 會拒絕(WinError 5 存取被拒)——
    2026-11-12 實測預覽幾秒後就因此整個停掉。稍等再試,還是不行就跳過這一幀。"""
    for _ in range(5):
        try:
            os.replace(tmp, path)
            return True
        except PermissionError:
            time.sleep(0.02)
    try:
        os.remove(tmp)
    except OSError:
        pass
    return False


def _write_json(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False)
    _replace(tmp, path)


def cmd_watch(a):
    import cv2
    import win32gui
    from capture import WindowCapture

    try:
        import psutil
    except ImportError:
        psutil = None
    os.makedirs(a.out, exist_ok=True)
    png = os.path.join(a.out, "preview.png")
    stj = os.path.join(a.out, "status.json")
    w = int(a.width)
    h = int(round(w * 9 / 16))
    cap = None
    times = []
    last_parent_check = 0.0
    while True:
        now = time.time()
        if a.parent and psutil is not None and now - last_parent_check > 1.0:
            last_parent_check = now
            if not psutil.pid_exists(a.parent):
                return 0
        try:
            st = {"ts": now, "ok": False, "minimized": False}
            if not win32gui.IsWindow(a.hwnd):
                st["error"] = "視窗已經關閉了"
                _write_json(stj, st)
                time.sleep(1.0)
                continue
            if cap is None:
                cap = WindowCapture(a.hwnd)
            try:
                _, _, cw, ch = win32gui.GetClientRect(a.hwnd)
                st["client"] = [cw, ch]
            except Exception:
                pass
            if win32gui.IsIconic(a.hwnd):
                st["minimized"] = True
                _write_json(stj, st)
                time.sleep(0.5)
                continue
            try:
                img = cap.grab()
            except Exception as e:
                img = None
                st["error"] = f"擷取失敗:{e}"
            if img is not None:
                small = cv2.resize(img, (w, h), interpolation=cv2.INTER_AREA)
                tmp = png + ".tmp.png"
                if cv2.imwrite(tmp, small):
                    _replace(tmp, png)
                times.append(now)
                times = times[-10:]
                st.update({
                    "ok": True,
                    "game": list(cap.client_size) if cap.client_size else None,
                    "aspect_ok": bool(cap.aspect_ok),
                    "web_found": cap.game_rect is not None,
                    "fps": ((len(times) - 1) / (times[-1] - times[0])
                            if len(times) > 1 and times[-1] > times[0] else None),
                })
            _write_json(stj, st)
        except Exception as e:
            # ★ 一輪失敗就下一輪再試,不要讓整個預覽停掉
            print(f"預覽這一輪失敗:{e!r}", file=sys.stderr, flush=True)
            time.sleep(0.5)
        time.sleep(max(0.0, 0.12 - (time.time() - now)))   # 預覽約 8 fps 就夠


def main(argv=None):
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("windows")
    p.add_argument("--exclude-pid", type=int)
    p = sub.add_parser("watch")
    p.add_argument("--hwnd", type=int, required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--width", type=int, default=560)
    p.add_argument("--parent", type=int)
    a = ap.parse_args(argv)
    if a.cmd == "windows":
        return cmd_windows(a)
    return cmd_watch(a)


if __name__ == "__main__":
    sys.exit(main())
