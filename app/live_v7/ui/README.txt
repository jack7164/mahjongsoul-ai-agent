這三支是從舊專案 mjai_live 原封不動複製過來的(2026-10-26):
    panel.py   局數 / 本場 / 供託
    winds.py   自風(四個角)
    scores.py  四家點數

為什麼是複製而不是 import:mjai_live 資料夾裡有跟 MJAI 同名的 river.py、
region_model.py、river_model.py、hand_model.py、event_zones.py、capture.py,
把它加進 sys.path 會把 MJAI 這邊的蓋掉,而且不會報錯。這三支只依賴
cv2 / numpy / 彼此,沒有同名問題,所以單獨搬過來。

樣板(templates\、templates_wind\)2026-10-31 已複製到 專案\assets\
(--old-dir 可以改位置)。

修改這三支之前先想清楚:舊專案的量測與樣板都是配這個版本的。
