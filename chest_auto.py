# -*- coding: utf-8 -*-
"""
宝箱オート開封ツール (multi-window chest auto-opener)

ブラウザゲームを複数窓（既定3窓）で開いている状態で、各窓について
  前準備クリック（宝箱タブの有効化 / 窓のフォーカス。既定2回）
    → コモン宝箱  開く → 閉じる
    → レア宝箱    開く → 閉じる
    → イモータル宝箱 開く → 閉じる
の順にクリックし、1サイクル終わったら指定秒（既定60秒）待って永久に繰り返す。
前準備クリックも宝箱の各クリックも、必要な回数だけステップを増減できる。

クリック座標はすべてティーチング（F8でマウス位置を取り込み）で設定でき、
設定は chest_config.json に保存される。

必要ライブラリ:  pip install pyautogui pynput
"""

import ctypes
import json
import os
import queue
import sys
import threading
import time
import traceback


# ---------------------------------------------------------------------------
# DPI 対応: pyautogui / Tk を触る前に必ず設定する
# （Windows のディスプレイ拡大率が 100% 以外でも座標がズレないようにする）
# ---------------------------------------------------------------------------
def _set_dpi_awareness():
    try:
        # PER_MONITOR_AWARE_V2 (Win10 1703+)
        ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
        return "per-monitor-v2"
    except Exception:
        pass
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
        return "per-monitor"
    except Exception:
        pass
    try:
        ctypes.windll.user32.SetProcessDPIAware()
        return "system"
    except Exception:
        return "none"


DPI_MODE = _set_dpi_awareness()

import tkinter as tk
from tkinter import ttk, messagebox, filedialog

try:
    import pyautogui
except Exception as e:  # pragma: no cover
    raise SystemExit("pyautogui が見つかりません。  pip install pyautogui\n" + str(e))

try:
    from pynput import keyboard as pynput_keyboard
except Exception as e:  # pragma: no cover
    raise SystemExit("pynput が見つかりません。  pip install pynput\n" + str(e))


pyautogui.FAILSAFE = True   # マウスを画面左上(0,0)へ飛ばすと緊急停止
pyautogui.PAUSE = 0.0       # 待ち時間は自前で管理する


APP_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(APP_DIR, "chest_config.json")

CHEST_KEYS = [
    ("common", "コモン宝箱"),
    ("rare", "レア宝箱"),
    ("immortal", "イモータル宝箱"),
]


# ---------------------------------------------------------------------------
# 設定
# ---------------------------------------------------------------------------
def make_step(label, wait):
    return {"label": label, "point": None, "wait": float(wait)}


def make_group(labels_waits):
    return {"enabled": True, "steps": [make_step(l, w) for l, w in labels_waits]}


def make_prep():
    """宝箱パネル(タブ)を有効化するための前準備クリック。既定3回。"""
    return make_group([("宝箱タブ1", 0.5), ("宝箱タブ2", 0.5), ("宝箱タブ3", 0.8)])


def make_chest():
    return make_group([("開く", 1.0), ("閉じる", 0.6)])


def make_window(i):
    return {
        "name": "窓{}".format(i + 1),
        "enabled": True,
        # 宝箱を開く前の下準備クリック（窓のフォーカス / 宝箱タブの有効化）
        "prep": make_prep(),
        "chests": {k: make_chest() for k, _ in CHEST_KEYS},
    }


# 設定フォーマットの版。既定ステップ構成を変えたら上げて _merge_defaults で追補する。
CONFIG_VERSION = 2


def default_config(n_windows=3):
    return {
        "config_version": CONFIG_VERSION,
        "interval_sec": 60.0,       # サイクル開始 → 次のサイクル開始までの周期
        "move_duration": 0.15,      # マウス移動にかける秒数
        "pre_click_wait": 0.15,     # 移動後クリックまでの待ち
        "restore_mouse": True,      # 1サイクル終了後にマウスを元位置へ戻す
        "windows": [make_window(i) for i in range(n_windows)],
    }


