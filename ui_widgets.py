"""管理员弹窗共用的基础小部件。

ui_admin（班级）与 ui_admin_stu（学生）都要用输入框、标签和居中定位，放在这里
避免互相 import 造成循环依赖。
"""

import tkinter as tk
from tkinter import ttk

from ui_theme import COLORS, font


def form_entry(parent, textvariable=None, width=30, show=None):
    """统一风格的输入框：白底、细边框、聚焦变主色。"""
    return tk.Entry(
        parent, textvariable=textvariable, width=width, show=show or "",
        bg=COLORS["card"], fg=COLORS["text"], font=font(11), relief="flat",
        highlightthickness=1, highlightbackground=COLORS["border"],
        highlightcolor=COLORS["primary"], insertbackground=COLORS["text"],
        disabledbackground=COLORS["row_alt"], disabledforeground=COLORS["muted"],
    )


def form_label(parent, text, size=10, bold=False, color=None, bg=None):
    return tk.Label(parent, text=text, bg=bg or COLORS["bg"],
                    fg=color or COLORS["text"], font=font(size, bold))


def _frame_rect(win):
    """外框（含标题栏与边框）的屏幕矩形；拿不到就返回 None。

    wm geometry 的 +x+y 定位的是外框，而 winfo_width / winfo_rootx 给的是客户区，
    两者差着标题栏和边框（150% 下左右各约 11px），只按客户区算中轴整窗会偏右。
    """
    try:
        import ctypes
        import ctypes.wintypes
        hwnd = int(win.wm_frame(), 16)
        rect = ctypes.wintypes.RECT()
        ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(rect))
        w, h = rect.right - rect.left, rect.bottom - rect.top
        if w > 0 and h > 0:
            return rect.left, rect.top, w, h
    except Exception:
        pass
    return None


def center_on(win, master, ratio=3):
    """把弹窗摆到父窗口中线上：水平居中，垂直按 ratio 偏上（越大越靠上）。

    分两步定位。第一步用窗口实际宽高摆一次 —— 固定尺寸的窗口（管理员设置
    1000x760）此时才有真实宽高，winfo_reqwidth 只是内容的自然宽度（950），
    少算的 50px 会让整窗偏右 25px，于是看起来和外观设置不在一条中线上。
    第二步量出外框尺寸再校正一次，把标题栏和边框的偏移也补进去。
    """
    win.update_idletasks()
    w, h = win.winfo_width(), win.winfo_height()
    if w <= 1 or h <= 1:                     # 还没映射时退回内容尺寸
        w, h = win.winfo_reqwidth(), win.winfo_reqheight()
    x = master.winfo_rootx() + (master.winfo_width() - w) // 2
    y = master.winfo_rooty() + (master.winfo_height() - h) // ratio
    win.geometry("+%d+%d" % (max(x, 0), max(y, 0)))
    win.update_idletasks()

    frame = _frame_rect(win)
    if frame is None:
        return
    _left, _top, fw, fh = frame
    # 量到的外框尺寸不合理（窗口还没显示、远程桌面返回 0 之类）就保留第一步结果
    if not (w - 2 <= fw <= w + 80) or not (h - 2 <= fh <= h + 120):
        return
    x = master.winfo_rootx() + (master.winfo_width() - fw) // 2
    y = master.winfo_rooty() + (master.winfo_height() - fh) // ratio
    win.geometry("+%d+%d" % (max(x, 0), max(y, 0)))


# ---------------------------------------------------------------- 弹窗尺寸
def screen_work_area(win):
    """屏幕可用工作区（宽, 高，像素）；Windows 下排除任务栏。

    进程已开启 DPI 感知，这里拿到的是物理像素，与 Tk geometry 的单位一致，
    所以 100%/150%/200% 缩放下都能直接用同一套数字。
    """
    try:
        import ctypes
        import ctypes.wintypes
        rect = ctypes.wintypes.RECT()
        # SPI_GETWORKAREA：主显示器去掉任务栏后的矩形
        ok = ctypes.windll.user32.SystemParametersInfoW(
            0x0030, 0, ctypes.byref(rect), 0)
        if ok:
            w = rect.right - rect.left
            h = rect.bottom - rect.top
            if w > 400 and h > 300:
                return w, h
    except Exception:
        pass
    try:
        w, h = win.winfo_screenwidth(), win.winfo_screenheight()
    except tk.TclError:
        return 1280, 800
    return max(400, int(w * 0.94)), max(300, int(h * 0.90))


