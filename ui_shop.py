"""积分商城：用积分兑换商品。

页面分两块：
- 上半部三栏：左边选学员（按积分降序，可筛班级/姓名）、中间选商品（含库存、
  上下架与商品管理）、右边填数量/备注并确认兑换；
- 下半部是兑换记录，可只看选中学员、按日期筛、导出 CSV。

兑换会写一笔负积分流水（备注含商品与数量），所以「积分管理」页的余额、
流水和统计卡会同步变化；积分余额仍是 t_point_record 的 SUM(delta)，不另存字段。
"""

import csv
import logging
import os
import tkinter as tk
from datetime import datetime
from tkinter import font as tkfont
from tkinter import messagebox, ttk

from mapper.t_points_mapper import PointsMapper
from mapper.t_shop_mapper import EXCHANGE_LIMIT, MAX_QTY, ShopMapper
from ui_theme import COLORS, RoundedButton, button_width, font
from ui_widgets import (center_on, check_box, data_tree, form_entry,
                        form_label)

ALL_CLASSES = "全部班级"
CSV_COLUMNS = ("时间", "班级", "学员", "商品", "数量", "单价积分",
               "消耗积分", "备注")

STU_COLUMNS = (
    ("姓名", 96, "w", False),
    ("班级", 150, "w", True),
    ("积分", 64, "center", False),
)
GOODS_COLUMNS = (
    ("商品", 150, "w", True),
    ("积分", 64, "center", False),
    ("库存", 64, "center", False),
    ("状态", 64, "center", False),
)
REC_COLUMNS = (
    # 时间/班级都不拉伸，保证完整；剩余宽度只给备注列
    ("时间", 258, "center", False),
    ("班级", 220, "w", False),
    ("学员", 90, "w", False),
    ("商品", 140, "w", False),
    ("数量", 60, "center", False),
    ("消耗", 66, "center", False),
    ("备注", 160, "w", True),
)


def _compact_style(widget):
    """本页专用紧凑表格样式，行高随字体/DPI 自适应，保证一屏多显示几行。"""
    style = ttk.Style(widget)
    try:
        line_px = tkfont.Font(font=font(11)).metrics("linespace")
    except Exception:
        line_px = 20
    row_px = max(32, min(46, int(line_px * 1.15)))
    base = "Data.Treeview"
    style.configure(
        "Shop.Treeview",
        background=style.lookup(base, "background") or COLORS["card"],
        fieldbackground=style.lookup(base, "fieldbackground") or COLORS["card"],
        foreground=style.lookup(base, "foreground") or COLORS["text"],
        borderwidth=0, relief="flat", font=font(11), rowheight=row_px)
    style.map("Shop.Treeview",
              background=[("selected", COLORS["primary_soft"])],
              foreground=[("selected", COLORS["text"])])
    style.layout("Shop.Treeview",
                 [("Shop.Treeview.treearea", {"sticky": "nswe"})])
    heading = base + ".Heading"
    style.configure(
        "Shop.Treeview.Heading",
        background=style.lookup(heading, "background") or COLORS["row_alt"],
        foreground=style.lookup(heading, "foreground") or "#475569",
        relief="flat", borderwidth=0, padding=(8, 5), font=font(10, True))
    style.map("Shop.Treeview.Heading",
              background=[("active", "#EEF2F7")])
    return row_px


def _date_ok(text):
    text = (text or "").strip()
    if not text:
        return True
    try:
        datetime.strptime(text, "%Y-%m-%d")
        return True
    except ValueError:
        return False