def _merge_defaults(cfg):
    """古い/壊れた設定ファイルを読んでも落ちないように穴埋めする。"""
    old_version = cfg.get("config_version", 1)
    base = default_config(len(cfg.get("windows") or []) or 3)
    for k, v in base.items():
        if k == "windows":
            continue
        cfg.setdefault(k, v)

    wins = cfg.get("windows")
    if not isinstance(wins, list) or not wins:
        cfg["windows"] = base["windows"]
        return cfg

    def fix_group(g, fallback):
        g.setdefault("enabled", True)
        steps = g.get("steps")
        if not isinstance(steps, list) or not steps:
            g["steps"] = fallback()["steps"]
        else:
            for si, st in enumerate(steps):
                st.setdefault("label", "ステップ{}".format(si + 1))
                st.setdefault("point", None)
                st.setdefault("wait", 0.6)
        return g

    for i, w in enumerate(wins):
        w.setdefault("name", "窓{}".format(i + 1))
        w.setdefault("enabled", True)

        prep = w.get("prep")
        if not isinstance(prep, dict):
            prep = make_prep()
            old = w.get("focus")   # 旧形式（単一フォーカス点）からの移行
            if old:
                prep["steps"].insert(0, {"label": "フォーカス", "point": old, "wait": 0.4})
            w["prep"] = prep
        fix_group(w["prep"], make_prep)
        w.pop("focus", None)

        # v1 -> v2: 前準備の既定が 2回 から 3回 に増えた。
        # 旧既定のままの設定にだけ3つ目を追補する（自分で増減した構成は触らない）。
        if old_version < 2:
            if [s["label"] for s in w["prep"]["steps"]] == ["宝箱タブ1", "宝箱タブ2"]:
                w["prep"]["steps"].append(make_step("宝箱タブ3", 0.8))

        chests = w.setdefault("chests", {})
        for key, _ in CHEST_KEYS:
            fix_group(chests.setdefault(key, make_chest()), make_chest)

    cfg["config_version"] = CONFIG_VERSION
    return cfg


def load_config(path=CONFIG_PATH):
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return _merge_defaults(json.load(f))
        except Exception:
            pass
    return default_config()


def save_config(cfg, path=CONFIG_PATH):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def fmt_point(p):
    if not p:
        return "未設定"
    return "({}, {})".format(int(p[0]), int(p[1]))


# ---------------------------------------------------------------------------
# スクロールできるフレーム
# ---------------------------------------------------------------------------
class ScrollFrame(ttk.Frame):
    def __init__(self, master, **kw):
        super().__init__(master, **kw)
        self.canvas = tk.Canvas(self, borderwidth=0, highlightthickness=0)
        vsb = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=vsb.set)
        vsb.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)

        self.inner = ttk.Frame(self.canvas)
        self._win = self.canvas.create_window((0, 0), window=self.inner, anchor="nw")

        self.inner.bind(
            "<Configure>",
            lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")),
        )
        self.canvas.bind(
            "<Configure>",
            lambda e: self.canvas.itemconfigure(self._win, width=e.width),
        )
        # マウスホイールは「カーソルがこのフレームの上にある間だけ」有効化する
        self.bind("<Enter>", lambda e: self.canvas.bind_all("<MouseWheel>", self._wheel))
        self.bind("<Leave>", lambda e: self.canvas.unbind_all("<MouseWheel>"))

    def _wheel(self, event):
        self.canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")


