"""dmxt 界面主题：配色、字体、圆角按钮与 ttk 样式。

只依赖标准库 tkinter，不引入第三方 UI 库，保持 PyInstaller 打包零额外依赖。
"""

import tkinter as tk
from tkinter import ttk

FAMILY = "微软雅黑"

COLORS = {
    # 基础
    "bg": "#F1F5F9",
    "card": "#FFFFFF",
    "border": "#E2E8F0",
    "text": "#0F172A",
    "muted": "#64748B",
    "subtle": "#94A3B8",
    # 主色
    "primary": "#2563EB",
    "primary_dark": "#1D4ED8",
    "primary_soft": "#EFF6FF",
    # 语义色
    "success": "#16A34A",
    "success_soft": "#ECFDF5",
    "danger": "#DC2626",
    "danger_soft": "#FEF2F2",
    "warn": "#D97706",
    "warn_soft": "#FFFBEB",
    # 侧边栏
    "sidebar": "#111827",
    "sidebar_hover": "#1F2937",
    "sidebar_text": "#CBD5E1",
    "sidebar_muted": "#6B7280",
    "sidebar_line": "#1F2937",
    # 表格
    "row_alt": "#F8FAFC",
}


def font(size, bold=False):
    """统一字体入口，避免各处字体族不一致。"""
    return (FAMILY, size, "bold") if bold else (FAMILY, size)


def _to_rgb(color):
    color = color.lstrip("#")
    return tuple(int(color[i:i + 2], 16) for i in (0, 2, 4))


def _to_hex(channels):
    return "#%02x%02x%02x" % tuple(max(0, min(255, int(v))) for v in channels)


def darken(color, factor=0.88):
    return _to_hex(v * factor for v in _to_rgb(color))


def lighten(color, factor=0.12):
    return _to_hex(v + (255 - v) * factor for v in _to_rgb(color))


def measure_text(text, font_obj=None):
    """量一段文字在 Tk 下的像素宽度。

    ui_theme.font() 返回的是 (family, size) 元组，本身没有 measure 方法，
    要转成 tkinter.font.Font 才能量；量不到时按字符宽度估算兜底。
    """
    spec = font_obj or font(11)
    if hasattr(spec, "measure"):
        try:
            return spec.measure(text)
        except Exception:
            pass
    try:
        from tkinter.font import Font
        return Font(font=spec).measure(text)
    except Exception:
        pass
    size = spec[1] if isinstance(spec, tuple) and len(spec) > 1 else 11
    unit = size * 1.33
    return int(sum(unit if ord(ch) > 0x2E80 else unit * 0.55 for ch in text))


def button_width(text, font_obj=None, min_width=0, padding=26):
    """按实际字体量文字宽度来定按钮宽，避免中文/符号被裁。

    Tk 的 font 以磅为单位，150% 缩放下同一句中文会比 100% 宽出 1.5 倍，
    所以按钮宽度不能写死，必须量。
    """
    return max(int(min_width), int(measure_text(text, font_obj) + padding))