class GoodsDialog:
    """新增/编辑一件商品。校验交给 ShopMapper，弹窗只负责收集输入。"""

    def __init__(self, page, goods=None):
        self.page = page
        self.goods = goods
        editing = goods is not None
        win = self.win = tk.Toplevel(page.app.master)
        win.title("编辑商品" if editing else "新增商品")
        win.configure(bg=COLORS["bg"])
        win.resizable(False, False)
        win.transient(page.app.master)

        root = tk.Frame(win, bg=COLORS["bg"])
        root.pack(fill=tk.BOTH, expand=True, padx=24, pady=(18, 16))
        tk.Label(root, text="编辑商品" if editing else "新增商品",
                 bg=COLORS["bg"], fg=COLORS["text"],
                 font=font(14, True)).pack(anchor="w")
        tk.Label(root, text="库存填 -1 表示不限量；所需积分必须是正整数。",
                 bg=COLORS["bg"], fg=COLORS["muted"], font=font(9)).pack(
                     anchor="w", pady=(4, 14))

        self.var_name = tk.StringVar(value=str(goods["goods_name"]) if editing else "")
        self.var_cost = tk.StringVar(value=str(goods["cost"]) if editing else "10")
        self.var_stock = tk.StringVar(value=str(goods["stock"]) if editing else "-1")
        self.var_note = tk.StringVar(value=str(goods["note"] or "") if editing else "")

        form = tk.Frame(root, bg=COLORS["bg"])
        form.pack(fill=tk.X)
        form.columnconfigure(1, weight=1)
        fields = (("商品名称", self.var_name, 32),
                  ("所需积分", self.var_cost, 12),
                  ("库存", self.var_stock, 12),
                  ("说明", self.var_note, 40))
        for row, (title, var, width) in enumerate(fields):
            form_label(form, title, 10, color=COLORS["muted"]).grid(
                row=row * 2, column=0, sticky="w", pady=(0, 3))
            form_entry(form, textvariable=var, width=width).grid(
                row=row * 2 + 1, column=0, sticky="we", ipady=4, pady=(0, 10))

        foot = tk.Frame(root, bg=COLORS["bg"])
        foot.pack(fill=tk.X, pady=(6, 0))
        RoundedButton(
            foot, text="取消", command=self.close,
            width=button_width("取消", font(10), 86), height=36, radius=10,
            bg=COLORS["card"], fg="#334155", hover_bg="#F8FAFC",
            border=COLORS["border"], parent_bg=COLORS["bg"],
            font_obj=font(10)).pack(side=tk.RIGHT)
        RoundedButton(
            foot, text="保存商品", command=self.save,
            width=button_width("保存商品", font(10), 104), height=36, radius=10,
            bg=COLORS["primary"], fg="#FFFFFF", hover_bg=COLORS["primary_dark"],
            border=COLORS["primary"], parent_bg=COLORS["bg"],
            font_obj=font(10)).pack(side=tk.RIGHT, padx=(0, 10))
        win.bind("<Return>", lambda _e: self.save())
        win.bind("<Escape>", lambda _e: self.close())
        center_on(win, page.app.master)

    def save(self):
        try:
            if self.goods is None:
                self.page.mapper.add_goods(
                    self.var_name.get(), self.var_cost.get(),
                    self.var_stock.get(), self.var_note.get())
                msg = "商品已新增"
            else:
                self.page.mapper.update_goods(
                    self.goods["goods_id"], self.var_name.get(),
                    self.var_cost.get(), self.var_stock.get(), self.var_note.get())
                msg = "商品已更新"
        except ValueError as exc:
            messagebox.showerror("填写有误", str(exc), parent=self.win)
            return
        except Exception as exc:
            logging.exception("保存商品失败")
            messagebox.showerror("保存失败", str(exc), parent=self.win)
            return
        self.page._last_msg = msg
        self.page.reload()
        self.close()

    def close(self):
        try:
            self.win.destroy()
        except tk.TclError:
            pass


