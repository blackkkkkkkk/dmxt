"""学员积分：按班级分类学员，加减积分并留下带备注的流水。

页面分两块：
- 上半部左边是学员列表，按「当前积分范围」内的积分降序排名并显示名次
  （名次 / 班级 / 姓名 / 积分 / 加分 / 扣分），右边是「调整积分」；
- 下半部是积分流水，可按班级、学生、日期查，也能导出 CSV。

「积分范围」同时管学员列表和流水：默认「今天」只看当日加减分，
切到「全部」就是累计总积分；统计卡、排名、表头都跟着范围走。
加减分记账始终以「全部流水的累计余额」为准（real_balance），
不会因为正在看今天就把历史积分抹掉。

积分余额不存字段，由流水 SUM 得到（见 mapper/t_points_mapper.py），
所以这里每改一笔都会同时刷新列表、统计卡和流水，三处永远一致。
流水只追加，不提供改和删；记错了再记一笔反向分值并写明备注即可。
"""

import csv
import logging
import os
import tkinter as tk
from datetime import date, datetime, timedelta
from tkinter import font as tkfont
from tkinter import messagebox, ttk

from mapper.t_points_mapper import RECORD_LIMIT, PointsMapper
from points_shortcuts import active_slots, key_display, load_slots
from ui_theme import COLORS, RoundedButton, button_width, font
from ui_widgets import check_box, data_tree, form_entry

ALL_CLASSES = "全部班级"
CSV_COLUMNS = ("时间", "班级", "学生", "变动", "变动后积分", "备注")
# 学员列表默认要能看到的行数；列表与流水之间可用鼠标拖动分隔条改高度
STU_VISIBLE_ROWS = 10

# 积分范围：一个下拉同时决定「学员积分/排名/统计卡」和「积分流水」的时间口径
RANGE_ALL = "全部"
RANGE_TODAY = "今天"
RANGE_7 = "近 7 天"
RANGE_30 = "近 30 天"
RANGE_CUSTOM = "自定义"
RANGE_CHOICES = (RANGE_ALL, RANGE_TODAY, RANGE_7, RANGE_30, RANGE_CUSTOM)
DEFAULT_RANGE = RANGE_TODAY


def today_text():
    return date.today().strftime("%Y-%m-%d")


def days_ago_text(days):
    return (date.today() - timedelta(days=days)).strftime("%Y-%m-%d")

STU_COLUMNS = (
    ("排名", 54, "center", False),
    # 后三列要放得下动态表头「今日积分 / 区间加分」这类四字标题
    # （实测 4 字约 52px + heading 左右各 10px padding，留足余量），
    # 宽度从班级/姓名两列匀过来，总宽基本不变
    ("班级", 84, "w", True),
    ("姓名", 74, "w", False),
    ("积分", 108, "center", False),
    ("加分", 104, "center", False),
    ("扣分", 104, "center", False),
)
REC_COLUMNS = (
    # 时间列要放得下完整的 YYYY-MM-DD HH:MM:SS，备注列让出对应宽度
    ("时间", 192, "center", False),
    ("班级", 200, "w", False),
    ("学生", 92, "w", False),
    ("变动", 74, "center", False),
    ("余额", 76, "center", False),
    ("备注", 236, "w", True),
)


def _date_ok(text):
    """日期筛选框允许留空；填了就必须是 YYYY-MM-DD。"""
    text = (text or "").strip()
    if not text:
        return True
    try:
        datetime.strptime(text, "%Y-%m-%d")
        return True
    except ValueError:
        return False


def _int_text(value):
    """分值显示：正分带 + 号，负分保留 - 号。"""
    number = int(value)
    return "+%d" % number if number > 0 else str(number)


def rank_students(rows):
    """按当前积分降序排列，并写入名次（同分并列同名次：1、1、3……）。

    名次只针对「当前筛选出来的学员」，切班级或搜索后重新从 1 开始；
    同分时按班级、姓名排，保证每次刷新顺序稳定、不会来回跳。
    """
    rows.sort(key=lambda r: (-int(r["balance"]), str(r["class_id"]),
                             str(r["stu_name"])))
    prev_balance = None
    rank = 0
    for index, row in enumerate(rows, 1):
        if row["balance"] != prev_balance:
            rank = index
            prev_balance = row["balance"]
        row["rank"] = rank
    return rows


