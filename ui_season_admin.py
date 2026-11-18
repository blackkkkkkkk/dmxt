"""暑假 / 寒假课程设置：班级、学员、课次三段式管理。

假期班的排课不统一（有时连着上五天，有时隔天），所以课次是一条条手工加的，
每个课次有自己的日期和时间段；点名在假期页里按课次走。

班级和学员都用软删除：state 置 0，记录仍留在库里，勾选「显示已删除」可以恢复。
课次是排课记录，删除时连同该次课的签到一起删——删除前会告诉用户会丢多少条。
"""

import logging
import tkinter as tk
from datetime import datetime
from tkinter import messagebox, ttk

from entity.season import SeasonClass, SeasonLesson, SeasonStu
from mapper.t_class_mapper import TClassMapper
from mapper.t_season_mapper import (ALIVE, DELETED, SeasonClassMapper,
                                    SeasonLessonMapper, SeasonSignMapper,
                                    SeasonStuMapper, ensure_schema)
from mapper.t_stu_mapper import TStuMapper
from ui_theme import COLORS, RoundedButton, button_width, font
from ui_widgets import (center_on, check_box, data_tree, field_row,
                        fit_dialog_to_screen, form_entry, form_label)

SEASON_TITLE = {"summer": "暑假课程", "winter": "寒假课程"}
DEFAULT_HOUR = "2.5"


def season_title(season):
    return SEASON_TITLE.get(season, "假期课程")


def normalize_date(text, default_year=None):
    """把用户输入的日期规范成 YYYY-MM-DD。

    允许 2026-07-06、2026/7/6、7-6（补当前年），省掉每次敲年份。
    解析失败抛 ValueError，由调用方转成表单错误提示。
    """
    raw = (text or "").strip().replace("/", "-").replace(".", "-")
    if not raw:
        return ""
    parts = [p for p in raw.split("-") if p != ""]
    if len(parts) == 2:
        raw = "%d-%s" % (default_year or datetime.now().year, "-".join(parts))
    return datetime.strptime(raw, "%Y-%m-%d").strftime("%Y-%m-%d")


def date_text(value):
    if value in (None, ""):
        return ""
    if hasattr(value, "strftime"):
        return value.strftime("%Y-%m-%d")
    return str(value)[:10]


def is_deleted(row):
    return str(getattr(row, "state", ALIVE)) == DELETED


def fmt_hour(value):
    if value in (None, ""):
        return ""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    return str(int(number)) if number == int(number) else ("%g" % number)


# ---------------------------------------------------------------- 入口


def open_season_admin(app, season, page="class", class_id=None, on_done=None):
    """打开（或前置）某个季节的设置窗口；重复点击不再开第二个。"""
    existing = getattr(app, "_season_admin", None)
    if existing is not None:
        try:
            if existing.win.winfo_exists():
                existing.show_page(page, class_id)
                existing.win.deiconify()
                existing.win.lift()
                existing.win.focus_force()
                return existing
        except tk.TclError:
            pass
        app._season_admin = None

    dialog = SeasonAdminDialog(app, season, page=page, class_id=class_id,
                               on_done=on_done)
    app._season_admin = dialog
    return dialog


def open_class_form(app, season, on_done=None):
    """从点名页直接「＋ 新建班级」：建完顺手把窗口留在设置里。"""
    existing = getattr(app, "_season_admin", None)
    parent = None
    if existing is not None:
        try:
            if existing.win.winfo_exists() and existing.season == season:
                parent = existing.win
        except tk.TclError:
            parent = None
    return SeasonClassForm(parent or app.master, season,
                           on_saved=lambda _msg: on_done and on_done())


def open_lesson_form(app, season, class_id, on_done=None):
    return SeasonLessonForm(app.master, class_id,
                            on_saved=lambda _msg: on_done and on_done())


# ---------------------------------------------------------------- 设置窗口


