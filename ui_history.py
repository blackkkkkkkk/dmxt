"""点名记录（历史）：把常规班和假期班的点名历史汇总成一张可查询的表。

两套表的记录在数据层已经合并（mapper/t_history_mapper.py），这里只负责：
选条件 → 查 → 显示 → 改 / 删 → 导出。明细和「按学生汇总」两个视图共用同一批
查询结果，切换视图不重新查库，老师来回看不会等。

改和删只在「明细」视图开放：汇总视图里一行对应好几条记录，没法确定要改哪一条。
"""

import csv
import logging
import os
import sys
import tkinter as tk
from datetime import date, datetime, timedelta
from tkinter import messagebox, ttk

from mapper.t_history_mapper import SOURCE_LABELS, SignHistoryMapper
from ui_theme import COLORS, RoundedButton, button_width, font
from ui_widgets import center_on, data_tree, form_entry, form_label

# 打开时默认只看「点名页当前选中的那一天 + 当前班级」，一眼对上刚点完名的学生；
# 想看更长的时间用页头的快捷范围，DEFAULT_DAYS 只给这些快捷按钮用
DEFAULT_DAYS = 30
# 与数据层保持一致，页面上要按这个数提示「已截断」
MAX_ROWS = 5000

ALL_SOURCES = "全部来源"
ALL_CLASSES = "全部班级"
VIEW_DETAIL = "detail"
VIEW_SUMMARY = "summary"

# 时间范围：和积分流水的「积分范围」同一套口径，切下拉就自动填起止日期
RANGE_TODAY = "今天"
RANGE_7 = "近 7 天"
RANGE_30 = "近 30 天"
RANGE_90 = "近 90 天"
RANGE_ALL = "全部"
RANGE_CUSTOM = "自定义"
RANGE_CHOICES = (RANGE_TODAY, RANGE_7, RANGE_30, RANGE_90,
                 RANGE_ALL, RANGE_CUSTOM)

SOURCE_CHOICES = (
    (ALL_SOURCES, None),
    ("常规班", "regular"),
    ("暑假班", "summer"),
    ("寒假班", "winter"),
)
SOURCE_KEY_BY_LABEL = {label: key for label, key in SOURCE_CHOICES}


# ---------------------------------------------------------------- 纯函数
def app_dir():
    """程序所在目录：源码运行取项目根，打包后取 exe 所在目录。"""
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def date_text(value):
    return value.strftime("%Y-%m-%d")


def default_start_text(days=DEFAULT_DAYS, today=None):
    today = today or date.today()
    return date_text(today - timedelta(days=days - 1))


def class_option_label(key, name):
    """班级下拉的显示名：编号 + 名称，同名班级也能分清。

    库里有些班级的 class_name 本身就写成「00_周五 C++」这种带编号的，
    这种情况不再重复加编号，避免出现「00  00_周五 C++」。
    """
    key = str(key or "").strip()
    name = str(name or "").strip()
    if not name:
        return key
    if not key or name == key:
        return name
    if name.startswith((key, key + "_", key + " ", key + "-", key + "·")):
        return name
    return "%s  %s" % (key, name)