def fit_dialog_to_screen(win, tree=None, min_rows=3, max_ratio=0.86,
                         min_w=460, min_h=340):
    """按屏幕分辨率给弹窗定初始尺寸，并允许鼠标拖拽调整大小。

    - 初始尺寸取内容自然大小，最多占屏幕工作区的 max_ratio（不会顶到屏幕边）；
    - 内容太高（高 DPI / 小屏）时逐行减少 Treeview 可见行数，保证筛选框、
      状态栏和底部按钮都留在窗口里，而不是被挤出去；
    - 之后窗口四边都能用鼠标拖，最大不超过屏幕工作区。
    """
    win.resizable(True, True)
    sw, sh = screen_work_area(win)
    max_w = max(min_w, int(sw * max_ratio))
    max_h = max(min_h, int(sh * max_ratio))
    win.update_idletasks()

    if tree is not None:
        try:
            rows = int(tree.cget("height") or 10)
        except (tk.TclError, ValueError):
            rows = 10
        while rows > min_rows and win.winfo_reqheight() > max_h:
            rows -= 1
            tree.configure(height=rows)
            win.update_idletasks()

    w = min(max(win.winfo_reqwidth(), min_w), max_w)
    h = min(max(win.winfo_reqheight(), min_h), max_h)
    win.geometry("%dx%d" % (w, h))
    win.minsize(min_w, min_h)
    win.maxsize(max(min_w + 100, sw - 16), max(min_h + 100, sh - 16))
    # 尺寸定好后摆到父窗口正中：不设位置时由系统决定，常常偏到一边
    try:
        center_on(win, win.master, ratio=2)
    except Exception:
        pass
    return w, h


def field_row(parent, row, label_text, widget, hint=""):
    """一行字段：标签在上、控件在下；提示文字挂在右侧。返回下一行行号。"""
    form_label(parent, label_text, 10, color=COLORS["muted"]).grid(
        row=row, column=0, sticky="w", pady=(0, 3))
    widget.grid(row=row + 1, column=0, sticky="we", ipady=6, pady=(0, 2))
    if hint:
        form_label(parent, hint, 8, color=COLORS["subtle"]).grid(
            row=row + 1, column=1, sticky="w", padx=(12, 0))
    return row + 2


def data_tree(parent, columns, height=10, selectmode="browse",
              style_name="Data.Treeview"):
    """白卡片 + 斑马纹表格 + 细滚动条。

    columns: [(列名, 宽度, 对齐, 是否拉伸)]。删除行用 deleted 标签统一置灰，
    各页面自己决定什么时候打这个标签。selectmode="extended" 可多选（批量删除用）。
    style_name 可换成某个页面专用的紧凑样式，默认行为与其它页面完全一致。
    """
    card = tk.Frame(parent, bg=COLORS["card"],
                    highlightbackground=COLORS["border"], highlightthickness=1)
    card.pack(fill=tk.BOTH, expand=True)
    shell = tk.Frame(card, bg=COLORS["card"])
    shell.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)

    tree = ttk.Treeview(shell, columns=[c[0] for c in columns], show="headings",
                        height=height, style=style_name, selectmode=selectmode)
    for name, width, anchor, stretch in columns:
        tree.heading(name, text=name, anchor=anchor)
        tree.column(name, anchor=anchor, width=width, stretch=stretch, minwidth=56)
    tree.tag_configure("odd", background=COLORS["card"], foreground=COLORS["text"])
    tree.tag_configure("even", background=COLORS["row_alt"], foreground=COLORS["text"])
    tree.tag_configure("deleted", background=COLORS["row_alt"],
                       foreground=COLORS["subtle"])

    scrollbar = ttk.Scrollbar(shell, orient="vertical", command=tree.yview,
                              style="Slim.Vertical.TScrollbar")
    tree.configure(yscrollcommand=scrollbar.set)
    tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
    scrollbar.pack(side=tk.RIGHT, fill=tk.Y, padx=(4, 0))
    return tree


def check_box(parent, text, variable, command=None, bg=None):
    """统一样式的勾选框：无边框、手型光标。"""
    return tk.Checkbutton(
        parent, text=text, variable=variable, command=command,
        bg=bg or COLORS["bg"], fg=COLORS["muted"],
        activebackground=bg or COLORS["bg"], activeforeground=COLORS["text"],
        selectcolor=COLORS["card"], font=font(9), bd=0,
        highlightthickness=0, cursor="hand2")