# ---------------------------------------------------------------------------
# 本体
# ---------------------------------------------------------------------------
class App:
    def __init__(self, root):
        self.root = root
        self.cfg = load_config()

        self.msg_q = queue.Queue()
        self.stop_event = threading.Event()
        self.worker = None

        # ティーチング状態
        self.teach_queue = []     # 取り込み待ちターゲットのリスト
        self.teach_index = -1

        # 座標表示用 StringVar: target -> StringVar
        self.point_vars = {}

        self.root.title("宝箱オート開封ツール")
        self.root.geometry("980x760")
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

        self._build_ui()
        self._start_hotkeys()
        self.root.after(80, self._pump)

        self.log("設定ファイル: {}".format(CONFIG_PATH))
        self.log("画面サイズ: {} x {}   (DPI mode: {})".format(*pyautogui.size(), DPI_MODE))
        self.log("F8=座標取得 / F9=開始・停止 / F12=緊急停止 / マウスを画面左上へ飛ばしても停止")

    # ---------------- UI ----------------
    def _build_ui(self):
        root = self.root

        # ===== 上部: 全体設定 =====
        top = ttk.LabelFrame(root, text="全体設定", padding=8)
        top.pack(fill="x", padx=8, pady=(8, 4))

        r = ttk.Frame(top)
        r.pack(fill="x")

        ttk.Label(r, text="繰り返し周期(秒)").pack(side="left")
        self.v_interval = tk.StringVar(value=str(self.cfg["interval_sec"]))
        self._bind_num(self.v_interval, self.cfg, "interval_sec", float)
        ttk.Entry(r, textvariable=self.v_interval, width=7).pack(side="left", padx=(4, 14))

        ttk.Label(r, text="マウス移動(秒)").pack(side="left")
        self.v_move = tk.StringVar(value=str(self.cfg["move_duration"]))
        self._bind_num(self.v_move, self.cfg, "move_duration", float)
        ttk.Entry(r, textvariable=self.v_move, width=6).pack(side="left", padx=(4, 14))

        ttk.Label(r, text="クリック前待ち(秒)").pack(side="left")
        self.v_pre = tk.StringVar(value=str(self.cfg["pre_click_wait"]))
        self._bind_num(self.v_pre, self.cfg, "pre_click_wait", float)
        ttk.Entry(r, textvariable=self.v_pre, width=6).pack(side="left", padx=(4, 14))

        self.v_restore = tk.BooleanVar(value=bool(self.cfg["restore_mouse"]))
        self.v_restore.trace_add(
            "write", lambda *_: self.cfg.__setitem__("restore_mouse", self.v_restore.get())
        )
        ttk.Checkbutton(r, text="終了後マウスを元に戻す", variable=self.v_restore).pack(
            side="left", padx=(0, 14)
        )

        ttk.Label(r, text="窓数").pack(side="left")
        self.v_nwin = tk.IntVar(value=len(self.cfg["windows"]))
        ttk.Spinbox(
            r, from_=1, to=8, width=3, textvariable=self.v_nwin, command=self._change_window_count
        ).pack(side="left", padx=4)

        # ===== 中央: 窓ごとのタブ =====
        self.nb = ttk.Notebook(root)
        self.nb.pack(fill="both", expand=True, padx=8, pady=4)
        self._build_window_tabs()

        # ===== 下部: 操作 =====
        ctrl = ttk.Frame(root)
        ctrl.pack(fill="x", padx=8, pady=(2, 4))

        self.btn_run = ttk.Button(ctrl, text="▶ 開始 (F9)", command=self.toggle_run)
        self.btn_run.pack(side="left")
        ttk.Button(ctrl, text="■ 停止 (F12)", command=self.stop_run).pack(side="left", padx=4)
        ttk.Button(ctrl, text="1サイクルだけ実行", command=self.run_once).pack(side="left", padx=4)
        ttk.Separator(ctrl, orient="vertical").pack(side="left", fill="y", padx=8)
        ttk.Button(ctrl, text="全窓を順にティーチング", command=self.teach_all).pack(side="left")
        ttk.Button(ctrl, text="ティーチング中止", command=self.teach_cancel).pack(side="left", padx=4)
        ttk.Separator(ctrl, orient="vertical").pack(side="left", fill="y", padx=8)
        ttk.Button(ctrl, text="保存", command=self.do_save).pack(side="left")
        ttk.Button(ctrl, text="読込", command=self.do_load).pack(side="left", padx=4)

        self.v_status = tk.StringVar(value="待機中")
        st = ttk.Label(root, textvariable=self.v_status, anchor="w",
                       relief="sunken", padding=(6, 3))
        st.pack(fill="x", padx=8)

        # ===== ログ =====
        logf = ttk.LabelFrame(root, text="ログ", padding=4)
        logf.pack(fill="both", padx=8, pady=(4, 8))
        self.txt = tk.Text(logf, height=10, wrap="none")
        sb = ttk.Scrollbar(logf, orient="vertical", command=self.txt.yview)
        self.txt.configure(yscrollcommand=sb.set, state="disabled")
        sb.pack(side="right", fill="y")
        self.txt.pack(side="left", fill="both", expand=True)

    def _bind_num(self, var, d, key, cast):
        def cb(*_):
            try:
                d[key] = cast(var.get())
            except Exception:
                pass
        var.trace_add("write", cb)

    def _change_window_count(self):
        try:
            n = int(self.v_nwin.get())
        except Exception:
            return
        wins = self.cfg["windows"]
        while len(wins) < n:
            wins.append(make_window(len(wins)))
        while len(wins) > n:
            wins.pop()
        self._build_window_tabs()

    def _build_window_tabs(self):
        for tab in self.nb.tabs():
            self.nb.forget(tab)
        self.point_vars = {}
        for wi, w in enumerate(self.cfg["windows"]):
            frame = ScrollFrame(self.nb)
            self.nb.add(frame, text=w["name"] or "窓{}".format(wi + 1))
            self._build_window_tab(frame.inner, wi)

    def _build_window_tab(self, parent, wi):
        w = self.cfg["windows"][wi]

        head = ttk.Frame(parent, padding=(6, 6))
        head.pack(fill="x")

        v_en = tk.BooleanVar(value=bool(w["enabled"]))
        v_en.trace_add("write", lambda *_: w.__setitem__("enabled", v_en.get()))
        ttk.Checkbutton(head, text="この窓を使う", variable=v_en).pack(side="left")

        ttk.Label(head, text="  名前").pack(side="left")
        v_name = tk.StringVar(value=w["name"])

        def rename(*_):
            w["name"] = v_name.get()
            try:
                self.nb.tab(wi, text=w["name"])
            except Exception:
                pass
        v_name.trace_add("write", rename)
        ttk.Entry(head, textvariable=v_name, width=14).pack(side="left", padx=4)

        # --- 他の窓からコピー ---
        cp = ttk.LabelFrame(parent, text="他の窓から座標をコピー（横並びの窓ならXをずらすだけで済みます）",
                            padding=6)
        cp.pack(fill="x", padx=6, pady=(4, 6))
        names = [x["name"] for x in self.cfg["windows"]]
        v_src = tk.StringVar(value=names[0] if names else "")
        ttk.Label(cp, text="コピー元").pack(side="left")
        ttk.Combobox(cp, textvariable=v_src, values=names, width=12,
                     state="readonly").pack(side="left", padx=4)
        sw = pyautogui.size()[0]
        v_dx = tk.StringVar(value=str(int(sw / max(1, len(self.cfg["windows"])))))
        v_dy = tk.StringVar(value="0")
        ttk.Label(cp, text="dX").pack(side="left", padx=(10, 2))
        ttk.Entry(cp, textvariable=v_dx, width=7).pack(side="left")
        ttk.Label(cp, text="dY").pack(side="left", padx=(10, 2))
        ttk.Entry(cp, textvariable=v_dy, width=7).pack(side="left")
        ttk.Button(cp, text="コピー実行",
                   command=lambda: self.copy_from(wi, v_src.get(), v_dx.get(), v_dy.get())
                   ).pack(side="left", padx=10)

        # --- 前準備クリック（宝箱タブの有効化 / 窓のフォーカス）---
        prep = w["prep"]
        pf = ttk.LabelFrame(
            parent,
            text="前準備クリック（宝箱を開ける前に押す場所。宝箱タブの有効化・窓のフォーカスなど）",
            padding=6)
        pf.pack(fill="x", padx=6, pady=(0, 6))

        ph = ttk.Frame(pf)
        ph.pack(fill="x", pady=(0, 4))
        v_pe = tk.BooleanVar(value=bool(prep["enabled"]))
        v_pe.trace_add("write", lambda *_, g=prep, v=v_pe: g.__setitem__("enabled", v.get()))
        ttk.Checkbutton(ph, text="毎サイクル、宝箱の前にこれを押す", variable=v_pe).pack(side="left")
        ttk.Button(ph, text="前準備だけティーチング",
                   command=lambda wi=wi: self.teach_group(wi, "prep")).pack(side="left", padx=8)
        ttk.Button(ph, text="＋ステップ追加",
                   command=lambda wi=wi: self.add_step(wi, "prep")).pack(side="left")

        for si, st in enumerate(prep["steps"]):
            self._point_row(pf, ("prep", wi, si), st["label"], step=st,
                            removable=len(prep["steps"]) > 1)

        # --- 宝箱ごと ---
        for key, jname in CHEST_KEYS:
            c = w["chests"][key]
            box = ttk.LabelFrame(parent, text=jname, padding=6)
            box.pack(fill="x", padx=6, pady=(0, 6))

            hdr = ttk.Frame(box)
            hdr.pack(fill="x", pady=(0, 4))
            v_ce = tk.BooleanVar(value=bool(c["enabled"]))
            v_ce.trace_add("write", lambda *_, c=c, v=v_ce: c.__setitem__("enabled", v.get()))
            ttk.Checkbutton(hdr, text="この宝箱を開ける", variable=v_ce).pack(side="left")
            ttk.Button(hdr, text="この宝箱をティーチング",
                       command=lambda wi=wi, key=key: self.teach_group(wi, key)).pack(
                side="left", padx=8)
            ttk.Button(hdr, text="＋ステップ追加",
                       command=lambda wi=wi, key=key: self.add_step(wi, key)).pack(side="left")

            for si, st in enumerate(c["steps"]):
                self._point_row(box, ("step", wi, key, si), st["label"], step=st,
                                removable=len(c["steps"]) > 1)

    def _point_row(self, parent, target, hint, step=None, removable=False):
        """1つの座標を編集する行を作る。"""
        row = ttk.Frame(parent)
        row.pack(fill="x", pady=1)

        if step is not None:
            v_label = tk.StringVar(value=step["label"])
            v_label.trace_add("write", lambda *_: step.__setitem__("label", v_label.get()))
            ttk.Entry(row, textvariable=v_label, width=10).pack(side="left")
        else:
            ttk.Label(row, text=hint, width=14).pack(side="left")

        var = tk.StringVar(value=fmt_point(self._get_point(target)))
        self.point_vars[target] = var
        ttk.Label(row, textvariable=var, width=16, anchor="w",
                  relief="groove", padding=(4, 1)).pack(side="left", padx=6)

        ttk.Button(row, text="取得(F8)", width=9,
                   command=lambda: self.teach_single(target)).pack(side="left")
        ttk.Button(row, text="テスト", width=7,
                   command=lambda: self.test_point(target)).pack(side="left", padx=3)
        ttk.Button(row, text="クリア", width=7,
                   command=lambda: self.clear_point(target)).pack(side="left")

        if step is not None:
            ttk.Label(row, text="  後の待ち(秒)").pack(side="left")
            v_wait = tk.StringVar(value=str(step["wait"]))
            self._bind_num(v_wait, step, "wait", float)
            ttk.Entry(row, textvariable=v_wait, width=6).pack(side="left", padx=3)

        if removable:
            ttk.Button(row, text="削除", width=6,
                       command=lambda: self.remove_step(target)).pack(side="left", padx=6)

    # ---------------- 座標アクセス ----------------
    def _group(self, wi, key):
        """key == "prep" なら前準備グループ、それ以外は宝箱グループ。"""
        w = self.cfg["windows"][wi]
        return w["prep"] if key == "prep" else w["chests"][key]

    def _locate(self, target):
        """target -> (steps リスト, その中の index)"""
        if target[0] == "prep":
            _, wi, si = target
            return self.cfg["windows"][wi]["prep"]["steps"], si
        _, wi, key, si = target
        return self.cfg["windows"][wi]["chests"][key]["steps"], si

    def _get_point(self, target):
        steps, si = self._locate(target)
        return steps[si]["point"]

    def _set_point(self, target, p):
        steps, si = self._locate(target)
        steps[si]["point"] = p
        var = self.point_vars.get(target)
        if var is not None:
            var.set(fmt_point(p))

    def _target_name(self, target):
        w = self.cfg["windows"][target[1]]
        steps, si = self._locate(target)
        gname = "前準備" if target[0] == "prep" else dict(CHEST_KEYS)[target[2]]
        return "{} / {} / {}".format(w["name"], gname, steps[si]["label"])

    # ---------------- ステップ追加削除 ----------------
    def add_step(self, wi, key):
        steps = self._group(wi, key)["steps"]
        steps.append(make_step("ステップ{}".format(len(steps) + 1), 0.6))
        self._rebuild_keep_tab()

    def remove_step(self, target):
        steps, si = self._locate(target)
        if len(steps) > 1:
            steps.pop(si)
            self._rebuild_keep_tab()

    def _rebuild_keep_tab(self):
        idx = self.nb.index(self.nb.select()) if self.nb.tabs() else 0
        self._build_window_tabs()
        try:
            self.nb.select(idx)
        except Exception:
            pass

    def copy_from(self, wi, src_name, dx, dy):
        try:
            dx = int(float(dx))
            dy = int(float(dy))
        except Exception:
            messagebox.showerror("エラー", "dX / dY は数値で入力してください")
            return
        src = None
        for i, w in enumerate(self.cfg["windows"]):
            if w["name"] == src_name:
                src = i
                break
        if src is None or src == wi:
            messagebox.showerror("エラー", "コピー元の窓を正しく選んでください")
            return

        s = self.cfg["windows"][src]
        d = self.cfg["windows"][wi]
        shift = lambda p: None if not p else [int(p[0]) + dx, int(p[1]) + dy]

        def copy_group(sg):
            return {
                "enabled": sg["enabled"],
                "steps": [
                    {"label": st["label"], "wait": st["wait"], "point": shift(st["point"])}
                    for st in sg["steps"]
                ],
            }

        d["prep"] = copy_group(s["prep"])
        for key, _ in CHEST_KEYS:
            d["chests"][key] = copy_group(s["chests"][key])
        self._rebuild_keep_tab()
        self.log("{} の座標を {} へ dX={} dY={} でコピーしました".format(
            s["name"], d["name"], dx, dy))

    # ---------------- ティーチング ----------------
    def _all_targets(self, wi=None, key=None):
        out = []
        idxs = range(len(self.cfg["windows"])) if wi is None else [wi]
        for i in idxs:
            w = self.cfg["windows"][i]
            if wi is None and not w["enabled"]:
                continue
            if key in (None, "prep"):
                for si in range(len(w["prep"]["steps"])):
                    out.append(("prep", i, si))
            for k, _ in CHEST_KEYS:
                if key is not None and k != key:
                    continue
                for si in range(len(w["chests"][k]["steps"])):
                    out.append(("step", i, k, si))
        return out

    def teach_single(self, target):
        self.teach_queue = [target]
        self.teach_index = 0
        self._teach_prompt()

    def teach_group(self, wi, key):
        """key == "prep" で前準備グループ、宝箱キーでその宝箱だけをティーチング。"""
        self.teach_queue = self._all_targets(wi=wi, key=key)
        self.teach_index = 0
        self._teach_prompt()

    def teach_all(self):
        self.teach_queue = self._all_targets()
        self.teach_index = 0
        self.log("ティーチング開始: 全 {} 点。順番にマウスを合わせて F8 を押してください。"
                 "（スキップしたい所は F8 を押さずに『ティーチング中止』）".format(len(self.teach_queue)))
        self._teach_prompt()

    def teach_cancel(self):
        self.teach_queue = []
        self.teach_index = -1
        self.set_status("待機中")

    def _teach_prompt(self):
        if 0 <= self.teach_index < len(self.teach_queue):
            t = self.teach_queue[self.teach_index]
            self.set_status("[ティーチング {}/{}]  ★ {} ★  にマウスを合わせて F8".format(
                self.teach_index + 1, len(self.teach_queue), self._target_name(t)))
        else:
            self.teach_queue = []
            self.teach_index = -1
            self.set_status("ティーチング完了")

    def capture_now(self):
        if not (0 <= self.teach_index < len(self.teach_queue)):
            self.log("取得対象が選ばれていません。『取得(F8)』か『全窓を順にティーチング』を押してから F8 を押してください。")
            return
        x, y = pyautogui.position()
        t = self.teach_queue[self.teach_index]
        self._set_point(t, [int(x), int(y)])
        self.log("取得: {} = ({}, {})".format(self._target_name(t), int(x), int(y)))
        self.teach_index += 1
        self._teach_prompt()
        save_config(self.cfg)

    def clear_point(self, target):
        self._set_point(target, None)

    def test_point(self, target):
        p = self._get_point(target)
        if not p:
            messagebox.showinfo("テスト", "座標が未設定です")
            return
        if self.worker and self.worker.is_alive():
            messagebox.showinfo("テスト", "自動実行中はテストできません")
            return

        def job():
            self.log("2秒後に {} をテストクリックします…".format(self._target_name(target)))
            time.sleep(2.0)
            try:
                self._click(p)
                self.log("テストクリック {}".format(fmt_point(p)))
            except Exception as e:
                self.log("テスト失敗: {}".format(e))

        threading.Thread(target=job, daemon=True).start()

    # ---------------- 実行 ----------------
    def toggle_run(self):
        if self.worker and self.worker.is_alive():
            self.stop_run()
        else:
            self.start_run(loop=True)

    def run_once(self):
        self.start_run(loop=False)

    def start_run(self, loop=True):
        if self.worker and self.worker.is_alive():
            return
        def group_enabled(t):
            return self._group(t[1], "prep" if t[0] == "prep" else t[2])["enabled"]

        missing = [self._target_name(t) for t in self._all_targets()
                   if group_enabled(t) and not self._get_point(t)]
        if missing:
            msg = "座標が未設定の項目があります。そこは飛ばして実行しますか？\n\n" + \
                  "\n".join("・" + m for m in missing[:12])
            if len(missing) > 12:
                msg += "\n… 他 {} 件".format(len(missing) - 12)
            if not messagebox.askyesno("確認", msg):
                return

        save_config(self.cfg)
        self.stop_event.clear()
        self.worker = threading.Thread(target=self._worker, args=(loop,), daemon=True)
        self.worker.start()
        self.btn_run.configure(text="■ 停止 (F9)")

    def stop_run(self):
        if self.worker and self.worker.is_alive():
            self.stop_event.set()
            self.set_status("停止要求中…")

    def on_worker_done(self):
        self.btn_run.configure(text="▶ 開始 (F9)")
        self.set_status("待機中")

    def _worker(self, loop):
        self.log("=" * 60)
        self.log("▶ 実行開始（{}）".format("繰り返し" if loop else "1サイクルのみ"))
        try:
            while not self.stop_event.is_set():
                t0 = time.monotonic()
                self._run_cycle()
                if not loop or self.stop_event.is_set():
                    break
                interval = float(self.cfg.get("interval_sec", 60.0))
                while not self.stop_event.is_set():
                    left = interval - (time.monotonic() - t0)
                    if left <= 0:
                        break
                    self.set_status("次のサイクルまで {:.0f} 秒".format(left))
                    self.stop_event.wait(min(0.5, left))
        except pyautogui.FailSafeException:
            self.log("■ フェイルセーフ作動（マウスが画面左上に移動）→ 停止しました")
        except Exception as e:
            self.log("! エラー: {}".format(e))
            self.log(traceback.format_exc())
        finally:
            self.stop_event.set()
            self.log("■ 実行終了")
            self.msg_q.put(("done", None))

    def _run_cycle(self):
        origin = pyautogui.position() if self.cfg.get("restore_mouse", True) else None

        for w in self.cfg["windows"]:
            if self.stop_event.is_set():
                return
            if not w.get("enabled", True):
                continue
            self.log("--- {} ---".format(w["name"]))

            # 前準備（宝箱タブの有効化など）→ 各宝箱、の順に実行する
            groups = [("前準備", w["prep"])] + [(j, w["chests"][k]) for k, j in CHEST_KEYS]
            for gname, g in groups:
                if not g.get("enabled", True):
                    continue
                for st in g["steps"]:
                    if self.stop_event.is_set():
                        return
                    p = st.get("point")
                    if not p:
                        self.log("  スキップ: {} / {} は座標未設定".format(gname, st["label"]))
                        continue
                    self._click(p)
                    self.log("  {} / {} → クリック {}".format(gname, st["label"], fmt_point(p)))
                    if not self._sleep(float(st.get("wait", 0.6))):
                        return

        if origin:
            try:
                pyautogui.moveTo(origin[0], origin[1], duration=0.1)
            except Exception:
                pass

    def _click(self, p):
        x, y = int(p[0]), int(p[1])
        pyautogui.moveTo(x, y, duration=float(self.cfg.get("move_duration", 0.15)))
        time.sleep(float(self.cfg.get("pre_click_wait", 0.15)))
        pyautogui.mouseDown()
        time.sleep(0.06)
        pyautogui.mouseUp()

    def _sleep(self, sec):
        """中断可能な待機。停止要求が来たら False を返す。"""
        end = time.monotonic() + max(0.0, sec)
        while True:
            left = end - time.monotonic()
            if left <= 0:
                return True
            if self.stop_event.wait(min(0.1, left)):
                return False

    # ---------------- 保存 / 読込 ----------------
    def do_save(self):
        save_config(self.cfg)
        self.log("保存しました: {}".format(CONFIG_PATH))

    def do_load(self):
        path = filedialog.askopenfilename(
            initialdir=APP_DIR, filetypes=[("JSON", "*.json"), ("All", "*.*")])
        if not path:
            return
        self.cfg = load_config(path)
        self.v_interval.set(str(self.cfg["interval_sec"]))
        self.v_move.set(str(self.cfg["move_duration"]))
        self.v_pre.set(str(self.cfg["pre_click_wait"]))
        self.v_restore.set(bool(self.cfg["restore_mouse"]))
        self.v_nwin.set(len(self.cfg["windows"]))
        self._build_window_tabs()
        self.log("読み込みました: {}".format(path))

    # ---------------- ホットキー ----------------
    def _start_hotkeys(self):
        def on_press(key):
            try:
                if key == pynput_keyboard.Key.f8:
                    self.msg_q.put(("capture", None))
                elif key == pynput_keyboard.Key.f9:
                    self.msg_q.put(("toggle", None))
                elif key == pynput_keyboard.Key.f12:
                    self.msg_q.put(("stop", None))
            except Exception:
                pass

        self.listener = pynput_keyboard.Listener(on_press=on_press)
        self.listener.daemon = True
        self.listener.start()

    # ---------------- ログ / 状態 ----------------
    def log(self, msg):
        self.msg_q.put(("log", msg))

    def set_status(self, msg):
        self.msg_q.put(("status", msg))

    def _pump(self):
        try:
            while True:
                kind, payload = self.msg_q.get_nowait()
                if kind == "log":
                    self.txt.configure(state="normal")
                    self.txt.insert("end", time.strftime("[%H:%M:%S] ") + str(payload) + "\n")
                    self.txt.see("end")
                    self.txt.configure(state="disabled")
                elif kind == "status":
                    self.v_status.set(str(payload))
                elif kind == "capture":
                    self.capture_now()
                elif kind == "toggle":
                    self.toggle_run()
                elif kind == "stop":
                    self.stop_run()
                elif kind == "done":
                    self.on_worker_done()
        except queue.Empty:
            pass
        self.root.after(80, self._pump)

    def on_close(self):
        self.stop_event.set()
        try:
            save_config(self.cfg)
        except Exception:
            pass
        try:
            self.listener.stop()
        except Exception:
            pass
        self.root.destroy()


def main():
    root = tk.Tk()
    try:
        ttk.Style().theme_use("vista")
    except Exception:
        pass
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