class SeasonAdminDialog:
    # 列宽按 150% 缩放下的实际文字量过：汉字约 20px/个，表头还有左右内边距
    COLUMNS_CLASS = (
        ("班级名称", 240, "w", True),
        ("课程", 96, "w", False),
        ("每次课时", 110, "center", False),
        ("起止日期", 300, "w", False),
        ("学员", 66, "center", False),
        ("课次", 66, "center", False),
    )
    COLUMNS_STU = (
        ("姓名", 180, "w", True),
        ("手机号", 160, "w", False),
        ("已签到", 96, "center", False),
        ("备注", 260, "w", True),
    )
    COLUMNS_LESSON = (
        ("课次", 100, "center", False),
        ("日期", 156, "w", False),
        ("时间段", 156, "w", False),
        ("内容", 260, "w", True),
        ("出勤", 96, "center", False),
    )

    def __init__(self, app, season, page="class", class_id=None, on_done=None):
        self.app = app
        self.season = season
        self.on_done = on_done
        self._page = None
        self._dirty = False

        self._classes = []
        self._students = []
        self._lessons = []
        self._stu_counts = {}
        self._sign_counts = {}
        self._attended = {}
        self._stu_cid_by_label = {}
        self._lesson_cid_by_label = {}
        self._form = None

        try:
            ensure_schema()
        except Exception:
            # 建表失败（库没连上/无权限）不阻断打开窗口，各页会显示具体错误
            logging.exception("假期课程表初始化失败")

        self.win = tk.Toplevel(app.master)
        self.win.title("%s设置" % season_title(season))
        self.win.configure(bg=COLORS["bg"])
        self.win.geometry("1040x740")
        self.win.minsize(920, 620)
        self.win.transient(app.master)

        # 班级页与学员页各管各的，勾了学员页不会连带班级页一起显示已删除
        self.var_del_class = tk.BooleanVar(value=False)
        self.var_del_stu = tk.BooleanVar(value=False)
        self.var_stu_class = tk.StringVar()
        self.var_lesson_class = tk.StringVar()

        self._build()
        center_on(self.win, app.master, ratio=4)
        self.win.protocol("WM_DELETE_WINDOW", self.close)
        self.win.bind("<Escape>", lambda _e: self.close())
        try:
            self.win.grab_set()
        except tk.TclError:
            pass
        self.show_page(page, class_id)

    # ------------------------------------------------------------ 布局
    def _build(self):
        root = tk.Frame(self.win, bg=COLORS["bg"])
        root.pack(fill=tk.BOTH, expand=True, padx=22, pady=(18, 16))

        head = tk.Frame(root, bg=COLORS["bg"])
        head.pack(fill=tk.X)
        form_label(head, "%s设置" % season_title(self.season), 16, True,
                   bg=COLORS["bg"]).pack(anchor="w")
        form_label(head, "假期班的上课日期不固定：先建班级，再把学员和每次课的日期排进来",
                   9, color=COLORS["muted"], bg=COLORS["bg"]).pack(
            anchor="w", pady=(3, 0))

        nav = tk.Frame(root, bg=COLORS["bg"])
        nav.pack(fill=tk.X, pady=(14, 12))
        self.nav_class = RoundedButton(
            nav, text="班级", command=lambda: self.show_page("class"),
            width=88, height=36, bg=COLORS["card"], fg="#334155",
            hover_bg="#F8FAFC", border=COLORS["border"], radius=10,
            parent_bg=COLORS["bg"], font_obj=font(10))
        self.nav_class.pack(side=tk.LEFT)
        self.nav_stu = RoundedButton(
            nav, text="学员", command=lambda: self.show_page("stu"),
            width=88, height=36, bg=COLORS["card"], fg="#334155",
            hover_bg="#F8FAFC", border=COLORS["border"], radius=10,
            parent_bg=COLORS["bg"], font_obj=font(10))
        self.nav_stu.pack(side=tk.LEFT, padx=(8, 0))
        self.nav_lesson = RoundedButton(
            nav, text="课次", command=lambda: self.show_page("lesson"),
            width=88, height=36, bg=COLORS["card"], fg="#334155",
            hover_bg="#F8FAFC", border=COLORS["border"], radius=10,
            parent_bg=COLORS["bg"], font_obj=font(10))
        self.nav_lesson.pack(side=tk.LEFT, padx=(8, 0))

        RoundedButton(nav, text="关闭", command=self.close, width=88, height=36,
                      bg=COLORS["card"], fg="#334155", hover_bg="#F8FAFC",
                      border=COLORS["border"], radius=10, parent_bg=COLORS["bg"],
                      font_obj=font(10)).pack(side=tk.RIGHT)

        self.body = tk.Frame(root, bg=COLORS["bg"])
        self.body.pack(fill=tk.BOTH, expand=True)

        self.pages = {}
        for key, builder in (("class", self._build_class_page),
                             ("stu", self._build_stu_page),
                             ("lesson", self._build_lesson_page)):
            frame = tk.Frame(self.body, bg=COLORS["bg"])
            builder(frame)
            self.pages[key] = frame

    def show_page(self, name, cid=None):
        if name not in self.pages:
            name = "class"
        for key, frame in self.pages.items():
            if key != name:
                frame.pack_forget()
        self.pages[name].pack(fill=tk.BOTH, expand=True)
        self.nav_class.set_selected(name == "class")
        self.nav_stu.set_selected(name == "stu")
        self.nav_lesson.set_selected(name == "lesson")
        self._page = name

        if name == "class":
            self.refresh_classes()
        elif name == "stu":
            self.refresh_stu_classes(cid)
            self.refresh_students()
        else:
            self.refresh_lesson_classes(cid)
            self.refresh_lessons()

    def on_changed(self, message=""):
        """写库成功后：本窗口刷新，并标记主界面需要重载。"""
        self._dirty = True
        if self._page == "class":
            self.refresh_classes()
        elif self._page == "stu":
            self.refresh_students()
        else:
            self.refresh_lessons()
        if message:
            label = getattr(self, "%s_status" % self._page, None)
            if label is not None:
                label.config(text=message, fg=COLORS["success"])

    def close(self):
        try:
            self.win.grab_release()
        except tk.TclError:
            pass
        self.win.destroy()
        self.app._season_admin = None
        if self._dirty and self.on_done is not None:
            self.on_done()

    # ------------------------------------------------------------ 班级页
    def _build_class_page(self, page):
        bar = tk.Frame(page, bg=COLORS["bg"])
        bar.pack(fill=tk.X, pady=(0, 10))

        RoundedButton(bar, text="＋ 新增班级", command=self.add_class, width=126,
                      height=38, radius=10, parent_bg=COLORS["bg"],
                      font_obj=font(10)).pack(side=tk.LEFT)

        self.cls_edit_btn = RoundedButton(
            bar, text="编辑", command=self.edit_class, width=88, height=38,
            bg=COLORS["card"], fg="#334155", hover_bg="#F8FAFC",
            border=COLORS["border"], radius=10, parent_bg=COLORS["bg"],
            font_obj=font(10))
        self.cls_edit_btn.pack(side=tk.LEFT, padx=(10, 0))

        # 同一个按钮两种身份：选中有效班级时是「删除」，选中已删除班级时是「恢复」
        self.cls_del_btn = RoundedButton(
            bar, text="删除", command=self.on_remove_class_click, width=88,
            height=38, bg=COLORS["card"], fg=COLORS["danger"],
            hover_bg=COLORS["danger_soft"], border=COLORS["border"], radius=10,
            parent_bg=COLORS["bg"], font_obj=font(10))
        self.cls_del_btn.pack(side=tk.LEFT, padx=(10, 0))

        stu_text = "管理该班学员 →"
        self.cls_stu_btn = RoundedButton(
            bar, text=stu_text,
            command=lambda: self.show_page("stu", self._selected_class_id()),
            width=button_width(stu_text, font(10), 148), height=38,
            bg=COLORS["card"], fg="#334155", hover_bg="#F8FAFC",
            border=COLORS["border"], radius=10, parent_bg=COLORS["bg"],
            font_obj=font(10))
        self.cls_stu_btn.pack(side=tk.LEFT, padx=(10, 0))

        lesson_text = "课次 →"
        self.cls_lesson_btn = RoundedButton(
            bar, text=lesson_text,
            command=lambda: self.show_page("lesson", self._selected_class_id()),
            width=button_width(lesson_text, font(10), 88), height=38,
            bg=COLORS["card"], fg="#334155", hover_bg="#F8FAFC",
            border=COLORS["border"], radius=10, parent_bg=COLORS["bg"],
            font_obj=font(10))
        self.cls_lesson_btn.pack(side=tk.LEFT, padx=(10, 0))

        check_box(bar, "显示已删除", self.var_del_class,
                  command=self.refresh_classes).pack(side=tk.RIGHT)

        self.class_tree = data_tree(page, self.COLUMNS_CLASS, height=8)
        self.class_tree.bind("<<TreeviewSelect>>",
                             lambda _e: self._sync_class_buttons())
        self.class_tree.bind("<Double-1>", lambda _e: self.edit_class())

        self.class_status = form_label(page, "", 9, color=COLORS["muted"],
                                       bg=COLORS["bg"])
        self.class_status.pack(anchor="w", pady=(10, 0))
        form_label(page, "删除班级只标记为无效：学员、课次、签到记录都保留，勾选「显示已删除」可以恢复",
                   9, color=COLORS["subtle"], bg=COLORS["bg"]).pack(anchor="w")

    def refresh_classes(self):
        try:
            self._classes = SeasonClassMapper().select_all(
                self.season, include_deleted=self.var_del_class.get())
            stats = SeasonClassMapper().stats_by_class(self.season)
        except Exception as e:
            logging.exception("读取假期班级失败")
            self._classes = []
            stats = {}
            self.class_tree.delete(*self.class_tree.get_children())
            self.class_status.config(text="读取班级失败：%s" % e, fg=COLORS["danger"])
            self._sync_class_buttons()
            return
        self.class_tree.delete(*self.class_tree.get_children())
        deleted_n = 0
        for idx, cls in enumerate(self._classes):
            deleted = is_deleted(cls)
            deleted_n += 1 if deleted else 0
            name = (cls.class_name or "未命名").strip()
            if deleted:
                name += "（已删除）"
            span = ""
            start, end = date_text(cls.start_date), date_text(cls.end_date)
            if start and end:
                span = "%s ~ %s" % (start, end)
            elif start:
                span = "%s 起" % start
            elif end:
                span = "至 %s" % end
            stu_n, lesson_n = stats.get(cls.sclass_id, (0, 0))
            self.class_tree.insert(
                "", tk.END, iid=str(cls.sclass_id),
                values=(name, cls.class_type or "—", fmt_hour(cls.class_hour),
                        span or "未设置", stu_n, lesson_n),
                tags=("deleted",) if deleted else
                     ("even" if idx % 2 else "odd",))
        total = len(self._classes) - deleted_n
        if deleted_n:
            self.class_status.config(
                text="共 %d 个班级，另有 %d 个已删除" % (total, deleted_n),
                fg=COLORS["muted"])
        else:
            self.class_status.config(text="共 %d 个班级" % total, fg=COLORS["muted"])
        self._sync_class_buttons()

    def _selected_class_id(self):
        selection = self.class_tree.selection()
        return int(selection[0]) if selection else None

    def _selected_class(self):
        cid = self._selected_class_id()
        for cls in self._classes:
            if cls.sclass_id == cid:
                return cls
        return None

    def _sync_class_buttons(self):
        cls = self._selected_class()
        deleted = cls is not None and is_deleted(cls)
        self.cls_edit_btn.set_enabled(cls is not None and not deleted)
        self.cls_stu_btn.set_enabled(cls is not None and not deleted)
        self.cls_lesson_btn.set_enabled(cls is not None and not deleted)
        self.cls_del_btn.set_enabled(cls is not None)
        if deleted:
            self.cls_del_btn.set_text("恢复")
            self.cls_del_btn.set_colors(COLORS["card"], COLORS["success"],
                                        COLORS["success_soft"],
                                        border=COLORS["border"])
        else:
            self.cls_del_btn.set_text("删除")
            self.cls_del_btn.set_colors(COLORS["card"], COLORS["danger"],
                                        COLORS["danger_soft"],
                                        border=COLORS["border"])

    def add_class(self):
        if self._form_open():
            return
        self._form = SeasonClassForm(self.win, self.season,
                                     on_saved=self.on_changed,
                                     on_close=self._clear_form)

    def edit_class(self):
        if self._form_open():
            return
        cls = self._selected_class()
        if cls is None or is_deleted(cls):
            return
        self._form = SeasonClassForm(self.win, self.season, original=cls,
                                     on_saved=self.on_changed,
                                     on_close=self._clear_form)

    def on_remove_class_click(self):
        cls = self._selected_class()
        if cls is None:
            return
        if is_deleted(cls):
            self.restore_class()
        else:
            self.delete_class()

    def delete_class(self):
        cls = self._selected_class()
        if cls is None:
            return
        stats = SeasonClassMapper().stats_by_class(self.season)
        stu_n, lesson_n = stats.get(cls.sclass_id, (0, 0))
        if not messagebox.askyesno(
                "删除班级",
                "确定删除「%s」吗？\n\n该班有 %d 名学员、%d 个课次。\n"
                "删除只是标记为无效：学员、课次与签到记录都保留，"
                "勾选「显示已删除」可以恢复。"
                % (cls.class_name, stu_n, lesson_n),
                icon="warning", default="no", parent=self.win):
            return
        try:
            SeasonClassMapper().delete(cls.sclass_id)
        except Exception as e:
            messagebox.showerror("删除失败", str(e), parent=self.win)
            return
        self.on_changed("已删除班级 %s（可恢复）" % cls.class_name)

    def restore_class(self):
        cls = self._selected_class()
        if cls is None or not is_deleted(cls):
            return
        try:
            SeasonClassMapper().restore(cls.sclass_id)
        except Exception as e:
            messagebox.showerror("恢复失败", str(e), parent=self.win)
            return
        self.on_changed("已恢复班级 %s" % cls.class_name)

    # ------------------------------------------------------------ 学员页
    def _build_stu_page(self, page):
        bar = tk.Frame(page, bg=COLORS["bg"])
        bar.pack(fill=tk.X, pady=(0, 10))

        form_label(bar, "班级", 10, color=COLORS["muted"],
                   bg=COLORS["bg"]).pack(side=tk.LEFT, padx=(0, 8))
        self.stu_class_box = ttk.Combobox(
            bar, textvariable=self.var_stu_class, state="readonly",
            style="Class.TCombobox", width=20, values=[])
        self.stu_class_box.pack(side=tk.LEFT)
        self.stu_class_box.bind("<<ComboboxSelected>>",
                                lambda _e: self.refresh_students())

        form_label(bar, "筛选", 10, color=COLORS["muted"],
                   bg=COLORS["bg"]).pack(side=tk.LEFT, padx=(14, 8))
        self.var_stu_filter = tk.StringVar()
        self.stu_filter = form_entry(bar, self.var_stu_filter, width=9)
        self.stu_filter.pack(side=tk.LEFT, padx=(0, 12), ipady=4)
        self.stu_filter.bind("<KeyRelease>", lambda _e: self._render_students())

        import_text = "从常规班导入"
        self.stu_import_btn = RoundedButton(
            bar, text=import_text, command=self.import_students,
            width=button_width(import_text, font(10), 126), height=38, bg=COLORS["card"], fg="#334155", hover_bg="#F8FAFC",
            border=COLORS["border"], radius=10, parent_bg=COLORS["bg"],
            font_obj=font(10))
        self.stu_import_btn.pack(side=tk.RIGHT)

        # 删除 / 恢复共用：选中已删除的学员时自动变成绿色「恢复」
        self.stu_del_btn = RoundedButton(
            bar, text="删除", command=self.on_remove_student_click, width=78,
            height=38, bg=COLORS["card"], fg=COLORS["danger"],
            hover_bg=COLORS["danger_soft"], border=COLORS["border"], radius=10,
            parent_bg=COLORS["bg"], font_obj=font(10))
        self.stu_del_btn.pack(side=tk.RIGHT, padx=(0, 8))

        self.stu_edit_btn = RoundedButton(
            bar, text="编辑", command=self.edit_student, width=78, height=38,
            bg=COLORS["card"], fg="#334155", hover_bg="#F8FAFC",
            border=COLORS["border"], radius=10, parent_bg=COLORS["bg"],
            font_obj=font(10))
        self.stu_edit_btn.pack(side=tk.RIGHT, padx=(0, 8))

        add_text = "＋ 新增学员"
        self.stu_add_btn = RoundedButton(
            bar, text=add_text, command=self.add_student,
            width=button_width(add_text, font(10), 118), height=38, radius=10,
            parent_bg=COLORS["bg"], font_obj=font(10))
        self.stu_add_btn.pack(side=tk.RIGHT, padx=(0, 8))

        self.stu_tree = data_tree(page, self.COLUMNS_STU, height=8)
        self.stu_tree.bind("<<TreeviewSelect>>",
                           lambda _e: self._sync_stu_buttons())
        self.stu_tree.bind("<Double-1>", lambda _e: self.edit_student())

        foot = tk.Frame(page, bg=COLORS["bg"])
        foot.pack(fill=tk.X, pady=(10, 0))
        self.stu_status = form_label(foot, "", 9, color=COLORS["muted"],
                                     bg=COLORS["bg"])
        self.stu_status.pack(side=tk.LEFT, anchor="w")
        check_box(foot, "显示已删除", self.var_del_stu,
                  command=self.refresh_students).pack(side=tk.RIGHT)

        form_label(page, "学员直接挂在这个假期班下，和常规班名单互不影响；删除后可用「显示已删除」找回",
                   9, color=COLORS["subtle"], bg=COLORS["bg"]).pack(anchor="w")

    def refresh_stu_classes(self, target=None):
        try:
            classes = SeasonClassMapper().select_all(self.season)
        except Exception as e:
            logging.exception("读取假期班级失败")
            classes = []
            self.stu_status.config(text="读取班级失败：%s" % e, fg=COLORS["danger"])
        self._stu_cid_by_label = {}
        labels = []
        for cls in classes:
            label = (cls.class_name or "未命名").strip()
            if cls.class_type:
                label += " · " + cls.class_type.strip()
            if label in self._stu_cid_by_label:
                label += " #%s" % cls.sclass_id
            labels.append(label)
            self._stu_cid_by_label[label] = cls.sclass_id
        self.stu_class_box.configure(values=labels)

        current = target if target in [c.sclass_id for c in classes] else None
        if current is None:
            current = self._stu_cid_by_label.get(self.var_stu_class.get())
        if current is None and labels:
            current = self._stu_cid_by_label[labels[0]]
        if current is None:
            self.var_stu_class.set("")
        else:
            self.var_stu_class.set(
                next(l for l, v in self._stu_cid_by_label.items() if v == current))

    def _current_stu_class(self):
        return self._stu_cid_by_label.get(self.var_stu_class.get())

    def refresh_students(self):
        cid = self._current_stu_class()
        self._students = []
        self._sign_counts = {}
        if cid is None:
            self.stu_tree.delete(*self.stu_tree.get_children())
            self.stu_status.config(
                text="还没有%s班，先到「班级」页新增一个" % season_title(self.season)[:2],
                fg=COLORS["warn"])
            self._sync_stu_buttons()
            return
        try:
            self._students = SeasonStuMapper().select_by_class(
                cid, include_deleted=self.var_del_stu.get())
            self._sign_counts = SeasonStuMapper().sign_counts(cid)
        except Exception as e:
            logging.exception("读取假期学员失败")
            self.stu_tree.delete(*self.stu_tree.get_children())
            self.stu_status.config(text="读取学员失败：%s" % e, fg=COLORS["danger"])
            self._sync_stu_buttons()
            return
        self._render_students()

    def _render_students(self):
        self.stu_tree.delete(*self.stu_tree.get_children())
        keyword = self.var_stu_filter.get().strip()
        shown = deleted_n = 0
        for idx, stu in enumerate(self._students):
            name = (stu.stu_name or "").strip()
            if keyword and keyword not in name:
                continue
            deleted = is_deleted(stu)
            if deleted:
                deleted_n += 1
                name += "（已删除）"
            self.stu_tree.insert(
                "", tk.END, iid=str(stu.sstu_id),
                values=(name, stu.phone or "—",
                        self._sign_counts.get(stu.sstu_id, 0), stu.note or "—"),
                tags=("deleted",) if deleted else
                     ("even" if idx % 2 else "odd",))
            shown += 1
        total = len(self._students)
        parts = ["%d 名学员" % total if not keyword
                 else "筛选「%s」：%d / %d 人" % (keyword, shown, total)]
        if deleted_n:
            parts.append("%d 名已删除" % deleted_n)
        self.stu_status.config(text=" · ".join(parts), fg=COLORS["muted"])
        self._sync_stu_buttons()

    def _selected_student(self):
        selection = self.stu_tree.selection()
        if not selection:
            return None
        sid = int(selection[0])
        for stu in self._students:
            if stu.sstu_id == sid:
                return stu
        return None

    def _sync_stu_buttons(self):
        stu = self._selected_student()
        deleted = stu is not None and is_deleted(stu)
        has_class = self._current_stu_class() is not None
        self.stu_edit_btn.set_enabled(stu is not None and not deleted)
        self.stu_add_btn.set_enabled(has_class)
        self.stu_import_btn.set_enabled(has_class)
        self.stu_del_btn.set_enabled(stu is not None)
        if deleted:
            self.stu_del_btn.set_text("恢复")
            self.stu_del_btn.set_colors(COLORS["card"], COLORS["success"],
                                        COLORS["success_soft"],
                                        border=COLORS["border"])
        else:
            self.stu_del_btn.set_text("删除")
            self.stu_del_btn.set_colors(COLORS["card"], COLORS["danger"],
                                        COLORS["danger_soft"],
                                        border=COLORS["border"])

    def add_student(self):
        if self._form_open():
            return
        cid = self._current_stu_class()
        if cid is None:
            return
        self._form = SeasonStuForm(self.win, cid, siblings=self._students,
                                   on_saved=self.on_changed,
                                   on_close=self._clear_form)

    def edit_student(self):
        if self._form_open():
            return
        stu = self._selected_student()
        if stu is None or is_deleted(stu):
            return
        self._form = SeasonStuForm(self.win, stu.sclass_id, original=stu,
                                   siblings=self._students,
                                   on_saved=self.on_changed,
                                   on_close=self._clear_form)

    def on_remove_student_click(self):
        stu = self._selected_student()
        if stu is None:
            return
        if is_deleted(stu):
            self.restore_student()
        else:
            self.delete_student()

    def delete_student(self):
        stu = self._selected_student()
        if stu is None or is_deleted(stu):
            return
        try:
            sign_n = SeasonSignMapper().count_by_stu(stu.sstu_id)
        except Exception as e:
            messagebox.showerror("读取失败", str(e), parent=self.win)
            return

        purge = False
        if sign_n:
            # 是 = 连签到一起清；否 = 只删学员；取消 = 什么都不做
            answer = messagebox.askyesnocancel(
                "删除学员",
                "确定删除学员「%s」吗？\n\n删除只是标记为无效，记录仍保留，"
                "勾选「显示已删除」可以恢复。\n\n该学员有 %d 条签到记录：\n"
                "· 选「是」连签到一起清除（不可恢复）\n"
                "· 选「否」只删除学员，保留签到记录" % (stu.stu_name, sign_n),
                icon="warning", parent=self.win)
            if answer is None:
                return
            purge = bool(answer)
        else:
            if not messagebox.askyesno(
                    "删除学员",
                    "确定删除学员「%s」吗？\n\n删除只是标记为无效，记录仍保留，"
                    "勾选「显示已删除」可以恢复。" % stu.stu_name,
                    icon="warning", default="no", parent=self.win):
                return

        try:
            if purge:
                SeasonSignMapper().delete_by_stu(stu.sstu_id)
            SeasonStuMapper().delete(stu.sstu_id)
        except Exception as e:
            messagebox.showerror("删除失败", str(e), parent=self.win)
            return
        message = "已删除学员 %s（可恢复）" % stu.stu_name
        if purge:
            message = "已删除学员 %s，并清除 %d 条签到记录" % (stu.stu_name, sign_n)
        self.on_changed(message)

    def restore_student(self):
        stu = self._selected_student()
        if stu is None or not is_deleted(stu):
            return
        try:
            SeasonStuMapper().restore(stu.sstu_id)
        except Exception as e:
            messagebox.showerror("恢复失败", str(e), parent=self.win)
            return
        self.on_changed("已恢复学员 %s" % stu.stu_name)

    def import_students(self):
        cid = self._current_stu_class()
        if cid is None:
            return
        dialog = ImportRegularDialog(self.win)
        dialog.wait()
        if not dialog.choice:
            return
        try:
            added, skipped = SeasonStuMapper().import_from_regular(
                cid, dialog.choice)
        except Exception as e:
            messagebox.showerror("导入失败", str(e), parent=self.win)
            return
        message = "从常规班导入 %d 人" % added
        if skipped:
            message += "，%d 人重名已跳过" % len(skipped)
        self.var_del_stu.set(False)
        self.on_changed(message)

    # ------------------------------------------------------------ 课次页
    def _build_lesson_page(self, page):
        bar = tk.Frame(page, bg=COLORS["bg"])
        bar.pack(fill=tk.X, pady=(0, 10))

        form_label(bar, "班级", 10, color=COLORS["muted"],
                   bg=COLORS["bg"]).pack(side=tk.LEFT, padx=(0, 8))
        self.lesson_class_box = ttk.Combobox(
            bar, textvariable=self.var_lesson_class, state="readonly",
            style="Class.TCombobox", width=24, values=[])
        self.lesson_class_box.pack(side=tk.LEFT)
        self.lesson_class_box.bind("<<ComboboxSelected>>",
                                   lambda _e: self.refresh_lessons())

        goto_text = "去点名 →"
        self.lesson_goto_btn = RoundedButton(
            bar, text=goto_text, command=self.goto_roll_call,
            width=button_width(goto_text, font(10), 104), height=38,
            bg=COLORS["card"], fg="#334155", hover_bg="#F8FAFC",
            border=COLORS["border"], radius=10, parent_bg=COLORS["bg"],
            font_obj=font(10))
        self.lesson_goto_btn.pack(side=tk.RIGHT)

        self.lesson_del_btn = RoundedButton(
            bar, text="删除", command=self.delete_lesson, width=78, height=38,
            bg=COLORS["card"], fg=COLORS["danger"], hover_bg=COLORS["danger_soft"],
            border=COLORS["border"], radius=10, parent_bg=COLORS["bg"],
            font_obj=font(10))
        self.lesson_del_btn.pack(side=tk.RIGHT, padx=(0, 8))

        self.lesson_edit_btn = RoundedButton(
            bar, text="编辑", command=self.edit_lesson, width=78, height=38,
            bg=COLORS["card"], fg="#334155", hover_bg="#F8FAFC",
            border=COLORS["border"], radius=10, parent_bg=COLORS["bg"],
            font_obj=font(10))
        self.lesson_edit_btn.pack(side=tk.RIGHT, padx=(0, 8))

        add_text = "＋ 新增课次"
        self.lesson_add_btn = RoundedButton(
            bar, text=add_text, command=self.add_lesson,
            width=button_width(add_text, font(10), 118), height=38, radius=10,
            parent_bg=COLORS["bg"], font_obj=font(10))
        self.lesson_add_btn.pack(side=tk.RIGHT, padx=(0, 8))

        self.lesson_tree = data_tree(page, self.COLUMNS_LESSON, height=8)
        self.lesson_tree.bind("<<TreeviewSelect>>",
                              lambda _e: self._sync_lesson_buttons())
        self.lesson_tree.bind("<Double-1>", lambda _e: self.edit_lesson())

        self.lesson_status = form_label(page, "", 9, color=COLORS["muted"],
                                        bg=COLORS["bg"])
        self.lesson_status.pack(anchor="w", pady=(10, 0))
        form_label(page, "每个课次就是「某天某个时间段的一次课」，点名按课次记录；"
                         "删除课次会连同该次课的签到一起删除",
                   9, color=COLORS["subtle"], bg=COLORS["bg"]).pack(anchor="w")

    def refresh_lesson_classes(self, target=None):
        try:
            classes = SeasonClassMapper().select_all(self.season)
        except Exception as e:
            logging.exception("读取假期班级失败")
            classes = []
            self.lesson_status.config(text="读取班级失败：%s" % e,
                                      fg=COLORS["danger"])
        self._lesson_cid_by_label = {}
        labels = []
        for cls in classes:
            label = (cls.class_name or "未命名").strip()
            if cls.class_type:
                label += " · " + cls.class_type.strip()
            if label in self._lesson_cid_by_label:
                label += " #%s" % cls.sclass_id
            labels.append(label)
            self._lesson_cid_by_label[label] = cls.sclass_id
        self.lesson_class_box.configure(values=labels)

        current = target if target in [c.sclass_id for c in classes] else None
        if current is None:
            current = self._lesson_cid_by_label.get(self.var_lesson_class.get())
        if current is None and labels:
            current = self._lesson_cid_by_label[labels[0]]
        if current is None:
            self.var_lesson_class.set("")
        else:
            self.var_lesson_class.set(
                next(l for l, v in self._lesson_cid_by_label.items() if v == current))

    def _current_lesson_class(self):
        return self._lesson_cid_by_label.get(self.var_lesson_class.get())

    def refresh_lessons(self):
        cid = self._current_lesson_class()
        self._lessons = []
        self._attended = {}
        if cid is None:
            self.lesson_tree.delete(*self.lesson_tree.get_children())
            self.lesson_status.config(
                text="还没有%s班，先到「班级」页新增一个" % season_title(self.season)[:2],
                fg=COLORS["warn"])
            self._sync_lesson_buttons()
            return
        try:
            self._lessons = SeasonLessonMapper().select_by_class(cid)
            self._attended = SeasonLessonMapper().attended_counts(cid)
            total = len(SeasonStuMapper().select_by_class(cid))
        except Exception as e:
            logging.exception("读取课次失败")
            self.lesson_tree.delete(*self.lesson_tree.get_children())
            self.lesson_status.config(text="读取课次失败：%s" % e, fg=COLORS["danger"])
            self._sync_lesson_buttons()
            return

        self.lesson_tree.delete(*self.lesson_tree.get_children())
        for idx, lesson in enumerate(self._lessons, start=1):
            attended = self._attended.get(lesson.lesson_id, 0)
            self.lesson_tree.insert(
                "", tk.END, iid=str(lesson.lesson_id),
                values=("第 %d 次" % idx, date_text(lesson.lesson_date),
                        lesson.lesson_time or "—", lesson.topic or "—",
                        "%d / %d" % (attended, total)),
                tags=("even" if idx % 2 == 0 else "odd",))
        self.lesson_status.config(
            text="共 %d 个课次" % len(self._lessons), fg=COLORS["muted"])
        self._sync_lesson_buttons()

    def _selected_lesson(self):
        selection = self.lesson_tree.selection()
        if not selection:
            return None
        lid = int(selection[0])
        for lesson in self._lessons:
            if lesson.lesson_id == lid:
                return lesson
        return None

    def _sync_lesson_buttons(self):
        has_class = self._current_lesson_class() is not None
        has_lesson = self._selected_lesson() is not None
        self.lesson_add_btn.set_enabled(has_class)
        self.lesson_edit_btn.set_enabled(has_lesson)
        self.lesson_del_btn.set_enabled(has_lesson)
        self.lesson_goto_btn.set_enabled(has_class)

    def add_lesson(self):
        if self._form_open():
            return
        cid = self._current_lesson_class()
        if cid is None:
            return
        self._form = SeasonLessonForm(self.win, cid, on_saved=self.on_changed,
                                      on_close=self._clear_form)

    def edit_lesson(self):
        if self._form_open():
            return
        lesson = self._selected_lesson()
        if lesson is None:
            return
        self._form = SeasonLessonForm(self.win, lesson.sclass_id,
                                      original=lesson,
                                      on_saved=self.on_changed,
                                      on_close=self._clear_form)

    def delete_lesson(self):
        lesson = self._selected_lesson()
        if lesson is None:
            return
        try:
            sign_n = SeasonLessonMapper().sign_count(lesson.lesson_id)
        except Exception as e:
            messagebox.showerror("读取失败", str(e), parent=self.win)
            return
        text = ("确定删除 %s %s 这次课吗？" % (
            date_text(lesson.lesson_date), lesson.lesson_time or ""))
        if sign_n:
            text += "\n\n该课次已有 %d 条签到记录，会一起删除且不可恢复。" % sign_n
        else:
            text += "\n\n这次课还没有签到记录。"
        if not messagebox.askyesno("删除课次", text, icon="warning",
                                   default="no", parent=self.win):
            return
        try:
            SeasonLessonMapper().delete(lesson.lesson_id)
        except Exception as e:
            messagebox.showerror("删除失败", str(e), parent=self.win)
            return
        self.on_changed("已删除课次 %s" % date_text(lesson.lesson_date))

    def goto_roll_call(self):
        """把主界面切到这次课，然后关掉设置窗口。"""
        cid = self._current_lesson_class()
        lesson = self._selected_lesson()
        if cid is None:
            return
        store = getattr(self.app, "season_last", None)
        if not isinstance(store, dict):
            store = {}
            self.app.season_last = store
        bucket = store.setdefault(self.season, {})
        bucket["class_id"] = cid
        if lesson is not None:
            bucket["lesson_id"] = lesson.lesson_id
        self._dirty = False   # 页面刷新交给下面这步，避免 close 里再刷一次
        self.close()
        self.app.refresh_current_page()

    # ------------------------------------------------------------ 公共
    def _clear_form(self):
        self._form = None

    def _form_open(self):
        form = self._form
        if form is None:
            return False
        try:
            if form.win.winfo_exists():
                form.win.lift()
                form.win.focus_force()
                return True
        except tk.TclError:
            pass
        self._form = None
        return False