class RoundedButton(tk.Canvas):
    """Canvas 绘制的圆角按钮：支持悬停、按下、选中三态，可整宽填充。"""

    def __init__(self, master, text="", command=None, width=160, height=44,
                 bg=COLORS["primary"], fg="#FFFFFF", hover_bg=None,
                 radius=12, padding=18, anchor="center", parent_bg=None,
                 font_obj=None, enabled=True, selected_bg=COLORS["primary"],
                 selected_fg="#FFFFFF", border=None, icon=None, icon_col=36):
        if parent_bg is None:
            try:
                parent_bg = master.cget("bg")
            except Exception:
                parent_bg = COLORS["bg"]
        super().__init__(master, width=width, height=height, bg=parent_bg,
                         highlightthickness=0, bd=0, takefocus=0,
                         cursor="hand2" if enabled else "arrow")

        self._text = text
        self._icon = icon
        self._icon_col = icon_col
        self._command = command
        self._normal_bg = bg
        self._normal_fg = fg
        self._normal_hover = hover_bg or darken(bg, 0.9)
        self._selected_bg = selected_bg
        self._selected_fg = selected_fg
        self._base = bg
        self._fill = bg
        self._fg = fg
        self._hover = self._normal_hover
        self._press = darken(self._hover, 0.9)
        self._radius = radius
        self._padding = padding
        self._anchor = anchor
        self._font = font_obj or font(12)
        self._enabled = enabled
        self._req = (width, height)
        self._border = border
        # 禁用时文字灰掉，否则看起来和能点的按钮一模一样
        if not enabled:
            self._fg = COLORS["subtle"]

        self.bind("<Configure>", lambda e: self._draw())
        if enabled:
            self.bind("<Enter>", self._on_enter)
            self.bind("<Leave>", self._on_leave)
            self.bind("<ButtonPress-1>", self._on_press)
            self.bind("<ButtonRelease-1>", self._on_release)
        self._draw()

    # ---------- 绘制 ----------
    @staticmethod
    def _round_rect(x1, y1, x2, y2, r):
        """闭合圆角矩形的控制点，配合 smooth=True 得到圆角。"""
        return [
            x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r,
            x2, y2 - r, x2, y2, x2 - r, y2, x1 + r, y2,
            x1, y2, x1, y2 - r, x1, y1 + r, x1, y1,
        ]

    def _draw(self):
        w, h = self.winfo_width(), self.winfo_height()
        if w <= 1 or h <= 1:
            w, h = self._req
        self.delete("all")
        r = max(0.0, min(self._radius, h / 2.0, w / 2.0))
        x1, y1, x2, y2 = (1.0, 1.0, w - 1.0, h - 1.0) if self._border else \
                         (0.5, 0.5, w - 0.5, h - 0.5)
        pts = self._round_rect(x1, y1, x2, y2, r)
        if self._border:
            self.create_polygon(pts, smooth=True, splinesteps=24,
                                fill=self._fill, outline=self._border, width=1)
        else:
            self.create_polygon(pts, smooth=True, splinesteps=24,
                                fill=self._fill, outline=self._fill)
        if self._anchor == "w":
            # 图标固定占一列、居中放在列里，文字从列尾开始。这样不同宽度的符号
            # （⚙ / ◐ / ◉ / ☀）不会把后面的文字推得前后不一，多行之间左边缘才齐。
            x = self._padding
            if self._icon:
                self.create_text(x + self._icon_col / 2.0, h / 2.0, text=self._icon,
                                 fill=self._fg, font=self._font, anchor="center")
                x += self._icon_col
            self.create_text(x, h / 2.0, text=self._text, fill=self._fg,
                             font=self._font, anchor="w")
        else:
            self.create_text(w / 2.0, h / 2.0, text=self._text, fill=self._fg,
                             font=self._font, anchor="center")

    # ---------- 交互 ----------
    def _on_enter(self, _=None):
        self._fill = self._hover
        self._draw()

    def _on_leave(self, _=None):
        self._fill = self._base
        self._draw()

    def _on_press(self, _=None):
        self._fill = self._press
        self._draw()

    def _on_release(self, event=None):
        self._fill = self._hover
        self._draw()
        if event is None:
            return
        inside = (0 <= event.x <= self.winfo_width()
                  and 0 <= event.y <= self.winfo_height())
        if inside:
            self.invoke()

    def invoke(self):
        if self._enabled and self._command is not None:
            self._command()

    # ---------- 对外方法 ----------
    def set_text(self, text):
        self._text = text
        self._draw()

    def set_colors(self, bg, fg, hover_bg=None, border=None):
        """换配色。同一个按钮在不同情境下承担不同动作时用，避免额外占版面。"""
        self._normal_bg = bg
        self._normal_fg = fg
        self._normal_hover = hover_bg or darken(bg, 0.9)
        self._base = bg
        # 禁用状态下换配色也要保持灰字，别把「点不动」画成「能点」
        self._fg = fg if self._enabled else COLORS["subtle"]
        self._hover = self._normal_hover
        self._press = darken(self._hover, 0.9)
        self._fill = self._base
        if border is not None:
            self._border = border
        self._draw()

    def set_selected(self, selected):
        """导航项选中态：选中用主色，取消后回到常态配色。"""
        if selected:
            self._base, self._fg = self._selected_bg, self._selected_fg
            self._hover = darken(self._selected_bg, 0.9)
        else:
            self._base, self._fg = self._normal_bg, self._normal_fg
            self._hover = self._normal_hover
        self._press = darken(self._hover, 0.9)
        self._fill = self._base
        self._draw()

    def set_enabled(self, enabled):
        self._enabled = enabled
        self.configure(cursor="hand2" if enabled else "arrow")
        if enabled:
            self._fg = self._normal_fg
            self.bind("<Enter>", self._on_enter)
            self.bind("<Leave>", self._on_leave)
            self.bind("<ButtonPress-1>", self._on_press)
            self.bind("<ButtonRelease-1>", self._on_release)
        else:
            for seq in ("<Enter>", "<Leave>", "<ButtonPress-1>", "<ButtonRelease-1>"):
                self.unbind(seq)
            self._fg = COLORS["subtle"]
        self._fill = self._base
        self._draw()


