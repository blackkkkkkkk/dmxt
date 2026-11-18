"""外观设置面板：切换预设配色、选自定义背景图、调遮罩强度。

改动即时预览，点「应用」才写入 ui_config.json，点「取消」恢复打开前的外观。
"""

import os
import tkinter as tk
from tkinter import filedialog

from ui_background import PRESETS, PRESET_MAP, lerp
from ui_theme import COLORS, RoundedButton, font
from ui_widgets import center_on

SWATCH_W, SWATCH_H = 190, 78
OVERLAY_OPTIONS = ((0, "无"), (15, "轻"), (30, "中"), (55, "重"))


def open_appearance_dialog(app):
    return AppearanceDialog(app)


class AppearanceDialog:
    def __init__(self, app):
        self.app = app
        self.original = dict(app.ui_config)
        self.cfg = dict(app.ui_config)
        self._swatches = {}

        win = tk.Toplevel(app.master)
        self.win = win
        win.title("外观设置")
        win.configure(bg=COLORS["bg"])
        win.resizable(False, False)
        win.transient(app.master)

        pad = 22
        root = tk.Frame(win, bg=COLORS["bg"])
        root.pack(fill=tk.BOTH, expand=True, padx=pad, pady=(18, 16))

        tk.Label(root, text="外观设置", bg=COLORS["bg"], fg=COLORS["text"],
                 font=font(16, True)).pack(anchor="w")
        tk.Label(root, text="背景决定内容区的底色；白色卡片始终浮在最上层",
                 bg=COLORS["bg"], fg=COLORS["muted"], font=font(9)).pack(
            anchor="w", pady=(3, 14))

        # ---------------- 预设配色 ----------------
        tk.Label(root, text="预设配色", bg=COLORS["bg"], fg=COLORS["text"],
                 font=font(10, True)).pack(anchor="w")
        grid = tk.Frame(root, bg=COLORS["bg"])
        grid.pack(fill=tk.X, pady=(8, 14))
        # 最后一列 / 最后一行不再补右、下间距，否则色卡网格的右边缘会比下面
        # 「应用」按钮的右边缘往里缩 9px，看着像整块色卡没对齐
        last_col = 2
        last_row = (len(PRESETS) - 1) // 3
        for idx, item in enumerate(PRESETS):
            key, name, top, bottom, dark = item
            cell = tk.Frame(grid, bg=COLORS["bg"])
            cell.grid(row=idx // 3, column=idx % 3,
                      padx=(0, 0) if idx % 3 == last_col else (0, 9),
                      pady=(0, 0) if idx // 3 == last_row else (0, 9))
            cv = tk.Canvas(cell, width=SWATCH_W, height=SWATCH_H,
                           highlightthickness=0, bd=0, cursor="hand2")
            cv.pack()
            cv.bind("<Button-1>", lambda _e, k=key: self.choose_preset(k))
            self._swatches[key] = cv
            self._paint_swatch(cv, top, bottom, name, dark)

        # ---------------- 自定义图片 ----------------
        tk.Label(root, text="自定义图片", bg=COLORS["bg"], fg=COLORS["text"],
                 font=font(10, True)).pack(anchor="w")

        pic_row = tk.Frame(root, bg=COLORS["bg"])
        pic_row.pack(fill=tk.X, pady=(8, 6))

        RoundedButton(pic_row, text="选择图片…", command=self.choose_image,
                      width=112, height=36, radius=10, parent_bg=COLORS["bg"],
                      font_obj=font(10)).pack(side=tk.LEFT)

        RoundedButton(pic_row, text="清除图片", command=self.clear_image,
                      width=104, height=36, bg=COLORS["card"], fg="#334155",
                      hover_bg="#F8FAFC", border=COLORS["border"], radius=10,
                      parent_bg=COLORS["bg"], font_obj=font(10)).pack(
            side=tk.LEFT, padx=(10, 0))

        self.path_label = tk.Label(pic_row, text="", bg=COLORS["bg"],
                                   fg=COLORS["muted"], font=font(9), anchor="w")
        self.path_label.pack(side=tk.LEFT, padx=(12, 0))

        tk.Label(root, text="支持 JPG / PNG / BMP / WebP；图片按铺满方式居中裁剪",
                 bg=COLORS["bg"], fg=COLORS["subtle"], font=font(9)).pack(
            anchor="w", pady=(2, 14))

        # ---------------- 遮罩 ----------------
        tk.Label(root, text="图片遮罩", bg=COLORS["bg"], fg=COLORS["text"],
                 font=font(10, True)).pack(anchor="w")
        tk.Label(root, text="图片较花时加一层遮罩，标题和表格看着不费眼（仅图片背景生效）",
                 bg=COLORS["bg"], fg=COLORS["subtle"], font=font(9)).pack(
            anchor="w", pady=(2, 7))

        self.overlay_var = tk.IntVar(value=self._nearest_overlay(self.cfg.get("overlay", 15)))
        row = tk.Frame(root, bg=COLORS["bg"])
        row.pack(fill=tk.X, pady=(0, 16))
        self._overlay_btns = {}
        for value, label in OVERLAY_OPTIONS:
            btn = RoundedButton(
                row, text=label, command=lambda v=value: self._set_overlay(v),
                width=64, height=32, bg=COLORS["card"], fg="#334155",
                hover_bg="#F8FAFC", border=COLORS["border"], radius=8,
                parent_bg=COLORS["bg"], font_obj=font(10),
                selected_bg=COLORS["primary"], selected_fg="#FFFFFF")
            btn.pack(side=tk.LEFT, padx=(0, 9))
            self._overlay_btns[value] = btn

        # ---------------- 底部按钮 ----------------
        tk.Frame(root, bg=COLORS["border"], height=1).pack(fill=tk.X)
        foot = tk.Frame(root, bg=COLORS["bg"])
        foot.pack(fill=tk.X, pady=(14, 0))

        RoundedButton(foot, text="应用", command=self.apply,
                      width=100, height=38, radius=10,
                      parent_bg=COLORS["bg"], font_obj=font(10)).pack(side=tk.RIGHT)

        RoundedButton(foot, text="取消", command=self.cancel,
                      width=100, height=38, bg=COLORS["card"], fg="#334155",
                      hover_bg="#F8FAFC", border=COLORS["border"], radius=10,
                      parent_bg=COLORS["bg"], font_obj=font(10)).pack(
            side=tk.RIGHT, padx=(0, 10))

        RoundedButton(foot, text="恢复默认", command=self.reset,
                      width=110, height=38, bg=COLORS["card"], fg="#334155",
                      hover_bg="#F8FAFC", border=COLORS["border"], radius=10,
                      parent_bg=COLORS["bg"], font_obj=font(10)).pack(side=tk.LEFT)

        self._refresh()

        win.update_idletasks()
        center_on(win, app.master)
        win.protocol("WM_DELETE_WINDOW", self.cancel)
        win.bind("<Escape>", lambda _e: self.cancel())
        try:
            win.grab_set()
        except tk.TclError:
            pass

    # ---------- 布局 ----------
    @staticmethod
    def _nearest_overlay(value):
        """旧配置里可能是任意数值，对齐到面板上的可选项。"""
        try:
            value = int(value)
        except (TypeError, ValueError):
            value = 15
        return min(OVERLAY_OPTIONS, key=lambda o: abs(o[0] - value))[0]

    def _center(self, master):
        """保留名字给旧调用方；定位规则已统一到 ui_widgets.center_on。"""
        center_on(self.win, master)

    def _paint_swatch(self, cv, top, bottom, name, dark):
        cv.delete("all")
        w, h = SWATCH_W, SWATCH_H
        steps = 26
        for i in range(steps):
            t = i / float(steps - 1)
            cv.create_rectangle(0, h * i / steps, w, h * (i + 1) / steps + 1,
                                fill=lerp(top, bottom, t), outline="")
        cv.create_rectangle(0, 0, w - 1, h - 1, outline=COLORS["border"])
        cv.create_text(12, h - 15, text=name, anchor="w",
                       fill="#FFFFFF" if dark else "#334155",
                       font=font(10, True))

    def _refresh(self):
        """同步勾选态、路径文案与遮罩按钮。"""
        active = self.cfg.get("type") == "image"
        for key, cv in self._swatches.items():
            cv.delete("sel")
            if not active and key == self.cfg.get("preset"):
                cv.create_rectangle(1, 1, SWATCH_W - 2, SWATCH_H - 2,
                                    outline=COLORS["primary"], width=2, tags="sel")
                cv.create_text(SWATCH_W - 12, 12, text="✓", anchor="ne",
                               fill="#FFFFFF" if PRESET_MAP[key]["dark"] else COLORS["primary"],
                               font=font(11, True), tags="sel")

        path = self.cfg.get("image") or ""
        if active and path:
            self.path_label.config(text=self._short(path), fg=COLORS["text"])
        elif active:
            self.path_label.config(text="（未选择文件）", fg=COLORS["muted"])
        else:
            self.path_label.config(text="当前使用预设配色", fg=COLORS["muted"])

        current = self._nearest_overlay(self.cfg.get("overlay", 15))
        for value, btn in self._overlay_btns.items():
            btn.set_selected(value == current)

    @staticmethod
    def _short(path, limit=42):
        name = os.path.basename(path)
        return name if len(name) <= limit else name[:limit - 1] + "…"

    # ---------- 交互 ----------
    def choose_preset(self, key):
        self.cfg["type"] = "preset"
        self.cfg["preset"] = key
        self.cfg["image"] = ""
        self._preview()

    def choose_image(self):
        path = filedialog.askopenfilename(
            parent=self.win, title="选择背景图片",
            filetypes=[("图片文件", "*.jpg *.jpeg *.png *.bmp *.webp *.gif"),
                       ("所有文件", "*.*")])
        if not path:
            return
        self.cfg["type"] = "image"
        self.cfg["image"] = path
        self._preview()

    def clear_image(self):
        self.cfg["type"] = "preset"
        self.cfg["image"] = ""
        self._preview()

    def _set_overlay(self, value):
        self.cfg["overlay"] = int(value)
        self.overlay_var.set(int(value))
        self._preview()

    def _preview(self):
        self._refresh()
        self.app.apply_background(self.cfg, persist=False)
        err = getattr(self.app.bg, "image_error", "")
        if err:
            self.path_label.config(text="读取失败：" + err[:34], fg=COLORS["danger"])

    def reset(self):
        from ui_background import DEFAULT_CONFIG
        self.cfg = dict(DEFAULT_CONFIG)
        self.overlay_var.set(int(self.cfg["overlay"]))
        self._preview()

    def apply(self):
        self.app.apply_background(self.cfg, persist=True)
        self._close()

    def cancel(self):
        self.app.apply_background(self.original, persist=False)
        self._close()

    def _close(self):
        try:
            self.win.grab_release()
        except tk.TclError:
            pass
        self.win.destroy()