# ---------------------------------------------------------------- 班级表单


class SeasonClassForm:
    """新增 / 编辑假期班级。名称为必填，同一季节内不允许重名。"""

    def __init__(self, parent, season, original=None, on_saved=None,
                 on_close=None):
        self.season = season
        self.original = original
        self.is_new = original is None
        self.on_saved = on_saved
        self.on_close = on_close

        self.win = tk.Toplevel(parent)
        self.win.title("新增班级" if self.is_new
                       else "编辑班级 · %s" % (original.class_name or ""))
        self.win.configure(bg=COLORS["bg"])
        self.win.resizable(False, False)
        self.win.transient(parent)

        self.var_name = tk.StringVar(
            value="" if self.is_new else (original.class_name or ""))
        self.var_type = tk.StringVar(
            value="" if self.is_new else (original.class_type or ""))
        self.var_hour = tk.StringVar(
            value=DEFAULT_HOUR if self.is_new else fmt_hour(original.class_hour))
        self.var_start = tk.StringVar(
            value="" if self.is_new else date_text(original.start_date))
        self.var_end = tk.StringVar(
            value="" if self.is_new else date_text(original.end_date))
        self.var_note = tk.StringVar(
            value="" if self.is_new else (original.note or ""))

        self._build()
        self.win.protocol("WM_DELETE_WINDOW", self.cancel)
        self.win.bind("<Escape>", lambda _e: self.cancel())
        self.win.bind("<Return>", lambda _e: self.save())
        try:
            self.win.grab_set()
        except tk.TclError:
            pass
        self.entries["班级名称 *"].focus_set()

    def _build(self):
        root = tk.Frame(self.win, bg=COLORS["bg"])
        root.pack(fill=tk.BOTH, expand=True, padx=24, pady=(20, 16))

        form_label(root, "新增%s班" % season_title(self.season)[:2] if self.is_new
                   else "编辑班级", 15, True, bg=COLORS["bg"]).pack(anchor="w")
        form_label(root, "带 * 的为必填项；班级名称在同一次假期里不能重复",
                   9, color=COLORS["muted"], bg=COLORS["bg"]).pack(
            anchor="w", pady=(3, 12))

        form = tk.Frame(root, bg=COLORS["bg"])
        form.pack(fill=tk.X)
        form.grid_columnconfigure(0, weight=1)

        self.entries = {}
        row = 0
        entry_name = form_entry(form, self.var_name, width=36)
        self.entries["班级名称 *"] = entry_name
        row = field_row(form, row, "班级名称 *", entry_name,
                        "例：2026 暑期 C++ 集训（1）")

        entry_type = form_entry(form, self.var_type, width=36)
        row = field_row(form, row, "课程内容", entry_type,
                        "例：C++ 三阶 / Python 入门，可留空")

        entry_hour = form_entry(form, self.var_hour, width=36)
        self.entries["每次课课时 *"] = entry_hour
        row = field_row(form, row, "每次课课时 *", entry_hour,
                        "点名时的默认课时，可临时改")

        entry_start = form_entry(form, self.var_start, width=36)
        self.entries["开班日期"] = entry_start
        row = field_row(form, row, "开班日期", entry_start,
                        "2026-07-06，也可只写 7-6")

        entry_end = form_entry(form, self.var_end, width=36)
        row = field_row(form, row, "结班日期", entry_end, "可留空")

        entry_note = form_entry(form, self.var_note, width=36)
        row = field_row(form, row, "备注", entry_note, "可留空")

        self.error = form_label(root, "", 9, color=COLORS["danger"], bg=COLORS["bg"])
        self.error.pack(anchor="w", pady=(10, 0))

        foot = tk.Frame(root, bg=COLORS["bg"])
        foot.pack(fill=tk.X, pady=(12, 0))
        RoundedButton(foot, text="保存", command=self.save, width=96, height=38,
                      radius=10, parent_bg=COLORS["bg"],
                      font_obj=font(10)).pack(side=tk.RIGHT)
        RoundedButton(foot, text="取消", command=self.cancel, width=96, height=38,
                      bg=COLORS["card"], fg="#334155", hover_bg="#F8FAFC",
                      border=COLORS["border"], radius=10, parent_bg=COLORS["bg"],
                      font_obj=font(10)).pack(side=tk.RIGHT, padx=(0, 10))

        center_on(self.win, self.win.master, ratio=3)

    def _fail(self, message, field=None):
        self.error.config(text=message)
        entry = self.entries.get(field) if field else None
        if entry is not None:
            entry.focus_set()

    def save(self):
        name = self.var_name.get().strip()
        if not name:
            return self._fail("请填写班级名称", "班级名称 *")
        if len(name) > 50:
            return self._fail("班级名称最多 50 个字符", "班级名称 *")
        try:
            duplicated = SeasonClassMapper().select_by_name(
                self.season, name,
                exclude_id=None if self.is_new else self.original.sclass_id)
        except Exception as e:
            return self._fail("检查班级名称失败：%s" % e)
        if duplicated is not None:
            return self._fail("「%s」已经有一个同名班级" % name, "班级名称 *")

        try:
            hour = float(self.var_hour.get().strip())
        except ValueError:
            return self._fail("每次课课时要填数字，例如 2.5", "每次课课时 *")
        if not (0 < hour <= 24):
            return self._fail("每次课课时应在 0～24 之间", "每次课课时 *")

        try:
            start = normalize_date(self.var_start.get())
            end = normalize_date(self.var_end.get())
        except ValueError:
            return self._fail("日期格式应为 2026-07-06（也可以只写 7-6）", "开班日期")
        if start and end and start > end:
            return self._fail("结班日期不能早于开班日期", "结班日期")

        cls = SeasonClass(
            sclass_id=None if self.is_new else self.original.sclass_id,
            season=self.season,
            class_name=name,
            class_type=self.var_type.get().strip() or None,
            class_hour=hour,
            start_date=start or None,
            end_date=end or None,
            note=self.var_note.get().strip() or None,
            state=ALIVE,
        )
        try:
            if self.is_new:
                SeasonClassMapper().insert(cls)
                message = "已新增班级 %s" % name
            else:
                SeasonClassMapper().update(cls)
                message = "已更新班级 %s" % name
        except Exception as e:
            return self._fail("保存失败：%s" % e)

        self._close()
        if self.on_saved is not None:
            self.on_saved(message)

    def cancel(self):
        self._close()

    def _close(self):
        try:
            self.win.grab_release()
        except tk.TclError:
            pass
        self.win.destroy()
        if self.on_close is not None:
            self.on_close()