def apply_ttk_style(root):
    """配置 ttk 主题：表格、滚动条、下拉框。"""
    style = ttk.Style(root)
    try:
        style.theme_use("clam")
    except tk.TclError:
        pass

    # 下拉框弹出列表（Tk 选项库，须在创建控件前设置）
    root.option_add("*TCombobox*Listbox.background", COLORS["card"])
    root.option_add("*TCombobox*Listbox.foreground", COLORS["text"])
    root.option_add("*TCombobox*Listbox.selectBackground", COLORS["primary"])
    root.option_add("*TCombobox*Listbox.selectForeground", "#FFFFFF")
    root.option_add("*TCombobox*Listbox.font", font(11))

    style.configure("Class.TCombobox",
                    fieldbackground=COLORS["card"],
                    background=COLORS["card"],
                    foreground=COLORS["text"],
                    arrowcolor=COLORS["muted"],
                    bordercolor=COLORS["border"],
                    lightcolor=COLORS["card"],
                    darkcolor=COLORS["card"],
                    padding=(10, 7),
                    font=font(11),
                    relief="flat")
    style.map("Class.TCombobox",
              fieldbackground=[("readonly", COLORS["card"])],
              bordercolor=[("focus", COLORS["primary"]), ("active", "#CBD5E1")],
              arrowcolor=[("active", COLORS["primary"])],
              foreground=[("readonly", COLORS["text"])])

    style.configure("Data.Treeview",
                    background=COLORS["card"],
                    fieldbackground=COLORS["card"],
                    foreground=COLORS["text"],
                    rowheight=46,
                    borderwidth=0,
                    font=font(11))
    style.configure("Data.Treeview.Heading",
                    background=COLORS["row_alt"],
                    foreground="#475569",
                    relief="flat",
                    borderwidth=0,
                    padding=(10, 12),
                    font=font(10, True))
    style.map("Data.Treeview.Heading", background=[("active", "#EEF2F7")])
    style.map("Data.Treeview",
              background=[("selected", COLORS["primary_soft"])],
              foreground=[("selected", COLORS["text"])])
    # 去掉 treeview 自带外框，让卡片边框接管
    style.layout("Data.Treeview", [("Data.Treeview.treearea", {"sticky": "nswe"})])

    style.configure("Slim.Vertical.TScrollbar",
                    gripcount=0,
                    background="#CBD5E1",
                    darkcolor="#CBD5E1",
                    lightcolor="#CBD5E1",
                    troughcolor=COLORS["card"],
                    bordercolor=COLORS["card"],
                    arrowcolor=COLORS["muted"],
                    width=10,
                    arrowsize=8)
    style.map("Slim.Vertical.TScrollbar",
              background=[("active", "#94A3B8"), ("pressed", "#94A3B8")])
    return style