def parse_date(text, label="日期"):
    """把老师手输的日期统一成 YYYY-MM-DD；空串返回 None，写错抛 ValueError。"""
    text = (text or "").strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d", "%Y%m%d"):
        try:
            return datetime.strptime(text, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    raise ValueError("%s「%s」不是有效日期，请写成 2026-11-13 这样的格式。" % (label, text))


def short_sign_time(text):
    """点名时间只留时分秒：2026年11月12日 22:29:37 → 22:29:37。"""
    text = (text or "").strip()
    if not text:
        return ""
    if " " in text:
        tail = text.split(" ")[-1].strip()
        if ":" in tail:
            return tail
    return text


def parse_hour(text, allow_empty=True):
    """课时输入归一：'2' → '2'、'2.5' → '2.5'、空 → ''；非法值抛 ValueError。"""
    text = (text or "").strip()
    if not text:
        if allow_empty:
            return ""
        raise ValueError("课时不能为空。")
    try:
        number = float(text)
    except ValueError:
        raise ValueError("课时要填数字，例如 2 或 2.5。")
    if number < 0:
        raise ValueError("课时不能是负数。")
    if number > 99:
        raise ValueError("课时太大了（最多填到 99）。")
    return str(int(number)) if number == int(number) else ("%g" % number)


def summarize(rows):
    """总数 / 已签到 / 未签到 / 出勤率（百分比，保留一位小数）。"""
    total = len(rows)
    signed = sum(1 for r in rows if r["signed"])
    absent = total - signed
    rate = (signed * 100.0 / total) if total else 0.0
    return {"total": total, "signed": signed, "absent": absent, "rate": rate}


def group_by_student(rows):
    """按「班级 + 学生」汇总出勤；缺勤多的排前面，一眼看到需要关注的学员。"""
    buckets = {}
    for row in rows:
        key = (row["class_name"], row["student"])
        item = buckets.get(key)
        if item is None:
            item = buckets[key] = {"class_name": row["class_name"],
                                   "student": row["student"],
                                   "signed": 0, "absent": 0, "total": 0}
        item["total"] += 1
        if row["signed"]:
            item["signed"] += 1
        else:
            item["absent"] += 1
    out = list(buckets.values())
    for item in out:
        item["rate"] = (item["signed"] * 100.0 / item["total"]) if item["total"] else 0.0
    out.sort(key=lambda b: (-b["absent"], b["class_name"], b["student"]))
    return out


def detail_values(row):
    """明细视图的一行。"""
    return (row["date"], row["weekday"], row["class_name"], row["student"],
            row["hour"], row["state"], short_sign_time(row["sign_time"]),
            row["source_label"])


def summary_values(item):
    """汇总视图的一行。"""
    return (item["student"], item["class_name"], item["signed"], item["absent"],
            item["total"], "%.0f%%" % item["rate"])


# ---------------------------------------------------------------- 页面
class HistoryPage:
    """点名记录页：筛选 + 明细/汇总 + 导出 CSV。"""

    DETAIL_COLUMNS = (
        ("日期", 132, "center", False),
        ("星期", 60, "center", False),
        ("班级", 250, "w", True),
        ("学生", 100, "w", False),
        ("课时", 64, "center", False),
        ("状态", 84, "center", False),
        ("点名时间", 96, "center", False),
        ("来源", 84, "center", False),
    )
    SUMMARY_COLUMNS = (
        ("学生", 110, "w", False),
        ("班级", 300, "w", True),
        ("出勤", 70, "center", False),
        ("缺勤", 70, "center", False),
        ("合计", 70, "center", False),
        ("出勤率", 90, "center", False),
    )

    def __init__(self, app):
        self.app = app
        self.mapper = SignHistoryMapper()
        self.rows = []
        self.view = VIEW_DETAIL
        self._class_map = {}      # 显示名 → (source, class_key)
        self._class_options = []  # [(显示名, (source, class_key))]，多选弹窗用
        self._options = {}        # source → [(class_key, 班级名)]
        self._row_by_iid = {}     # 表格行 iid → 记录字典（改 / 删靠它定位）
        self._menu = None         # 右键菜单（每次重建表格时一起重建）
        self._class_default_applied = False
        # 班级多选：空列表=全部班级；元素是 (source, class_key)
        self.selected_classes = []
        self._setting_range = False   # 批量写日期框时屏蔽「切自定义」的 trace

        # 默认日期 = 点名页当前选中的那一天：正常点名是今天，补点名时自动
        # 跟到那一天，刚保存的记录一打开就能看到（很早的日期也不会被挡住）。
        day = self._default_day()
        self.var_start = tk.StringVar(value=day)
        self.var_end = tk.StringVar(value=day)
        self.var_range = tk.StringVar(
            value=RANGE_TODAY if day == date_text(date.today()) else RANGE_CUSTOM)
        self.var_source = tk.StringVar(value=ALL_SOURCES)
        # 摘要文字：全部班级 / 单个班名 / 已选 N 个班
        self.var_class = tk.StringVar(value=ALL_CLASSES)
        self.var_keyword = tk.StringVar()
        self.var_absent = tk.BooleanVar(value=False)
        self.var_signed = tk.BooleanVar(value=False)

        self.status_label = None
        self.hint = None
        self.tree = None
        self.class_btn = None
        self.class_summary = None
        self._dropdown = None     # 当前打开的班级下拉面板

        self._build()
        self._refresh_class_choices()   # 加载班级选项 + 默认选中当前班级
        self.reload()

    # ------------------------------------------------------------ 默认条件
    def _default_day(self):
        """默认看哪天：点名页当前选中的日期；没设或写坏了就用今天。"""
        value = getattr(self.app, "sign_date", "") or ""
        if len(value) == 10 and value[4] == "-" and value[7] == "-":
            try:
                return parse_date(value)
            except ValueError:
                pass
        return date_text(date.today())

    def _default_class_label(self):
        """当前班级在下拉里对应的显示名；优先常规班，找不到返回 None。"""
        cid = str(getattr(self.app, "current_class", "") or "")
        if not cid:
            return None
        pairs = list(self._class_map.items())
        for label, (src, key) in pairs:
            if src == "regular" and str(key) == cid:
                return label
        for label, (_src, key) in pairs:
            if str(key) == cid:
                return label
        return None

    # ------------------------------------------------------------ 构建
    def _build(self):
        app = self.app
        app.set_active_nav("history")
        page = app.bg.new_page(padx=30, pady=24)

        page.header(
            "点名记录",
            "常规班与暑假/寒假班的点名历史都在这里；双击一行可修改，右键或 Delete 键可删除",
            links=[
                ("刷新", self.reload),
                ("今天", lambda: self.apply_range(RANGE_TODAY)),
                ("全部", lambda: self.apply_range(RANGE_ALL)),
                ("近 90 天", lambda: self.apply_range(RANGE_90)),
                ("近 30 天", lambda: self.apply_range(RANGE_30)),
                ("近 7 天", lambda: self.apply_range(RANGE_7)),
            ],
        )

        c1, self.total_label = app._stat_card("记录条数", COLORS["primary"])
        c2, self.signed_label = app._stat_card("已签到", COLORS["success"])
        c3, self.absent_label = app._stat_card("未签到", COLORS["warn"])
        c4, self.rate_label = app._stat_card("出勤率", COLORS["primary_dark"])
        page.stat_row([c1, c2, c3, c4])

        self._build_filters(page)
        self._build_table(page)
        page.layout()

    def _build_filters(self, page):
        card = self.app._card()
        body = tk.Frame(card, bg=COLORS["card"])
        body.pack(fill=tk.X, padx=16, pady=(10, 12))

        # 第一行：时间范围 / 日期区间 / 来源 / 班级（多选） / 学生
        line1 = tk.Frame(body, bg=COLORS["card"])
        line1.pack(fill=tk.X)

        self._label(line1, "时间范围").pack(side=tk.LEFT, padx=(0, 8))
        self.range_box = ttk.Combobox(
            line1, textvariable=self.var_range, state="readonly",
            style="Class.TCombobox", width=9, values=list(RANGE_CHOICES))
        self.range_box.pack(side=tk.LEFT)
        self.range_box.bind("<<ComboboxSelected>>",
                            lambda _e: self.apply_range(self.var_range.get()))

        self._label(line1, "日期").pack(side=tk.LEFT, padx=(16, 8))
        form_entry(line1, textvariable=self.var_start, width=10).pack(
            side=tk.LEFT, ipady=4)
        self._label(line1, "至").pack(side=tk.LEFT, padx=6)
        form_entry(line1, textvariable=self.var_end, width=10).pack(
            side=tk.LEFT, ipady=4)
        # 手改日期只把下拉切成「自定义」，真正查库还是点查询/回车，避免边输边查
        self.var_start.trace_add("write", lambda *_a: self._on_date_changed())
        self.var_end.trace_add("write", lambda *_a: self._on_date_changed())

        self._label(line1, "来源").pack(side=tk.LEFT, padx=(16, 8))
        self.source_box = ttk.Combobox(
            line1, textvariable=self.var_source, state="readonly",
            style="Class.TCombobox", width=8,
            values=[label for label, _ in SOURCE_CHOICES])
        self.source_box.pack(side=tk.LEFT)
        self.source_box.bind("<<ComboboxSelected>>", self.on_source_change)

        self._label(line1, "班级").pack(side=tk.LEFT, padx=(16, 8))
        self.class_btn = RoundedButton(
            line1, text="选择班级…", command=self.open_class_picker,
            width=button_width("选择班级…", font(9), 92), height=30, radius=8,
            bg=COLORS["card"], fg=COLORS["primary"],
            hover_bg=COLORS["primary_soft"], border=COLORS["border"],
            parent_bg=COLORS["card"], font_obj=font(9))
        self.class_btn.pack(side=tk.LEFT)
        self.class_summary = tk.Label(
            line1, text=ALL_CLASSES, bg=COLORS["card"], fg=COLORS["text"],
            font=font(9), anchor="w")
        self.class_summary.pack(side=tk.LEFT, padx=(8, 0))

        # 第二行：学生搜索 + 视图切换 + 只看未签到 + 三个动作
        line2 = tk.Frame(body, bg=COLORS["card"])
        line2.pack(fill=tk.X, pady=(10, 0))

        self._label(line2, "学生").pack(side=tk.LEFT, padx=(0, 8))
        entry = form_entry(line2, textvariable=self.var_keyword, width=10)
        entry.pack(side=tk.LEFT, ipady=4)
        entry.bind("<Return>", lambda _e: self.reload())

        self.btn_detail = RoundedButton(
            line2, text="明细", command=lambda: self.switch_view(VIEW_DETAIL),
            width=button_width("明细", font(9), 68), height=32, radius=8,
            bg=COLORS["card"], fg="#334155", hover_bg="#F1F5F9",
            border=COLORS["border"], parent_bg=COLORS["card"], font_obj=font(9))
        self.btn_detail.pack(side=tk.LEFT, padx=(18, 0))
        self.btn_summary = RoundedButton(
            line2, text="按学生汇总", command=lambda: self.switch_view(VIEW_SUMMARY),
            width=button_width("按学生汇总", font(9), 96), height=32, radius=8,
            bg=COLORS["card"], fg="#334155", hover_bg="#F1F5F9",
            border=COLORS["border"], parent_bg=COLORS["card"], font_obj=font(9))
        self.btn_summary.pack(side=tk.LEFT, padx=(8, 0))
        self.btn_detail.set_selected(True)

        # 「只看签到 / 只看未签到」做成开关按钮：勾选框在小字号下描边容易看不清。
        # 两个开关互斥，按下其中一个另一个自动弹起。
        self.btn_signed = RoundedButton(
            line2, text="只看签到", command=self.toggle_signed,
            width=button_width("只看签到", font(9), 84), height=32, radius=8,
            bg=COLORS["card"], fg="#334155", hover_bg="#F1F5F9",
            border=COLORS["border"], parent_bg=COLORS["card"], font_obj=font(9))
        self.btn_signed.pack(side=tk.LEFT, padx=(12, 0))

        self.btn_absent = RoundedButton(
            line2, text="只看未签到", command=self.toggle_absent,
            width=button_width("只看未签到", font(9), 96), height=32, radius=8,
            bg=COLORS["card"], fg="#334155", hover_bg="#F1F5F9",
            border=COLORS["border"], parent_bg=COLORS["card"], font_obj=font(9))
        self.btn_absent.pack(side=tk.LEFT, padx=(8, 0))

        # 右侧从右往左：查询 → 重置 → 导出
        RoundedButton(
            line2, text="查询", command=self.reload,
            width=button_width("查询", font(10), 76), height=34, radius=8,
            parent_bg=COLORS["card"], font_obj=font(10)).pack(side=tk.RIGHT)
        RoundedButton(
            line2, text="重置", command=self.reset_filters,
            width=button_width("重置", font(10), 76), height=34, radius=8,
            bg=COLORS["card"], fg="#334155", hover_bg="#F8FAFC",
            border=COLORS["border"], parent_bg=COLORS["card"],
            font_obj=font(10)).pack(side=tk.RIGHT, padx=(0, 8))
        RoundedButton(
            line2, text="导出 CSV", command=self.export_csv,
            width=button_width("导出 CSV", font(10), 92), height=34, radius=8,
            bg=COLORS["card"], fg="#334155", hover_bg="#F8FAFC",
            border=COLORS["border"], parent_bg=COLORS["card"],
            font_obj=font(10)).pack(side=tk.RIGHT, padx=(0, 8))

        self.status_label = tk.Label(
            line2, text="", bg=COLORS["card"], fg=COLORS["subtle"], font=font(9))
        self.status_label.pack(side=tk.RIGHT, padx=(0, 14))

        page.block(card, gap=14)

    def _build_table(self, page):
        self.table_card = self.app._card()
        self._create_tree()
        page.block(self.table_card, gap=12, expand=True)

    def _create_tree(self):
        for child in self.table_card.winfo_children():
            child.destroy()
        self._row_by_iid = {}
        self._menu = None
        self._build_table_bar()

        detail = self.view == VIEW_DETAIL
        columns = self.DETAIL_COLUMNS if detail else self.SUMMARY_COLUMNS
        # 明细允许多选：误录入的整批记录可以一次删掉
        self.tree = data_tree(self.table_card, columns, height=14,
                              selectmode="extended" if detail else "browse")
        self.tree.tag_configure("signed", background=COLORS["success_soft"],
                                foreground="#15803D")
        self.tree.tag_configure("absent", background=COLORS["danger_soft"],
                                foreground="#B91C1C")
        self.tree.tag_configure("warn", background=COLORS["warn_soft"],
                                foreground="#B45309")
        self.hint = tk.Label(self.table_card, text="", bg=COLORS["card"],
                             fg=COLORS["muted"], font=font(11), justify="center")
        if detail:
            self._bind_row_actions()

    def _build_table_bar(self):
        """表格上方的操作条：左边写操作提示，右边「修改 / 删除」。"""
        bar = tk.Frame(self.table_card, bg=COLORS["card"])
        bar.pack(fill=tk.X, pady=(0, 8))
        detail = self.view == VIEW_DETAIL
        tip = ("双击一行即可修改；右键或 Delete 删除，按住 Ctrl 可多选批量删除"
               if detail else "汇总视图用来看数据；要修改或删除请先切回「明细」")
        tk.Label(bar, text=tip, bg=COLORS["card"], fg=COLORS["subtle"],
                 font=font(9)).pack(side=tk.LEFT)
        if not detail:
            return
        RoundedButton(
            bar, text="删除记录", command=self.delete_selected,
            width=button_width("删除记录", font(9), 78), height=30, radius=8,
            bg=COLORS["card"], fg=COLORS["danger"],
            hover_bg=COLORS["danger_soft"], border="#FCA5A5",
            parent_bg=COLORS["card"], font_obj=font(9)).pack(side=tk.RIGHT)
        RoundedButton(
            bar, text="修改记录", command=self.edit_selected,
            width=button_width("修改记录", font(9), 78), height=30, radius=8,
            bg=COLORS["card"], fg="#334155", hover_bg="#F8FAFC",
            border=COLORS["border"], parent_bg=COLORS["card"],
            font_obj=font(9)).pack(side=tk.RIGHT, padx=(0, 8))

    def _bind_row_actions(self):
        """右键菜单 + 双击修改 + Delete 删除，三个入口都指向同一对方法。"""
        menu = tk.Menu(self.tree, tearoff=0, font=font(10), bd=1,
                       bg=COLORS["card"], fg=COLORS["text"],
                       activebackground=COLORS["primary_soft"],
                       activeforeground=COLORS["text"])
        menu.add_command(label="修改这条记录", command=self.edit_selected)
        menu.add_command(label="删除这条记录", command=self.delete_selected)
        self._menu = menu
        self.tree.bind("<Button-3>", self._on_right_click)
        self.tree.bind("<Double-1>", self._on_double_click)
        self.tree.bind("<Delete>", lambda _e: self.delete_selected())

    @staticmethod
    def _label(parent, text):
        return tk.Label(parent, text=text, bg=COLORS["card"],
                        fg=COLORS["muted"], font=font(9))

    # ------------------------------------------------------------ 筛选交互
    def _source_key(self):
        return SOURCE_KEY_BY_LABEL.get(self.var_source.get())

    def _load_options(self):
        try:
            self._options = self.mapper.class_options()
        except Exception:
            logging.exception("读取班级下拉失败")
            self._options = {}

    def _refresh_class_choices(self):
        """加载班级选项，并保持多选状态；来源变化时剔除不属于该来源的班。"""
        if not self._options:
            self._load_options()
        source = self._source_key()
        sources = [source] if source else list(SOURCE_LABELS.keys())
        self._class_map = {}
        options = []          # [(显示名, (source, class_key))]
        for src in sources:
            for key, name in self._options.get(src, []):
                base = class_option_label(key, name)
                label = base if source else "%s · %s" % (
                    SOURCE_LABELS.get(src, src), base)
                if label in self._class_map:
                    label = "%s #%s" % (label, key)
                pair = (src, str(key))
                self._class_map[label] = pair
                options.append((label, pair))
        self._class_options = options
        allowed = {pair for _label, pair in options}
        self.selected_classes = [pair for pair in self.selected_classes
                                 if (pair[0], str(pair[1])) in allowed]
        # 第一次打开时把「点名页当前班级」勾上：老师刚点完哪个班，记录页
        # 就默认看哪个班。只在首次生效，之后不再抢用户自己选的班级。
        if not self._class_default_applied:
            self._class_default_applied = True
            cid = str(getattr(self.app, "current_class", "") or "")
            picked = None
            if cid:
                picked = next((pair for _label, pair in options
                               if pair[0] == "regular" and pair[1] == cid), None)
                if picked is None:
                    picked = next((pair for _label, pair in options
                                   if pair[1] == cid), None)
            if picked:
                self.selected_classes = [picked]
        self._update_class_summary()

    def _label_for_pair(self, pair):
        src, key = pair[0], str(pair[1])
        for label, value in self._class_map.items():
            if value == (src, key):
                return label
        return key

    def _update_class_summary(self):
        """按钮旁边的摘要：全部班级 / 某个班名 / 已选 N 个班。"""
        pairs = list(self.selected_classes)
        if not pairs:
            text = ALL_CLASSES
        elif len(pairs) == 1:
            text = self._label_for_pair(pairs[0])
        else:
            text = "已选 %d 个班" % len(pairs)
        self.var_class.set(text)
        if self.class_summary is not None:
            try:
                self.class_summary.config(text=text)
            except tk.TclError:
                pass

    def open_class_picker(self):
        """点「选择班级…」：面板没开就展开，已经开着就收起（toggle）。"""
        dropdown = getattr(self, "_dropdown", None)
        if dropdown is not None and not getattr(dropdown, "_closed", True):
            dropdown.close(apply=True)
            return None
        from ui_history_picker import open_class_picker
        self._dropdown = open_class_picker(
            self.app, list(getattr(self, "_class_options", [])),
            list(self.selected_classes), self._apply_classes,
            anchor=self.class_btn)
        return self._dropdown

    def _apply_classes(self, pairs):
        self.selected_classes = [(src, str(key)) for src, key in pairs]
        self._update_class_summary()
        self.reload()

    def on_source_change(self, _event=None):
        self._refresh_class_choices()
        self.reload()

    def on_class_change(self, _event=None):
        self.reload()

    def toggle_absent(self):
        """「只看未签到」开关：按下去就是只看没来的；与「只看签到」互斥。"""
        want = not self.var_absent.get()
        self.var_absent.set(want)
        if want:
            self.var_signed.set(False)
            self.btn_signed.set_selected(False)
        self.btn_absent.set_selected(want)
        self.reload()

    def toggle_signed(self):
        """「只看签到」开关：按下去只看来了的；与「只看未签到」互斥。"""
        want = not self.var_signed.get()
        self.var_signed.set(want)
        if want:
            self.var_absent.set(False)
            self.btn_absent.set_selected(False)
        self.btn_signed.set_selected(want)
        self.reload()

    def apply_range(self, label):
        """切时间范围：自动填起止日期并查询；手改日期框会切成「自定义」。"""
        if label not in RANGE_CHOICES:
            label = RANGE_TODAY
        if label == RANGE_CUSTOM:
            self.var_range.set(label)
            self.reload()
            return
        today = date.today()
        if label == RANGE_ALL:
            start = end = ""
        elif label == RANGE_TODAY:
            start = end = date_text(today)
        else:
            days = {RANGE_7: 7, RANGE_30: 30, RANGE_90: 90}[label]
            start, end = default_start_text(days, today), date_text(today)
        # 批量写日期框时屏蔽 trace，避免刚写一个就被改成「自定义」
        self._setting_range = True
        self.var_range.set(label)
        self.var_start.set(start)
        self.var_end.set(end)
        self._setting_range = False
        self.reload()

    def _on_date_changed(self):
        """日期框被手改：只切下拉口径，真正查询仍由「查询」/回车触发。"""
        if self._setting_range:
            return
        if self.var_range.get() != RANGE_CUSTOM:
            self.var_range.set(RANGE_CUSTOM)

    # 旧接口保留：老测试和老代码还在用 quick_range(days)
    def quick_range(self, days):
        if days is None:
            self.apply_range(RANGE_ALL)
            return
        label = {1: RANGE_TODAY, 7: RANGE_7, 30: RANGE_30,
                 90: RANGE_90}.get(int(days), RANGE_CUSTOM)
        self.apply_range(label)

    def reset_filters(self):
        """重置回默认视图：点名日 + 当前班级（单班）。"""
        day = self._default_day()
        self._setting_range = True
        self.var_start.set(day)
        self.var_end.set(day)
        self.var_range.set(
            RANGE_TODAY if day == date_text(date.today()) else RANGE_CUSTOM)
        self._setting_range = False
        self.var_source.set(ALL_SOURCES)
        self.var_keyword.set("")
        self.var_absent.set(False)
        self.var_signed.set(False)
        self.btn_absent.set_selected(False)
        self.btn_signed.set_selected(False)
        # 班级也回到默认的「点名前那个班」
        self.selected_classes = []
        self._class_default_applied = False
        self._refresh_class_choices()
        self.reload()

    # ------------------------------------------------------------ 查询
    def reload(self):
        try:
            start = parse_date(self.var_start.get(), "开始日期")
            end = parse_date(self.var_end.get(), "结束日期")
        except ValueError as exc:
            messagebox.showwarning("日期格式不对", str(exc))
            return False

        if start and end and start > end:
            messagebox.showwarning("日期区间不对", "开始日期不能晚于结束日期。")
            return False

        source = self._source_key()
        # 班级多选：空列表=全部班级；选了具体班就只查这几个班
        class_keys = [(src, str(key)) for src, key in self.selected_classes]
        if source:
            class_keys = [pair for pair in class_keys if pair[0] == source]

        try:
            self.rows = self.mapper.query(
                start=start, end=end, source=source,
                class_keys=class_keys or None,
                keyword=self.var_keyword.get(), only_absent=self.var_absent.get(),
                only_signed=self.var_signed.get(), limit=MAX_ROWS)
        except Exception as exc:
            logging.exception("查询点名记录失败")
            self.rows = []
            messagebox.showerror("查询失败", "读取点名记录失败：%s" % exc)
            self._render()
            return False

        self._render()
        return True

    # ------------------------------------------------------------ 渲染
    def switch_view(self, view):
        if view == self.view:
            return
        self.view = view
        self.btn_detail.set_selected(view == VIEW_DETAIL)
        self.btn_summary.set_selected(view == VIEW_SUMMARY)
        # 表格卡片尺寸由画布 item 固定，换列不需要重新排版，直接重画数据
        self._create_tree()
        self._render()

    def _render(self):
        stats = summarize(self.rows)
        self._set_text(self.total_label, str(stats["total"]))
        self._set_text(self.signed_label, str(stats["signed"]))
        self._set_text(self.absent_label, str(stats["absent"]))
        self._set_text(self.rate_label,
                       "%.1f%%" % stats["rate"] if stats["total"] else "—")
        if self.status_label is not None:
            text = "共 %d 条" % stats["total"]
            if stats["total"] >= MAX_ROWS:
                text += "（已达上限 %d 条，请缩小日期范围）" % MAX_ROWS
            try:
                self.status_label.config(text=text)
            except tk.TclError:
                pass
        self._fill_tree()

    @staticmethod
    def _set_text(label, text):
        try:
            label.config(text=text)
        except (tk.TclError, AttributeError):
            pass

    def _detail_tags(self, idx, row):
        if row["signed"]:
            return ("signed",)
        return ("even",) if idx % 2 else ("odd",)

    def _fill_tree(self):
        tree = self.tree
        for item in tree.get_children():
            tree.delete(item)
        self._row_by_iid = {}

        if self.view == VIEW_DETAIL:
            # iid 与记录一一对应，双击 / 右键时不用再按行号反查
            for idx, row in enumerate(self.rows):
                iid = "r%d" % idx
                tree.insert("", tk.END, iid=iid, values=detail_values(row),
                            tags=self._detail_tags(idx, row))
                self._row_by_iid[iid] = row
        else:
            for idx, item in enumerate(group_by_student(self.rows)):
                tags = ("warn",) if item["absent"] else (
                    ("even",) if idx % 2 else ("odd",))
                tree.insert("", tk.END, values=summary_values(item), tags=tags)

        if not self.rows:
            self._show_hint("没有符合条件的记录\n换个日期范围，或点「重置」看全部")
        else:
            self._show_hint("")

    def _show_hint(self, text):
        if self.hint is None:
            return
        try:
            if text:
                self.hint.configure(text=text)
                self.hint.place(relx=0.5, rely=0.5, anchor="center")
            else:
                self.hint.place_forget()
        except tk.TclError:
            pass

    # ------------------------------------------------------------ 修改 / 删除
    def _selected_rows(self):
        """当前选中的明细行（同一个 rec_id 只算一次）；汇总视图返回空。"""
        if self.view != VIEW_DETAIL or self.tree is None:
            return []
        rows, seen = [], set()
        try:
            selection = self.tree.selection()
        except tk.TclError:
            return []
        for iid in selection:
            row = self._row_by_iid.get(iid)
            if row is None:
                continue
            key = (row.get("source"), row.get("rec_id"))
            if key in seen:
                continue
            seen.add(key)
            rows.append(row)
        return rows

    def _on_right_click(self, event):
        """右键：先把光标落到点中的那一行，再弹菜单（不打扰已多选的行）。"""
        row_id = self.tree.identify_row(event.y)
        if not row_id:
            return
        if row_id not in self.tree.selection():
            self.tree.selection_set(row_id)
        self.tree.focus(row_id)
        if self._menu is None:
            return
        try:
            self._menu.tk_popup(event.x_root, event.y_root)
        finally:
            try:
                self._menu.grab_release()
            except tk.TclError:
                pass

    def _on_double_click(self, event):
        """双击单元格 = 修改这一条；双击表头 / 空白处不响应。"""
        if self.tree.identify("region", event.x, event.y) != "cell":
            return
        row_id = self.tree.identify_row(event.y)
        if not row_id:
            return
        self.tree.selection_set(row_id)
        self.tree.focus(row_id)
        self.edit_selected()

    def edit_selected(self):
        rows = self._selected_rows()
        if not rows:
            messagebox.showinfo(
                "先选一条记录",
                "在明细里点一行，再点「修改记录」，或者直接双击这一行。")
            return
        if len(rows) > 1:
            messagebox.showinfo(
                "一次只能改一条",
                "修改只支持单条，请只选中一行。（删除可以多选批量做）")
            return
        RecordEditDialog(self, rows[0])

    def delete_selected(self):
        rows = self._selected_rows()
        if not rows:
            messagebox.showinfo(
                "先选记录",
                "在明细里选中要删除的记录（按住 Ctrl 可以多选），\n"
                "再点「删除记录」或按 Delete 键。")
            return
        lines = []
        for row in rows[:8]:
            lines.append("· %s  %s  %s（%s）" % (
                row["date"], row["class_name"], row["student"], row["state"]))
        if len(rows) > 8:
            lines.append("· …… 另有 %d 条" % (len(rows) - 8))
        if not messagebox.askyesno(
                "删除确认",
                "确定删除下面 %d 条点名记录吗？\n\n%s\n\n"
                "删除后不能撤销（想找回可用「备份与恢复」里的旧备份）；\n"
                "如果只是想改签到状态，用「修改记录」更合适。"
                % (len(rows), "\n".join(lines))):
            return

        try:
            removed = self.mapper.delete_records(
                [(row.get("source"), row.get("rec_id")) for row in rows])
        except Exception as exc:
            logging.exception("删除点名记录失败")
            messagebox.showerror("删除失败", "删除记录时出错：%s" % exc)
            self.reload()
            return

        touched = self._today_classes(rows)
        self.reload()
        self._notify_app(touched)
        if removed < len(rows):
            messagebox.showwarning(
                "部分记录已不存在",
                "实际删除 %d 条，另有 %d 条在库里已找不到（可能被别处删过）。"
                % (removed, len(rows) - removed))
        else:
            messagebox.showinfo("删除成功", "已删除 %d 条点名记录。" % removed)

    def apply_edit(self, row, signed, hour, note):
        """把一条修改写回库；成功返回 (True, "")，失败返回 (False, 原因)。"""
        if not row.get("rec_id"):
            return False, "这条记录没有编号（可能是很早的老数据），无法修改。"
        try:
            affected = self.mapper.update_record(
                row.get("source"), row["rec_id"], signed, hour, note)
        except Exception as exc:
            logging.exception("修改点名记录失败")
            return False, "写入数据库失败：%s" % exc
        if not affected:
            return False, "这条记录已经不在了（可能被删掉过），请刷新后重试。"

        # 改完回到同一行：连着改几条不用重新找
        target = dict(row)
        target["signed"] = bool(signed)
        target["state"] = "已签到" if signed else "未签到"
        self.reload()
        self._notify_app(self._today_classes([row]))
        self._select_rec(target)
        return True, ""

    def _select_rec(self, target):
        if self.view != VIEW_DETAIL or self.tree is None:
            return
        for iid, row in self._row_by_iid.items():
            if (row.get("source") == target.get("source")
                    and row.get("rec_id") == target.get("rec_id")):
                try:
                    self.tree.selection_set(iid)
                    self.tree.focus(iid)
                    self.tree.see(iid)
                except tk.TclError:
                    pass
                return

    def _today_classes(self, rows):
        """被改动、且属于「今天 / 点名页当前日期」的常规班：内存缓存要一起作废。

        点名页可以选往日补点名，改那一天的签到记录时也要同步，不能只认今天。
        """
        days = {date_text(date.today())}
        selected = getattr(getattr(self, "app", None), "sign_date", "") or ""
        if selected:
            days.add(str(selected))
        out = []
        for row in rows:
            if (row.get("source") != "regular"
                    or str(row.get("date") or "") not in days):
                continue
            key = row.get("class_key") or ""
            if key and key not in out:
                out.append(key)
        return out

    def _notify_app(self, today_classes=()):
        """记录被改/删后，让主界面立刻按数据库重算点名状态。

        点名页的勾选是内存字典（sign_in_status），光刷「今日签到」缓存不够：
        只清缓存的话，删掉的记录在点名页依旧显示「已签到」，连点「刷新数据」
        都盖不掉（刷新前又把界面状态存回了内存）。统一交给主界面重算。
        """
        app = self.app
        resync = getattr(app, "resync_sign_status", None)
        if callable(resync):
            try:
                resync(today_classes)
            except Exception:
                logging.exception("改动记录后同步点名页状态失败")
            return
        # 兜底：老版本主界面没有 resync 时，至少把今日签到缓存刷掉
        try:
            app.today_sign = {}
            app.today_sign_loaded = False
            app.load_today_sign(force=True)
            app.sign_in_status = app.init_sign_in_status()
        except Exception:
            logging.exception("改动记录后刷新今日签到缓存失败")
        for cid in today_classes:
            for name in ("sign_in_status_by_class", "hour_overrides"):
                bucket = getattr(app, name, None)
                if isinstance(bucket, dict):
                    bucket.pop(cid, None)

    # ------------------------------------------------------------ 导出
    def export_csv(self):
        if not self.rows:
            messagebox.showinfo("没有记录", "当前条件下没有点名记录，无需导出。")
            return
        try:
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            if self.view == VIEW_SUMMARY:
                header = ["学生", "班级", "出勤次数", "缺勤次数", "合计次数", "出勤率"]
                body = [[i["student"], i["class_name"], i["signed"], i["absent"],
                         i["total"], "%.0f%%" % i["rate"]]
                        for i in group_by_student(self.rows)]
                prefix = "点名汇总"
            else:
                header = ["日期", "星期", "班级", "学生", "课时", "状态",
                          "点名时间", "来源", "上课时间", "备注"]
                body = [[r["date"], r["weekday"], r["class_name"], r["student"],
                         r["hour"], r["state"], short_sign_time(r["sign_time"]),
                         r["source_label"], r["lesson_time"], r["note"]]
                        for r in self.rows]
                prefix = "点名记录"

            # 导出到用户数据目录（安装目录可能只读）
            try:
                from service.dp import data_dir
                out_dir = data_dir()
            except Exception:
                out_dir = app_dir()
            path = os.path.join(out_dir, "%s_%s.csv" % (prefix, stamp))
            # utf-8-sig：Excel 直接双击打开不乱码
            with open(path, "w", encoding="utf-8-sig", newline="") as f:
                writer = csv.writer(f)
                writer.writerow(header)
                writer.writerows(body)
            messagebox.showinfo("导出成功", "已导出 %d 行到：\n%s" % (len(body), path))
        except Exception as exc:
            logging.exception("导出点名记录失败")
            messagebox.showerror("导出失败", "%s" % exc)


# ---------------------------------------------------------------- 修改弹窗
class RecordEditDialog:
    """改一条点名记录：签到状态、课时、备注。

    日期和班级不给改：常规班的记录日期由创建时间决定，改了等于换一天；
    假期班的日期挂在「课次」上，一个班共用一条课次，改一条会连累全班。
    """

    def __init__(self, page, row):
        self.page = page
        self.row = row
        win = self.win = tk.Toplevel(page.app.master)
        win.title("修改点名记录")
        win.configure(bg=COLORS["bg"])
        win.resizable(False, False)
        win.transient(page.app.master)

        root = tk.Frame(win, bg=COLORS["bg"])
        root.pack(fill=tk.BOTH, expand=True, padx=24, pady=(18, 16))

        tk.Label(root, text="修改点名记录", bg=COLORS["bg"], fg=COLORS["text"],
                 font=font(14, True)).pack(anchor="w")
        tk.Label(root, text=self._summary_text(row), bg=COLORS["bg"],
                 fg=COLORS["muted"], font=font(9), justify="left").pack(
                     anchor="w", pady=(4, 14))

        self.var_state = tk.StringVar(
            value="已签到" if row.get("signed") else "未签到")
        self.var_hour = tk.StringVar(value=str(row.get("hour") or ""))
        self.var_note = tk.StringVar(value=str(row.get("note") or ""))

        form = tk.Frame(root, bg=COLORS["bg"])
        form.pack(fill=tk.X)
        form.columnconfigure(1, weight=1)

        form_label(form, "签到状态", 10, color=COLORS["muted"]).grid(
            row=0, column=0, sticky="w", pady=(0, 3))
        form_label(form, "课时", 10, color=COLORS["muted"]).grid(
            row=0, column=1, sticky="w", padx=(16, 0), pady=(0, 3))

        self.state_box = ttk.Combobox(
            form, textvariable=self.var_state, state="readonly",
            style="Class.TCombobox", width=12, values=["已签到", "未签到"])
        self.state_box.grid(row=1, column=0, sticky="w")
        form_entry(form, textvariable=self.var_hour, width=12).grid(
            row=1, column=1, sticky="w", padx=(16, 0), ipady=4)

        form_label(form, "备注", 10, color=COLORS["muted"]).grid(
            row=2, column=0, columnspan=2, sticky="w", pady=(14, 3))
        note_entry = form_entry(form, textvariable=self.var_note, width=40)
        note_entry.grid(row=3, column=0, columnspan=2, sticky="we", ipady=4)
        tk.Label(root, text="备注可留空，最多 200 字（如「家长请假」「补签」）",
                 bg=COLORS["bg"], fg=COLORS["subtle"], font=font(8)).pack(
                     anchor="w", pady=(4, 0))

        foot = tk.Frame(root, bg=COLORS["bg"])
        foot.pack(fill=tk.X, pady=(18, 0))
        RoundedButton(
            foot, text="取消", command=self.close, width=92, height=36,
            radius=10, bg=COLORS["card"], fg="#334155", hover_bg="#F8FAFC",
            border=COLORS["border"], parent_bg=COLORS["bg"],
            font_obj=font(10)).pack(side=tk.RIGHT)
        RoundedButton(
            foot, text="保存修改", command=self.save, width=108, height=36,
            radius=10, parent_bg=COLORS["bg"],
            font_obj=font(10)).pack(side=tk.RIGHT, padx=(0, 10))

        win.bind("<Return>", lambda _e: self.save())
        win.bind("<Escape>", lambda _e: self.close())

        center_on(win, page.app.master)
        try:
            win.grab_set()
        except tk.TclError:
            pass
        self.state_box.focus_set()

    @staticmethod
    def _summary_text(row):
        head = " · ".join(x for x in (row.get("class_name"), row.get("student")) if x)
        when = row.get("date") or ""
        if row.get("weekday"):
            when = ("%s %s" % (when, row["weekday"])).strip()
        tail = row.get("source_label") or ""
        return "%s\n%s · %s" % (head, when, tail)

    def save(self):
        try:
            hour = parse_hour(self.var_hour.get())
        except ValueError as exc:
            messagebox.showwarning("课时不对", str(exc), parent=self.win)
            return
        note = self.var_note.get().strip()
        if len(note) > 200:
            messagebox.showwarning(
                "备注太长", "备注请控制在 200 字以内（现在 %d 字）。" % len(note),
                parent=self.win)
            return

        signed = self.var_state.get() == "已签到"
        ok, msg = self.page.apply_edit(self.row, signed, hour, note)
        if ok:
            self.close()
        else:
            messagebox.showerror("修改失败", msg, parent=self.win)

    def close(self):
        try:
            self.win.destroy()
        except tk.TclError:
            pass