# ---------------------------------------------------------------- 学员表单


class SeasonStuForm:
    """新增 / 编辑假期学员。同班重名会拦下来：签到记录靠姓名认人。"""

    def __init__(self, parent, sclass_id, original=None, siblings=None,
                 on_saved=None, on_close=None):
        self.sclass_id = sclass_id
        self.original = original
        self.is_new = original is None
        self.siblings = list(siblings or [])
        self.on_saved = on_saved
        self.on_close = on_close

        self.win = tk.Toplevel(parent)
        self.win.title("新增学员" if self.is_new else "编辑学员 %s"
                       % (original.stu_name or ""))
        self.win.configure(bg=COLORS["bg"])
        self.win.transient(parent)

        self.var_name = tk.StringVar(
            value="" if self.is_new else (original.stu_name or ""))
        self.var_phone = tk.StringVar(
            value="" if self.is_new else (original.phone or ""))
        self.var_note = tk.StringVar(
            value="" if self.is_new else (original.note or ""))

        self._build()
        fit_dialog_to_screen(self.win)
        center_on(self.win, self.win.master, ratio=3)
        self.win.protocol("WM_DELETE_WINDOW", self.cancel)
        self.win.bind("<Escape>", lambda _e: self.cancel())
        self.win.bind("<Return>", lambda _e: self.save())
        try:
            self.win.grab_set()
        except tk.TclError:
            pass
        self.entries["姓名 *"].focus_set()

    def _build(self):
        root = tk.Frame(self.win, bg=COLORS["bg"])
        root.pack(fill=tk.BOTH, expand=True, padx=24, pady=(20, 16))

        form_label(root, "新增学员" if self.is_new else "编辑学员", 15, True,
                   bg=COLORS["bg"]).pack(anchor="w")
        form_label(root, "同一班级内不要重名，否则签到记录会混在一起",
                   9, color=COLORS["muted"], bg=COLORS["bg"]).pack(
            anchor="w", pady=(3, 12))

        form = tk.Frame(root, bg=COLORS["bg"])
        form.pack(fill=tk.X)
        form.grid_columnconfigure(0, weight=1)

        self.entries = {}
        row = 0
        entry_name = form_entry(form, self.var_name, width=32)
        self.entries["姓名 *"] = entry_name
        row = field_row(form, row, "姓名 *", entry_name, "必填")

        entry_phone = form_entry(form, self.var_phone, width=32)
        row = field_row(form, row, "手机号", entry_phone, "可留空")

        entry_note = form_entry(form, self.var_note, width=32)
        row = field_row(form, row, "备注", entry_note,
                        "例：家长接送 / 需午餐")

        self.error = form_label(root, "", 9, color=COLORS["danger"], bg=COLORS["bg"])
        self.error.pack(anchor="w", pady=(10, 0))

        foot = tk.Frame(root, bg=COLORS["bg"])
        foot.pack(fill=tk.X, pady=(12, 0))
        RoundedButton(foot, text="保存", command=self.save, width=96, height=38,
                      radius=10, parent_bg=COLORS["bg"],
                      font_obj=font(10)).pack(side=tk.RIGHT)
        RoundedButton(foot, text="取消", command=self.cancel, width=96, height=38,
                      bg=COLORS["card"], fg="#334155", hover_bg="#F8FAFC",
                      border=COLORS["border"], radius=10, parent_bg=COLORS["bg"],
                      font_obj=font(10)).pack(side=tk.RIGHT, padx=(0, 10))

        center_on(self.win, self.win.master, ratio=3)

    def _fail(self, message, field=None):
        self.error.config(text=message)
        entry = self.entries.get(field) if field else None
        if entry is not None:
            entry.focus_set()

    def save(self):
        name = self.var_name.get().strip()
        if not name:
            return self._fail("请填写学员姓名", "姓名 *")
        if len(name) > 30:
            return self._fail("姓名最多 30 个字符", "姓名 *")

        for other in self.siblings:
            if self.original is not None and other.sstu_id == self.original.sstu_id:
                continue
            if is_deleted(other):
                continue
            if (other.stu_name or "").strip() == name:
                return self._fail("这个班已有同名学员 %s" % name, "姓名 *")

        phone = self.var_phone.get().strip()
        if phone and not all(ch.isdigit() or ch in "+- " for ch in phone):
            return self._fail("手机号只能包含数字、+、- 和空格", "手机号")

        stu = SeasonStu(
            sstu_id=None if self.is_new else self.original.sstu_id,
            sclass_id=self.sclass_id,
            stu_name=name,
            phone=phone or None,
            note=self.var_note.get().strip() or None,
            state=ALIVE,
        )
        mapper = SeasonStuMapper()
        try:
            if self.is_new:
                deleted = mapper.select_deleted_by_name(self.sclass_id, name)
                if deleted is not None:
                    stu.sstu_id = deleted.sstu_id
                    mapper.update(stu, old_name=deleted.stu_name)
                    message = "已恢复此前删除的学员 %s（历史签到仍在）" % name
                else:
                    mapper.insert(stu)
                    message = "已新增学员 %s" % name
            else:
                mapper.update(stu, old_name=self.original.stu_name)
                message = "已更新学员 %s" % name
        except Exception as e:
            return self._fail("保存失败：%s" % e)

        self._close()
        if self.on_saved is not None:
            self.on_saved(message)

    def cancel(self):
        self._close()

    def _close(self):
        try:
            self.win.grab_release()
        except tk.TclError:
            pass
        self.win.destroy()
        if self.on_close is not None:
            self.on_close()


