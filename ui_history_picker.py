# -*- coding: utf-8 -*-
"""点名记录 → 班级多选「下拉面板」。

像网页的 multiple select：点「选择班级…」在按钮下方弹出一块带勾选框的
面板，勾完点「完成」或点面板外空白处即生效；一个都不勾 = 全部班级。
Tk 没有原生的多选下拉，这里用无边框 Toplevel + grab 模拟：
面板外的点击会被 grab 转到面板上，按坐标判断后自动收起。
"""

import tkinter as tk
from tkinter import ttk

from ui_theme import COLORS, RoundedButton, button_width, font


def open_class_picker(app, options, selected, on_apply, anchor=None):
    return ClassDropdown(app, options, selected, on_apply, anchor=anchor)


class ClassDropdown:
    def __init__(self, app, options, selected, on_apply, anchor=None):
        self.app = app
        self.on_apply = on_apply
        self.anchor = anchor
        self.vars = []
        self._closed = False
        self._w = 340
        self._h = 300
        self._master_bind = None
        self._esc_bind = None
        self._resize_origin = None
        self.canvas = None
        self.scrollbar = None
        self._grip = None
        selected_set = {(src, str(key)) for src, key in (selected or [])}

        win = tk.Toplevel(app.master)
        self.win = win
        # 先藏起来，等尺寸和位置算好再显示：否则会先在左上角闪一帧
        win.withdraw()
        win.overrideredirect(True)
        win.configure(bg=COLORS["border"])     # 1px 边框
        try:
            win.attributes("-topmost", True)
        except tk.TclError:
            pass

        outer = tk.Frame(win, bg=COLORS["card"])
        outer.pack(fill=tk.BOTH, expand=True, padx=1, pady=1)

        head = tk.Frame(outer, bg=COLORS["card"])
        head.pack(fill=tk.X, padx=10, pady=(8, 4))
        tk.Label(head, text="选择班级（可多选）", bg=COLORS["card"],
                 fg=COLORS["text"], font=font(9, True)).pack(side=tk.LEFT)
        RoundedButton(
            head, text="清空", command=lambda: self._set_all(False),
            width=button_width("清空", font(8), 44), height=22, radius=6,
            bg=COLORS["card"], fg=COLORS["text"], hover_bg=COLORS["row_alt"],
            border=COLORS["border"], parent_bg=COLORS["card"],
            font_obj=font(8)).pack(side=tk.RIGHT)
        RoundedButton(
            head, text="全选", command=lambda: self._set_all(True),
            width=button_width("全选", font(8), 44), height=22, radius=6,
            bg=COLORS["card"], fg=COLORS["text"], hover_bg=COLORS["row_alt"],
            border=COLORS["border"], parent_bg=COLORS["card"],
            font_obj=font(8)).pack(side=tk.RIGHT, padx=(0, 6))

        holder = tk.Frame(outer, bg=COLORS["card"])
        holder.pack(fill=tk.BOTH, expand=True, padx=8)
        listed = max(60, min(280, len(options) * 29))
        canvas = tk.Canvas(holder, bg=COLORS["card"], highlightthickness=0,
                           bd=0, height=listed, width=self._w - 30)
        bar = ttk.Scrollbar(holder, orient="vertical", command=canvas.yview)
        inner = tk.Frame(canvas, bg=COLORS["card"])
        canvas.create_window((0, 0), window=inner, anchor="nw")
        inner.bind("<Configure>",
                   lambda _e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.configure(yscrollcommand=bar.set)
        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        # 滚动条常显：面板可以被拖小，内容多时随时能拖
        bar.pack(side=tk.RIGHT, fill=tk.Y)
        self.canvas, self.scrollbar = canvas, bar

        if not options:
            tk.Label(inner, text="还没有可选的班级（先有签到记录才会出现）",
                     bg=COLORS["card"], fg=COLORS["muted"],
                     font=font(9)).pack(anchor="w", padx=8, pady=10)
        for label, pair in options:
            var = tk.BooleanVar(value=pair in selected_set)
            tk.Checkbutton(
                inner, text=label, variable=var, bg=COLORS["card"],
                fg=COLORS["text"], activebackground=COLORS["card"],
                activeforeground=COLORS["text"], selectcolor="#FFFFFF",
                font=font(10), anchor="w", justify=tk.LEFT,
                padx=6, pady=3).pack(fill=tk.X)
            self.vars.append((var, pair))

        def _wheel(event):
            try:
                canvas.yview_scroll(int(-event.delta / 120), "units")
            except tk.TclError:
                pass
        # 勾选框铺满整行，鼠标停在上面也能滚，不然看起来像滚轮失灵
        for widget in [canvas, inner] + list(inner.winfo_children()):
            widget.bind("<MouseWheel>", _wheel)

        foot = tk.Frame(outer, bg=COLORS["card"])
        # 右侧留 22px 给右下角的拖拽手柄，别压住「完成」按钮
        foot.pack(fill=tk.X, padx=(10, 22), pady=(4, 8))
        tk.Label(foot, text="不勾 = 全部班级", bg=COLORS["card"],
                 fg=COLORS["muted"], font=font(8)).pack(side=tk.LEFT)
        RoundedButton(
            foot, text="完成", command=self.confirm,
            width=button_width("完成", font(9), 60), height=26, radius=7,
            bg=COLORS["primary"], fg="#FFFFFF",
            hover_bg=COLORS["primary_dark"], border=COLORS["primary"],
            parent_bg=COLORS["card"], font_obj=font(9)).pack(side=tk.RIGHT)

        # 右下角手柄：按住拖动可以改面板大小（overrideredirect 窗口没有系统边框）
        grip = tk.Canvas(outer, width=14, height=14, bg=COLORS["card"],
                         highlightthickness=0, bd=0, cursor="size_nw_se")
        grip.create_line(4, 13, 13, 4, fill=COLORS["subtle"])
        grip.create_line(8, 13, 13, 8, fill=COLORS["subtle"])
        grip.create_line(11, 13, 13, 11, fill=COLORS["subtle"])
        grip.place(relx=1.0, rely=1.0, anchor="se", x=-3, y=-3)
        grip.bind("<ButtonPress-1>", self._resize_start)
        grip.bind("<B1-Motion>", self._resize_move)
        self._grip = grip

        # 按内容实际高度定列表高度：先按行数估会出现最后一行被裁
        win.update_idletasks()
        content_h = inner.winfo_reqheight()
        canvas.configure(height=max(60, min(280, content_h)))
        win.update_idletasks()
        w = max(self._w, win.winfo_reqwidth())
        h = win.winfo_reqheight()
        sw, sh = win.winfo_screenwidth(), win.winfo_screenheight()
        if anchor is not None:
            try:
                x = anchor.winfo_rootx()
                y = anchor.winfo_rooty() + anchor.winfo_height() + 2
            except tk.TclError:
                x, y = win.winfo_pointerx(), win.winfo_pointery() + 4
        else:
            x = win.winfo_pointerx()
            y = win.winfo_pointery() + 4
        if y + h > sh - 10:
            base = anchor.winfo_rooty() if anchor is not None else y
            y = max(10, base - h - 2)
        x = max(8, min(x, sw - w - 8))
        self._w, self._h = w, h
        win.geometry("%dx%d+%d+%d" % (w, h, x, y))

        # 不用 grab_set：grab 会把「点开面板」的那次鼠标事件重定向到面板上，
        # 按坐标看起来落在外面，面板一闪就被关掉，列表根本打不开。
        # 改成监听主窗口的点击：点面板外就收起，点「选择班级」按钮由页面
        # 自己做 open/close 切换。
        self._master_bind = app.master.bind(
            "<Button-1>", self._on_master_click, add="+")
        self._esc_bind = app.master.bind(
            "<Escape>", lambda _e: self.close(apply=True), add="+")
        win.deiconify()
        win.lift()

    # ------------------------------------------------------------ 交互
    def _resize_start(self, event):
        self._resize_origin = (int(event.x_root), int(event.y_root),
                               self.win.winfo_width(),
                               self.win.winfo_height())

    def _resize_move(self, event):
        """拖右下角改大小：保持左上角不动，限制在 280x160 ~ 屏幕内。"""
        if self._resize_origin is None:
            return
        ox, oy, ow, oh = self._resize_origin
        w = ow + int(event.x_root) - ox
        h = oh + int(event.y_root) - oy
        sw = self.win.winfo_screenwidth()
        sh = self.win.winfo_screenheight()
        max_w = max(300, sw - self.win.winfo_rootx() - 10)
        max_h = max(180, sh - self.win.winfo_rooty() - 10)
        w = max(280, min(w, max_w))
        h = max(160, min(h, max_h))
        self._w, self._h = w, h
        self.win.geometry("%dx%d" % (w, h))

    def _set_all(self, value):
        for var, _pair in self.vars:
            var.set(bool(value))

    def selected_pairs(self):
        return [pair for var, pair in self.vars if var.get()]

    def _on_master_click(self, event):
        """点主窗口上的别处就收起；点面板或「选择班级」按钮本身不处理。"""
        if self._closed:
            return
        try:
            x, y = int(event.x_root), int(event.y_root)
            px, py = self.win.winfo_rootx(), self.win.winfo_rooty()
            pw, ph = self.win.winfo_width(), self.win.winfo_height()
        except tk.TclError:
            return
        if px <= x <= px + pw and py <= y <= py + ph:
            return
        if self.anchor is not None:
            try:
                ax, ay = self.anchor.winfo_rootx(), self.anchor.winfo_rooty()
                if (ax <= x <= ax + self.anchor.winfo_width()
                        and ay <= y <= ay + self.anchor.winfo_height()):
                    return
            except tk.TclError:
                pass
        self.close(apply=True)

    def confirm(self):
        self.close(apply=True)

    def close(self, apply=True):
        if self._closed:
            return
        self._closed = True
        pairs = self.selected_pairs() if apply else None
        for sequence, funcid in (("<Button-1>", self._master_bind),
                                 ("<Escape>", self._esc_bind)):
            if not funcid:
                continue
            try:
                self.app.master.unbind(sequence, funcid)
            except Exception:
                pass
        self._master_bind = self._esc_bind = None
        try:
            self.win.destroy()
        except tk.TclError:
            pass
        if apply and self.on_apply:
            self.on_apply(pairs)