def _compact_stu_style(widget):
    """给积分页学员表建一份紧凑样式，好让一屏能放下 10 行。

    行高按当前字体的实际行距算：普通 DPI 下 34px 足够，高 DPI 下自动
    回到 46px，字不会被裁。只作用于 Points.Treeview，不影响其它页面。
    """
    style = ttk.Style(widget)
    try:
        line_px = tkfont.Font(font=font(11)).metrics("linespace")
    except Exception:
        line_px = 20
    row_px = max(32, min(46, int(line_px * 1.15)))
    base = "Data.Treeview"
    style.configure(
        "Points.Treeview",
        background=style.lookup(base, "background") or COLORS["card"],
        fieldbackground=style.lookup(base, "fieldbackground") or COLORS["card"],
        foreground=style.lookup(base, "foreground") or COLORS["text"],
        borderwidth=0, relief="flat", font=font(11), rowheight=row_px)
    style.map("Points.Treeview",
              background=[("selected", COLORS["primary_soft"])],
              foreground=[("selected", COLORS["text"])])
    style.layout("Points.Treeview",
                 [("Points.Treeview.treearea", {"sticky": "nswe"})])
    heading = base + ".Heading"
    style.configure(
        "Points.Treeview.Heading",
        background=style.lookup(heading, "background") or COLORS["row_alt"],
        foreground=style.lookup(heading, "foreground") or "#475569",
        relief="flat", borderwidth=0, padding=(10, 5), font=font(10, True))
    style.map("Points.Treeview.Heading",
              background=[("active", "#EEF2F7")])
    return row_px