# ---------------------------------------------------------------- 课次表单


class SeasonLessonForm:
    """新增 / 编辑课次：一条记录 = 某天某个时间段的一次课。"""

    def __init__(self, parent, sclass_id, original=None, on_saved=None,
                 on_close=None):
        self.sclass_id = sclass_id
        self.original = original
        self.is_new = original is None
        self.on_saved = on_saved
        self.on_close = on_close

        self.win = tk.Toplevel(parent)
        self.win.title("新增课次" if self.is_new else "编辑课次")
        self.win.configure(bg=COLORS["bg"])
        self.win.resizable(False, False)
        self.win.transient(parent)

        self.var_date = tk.StringVar(
            value=datetime.now().strftime("%Y-%m-%d") if self.is_new
            else date_text(original.lesson_date))
        self.var_time = tk.StringVar(
            value="" if self.is_new else (original.lesson_time or ""))
        self.var_topic = tk.StringVar(
            value="" if self.is_new else (original.topic or ""))
        self.var_note = tk.StringVar(
            value="" if self.is_new else (original.note or ""))

        self._build()
        self.win.protocol("WM_DELETE_WINDOW", self.cancel)
        self.win.bind("<Escape>", lambda _e: self.cancel())
        self.win.bind("<Return>", lambda _e: self.save())
        try:
            self.win.grab_set()
        except tk.TclError:
            pass
        self.entries["上课日期 *"].focus_set()

    def _build(self):
        root = tk.Frame(self.win, bg=COLORS["bg"])
        root.pack(fill=tk.BOTH, expand=True, padx=24, pady=(20, 16))

        form_label(root, "新增课次" if self.is_new else "编辑课次", 15, True,
                   bg=COLORS["bg"]).pack(anchor="w")
        form_label(root, "假期班上课时间不固定，一次课就加一条；点名前先建好当天的课次",
                   9, color=COLORS["muted"], bg=COLORS["bg"]).pack(
            anchor="w", pady=(3, 12))

        form = tk.Frame(root, bg=COLORS["bg"])
        form.pack(fill=tk.X)
        form.grid_columnconfigure(0, weight=1)

        self.entries = {}
        row = 0
        entry_date = form_entry(form, self.var_date, width=32)
        self.entries["上课日期 *"] = entry_date
        row = field_row(form, row, "上课日期 *", entry_date,
                        "2026-07-08，也可只写 7-8")

        entry_time = form_entry(form, self.var_time, width=32)
        row = field_row(form, row, "时间段", entry_time,
                        "例：09:00-12:00，可留空")

        entry_topic = form_entry(form, self.var_topic, width=32)
        row = field_row(form, row, "本次内容", entry_topic,
                        "例：循环与数组，可留空")

        entry_note = form_entry(form, self.var_note, width=32)
        row = field_row(form, row, "备注", entry_note, "可留空")

        self.error = form_label(root, "", 9, color=COLORS["danger"], bg=COLORS["bg"])
        self.error.pack(anchor="w", pady=(10, 0))

        foot = tk.Frame(root, bg=COLORS["bg"])
        foot.pack(fill=tk.X, pady=(12, 0))
        RoundedButton(foot, text="保存", command=self.save, width=96, height=38,
                      radius=10, parent_bg=COLORS["bg"],
                      font_obj=font(10)).pack(side=tk.RIGHT)
        RoundedButton(foot, text="取消", command=self.cancel, width=96, height=38,
                      bg=COLORS["card"], fg="#334155", hover_bg="#F8FAFC",
                      border=COLORS["border"], radius=10, parent_bg=COLORS["bg"],
                      font_obj=font(10)).pack(side=tk.RIGHT, padx=(0, 10))

        center_on(self.win, self.win.master, ratio=3)

    def _fail(self, message, field=None):
        self.error.config(text=message)
        entry = self.entries.get(field) if field else None
        if entry is not None:
            entry.focus_set()

    def save(self):
        try:
            lesson_date = normalize_date(self.var_date.get())
        except ValueError:
            return self._fail("日期格式应为 2026-07-08（也可以只写 7-8）",
                              "上课日期 *")
        if not lesson_date:
            return self._fail("请填写上课日期", "上课日期 *")

        # 同一天同一时间段重复建课次，点名时会分不清是哪一次，先拦下来
        time_text = self.var_time.get().strip()
        try:
            siblings = SeasonLessonMapper().select_by_class(self.sclass_id)
        except Exception:
            siblings = []
        for other in siblings:
            if self.original is not None and other.lesson_id == self.original.lesson_id:
                continue
            if date_text(other.lesson_date) != lesson_date:
                continue
            if (other.lesson_time or "").strip() == time_text:
                return self._fail(
                    "%s %s 已经有课次了，换个时间段或改日期" % (lesson_date, time_text or ""),
                    "上课日期 *")

        lesson = SeasonLesson(
            lesson_id=None if self.is_new else self.original.lesson_id,
            sclass_id=self.sclass_id,
            lesson_date=lesson_date,
            lesson_time=self.var_time.get().strip() or None,
            topic=self.var_topic.get().strip() or None,
            note=self.var_note.get().strip() or None,
        )
        try:
            if self.is_new:
                SeasonLessonMapper().insert(lesson)
                message = "已新增课次 %s" % lesson_date
            else:
                SeasonLessonMapper().update(lesson)
                message = "已更新课次 %s" % lesson_date
        except Exception as e:
            return self._fail("保存失败：%s" % e)

        self._close()
        if self.on_saved is not None:
            self.on_saved(message)

    def cancel(self):
        self._close()

    def _close(self):
        try:
            self.win.grab_release()
        except tk.TclError:
            pass
        self.win.destroy()
        if self.on_close is not None:
            self.on_close()