class ShopPage:
    """积分商城页。由 version2.0.show_shop() 创建，切页时整个重建。"""

    def __init__(self, app):
        self.app = app
        self.points = PointsMapper()
        self.mapper = ShopMapper()
        self.students = []
        self.goods = []
        self.records = []
        self._class_map = {}
        self._stu_by_iid = {}
        self._goods_by_iid = {}
        self._last_msg = ""

        self.var_class = tk.StringVar(value=ALL_CLASSES)
        self.var_keyword = tk.StringVar()
        self.var_start = tk.StringVar()
        self.var_end = tk.StringVar()
        self.var_only_selected = tk.BooleanVar(value=False)
        self.var_qty = tk.StringVar(value="1")
        self.var_note = tk.StringVar()

        self.stu_tree = None
        self.goods_tree = None
        self.rec_tree = None
        self.main_body = None
        self.main_default = 0
        self.msg_label = None
        self.preview_label = None
        self.selected_stu_label = None
        self.selected_goods_label = None
        self.status_label = None
        self.goods_label = self.ex_label = self.cost_label = self.people_label = None
        self.toggle_btn = None

        self._build()
        self.reload()

    # ------------------------------------------------------------ 构建
    def _build(self):
        app = self.app
        app.set_active_nav("shop")
        self.row_px = _compact_style(app.master)
        page = app.bg.new_page(padx=30, pady=12)
        page.header(
            "积分商城",
            "左边选学员、中间选商品，兑换会扣掉对应积分并生成积分流水与兑换记录",
            links=[("刷新", self.reload), ("导出兑换记录", self.export_csv)],
        )
        c1, self.goods_label = app._stat_card("在售商品", COLORS["primary"])
        c2, self.ex_label = app._stat_card("兑换笔数", COLORS["primary_dark"])
        c3, self.cost_label = app._stat_card("累计消耗积分", COLORS["warn"])
        c4, self.people_label = app._stat_card("参与兑换人数", COLORS["success"])
        page.stat_row([c1, c2, c3, c4], gap_before=6)

        self._build_main(page)
        self._build_records(page)
        page.layout()

    def _label(self, parent, text, size=10, color=None):
        return tk.Label(parent, text=text, bg=COLORS["card"],
                        fg=color or COLORS["muted"], font=font(size))

    def _build_main(self, page):
        card = self.app._card()
        body = tk.Frame(card, bg=COLORS["card"], height=380)
        body.pack(fill=tk.BOTH, expand=True, padx=14, pady=10)
        body.pack_propagate(False)
        self.main_body = body

        left = tk.Frame(body, bg=COLORS["card"])
        left.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 10))
        mid = tk.Frame(body, bg=COLORS["card"])
        mid.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 10))
        right = tk.Frame(body, bg=COLORS["card"], width=336)
        right.pack(side=tk.RIGHT, fill=tk.Y)
        right.pack_propagate(False)

        # --- 左：选学员 ---
        tk.Label(left, text="① 选择学员（按积分降序）", bg=COLORS["card"],
                 fg=COLORS["text"], font=font(11, True)).pack(anchor="w", pady=(0, 6))
        filt = tk.Frame(left, bg=COLORS["card"])
        filt.pack(fill=tk.X, pady=(0, 6))
        self._label(filt, "班级", 9).pack(side=tk.LEFT, padx=(0, 6))
        self.class_box = ttk.Combobox(
            filt, textvariable=self.var_class, state="readonly",
            style="Class.TCombobox", width=12)
        self.class_box.pack(side=tk.LEFT, ipady=2)
        self.class_box.bind("<<ComboboxSelected>>", lambda _e: self.reload())
        self._label(filt, "姓名", 9).pack(side=tk.LEFT, padx=(8, 6))
        form_entry(filt, textvariable=self.var_keyword, width=8).pack(
            side=tk.LEFT, ipady=3)
        self.var_keyword.trace_add("write", lambda *_a: self.reload())
        RoundedButton(
            filt, text="重置", command=self.reset_filters,
            width=button_width("重置", font(9), 48), height=26, radius=7,
            bg=COLORS["card"], fg=COLORS["muted"], hover_bg="#F8FAFC",
            border=COLORS["border"], parent_bg=COLORS["card"],
            font_obj=font(9)).pack(side=tk.RIGHT)

        self.stu_tree = data_tree(left, STU_COLUMNS, height=8,
                                  style_name="Shop.Treeview")
        self.stu_tree.bind("<<TreeviewSelect>>", lambda _e: self._update_form())

        # --- 中：选商品 ---
        tk.Label(mid, text="② 选择商品", bg=COLORS["card"],
                 fg=COLORS["text"], font=font(11, True)).pack(anchor="w", pady=(0, 6))
        self.goods_tree = data_tree(mid, GOODS_COLUMNS, height=8,
                                    style_name="Shop.Treeview")
        self.goods_tree.tag_configure("off", foreground=COLORS["subtle"])
        self.goods_tree.bind("<<TreeviewSelect>>", lambda _e: self._update_form())

        tools = tk.Frame(mid, bg=COLORS["card"])
        tools.pack(fill=tk.X, pady=(8, 0))
        for text, cmd in (("＋ 新增", lambda: self.open_goods_dialog()),
                          ("编辑", self.edit_goods),
                          ("删除", self.delete_selected_goods)):
            RoundedButton(
                tools, text=text, command=cmd,
                width=button_width(text, font(9), 56), height=28, radius=7,
                bg=COLORS["card"], fg=COLORS["muted"], hover_bg="#F8FAFC",
                border=COLORS["border"], parent_bg=COLORS["card"],
                font_obj=font(9)).pack(side=tk.LEFT, padx=(0, 6))
        self.toggle_btn = RoundedButton(
            tools, text="下架", command=self.toggle_goods_state,
            width=button_width("下架", font(9), 56), height=28, radius=7,
            bg=COLORS["warn_soft"], fg=COLORS["warn"], hover_bg="#F8FAFC",
            border=COLORS["border"], parent_bg=COLORS["card"],
            font_obj=font(9))
        self.toggle_btn.pack(side=tk.LEFT)

        # --- 右：兑换操作 ---
        tk.Label(right, text="③ 确认兑换", bg=COLORS["card"],
                 fg=COLORS["text"], font=font(11, True)).pack(anchor="w", pady=(0, 8))
        self.selected_stu_label = tk.Label(
            right, text="学员：请先在左边选一名学生", bg=COLORS["card"],
            fg=COLORS["text"], font=font(10, True), anchor="w",
            justify="left", wraplength=312)
        self.selected_stu_label.pack(fill=tk.X, pady=(0, 4))
        self.selected_goods_label = tk.Label(
            right, text="商品：请在中间选一件商品", bg=COLORS["card"],
            fg=COLORS["text"], font=font(10, True), anchor="w",
            justify="left", wraplength=312)
        self.selected_goods_label.pack(fill=tk.X, pady=(0, 8))

        # 数量和备注各占一行：挤在同一行时备注框会被压成三四个字宽
        line = tk.Frame(right, bg=COLORS["card"])
        line.pack(fill=tk.X, pady=(0, 6))
        self._label(line, "数量").pack(side=tk.LEFT, padx=(0, 8))
        form_entry(line, textvariable=self.var_qty, width=8).pack(
            side=tk.LEFT, ipady=3)
        self._label(line, "件（1~99）", 9, COLORS["subtle"]).pack(
            side=tk.LEFT, padx=(8, 0))
        note_line = tk.Frame(right, bg=COLORS["card"])
        note_line.pack(fill=tk.X, pady=(0, 6))
        self._label(note_line, "备注").pack(side=tk.LEFT, padx=(0, 8))
        form_entry(note_line, textvariable=self.var_note, width=24).pack(
            side=tk.LEFT, fill=tk.X, expand=True, ipady=3)
        self.var_qty.trace_add("write", lambda *_a: self._update_preview())

        self.preview_label = self._label(right, "请先选好学员和商品", 10,
                                         COLORS["primary"])
        self.preview_label.pack(fill=tk.X, pady=(2, 8))
        RoundedButton(
            right, text="确认兑换", command=self.apply_exchange,
            width=button_width("确认兑换", font(10), 104), height=34, radius=9,
            bg=COLORS["primary"], fg="#FFFFFF", hover_bg=COLORS["primary_dark"],
            border=COLORS["primary"], parent_bg=COLORS["card"],
            font_obj=font(10)).pack(anchor="w")
        self.msg_label = self._label(right, "", 9, COLORS["success"])
        self.msg_label.pack(fill=tk.X, pady=(8, 0))
        self._label(right, "兑换会扣分，可在积分管理页看到流水",
                    9, COLORS["subtle"]).pack(fill=tk.X, pady=(2, 0))

        # 主区高度按实际控件算，不同 DPI 下都能完整显示三栏
        self.stu_tree.update_idletasks()
        card.update_idletasks()
        self.main_default = max(300, card.winfo_reqheight())
        initial = self._saved_split() or self.main_default
        page.resizable_block(
            card, gap=4, height=initial, min_height=260,
            on_change=self._on_split_change,
            on_release=self._on_split_release,
            tip="⇕ 拖动调整高度（双击复原）")

    def _build_records(self, page):
        card = self.app._card()
        bar = tk.Frame(card, bg=COLORS["card"])
        bar.pack(fill=tk.X, padx=14, pady=(6, 4))
        tk.Label(bar, text="兑换记录", bg=COLORS["card"], fg=COLORS["text"],
                 font=font(12, True)).pack(side=tk.LEFT)
        self._label(bar, "从", 9).pack(side=tk.LEFT, padx=(16, 6))
        form_entry(bar, textvariable=self.var_start, width=11).pack(
            side=tk.LEFT, ipady=2)
        self._label(bar, "至", 9).pack(side=tk.LEFT, padx=(8, 6))
        form_entry(bar, textvariable=self.var_end, width=11).pack(
            side=tk.LEFT, ipady=2)
        self.var_start.trace_add("write", lambda *_a: self._reload_records())
        self.var_end.trace_add("write", lambda *_a: self._reload_records())
        check_box(bar, "只看选中学员", self.var_only_selected,
                  command=self._reload_records, bg=COLORS["card"]).pack(
                      side=tk.LEFT, padx=(12, 0))
        RoundedButton(
            bar, text="查询", command=self._reload_records,
            width=button_width("查询", font(9), 52), height=26, radius=7,
            bg=COLORS["card"], fg=COLORS["muted"], hover_bg="#F8FAFC",
            border=COLORS["border"], parent_bg=COLORS["card"],
            font_obj=font(9)).pack(side=tk.LEFT, padx=(8, 0))
        self.status_label = self._label(bar, "", 9, COLORS["subtle"])
        self.status_label.pack(side=tk.RIGHT)

        self.rec_tree = data_tree(card, REC_COLUMNS, height=5,
                                  style_name="Shop.Treeview")
        page.block(card, gap=0, expand=True)

    # ------------------------------------------------------------ 分隔条
    def _saved_split(self):
        value = getattr(self.app, "shop_split_height", None)
        if value is None:
            cfg = getattr(self.app, "ui_config", {}) or {}
            value = cfg.get("shop_split")
        try:
            value = int(value)
        except (TypeError, ValueError):
            return None
        return value if 220 <= value <= 2000 else None

    def _on_split_change(self, height):
        value = int(height)
        self.app.shop_split_height = value
        cfg = getattr(self.app, "ui_config", None)
        if isinstance(cfg, dict):
            cfg["shop_split"] = value
        if self.main_body is not None:
            try:
                self.main_body.configure(height=max(180, value - 26))
            except tk.TclError:
                pass

    def _on_split_release(self, height):
        cfg = getattr(self.app, "ui_config", None)
        if not isinstance(cfg, dict):
            return
        cfg["shop_split"] = int(height)
        try:
            self.app.save_ui_config(cfg)
        except Exception:
            logging.exception("保存积分商城布局失败")

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

    def reset_filters(self):
        self.var_class.set(ALL_CLASSES)
        self.var_keyword.set("")
        self.var_start.set("")
        self.var_end.set("")
        self.var_only_selected.set(False)
        self.reload()

    def reload(self, keep_selection=True):
        stu_id = self._selected_stu_id() if keep_selection else None
        goods_id = self._selected_goods_id() if keep_selection else None
        self._refresh_class_options()
        self._load_students()
        self._load_goods()
        self._load_records()
        self._update_stats()
        if stu_id:
            self._select_stu(stu_id)
        if goods_id:
            self._select_goods(goods_id)
        self._update_form()

    def _load_students(self):
        balances = self.points.balance_map()
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
                rows.append({
                    "stu_id": sid,
                    "stu_name": str(name),
                    "class_id": str(class_id),
                    "class_label": self._class_label(class_id),
                    "balance": int(balances.get(sid, 0)),
                })
        # 与积分管理页一致：积分高的排前面，方便挑有分可兑的学员
        rows.sort(key=lambda r: (-r["balance"], r["class_id"], r["stu_name"]))
        self.students = rows
        self._fill_students()

    def _fill_students(self):
        tree = self.stu_tree
        for item in tree.get_children():
            tree.delete(item)
        self._stu_by_iid = {}
        for idx, row in enumerate(self.students):
            iid = row["stu_id"]
            if iid in self._stu_by_iid:
                iid = "%s_%d" % (iid, idx)
            tags = ("even",) if idx % 2 else ("odd",)
            tree.insert("", tk.END, iid=iid, values=(
                row["stu_name"], row["class_label"], row["balance"]), tags=tags)
            self._stu_by_iid[iid] = row

    def _load_goods(self):
        self.goods = self.mapper.list_goods(include_off=True)
        self._fill_goods()

    def _fill_goods(self):
        tree = self.goods_tree
        for item in tree.get_children():
            tree.delete(item)
        self._goods_by_iid = {}
        for idx, row in enumerate(self.goods):
            off = str(row["state"]) != "1"
            stock = int(row["stock"])
            tree.insert("", tk.END, iid=str(row["goods_id"]), values=(
                row["goods_name"], row["cost"],
                "不限" if stock < 0 else stock,
                "下架" if off else "在售"),
                tags=("off",) if off else (("even",) if idx % 2 else ("odd",)))
            self._goods_by_iid[str(row["goods_id"])] = row

    def _load_records(self):
        start, end = self.var_start.get().strip(), self.var_end.get().strip()
        if not _date_ok(start) or not _date_ok(end):
            self.status_label.config(text="日期格式应为 YYYY-MM-DD")
            return
        stu_id = self._selected_stu_id() if self.var_only_selected.get() else None
        try:
            self.records = self.mapper.query(
                stu_id=stu_id, start=start or None, end=end or None,
                limit=EXCHANGE_LIMIT)
        except Exception as exc:
            logging.exception("读取兑换记录失败")
            self.records = []
            self.status_label.config(text="读取失败：%s" % str(exc)[:40])
            return
        self._fill_records()

    def _fill_records(self):
        tree = self.rec_tree
        for item in tree.get_children():
            tree.delete(item)
        for row in self.records:
            tree.insert("", tk.END, values=(
                row["create_time"], self._class_label(row["class_id"]),
                row["stu_name"], row["goods_name"], row["qty"],
                row["total_cost"], row["note"] or ""))
        text = "共 %d 条" % len(self.records)
        if len(self.records) >= EXCHANGE_LIMIT:
            text += "（已到上限，请缩小日期范围）"
        self.status_label.config(text=text)

    def _update_stats(self):
        t = self.mapper.totals()
        self.goods_label.config(text=str(t["goods"]))
        self.ex_label.config(text=str(t["exchanges"]))
        self.cost_label.config(text=str(t["cost"]))
        self.people_label.config(text=str(t["people"]))

    def _reload_records(self):
        self._load_records()

    # ------------------------------------------------------------ 选中
    def _selected_stu_id(self):
        sel = self.stu_tree.selection() if self.stu_tree else ()
        row = self._stu_by_iid.get(sel[0]) if sel else None
        return row["stu_id"] if row else None

    def _selected_stu(self):
        sel = self.stu_tree.selection() if self.stu_tree else ()
        return self._stu_by_iid.get(sel[0]) if sel else None

    def _selected_goods_id(self):
        sel = self.goods_tree.selection() if self.goods_tree else ()
        return sel[0] if sel else None

    def _selected_goods(self):
        sel = self.goods_tree.selection() if self.goods_tree else ()
        return self._goods_by_iid.get(sel[0]) if sel else None

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

    def _select_goods(self, goods_id):
        iid = str(goods_id)
        if iid in self._goods_by_iid:
            try:
                self.goods_tree.selection_set(iid)
                self.goods_tree.focus(iid)
                self.goods_tree.see(iid)
            except tk.TclError:
                pass

    # ------------------------------------------------------------ 表单
    def _update_form(self):
        stu = self._selected_stu()
        goods = self._selected_goods()
        if self.selected_stu_label is not None:
            self.selected_stu_label.config(
                text=("学员：%s（%d 分）" %
                      (stu["stu_name"], stu["balance"]))
                if stu else "学员：请先在左边选一名学生")
        if self.selected_goods_label is not None:
            self.selected_goods_label.config(
                text=("商品：%s · %s 分/件" %
                      (goods["goods_name"], goods["cost"]))
                if goods else "商品：请在中间选一件商品")
        if self.toggle_btn is not None:
            self.toggle_btn.set_text(
                "上架" if goods and str(goods["state"]) != "1" else "下架")
        self._update_preview()
        if self.var_only_selected.get():
            self._reload_records()

    def _update_preview(self):
        if self.preview_label is None:
            return
        stu, goods = self._selected_stu(), self._selected_goods()
        if stu is None or goods is None:
            self.preview_label.config(text="请先选好学员和商品",
                                      fg=COLORS["muted"])
            return
        try:
            qty = int(self.var_qty.get().strip())
        except (TypeError, ValueError):
            self.preview_label.config(text="数量要填整数", fg=COLORS["danger"])
            return
        if qty < 1 or qty > MAX_QTY:
            self.preview_label.config(
                text="数量要在 1 ~ %d 之间" % MAX_QTY, fg=COLORS["danger"])
            return
        if str(goods["state"]) != "1":
            self.preview_label.config(text="该商品已下架，请先上架",
                                      fg=COLORS["danger"])
            return
        total = int(goods["cost"]) * qty
        left = int(stu["balance"]) - total
        if left < 0:
            self.preview_label.config(
                text="需 %d 分，积分不足（还差 %d 分）" % (total, -left),
                fg=COLORS["danger"])
        else:
            self.preview_label.config(
                text="需 %d 分  →  兑换后剩 %d 分" % (total, left),
                fg=COLORS["primary"])
        if self.msg_label is not None and self._last_msg:
            self.msg_label.config(text=self._last_msg, fg=COLORS["success"])

    # ------------------------------------------------------------ 兑换
    def apply_exchange(self):
        stu, goods = self._selected_stu(), self._selected_goods()
        if stu is None:
            messagebox.showinfo("先选学员", "请先在左边学员列表里点选一名学生。")
            return
        if goods is None:
            messagebox.showinfo("先选商品", "请先在中间商品列表里点选一件商品。")
            return
        try:
            qty = int(self.var_qty.get().strip())
        except (TypeError, ValueError):
            messagebox.showerror("数量不对", "兑换数量要填整数。")
            return
        if qty < 1 or qty > MAX_QTY:
            messagebox.showerror("数量不对",
                                 "单次兑换数量要在 1 ~ %d 之间。" % MAX_QTY)
            return
        if str(goods["state"]) != "1":
            messagebox.showerror("商品已下架", "这件商品已下架，请先上架再兑换。")
            return
        total = int(goods["cost"]) * qty
        if total > int(stu["balance"]):
            messagebox.showerror(
                "积分不足",
                "%s 当前 %d 分，兑换 %s ×%d 需要 %d 分，还差 %d 分。" %
                (stu["stu_name"], stu["balance"], goods["goods_name"],
                 qty, total, total - stu["balance"]))
            return
        if not messagebox.askyesno(
                "确认兑换",
                "学员：%s（当前 %d 分）\n商品：%s  %d 分/件 × %d\n"
                "共扣 %d 分，兑换后剩 %d 分。\n\n确认兑换吗？" %
                (stu["stu_name"], stu["balance"], goods["goods_name"],
                 goods["cost"], qty, total, stu["balance"] - total)):
            return
        try:
            rec = self.mapper.exchange(
                stu["stu_id"], stu["stu_name"], stu["class_id"],
                goods["goods_id"], qty, self.var_note.get().strip())
        except ValueError as exc:
            messagebox.showerror("兑换失败", str(exc))
            return
        except Exception as exc:
            logging.exception("积分兑换失败")
            messagebox.showerror("兑换失败", "写入失败：%s" % exc)
            return
        self.var_qty.set("1")
        self.var_note.set("")
        self._last_msg = "已为 %s 兑换 %s ×%d，扣 %d 分，剩 %d 分" % (
            rec["stu_name"], rec["goods_name"], rec["qty"],
            rec["total_cost"], rec["balance_after"])
        self.reload(keep_selection=True)
        self.msg_label.config(text=self._last_msg, fg=COLORS["success"])

    # ------------------------------------------------------------ 商品管理
    def open_goods_dialog(self, goods=None):
        self._dlg = GoodsDialog(self, goods)
        return self._dlg

    def edit_goods(self):
        goods = self._selected_goods()
        if goods is None:
            messagebox.showinfo("先选商品", "请先点选一件商品再编辑。")
            return None
        return self.open_goods_dialog(goods)

    def toggle_goods_state(self):
        goods = self._selected_goods()
        if goods is None:
            messagebox.showinfo("先选商品", "请先点选一件商品。")
            return
        new_state = "1" if str(goods["state"]) != "1" else "0"
        self.mapper.set_goods_state(goods["goods_id"], new_state)
        self._last_msg = "「%s」已%s" % (
            goods["goods_name"], "上架" if new_state == "1" else "下架")
        self.reload(keep_selection=True)
        self.msg_label.config(text=self._last_msg, fg=COLORS["success"])

    def delete_selected_goods(self):
        goods = self._selected_goods()
        if goods is None:
            messagebox.showinfo("先选商品", "请先点选一件商品。")
            return
        if not messagebox.askyesno(
                "删除商品", "确定删除「%s」吗？\n有兑换记录的商品不能删，请改用下架。"
                % goods["goods_name"]):
            return
        try:
            self.mapper.delete_goods(goods["goods_id"])
        except ValueError as exc:
            messagebox.showerror("无法删除", str(exc))
            return
        self._last_msg = "已删除商品「%s」" % goods["goods_name"]
        self.reload(keep_selection=False)
        self.msg_label.config(text=self._last_msg, fg=COLORS["success"])

    # ------------------------------------------------------------ 导出
    def export_csv(self):
        if not self.records:
            messagebox.showinfo("没有可导出的记录", "当前筛选条件下没有兑换记录。")
            return
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        try:
            from service.dp import data_dir
            out_dir = data_dir()
        except Exception:
            out_dir = os.getcwd()
        path = os.path.join(out_dir, "积分兑换记录_%s.csv" % stamp)
        try:
            with open(path, "w", newline="", encoding="utf-8-sig") as f:
                writer = csv.writer(f)
                writer.writerow(CSV_COLUMNS)
                for r in self.records:
                    writer.writerow((
                        r["create_time"], self._class_label(r["class_id"]),
                        r["stu_name"], r["goods_name"], r["qty"], r["cost"],
                        r["total_cost"], r["note"] or ""))
        except Exception as exc:
            logging.exception("导出兑换记录失败")
            messagebox.showerror("导出失败", str(exc))
            return
        messagebox.showinfo("导出成功", "已导出 %d 条到：\n%s" %
                            (len(self.records), path))