class PointsPage:
    """积分管理页。由 version2.0.show_points() 创建，切页时整个重建。"""

    def __init__(self, app):
        self.app = app
        self.mapper = PointsMapper()
        self.students = []          # 当前筛选下的学员
        self.records = []           # 当前筛选下的流水
        self._class_map = {}        # 下拉显示名 → class_id（"" 表示全部）
        self._stu_by_iid = {}
        self._last_msg = ""

        self.var_class = tk.StringVar(value=ALL_CLASSES)
        self.var_keyword = tk.StringVar()
        self.var_range = tk.StringVar(value=DEFAULT_RANGE)
        # 流水日期框默认就有值（当天），不再让老师面对两个空框猜格式
        self.var_start = tk.StringVar(value=today_text())
        self.var_end = tk.StringVar(value=today_text())
        self.var_only_selected = tk.BooleanVar(value=False)
        self.var_delta = tk.StringVar()
        self.var_note = tk.StringVar()

        self._setting_range = False     # 下拉批量写日期时屏蔽 trace
        self.range_box = None
        self.range_hint = None
        self.stu_tree = None
        self.rec_tree = None
        self.main_body = None        # 上半区内部容器，随分隔条一起改高度
        self.main_default = 0        # 10 行学员对应的默认卡片高度
        self.msg_label = None
        self.status_label = None
        self.selected_label = None
        self.preview_label = None
        self.total_title = self.gain_title = self.loss_title = None

        self._build()
        self.reload()

    # ------------------------------------------------------------ 构建
    def _build(self):
        app = self.app
        app.set_active_nav("points")
        self.row_px = _compact_stu_style(app.master)
        page = app.bg.new_page(padx=30, pady=10)
        page.header(
            "积分管理",
            "学员按积分降序排名（同分并列）；「积分范围」同时管学员积分与流水：全部=累计，今天=当日",
            links=[("刷新", self.reload), ("全部", lambda: self.apply_range(RANGE_ALL)),
                   ("今天", lambda: self.apply_range(RANGE_TODAY)),
                   ("快捷键设置", self.open_shortcut_settings),
                   ("导出流水", self.export_csv)],
        )

        c1, self.count_label, _ = self._stat_card_titled(
            "学员人数", COLORS["primary"])
        c2, self.total_label, self.total_title = self._stat_card_titled(
            "积分总计", COLORS["primary_dark"])
        c3, self.gain_label, self.gain_title = self._stat_card_titled(
            "累计获得", COLORS["success"])
        c4, self.loss_label, self.loss_title = self._stat_card_titled(
            "累计扣减", COLORS["warn"])
        page.stat_row([c1, c2, c3, c4], gap_before=6)

        self._build_main(page)
        self._build_records(page)
        page.layout()

    def _stat_card_titled(self, title, accent):
        """复用主程序的统计卡，同时把标题 label 留下来供范围切换时改名。

        _stat_card 只返回数值 label，这里按它固定的内部结构取标题，
        取不到就返回 None（改名会被跳过，不影响功能）。
        """
        card, value = self.app._stat_card(title, accent)
        title_label = None
        try:
            title_label = card.winfo_children()[1].winfo_children()[0]
        except (IndexError, tk.TclError, AttributeError):
            pass
        return card, value, title_label

    def _label(self, parent, text, size=10, color=None):
        return tk.Label(parent, text=text, bg=COLORS["card"],
                        fg=color or COLORS["muted"], font=font(size))

    def _build_main(self, page):
        card = self.app._card()
        # 固定主区高度：画布没有滚动，若让内容自然撑高，下面的流水表格
        # 会被挤出可视区。固定后两张表格会在容器里自适应行数。
        body = tk.Frame(card, bg=COLORS["card"], height=235)
        body.pack(fill=tk.BOTH, expand=True, padx=14, pady=12)
        body.pack_propagate(False)
        self.main_body = body

        left = tk.Frame(body, bg=COLORS["card"])
        left.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        right = tk.Frame(body, bg=COLORS["card"], width=360)
        right.pack(side=tk.RIGHT, fill=tk.Y, padx=(16, 0))
        right.pack_propagate(False)

        # --- 左：筛选（保持单行！）+ 学员列表 ---
        # 「积分范围」下拉放在下方流水栏（那里是整页宽度，放得下）。这里若
        # 再加一行，固定高度的卡片就装不下默认 10 行学员了，只留一个短
        # 标签标明当前统计口径，表头也会跟着改名。
        filt = tk.Frame(left, bg=COLORS["card"])
        filt.pack(fill=tk.X, pady=(0, 8))
        self._label(filt, "班级").pack(side=tk.LEFT, padx=(0, 8))
        self.class_box = ttk.Combobox(
            filt, textvariable=self.var_class, state="readonly",
            style="Class.TCombobox", width=26)
        self.class_box.pack(side=tk.LEFT, ipady=3)
        self.class_box.bind("<<ComboboxSelected>>", lambda _e: self.reload())
        self._label(filt, "姓名").pack(side=tk.LEFT, padx=(14, 8))
        form_entry(filt, textvariable=self.var_keyword, width=12).pack(
            side=tk.LEFT, ipady=4)
        self.var_keyword.trace_add("write", lambda *_a: self.reload())
        RoundedButton(
            filt, text="重置", command=self.reset_filters,
            width=button_width("重置", font(9), 56), height=30, radius=8,
            bg=COLORS["card"], fg=COLORS["muted"], hover_bg="#F8FAFC",
            border=COLORS["border"], parent_bg=COLORS["card"],
            font_obj=font(9)).pack(side=tk.RIGHT)
        self.range_hint = self._label(filt, "", 9, COLORS["primary"])
        self.range_hint.pack(side=tk.RIGHT, padx=(0, 12))

        self.stu_tree = data_tree(left, STU_COLUMNS, height=STU_VISIBLE_ROWS,
                                  style_name="Points.Treeview")
        self.stu_tree.tag_configure("negative", background=COLORS["danger_soft"],
                                    foreground="#B91C1C")
        self.stu_tree.bind("<<TreeviewSelect>>", lambda _e: self._update_form())

        # --- 右：调整积分。画布不滚动，这里排得紧凑些，保证小窗口下
        #     「确认调整」按钮也在可视区内 ---
        # 不再单独占一行放「调整积分」小标题：页头已说明本页用途，
        # 省下的高度留给下面的确认按钮（画布不滚动，超出的部分会被裁掉）
        self.selected_label = tk.Label(
            right, text="请先在左边选中一名学生", bg=COLORS["card"],
            fg=COLORS["text"], font=font(10, True))
        self.selected_label.pack(anchor="w", pady=(0, 6))
        # 老师不用背快捷键：已配置的按键和分值直接列在右侧
        self.shortcut_hint = self._label(right, "", 9, COLORS["primary"])
        self.shortcut_hint.config(wraplength=340, justify=tk.LEFT, anchor="w")
        self.shortcut_hint.pack(fill=tk.X, pady=(0, 6))
        self.refresh_shortcut_hint()

        quick_holder = tk.Frame(right, bg=COLORS["card"])
        quick_holder.pack(fill=tk.X, pady=(0, 4))
        for row_values in ((1, 3, 5, 10), (-1, -3, -5, -10)):
            line = tk.Frame(quick_holder, bg=COLORS["card"])
            line.pack(fill=tk.X, pady=1)
            for value in row_values:
                text = _int_text(value)
                fg = COLORS["success"] if value > 0 else COLORS["danger"]
                soft = COLORS["success_soft"] if value > 0 else COLORS["danger_soft"]
                RoundedButton(
                    line, text=text, command=lambda v=value: self.set_delta(v),
                    width=button_width(text, font(9), 62), height=26, radius=7,
                    bg=soft, fg=fg, hover_bg=COLORS["row_alt"],
                    border=COLORS["border"], parent_bg=COLORS["card"],
                    font_obj=font(9)).pack(side=tk.LEFT, padx=(0, 6))

        line = tk.Frame(right, bg=COLORS["card"])
        line.pack(fill=tk.X, pady=(0, 3))
        self._label(line, "分值").pack(side=tk.LEFT, padx=(0, 8))
        form_entry(line, textvariable=self.var_delta, width=10).pack(
            side=tk.LEFT, ipady=3)
        self._label(line, "正加负减", 9, COLORS["subtle"]).pack(
            side=tk.LEFT, padx=(8, 0))
        self.var_delta.trace_add("write", lambda *_a: self._update_preview())

        line2 = tk.Frame(right, bg=COLORS["card"])
        line2.pack(fill=tk.X, pady=(0, 3))
        self._label(line2, "备注").pack(side=tk.LEFT, padx=(0, 8))
        form_entry(line2, textvariable=self.var_note, width=28).pack(
            side=tk.LEFT, fill=tk.X, expand=True, ipady=3)

        action = tk.Frame(right, bg=COLORS["card"])
        action.pack(fill=tk.X, pady=(4, 0))
        RoundedButton(
            action, text="确认调整", command=self.apply_adjust,
            width=button_width("确认调整", font(10), 104), height=34, radius=9,
            bg=COLORS["primary"], fg="#FFFFFF", hover_bg=COLORS["primary_dark"],
            border=COLORS["primary"], parent_bg=COLORS["card"],
            font_obj=font(10)).pack(side=tk.LEFT)
        self.preview_label = self._label(action, "", 9, COLORS["primary"])
        self.preview_label.pack(side=tk.LEFT, padx=(10, 0))
        # 操作反馈也显示在这一行：画布不滚动，另起一行在小窗口里会被裁掉
        self.msg_label = self.preview_label

        # 默认高度按「10 行学员 + 表头 + 筛选行 + 内边距」实测计算，
        # 不同 DPI / 字体下都能真的看到 10 行，而不是写死一个像素值
        self.stu_tree.update_idletasks()
        filt.update_idletasks()
        body_h = (self.stu_tree.winfo_reqheight()
                  + filt.winfo_reqheight() + 8 + 16 + 8)
        body.configure(height=body_h)
        card.update_idletasks()
        self.main_default = card.winfo_reqheight()
        initial = self._saved_split() or self.main_default
        page.resizable_block(
            card, gap=4, height=initial, min_height=210,
            on_change=self._on_split_change,
            on_release=self._on_split_release,
            tip="⇕ 拖动调整高度（双击复原）")

    def _saved_split(self):
        """上次拖出的学员列表高度；没有或异常时返回 None。"""
        value = getattr(self.app, "points_split_height", None)
        if value is None:
            cfg = getattr(self.app, "ui_config", {}) or {}
            value = cfg.get("points_split")
        try:
            value = int(value)
        except (TypeError, ValueError):
            return None
        return value if 200 <= value <= 2000 else None

    def _on_split_change(self, height):
        value = int(height)
        self.app.points_split_height = value
        cfg = getattr(self.app, "ui_config", None)
        if isinstance(cfg, dict):
            cfg["points_split"] = value
        # 卡片高度由画布控制，这里同步内部容器的请求高度，
        # 免得 pack_propagate(False) 让它停在构建时的旧值
        if self.main_body is not None:
            try:
                self.main_body.configure(height=max(120, value - 26))
            except tk.TclError:
                pass

    def _on_split_release(self, height):
        cfg = getattr(self.app, "ui_config", None)
        if not isinstance(cfg, dict):
            return
        cfg["points_split"] = int(height)
        try:
            self.app.save_ui_config(cfg)
        except Exception:
            logging.exception("保存积分页学员列表高度失败")

    def _build_records(self, page):
        card = self.app._card()
        bar = tk.Frame(card, bg=COLORS["card"])
        bar.pack(fill=tk.X, padx=14, pady=(6, 4))

        tk.Label(bar, text="积分流水", bg=COLORS["card"], fg=COLORS["text"],
                 font=font(12, True)).pack(side=tk.LEFT)
        # 范围下拉同时管上方学员积分/排名/统计卡和下方流水，避免两处口径不一致
        self._label(bar, "积分范围").pack(side=tk.LEFT, padx=(14, 6))
        self.range_box = ttk.Combobox(
            bar, textvariable=self.var_range, state="readonly",
            style="Class.TCombobox", width=9, values=list(RANGE_CHOICES))
        self.range_box.pack(side=tk.LEFT, ipady=3)
        self.range_box.bind("<<ComboboxSelected>>",
                            lambda _e: self.apply_range(self.var_range.get()))
        self._label(bar, "从").pack(side=tk.LEFT, padx=(12, 6))
        form_entry(bar, textvariable=self.var_start, width=11).pack(
            side=tk.LEFT, ipady=3)
        self._label(bar, "至").pack(side=tk.LEFT, padx=(8, 6))
        form_entry(bar, textvariable=self.var_end, width=11).pack(
            side=tk.LEFT, ipady=3)
        # 两处日期框共用一个 StringVar：任一边改动都重算学员积分和流水
        self.var_start.trace_add("write", lambda *_a: self._on_date_changed())
        self.var_end.trace_add("write", lambda *_a: self._on_date_changed())

        check_box(bar, "只看选中学生", self.var_only_selected,
                  command=self._reload_records, bg=COLORS["card"]).pack(
            side=tk.LEFT, padx=(14, 0))
        RoundedButton(
            bar, text="查询", command=self._reload_records,
            width=button_width("查询", font(9), 56), height=28, radius=7,
            bg=COLORS["card"], fg=COLORS["muted"], hover_bg="#F8FAFC",
            border=COLORS["border"], parent_bg=COLORS["card"],
            font_obj=font(9)).pack(side=tk.LEFT, padx=(10, 0))
        self.status_label = self._label(bar, "", 9, COLORS["subtle"])
        self.status_label.pack(side=tk.RIGHT)

        self.rec_tree = data_tree(card, REC_COLUMNS, height=7,
                                  style_name="Points.Treeview")
        self.rec_tree.tag_configure("plus", background=COLORS["success_soft"],
                                    foreground="#15803D")
        self.rec_tree.tag_configure("minus", background=COLORS["danger_soft"],
                                    foreground="#B91C1C")
        page.block(card, gap=0, expand=True)

    # ------------------------------------------------------------ 数据
    def _class_label(self, class_id):
        for cls in getattr(self.app, "class_all", []):
            if str(getattr(cls, "class_id", "")) == str(class_id):
                name = (getattr(cls, "class_name", "") or "").strip()
                return "%s  %s" % (class_id, name) if name else str(class_id)
        return str(class_id)

    def _refresh_class_options(self):
        self._class_map = {ALL_CLASSES: ""}
        for class_id in self.app.student_data.keys():
            self._class_map[self._class_label(class_id)] = str(class_id)
        self.class_box["values"] = list(self._class_map)
        if self.var_class.get() not in self._class_map:
            self.var_class.set(ALL_CLASSES)

    def _current_class(self):
        return self._class_map.get(self.var_class.get(), "")

    # ------------------------------------------------------------ 积分范围
    def apply_range(self, label):
        """切换积分统计范围：学员积分 / 排名 / 统计卡 / 流水一起跟着变。"""
        if label not in RANGE_CHOICES:
            label = RANGE_TODAY
        if label == RANGE_CUSTOM:
            self._update_range_hint()
            return
        if label == RANGE_ALL:
            start = end = ""
        elif label == RANGE_7:
            start, end = days_ago_text(6), today_text()
        elif label == RANGE_30:
            start, end = days_ago_text(29), today_text()
        else:
            start = end = today_text()
        # 批量写两个日期框时屏蔽 trace，避免触发两次全量刷新
        self._setting_range = True
        self.var_range.set(label)
        self.var_start.set(start)
        self.var_end.set(end)
        self._setting_range = False
        self.reload()

    def _on_date_changed(self):
        """日期框被改动（含下拉设置）：同步范围标签并重算学员积分与流水。"""
        if self._setting_range:
            return
        if self.var_range.get() != RANGE_CUSTOM:
            self.var_range.set(RANGE_CUSTOM)
        self.reload()

    def _range_dates(self):
        """给数据层用的 (start, end)；日期写坏了先不过滤，不把 SQL 弄崩。"""
        start, end = self.var_start.get().strip(), self.var_end.get().strip()
        if not _date_ok(start) or not _date_ok(end):
            return None, None
        return start or None, end or None

    def _update_range_hint(self):
        label = self.var_range.get()
        if self.range_hint is not None:
            self.range_hint.config(text={
                RANGE_ALL: "统计：累计总积分",
                RANGE_TODAY: "统计：今日积分",
                RANGE_CUSTOM: "统计：自定义区间",
            }.get(label, "统计：区间积分"))
        # 列名跟着范围改名，免得把「今日积分」当成学生的累计总积分
        if self.stu_tree is not None:
            prefix = ("" if label == RANGE_ALL else
                      "今日" if label == RANGE_TODAY else "区间")
            for name in ("积分", "加分", "扣分"):
                self.stu_tree.heading(name, text=prefix + name)
        # 三张统计卡的标题也跟着改：看今天时写「今日获得」而不是「累计获得」
        titles = {
            RANGE_ALL: ("积分总计", "累计获得", "累计扣减"),
            RANGE_TODAY: ("今日积分", "今日获得", "今日扣减"),
        }.get(label, ("区间积分", "区间获得", "区间扣减"))
        for widget, text in ((self.total_title, titles[0]),
                             (self.gain_title, titles[1]),
                             (self.loss_title, titles[2])):
            if widget is not None:
                try:
                    widget.config(text=text)
                except tk.TclError:
                    pass

    def reset_filters(self):
        self.var_class.set(ALL_CLASSES)
        self.var_keyword.set("")
        self.var_only_selected.set(False)
        self.apply_range(DEFAULT_RANGE)

    def reload(self, keep_selection=True):
        selected = self._selected_stu_id() if keep_selection else None
        self._refresh_class_options()
        self._update_range_hint()
        self._load_students()
        self._load_records()
        self._update_stats()
        if selected:
            self._select_stu(selected)
        self._update_form()

    def _load_students(self):
        start, end = self._range_dates()
        # balance / gain / loss 是「当前范围」的显示值，用于排名和统计卡；
        # real_balance 是不带日期的累计余额，加减分记账只认它。
        balances = self.mapper.balance_map(start, end)
        gains = self.mapper.gains_map(start, end)
        real_balances = self.mapper.balance_map()
        cls = self._current_class()
        keyword = self.var_keyword.get().strip()
        rows = []
        for class_id, names in self.app.student_data.items():
            if cls and str(class_id) != cls:
                continue
            ids = self.app.student_ids.get(class_id, [])
            for name, sid in zip(names, ids):
                if keyword and keyword not in str(name):
                    continue
                sid = str(sid)
                gain, loss = gains.get(sid, (0, 0))
                rows.append({
                    "stu_id": sid,
                    "stu_name": str(name),
                    "class_id": str(class_id),
                    "class_label": self._class_label(class_id),
                    "balance": int(balances.get(sid, 0)),
                    "real_balance": int(real_balances.get(sid, 0)),
                    "gain": gain,
                    "loss": loss,
                })
        self.students = rank_students(rows)
        self._fill_students()

    def _fill_students(self):
        tree = self.stu_tree
        for item in tree.get_children():
            tree.delete(item)
        self._stu_by_iid = {}
        for idx, row in enumerate(self.students):
            key = row["stu_id"]
            tags = ("negative",) if row["balance"] < 0 else (
                ("even",) if idx % 2 else ("odd",))
            iid = key if key not in self._stu_by_iid else "%s_%d" % (key, idx)
            tree.insert("", tk.END, iid=iid, values=(
                row["rank"], row["class_label"], row["stu_name"],
                row["balance"], row["gain"], row["loss"]), tags=tags)
            self._stu_by_iid[iid] = row

    def _reload_records(self):
        self._load_records()
        self._update_stats()

    def _load_records(self):
        start, end = self.var_start.get().strip(), self.var_end.get().strip()
        if not _date_ok(start) or not _date_ok(end):
            self.status_label.config(text="日期格式应为 YYYY-MM-DD")
            return
        stu_id = self._selected_stu_id() if self.var_only_selected.get() else None
        try:
            self.records = self.mapper.query(
                class_id=self._current_class() or None,
                stu_id=stu_id,
                keyword=self.var_keyword.get().strip() or None,
                start=start or None, end=end or None,
                limit=RECORD_LIMIT)
        except Exception as exc:
            logging.exception("读取积分流水失败")
            self.records = []
            self.status_label.config(text="读取失败：%s" % str(exc)[:40])
            return
        self._fill_records()

    def _fill_records(self):
        tree = self.rec_tree
        for item in tree.get_children():
            tree.delete(item)
        for row in self.records:
            delta = int(row["delta"])
            tree.insert("", tk.END, values=(
                row["create_time"], self._class_label(row["class_id"]),
                row["stu_name"], _int_text(delta), row["balance_after"],
                row["note"] or ""),
                tags=("plus",) if delta > 0 else ("minus",))
        text = "共 %d 条" % len(self.records)
        if len(self.records) >= RECORD_LIMIT:
            text += "（已到上限，请缩小日期范围）"
        self.status_label.config(text=text)

    def _update_stats(self):
        self.count_label.config(text=str(len(self.students)))
        self.total_label.config(text=str(sum(r["balance"] for r in self.students)))
        self.gain_label.config(text=str(sum(r["gain"] for r in self.students)))
        self.loss_label.config(text=str(sum(r["loss"] for r in self.students)))

    # ------------------------------------------------------------ 交互
    def _selected_stu_id(self):
        sel = self.stu_tree.selection() if self.stu_tree else ()
        if not sel:
            return None
        row = self._stu_by_iid.get(sel[0])
        return row["stu_id"] if row else None

    def _selected_row(self):
        sel = self.stu_tree.selection() if self.stu_tree else ()
        return self._stu_by_iid.get(sel[0]) if sel else None

    def _select_stu(self, stu_id):
        for iid, row in self._stu_by_iid.items():
            if row["stu_id"] == str(stu_id):
                try:
                    self.stu_tree.selection_set(iid)
                    self.stu_tree.focus(iid)
                    self.stu_tree.see(iid)
                except tk.TclError:
                    pass
                return

    def set_delta(self, value):
        self.var_delta.set(str(int(value)))

    def _update_form(self):
        row = self._selected_row()
        if row is None:
            self.selected_label.config(text="请先在左边选中一名学生")
        else:
            # 加减分按累计余额记账；正在看「今天」时另外标出本期分值，
            # 免得老师把列表里的今日积分误当成学生实际拥有的总积分。
            real = int(row.get("real_balance", row["balance"]))
            text = "%s · %s     当前 %d 分" % (
                row["stu_name"], row["class_label"], real)
            if real != row["balance"]:
                text += "（本期 %+d）" % row["balance"]
            self.selected_label.config(text=text, fg=COLORS["text"])
        self._update_preview()
        if self.var_only_selected.get():
            self._reload_records()

    def _show_feedback(self):
        """没在输入分值时，这一行用来显示上一次操作结果。

        提交后选人事件是异步到达的，会再调一次 _update_preview；把反馈放在
        这里才不会被那次回调擦掉。用户一开始输新分值就自动让位给预览。
        """
        self.preview_label.config(
            text=self._last_msg,
            fg=COLORS["success"] if self._last_msg else COLORS["primary"])

    def _update_preview(self):
        if self.preview_label is None:
            return
        row = self._selected_row()
        raw = self.var_delta.get().strip()
        try:
            delta = int(raw)
        except ValueError:
            self._show_feedback()
            return
        if not delta or row is None:
            self._show_feedback()
            return
        self._last_msg = ""
        self.preview_label.config(
            text="本次 %s 分  →  调整后 %d 分" %
                 (_int_text(delta),
                  int(row.get("real_balance", row["balance"])) + delta),
            fg=COLORS["primary"])

    def apply_adjust(self):
        row = self._selected_row()
        if row is None:
            messagebox.showinfo("先选学生", "请先在左边学员列表里点选一名学生。")
            return
        try:
            delta = int(self.var_delta.get().strip())
        except ValueError:
            messagebox.showerror("分值不对", "分值要填整数，正数加分、负数减分。")
            return
        if delta == 0:
            messagebox.showerror("分值不对", "0 分不用记，请填一个非零整数。")
            return
        if abs(delta) > 9999:
            messagebox.showerror("分值太大", "单次调整请控制在 ±9999 分以内。")
            return
        note = self.var_note.get().strip()
        real_balance = int(row.get("real_balance", row["balance"]))
        if real_balance + delta < 0:
            if not messagebox.askyesno(
                    "会扣成负分",
                    "%s 当前 %d 分，本次 %d 分后为 %d 分（负数）。\n\n"
                    "确定要继续吗？" % (row["stu_name"], real_balance,
                                      delta, real_balance + delta)):
                return
        try:
            rec = self.mapper.add_record(
                row["stu_id"], row["stu_name"], row["class_id"], delta, note)
        except Exception as exc:
            logging.exception("积分调整失败")
            messagebox.showerror("保存失败", "写入积分流水失败：%s" % exc)
            return
        self.var_delta.set("")
        self.var_note.set("")
        self._last_msg = "已为 %s %s 分，当前 %d 分" % (
            rec["stu_name"], _int_text(rec["delta"]), rec["balance_after"])
        self.reload(keep_selection=True)
        self.msg_label.config(text=self._last_msg, fg=COLORS["success"])

    # ------------------------------------------------------------ 快捷键
    def open_shortcut_settings(self):
        """页头「快捷键设置」：保存后立刻重绑按键并刷新右侧提示。"""
        from ui_points_shortcuts import open_shortcut_settings as open_dialog
        return open_dialog(
            self.app, on_saved=lambda _slots: self.refresh_shortcut_hint())

    def refresh_shortcut_hint(self):
        label = getattr(self, "shortcut_hint", None)
        if label is None:
            return
        manager = getattr(self.app, "shortcuts", None)
        slots = manager.slots if manager is not None else load_slots()
        items = ["%s %s" % (key_display(slot["key"]), _int_text(slot["delta"]))
                 for _index, slot in active_slots(slots)]
        text = ("快捷键：" + " · ".join(items[:6]) if items else
                "未设置快捷键：点页头「快捷键设置」添加")
        label.config(text=text)

    def quick_adjust(self, index):
        """按快捷键给当前选中的学员记一笔：分值、备注都来自自定义槽位。

        和手工调整一样按累计余额记账、扣成负分要确认；区别是不改表单里的
        分值 / 备注，按一次记一笔，写完后列表、统计卡、流水一起刷新。
        """
        manager = getattr(self.app, "shortcuts", None)
        slot = manager.slot(index) if manager is not None else None
        if slot is None:
            return
        row = self._selected_row()
        if row is None:
            self._last_msg = "请先选中一名学生，再按快捷键"
            self.msg_label.config(text=self._last_msg, fg=COLORS["warn"])
            return
        delta = int(slot["delta"])
        note = slot.get("note", "")
        real_balance = int(row.get("real_balance", row["balance"]))
        if real_balance + delta < 0:
            if not messagebox.askyesno(
                    "会扣成负分",
                    "%s 当前 %d 分，快捷键 %d 分后为 %d 分（负数）。\n\n"
                    "确定要继续吗？" % (row["stu_name"], real_balance,
                                      delta, real_balance + delta)):
                return
        try:
            rec = self.mapper.add_record(
                row["stu_id"], row["stu_name"], row["class_id"], delta, note)
        except Exception as exc:
            logging.exception("快捷积分失败")
            messagebox.showerror("保存失败", "写入积分流水失败：%s" % exc)
            return
        self._last_msg = "已为 %s %s 分，当前 %d 分" % (
            rec["stu_name"], _int_text(rec["delta"]), rec["balance_after"])
        self.reload(keep_selection=True)
        self.msg_label.config(text=self._last_msg, fg=COLORS["success"])

    # ------------------------------------------------------------ 导出
    def export_csv(self):
        if not self.records:
            messagebox.showinfo("没有可导出的记录", "当前筛选条件下没有积分流水。")
            return
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        try:
            from service.dp import data_dir
            out_dir = data_dir()
        except Exception:
            out_dir = os.getcwd()
        path = os.path.join(out_dir, "积分流水_%s.csv" % stamp)
        try:
            with open(path, "w", newline="", encoding="utf-8-sig") as f:
                writer = csv.writer(f)
                writer.writerow(CSV_COLUMNS)
                for r in self.records:
                    writer.writerow((
                        r["create_time"], self._class_label(r["class_id"]),
                        r["stu_name"], _int_text(r["delta"]),
                        r["balance_after"], r["note"] or ""))
        except Exception as exc:
            logging.exception("导出积分流水失败")
            messagebox.showerror("导出失败", "%s" % exc)
            return
        messagebox.showinfo("导出成功", "已导出 %d 条到：\n%s" %
                            (len(self.records), path))