# ---------------------------------------------------------------- 从常规班导入


class ImportRegularDialog:
    """选一个常规班，把它的在读学生复制进当前假期班。"""

    def __init__(self, parent):
        self.choice = None

        self.win = tk.Toplevel(parent)
        self.win.title("从常规班导入学员")
        self.win.configure(bg=COLORS["bg"])
        self.win.resizable(False, False)
        self.win.transient(parent)

        root = tk.Frame(self.win, bg=COLORS["bg"])
        root.pack(fill=tk.BOTH, expand=True, padx=26, pady=(20, 16))

        form_label(root, "从常规班导入学员", 15, True, bg=COLORS["bg"]).pack(
            anchor="w")
        form_label(root, "会把所选班级的在读学生复制过来，同名的自动跳过；"
                         "导入后仍可单独增删",
                   9, color=COLORS["muted"], bg=COLORS["bg"]).pack(
            anchor="w", pady=(3, 14))

        self._labels = {}
        labels = []
        try:
            classes = TClassMapper().select_all()
            counts = TStuMapper().counts_by_class()
        except Exception as e:
            classes, counts = [], {}
            form_label(root, "读取常规班级失败：%s" % e, 9,
                       color=COLORS["danger"], bg=COLORS["bg"]).pack(anchor="w")
        for cls in classes:
            label = "%s · %s %s（%d 人）" % (
                cls.class_id, cls.class_day or "", cls.class_type or "",
                counts.get(cls.class_id, 0))
            label = label.replace("  ", " ").strip()
            labels.append(label)
            self._labels[label] = cls.class_id

        self.var_class = tk.StringVar()
        self.box = ttk.Combobox(root, textvariable=self.var_class, values=labels,
                                state="readonly", style="Class.TCombobox",
                                width=34)
        self.box.pack(fill=tk.X)
        if labels:
            self.box.current(0)

        foot = tk.Frame(root, bg=COLORS["bg"])
        foot.pack(fill=tk.X, pady=(18, 0))
        RoundedButton(foot, text="导入", command=self.ok, width=96, height=38,
                      radius=10, parent_bg=COLORS["bg"],
                      font_obj=font(10)).pack(side=tk.RIGHT)
        RoundedButton(foot, text="取消", command=self.cancel, width=96, height=38,
                      bg=COLORS["card"], fg="#334155", hover_bg="#F8FAFC",
                      border=COLORS["border"], radius=10, parent_bg=COLORS["bg"],
                      font_obj=font(10)).pack(side=tk.RIGHT, padx=(0, 10))

        center_on(self.win, parent, ratio=3)
        self.win.protocol("WM_DELETE_WINDOW", self.cancel)
        self.win.bind("<Escape>", lambda _e: self.cancel())
        try:
            self.win.grab_set()
        except tk.TclError:
            pass

    def wait(self):
        self.win.wait_window(self.win)

    def ok(self):
        self.choice = self._labels.get(self.var_class.get())
        if self.choice is None:
            return
        self._close()

    def cancel(self):
        self.choice = None
        self._close()

    def _close(self):
        try:
            self.win.grab_release()
        except tk.TclError:
            pass
        self.win.destroy()
