"""暑假 / 寒假课程：按课次点名的页面（主窗口内）。

假期班和常规班最大的差别是上课日期不固定，所以这里不用「今天该点哪次名」，
而是让老师在下拉框里选一次课（第 N 次课 · 07-08 09:00-12:00），再给这次课
点谁来了。每次课的出勤单独存在 t_season_sign 里，互不覆盖。

点一下就写库（upsert），切课次、切页、改外观重建页面都不会丢，省掉「保存」
这一步——假期课一天两三次，忘了点保存的代价比多写一次 SQL 大得多。
"""

import logging
import os
import re
import tkinter as tk
from datetime import date, datetime
from tkinter import messagebox, ttk

from mapper.t_season_mapper import (SeasonClassMapper, SeasonLessonMapper,
                                    SeasonSignMapper, SeasonStuMapper,
                                    ensure_schema)
from ui_theme import COLORS, RoundedButton, button_width, font

SEASON_TITLES = {
    "summer": ("暑假课程", "☀"),
    "winter": ("寒假课程", "❄"),
}
SEASON_SUBTITLES = {
    "summer": "暑假班上课日期不固定，先建班和课次，再按课次点名",
    "winter": "寒假班上课日期不固定，先建班和课次，再按课次点名",
}
DEFAULT_HOUR = 2.5


def app_dir():
    """程序所在目录：源码运行取项目根，打包后取 exe 所在目录。"""
    import sys
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def fmt_hour(value):
    """课时显示：2.0 → 2，2.5 → 2.5。"""
    if value in (None, ""):
        return ""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    return str(int(number)) if number == int(number) else ("%g" % number)


def date_of(lesson):
    value = getattr(lesson, "lesson_date", None)
    if value in (None, ""):
        return ""
    if hasattr(value, "strftime"):
        return value.strftime("%Y-%m-%d")
    return str(value)[:10]


def short_date(lesson):
    """课次下拉里的短日期：2026-07-08 → 07-08。"""
    text = date_of(lesson)
    return text[5:] if len(text) == 10 else text


def lesson_label(index, lesson):
    """下拉里只放必要信息：第几次、日期、时间段；内容在页面上那行显示。"""
    bits = ["第%d次" % index, short_date(lesson)]
    if (lesson.lesson_time or "").strip():
        bits.append(lesson.lesson_time.strip())
    return " · ".join([b for b in bits if b])


def pick_default_lesson(lessons, today=None):
    """默认选中哪次课：优先今天，其次最近的一次（补录/预告都顺手）。"""
    if not lessons:
        return None
    today = today or date.today()
    today_text = today.strftime("%Y-%m-%d")

    def key(item):
        return (date_of(item), (item.lesson_time or ""))

    same_day = [l for l in lessons if date_of(l) == today_text]
    if same_day:
        return sorted(same_day, key=key)[-1]
    past = [l for l in lessons if date_of(l) <= today_text]
    if past:
        return sorted(past, key=key)[-1]
    return sorted(lessons, key=key)[0]


class SeasonPage:
    """一次渲染一整页，切换班级/课次只更新表格，不重建页面。"""

    COLUMNS = ("学生姓名", "课时数", "签到状态", "操作")

    def __init__(self, app, season):
        self.app = app
        self.season = season
        self.title = SEASON_TITLES.get(season, ("假期课程", "◇"))[0]

        self.classes = []
        self.lessons = []
        self.students = []
        self._cid_by_label = {}
        self._lid_by_label = {}
        self._lesson_by_id = {}
        self._signs = {}
        self._state = {}   # sstu_id(str) -> "1" 已签到 / "0" 未签到
        self._hours = {}   # sstu_id(str) -> 课时
        self._forms = {}

        store = getattr(app, "season_last", None)
        if not isinstance(store, dict):
            store = {}
            app.season_last = store
        self._store = store.setdefault(season, {})

        self.var_class = tk.StringVar()
        self.var_lesson = tk.StringVar()

        self._build()
        self.reload_data()

    # ------------------------------------------------------------ 构建
    def _build(self):
        app = self.app
        app.set_active_nav(self.season)
        page = app.bg.new_page(padx=30, pady=24)

        page.header(
            self.title, SEASON_SUBTITLES.get(self.season, ""),
            links=[
                ("刷新", self.reload_data),
                ("＋ 新建班级", self.add_class),
                ("学员管理", self.open_students),
                ("课次管理", self.open_lessons),
            ],
        )

        c1, self.stu_count_label = app._stat_card("本班学员", COLORS["primary"])
        c2, self.lesson_count_label = app._stat_card("课次总数", COLORS["success"])
        c3, self.signed_count_label = app._stat_card("本次已签到", COLORS["warn"])
        page.stat_row([c1, c2, c3])

        # 工具条：班级 + 课次 + 两个动作
        toolbar = app._card()
        bar = tk.Frame(toolbar, bg=COLORS["card"])
        bar.pack(fill=tk.X, padx=16, pady=12)

        tk.Label(bar, text="班级", bg=COLORS["card"], fg=COLORS["muted"],
                 font=font(10)).pack(side=tk.LEFT, padx=(0, 8))
        self.class_box = ttk.Combobox(
            bar, textvariable=self.var_class, state="readonly",
            style="Class.TCombobox", width=18, values=[])
        self.class_box.pack(side=tk.LEFT)
        self.class_box.bind("<<ComboboxSelected>>", self.on_class_change)

        tk.Label(bar, text="课次", bg=COLORS["card"], fg=COLORS["muted"],
                 font=font(10)).pack(side=tk.LEFT, padx=(16, 8))
        self.lesson_box = ttk.Combobox(
            bar, textvariable=self.var_lesson, state="readonly",
            style="Class.TCombobox", width=24, values=[])
        self.lesson_box.pack(side=tk.LEFT)
        self.lesson_box.bind("<<ComboboxSelected>>", self.on_lesson_change)

        all_text = "全部签到"
        self.all_btn = RoundedButton(
            bar, text=all_text, command=self.sign_all,
            width=button_width(all_text, font(10), 96), height=38,
            radius=10, parent_bg=COLORS["card"], font_obj=font(10))
        self.all_btn.pack(side=tk.RIGHT)

        export_text = "导出记录"
        self.export_btn = RoundedButton(
            bar, text=export_text, command=self.export_record,
            width=button_width(export_text, font(10), 104), height=38,
            bg=COLORS["card"], fg="#334155", hover_bg="#F8FAFC",
            border=COLORS["border"], radius=10, parent_bg=COLORS["card"],
            font_obj=font(10))
        self.export_btn.pack(side=tk.RIGHT, padx=(0, 10))

        page.block(toolbar, gap=14)

        # 上下文行：这次课是什么、点名进度、保存状态
        context = tk.Frame(app.bg, bg=COLORS["bg"])
        self.context_label = tk.Label(
            context, text="", bg=COLORS["bg"], fg=COLORS["muted"], font=font(10))
        self.context_label.pack(side=tk.LEFT)
        self.save_label = tk.Label(
            context, text="", bg=COLORS["bg"], fg=COLORS["subtle"], font=font(9))
        self.save_label.pack(side=tk.RIGHT)
        page.block(context, gap=10)

        # 点名表
        card = app._card()
        shell = tk.Frame(card, bg=COLORS["card"])
        shell.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)

        self.tree = ttk.Treeview(
            shell, columns=self.COLUMNS, show="headings",
            height=8, style="Data.Treeview")
        self.tree.heading("学生姓名", text="学生姓名", anchor="w")
        self.tree.heading("课时数", text="课时数", anchor="center")
        self.tree.heading("签到状态", text="签到状态", anchor="center")
        self.tree.heading("操作", text="操作", anchor="center")
        self.tree.column("学生姓名", anchor="w", stretch=True, width=360)
        self.tree.column("课时数", anchor="center", stretch=False, width=160)
        self.tree.column("签到状态", anchor="center", stretch=False, width=170)
        self.tree.column("操作", anchor="center", stretch=False, width=150)
        self.tree.tag_configure("signed", background=COLORS["success_soft"],
                                foreground="#15803D")
        self.tree.tag_configure("odd", background=COLORS["card"],
                                foreground=COLORS["text"])
        self.tree.tag_configure("even", background=COLORS["row_alt"],
                                foreground=COLORS["text"])
        self.tree.bind("<Button-1>", self.toggle_sign_in)
        self.tree.bind("<Double-1>", self.on_double_click_hour)
        self.tree.bind("<Motion>", self._on_tree_motion)

        scrollbar = ttk.Scrollbar(shell, orient="vertical",
                                  command=self.tree.yview,
                                  style="Slim.Vertical.TScrollbar")
        self.tree.configure(yscrollcommand=scrollbar.set)
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y, padx=(4, 0))

        # 空态提示：卡片正中，随数据有无自动显隐
        self.hint = tk.Label(card, text="", bg=COLORS["card"], fg=COLORS["muted"],
                             font=font(11), justify="center")
        self.hint_btn = None

        page.block(card, gap=12, expand=True)
        page.layout()

    def _show_hint(self, lines, button=None):
        """空态：一段说明 +（可选）一个主按钮，直接摆在表格卡片中间。"""
        if self.hint_btn is not None:
            self.hint_btn.destroy()
            self.hint_btn = None
        if not lines:
            self.hint.place_forget()
            return
        self.hint.configure(text="\n".join(lines))
        self.hint.place(relx=0.5, rely=0.42, anchor="center")
        if button is not None:
            text, command = button
            self.hint_btn = RoundedButton(
                self.hint.master, text=text, command=command,
                width=button_width(text, font(10), 140), height=40,
                radius=10, parent_bg=COLORS["card"], font_obj=font(10))
            self.hint_btn.place(relx=0.5, rely=0.62, anchor="center")

    # ------------------------------------------------------------ 数据
    def _selected_class(self):
        cid = self._cid_by_label.get(self.var_class.get())
        for c in self.classes:
            if c.sclass_id == cid:
                return c
        return None

    def _selected_lesson(self):
        lid = self._lid_by_label.get(self.var_lesson.get())
        return self._lesson_by_id.get(lid)

    def reload_data(self):
        """重读班级、课次、学员、签到；保持当前选择（选择存在 app.season_last）。"""
        self.students = []
        self.lessons = []
        self._signs = {}
        self._state = {}
        self._hours = {}
        try:
            ensure_schema()
            self.classes = SeasonClassMapper().select_all(self.season)
        except Exception as e:
            logging.exception("读取假期班级失败")
            self.classes = []
            self._render_empty("读取假期班级失败：%s" % e)
            self._set_tools_enabled(False)
            return False

        self._cid_by_label = {}
        labels = []
        for c in self.classes:
            # 下拉只放班级名（老师自己起的，一眼能认），课程类型在设置窗口里看
            label = (c.class_name or "未命名班级").strip()
            # 理论上班级名不重名，真出现重名时补上编号，避免选错
            if label in self._cid_by_label:
                label = "%s #%s" % (label, c.sclass_id)
            labels.append(label)
            self._cid_by_label[label] = c.sclass_id
        self.class_box.configure(values=labels)

        target = self._store.get("class_id")
        if target not in [c.sclass_id for c in self.classes]:
            target = self.classes[0].sclass_id if self.classes else None
        if target is None:
            self.var_class.set("")
            self._set_tools_enabled(False)
            self._render_empty(
                ["还没有%s班" % self.title[:2],
                 "点右上「＋ 新建班级」建一个，再添加学员和课次"],
                button=("＋ 新建班级", self.add_class))
            self._update_labels()
            return False

        self._store["class_id"] = target
        self.var_class.set(next(l for l, v in self._cid_by_label.items() if v == target))

        self._load_lessons(target)
        self._load_students(target)
        self._render_rows()
        self._update_labels()
        self._set_tools_enabled(True)
        return True

    def _load_lessons(self, cid):
        try:
            self.lessons = SeasonLessonMapper().select_by_class(cid)
        except Exception as e:
            logging.exception("读取课次失败")
            self.lessons = []
            self._render_empty(["读取课次失败：%s" % e])
            return

        self._lid_by_label = {}
        self._lesson_by_id = {}
        labels = []
        for idx, lesson in enumerate(self.lessons, start=1):
            label = lesson_label(idx, lesson)
            if label in self._lid_by_label:
                label = "%s #%s" % (label, lesson.lesson_id)
            labels.append(label)
            self._lid_by_label[label] = lesson.lesson_id
            self._lesson_by_id[lesson.lesson_id] = lesson
        self.lesson_box.configure(values=labels)

        target = self._store.get("lesson_id")
        if target not in self._lesson_by_id:
            picked = pick_default_lesson(self.lessons)
            target = picked.lesson_id if picked else None
            self._store["lesson_id"] = target
        if target is None:
            self.var_lesson.set("")
            self._signs = {}
            self._state = {}
            self._hours = {}
            return

        self.var_lesson.set(
            next(l for l, v in self._lid_by_label.items() if v == target))
        self._load_signs(target)

    def _load_signs(self, lesson_id):
        try:
            self._signs = SeasonSignMapper().select_by_lesson(lesson_id)
        except Exception as e:
            logging.exception("读取签到失败")
            self._signs = {}
            self.save_label.config(text="读取签到失败：%s" % e, fg=COLORS["danger"])

    def _load_students(self, cid):
        try:
            self.students = SeasonStuMapper().select_by_class(cid)
        except Exception as e:
            logging.exception("读取假期学员失败")
            self.students = []
            self._render_empty(["读取学员失败：%s" % e])

    def _default_hour(self):
        cls = self._selected_class()
        hour = getattr(cls, "class_hour", None)
        try:
            return float(hour) if hour not in (None, "") else DEFAULT_HOUR
        except (TypeError, ValueError):
            return DEFAULT_HOUR

    # ------------------------------------------------------------ 渲染
    def _render_empty(self, lines, button=None):
        self.tree.delete(*self.tree.get_children())
        self._show_hint(lines, button)
        if self.context_label is not None:
            self.context_label.config(text="")
        self.save_label.config(text="")

    def _render_rows(self):
        self.tree.delete(*self.tree.get_children())
        self._state = {}
        self._hours = {}
        if not self.students:
            cls = self._selected_class()
            if cls is not None:
                self._render_empty(
                    ["这个班还没有学员",
                     "点右上「学员管理」逐个添加，也可以直接从常规班导入"],
                    button=("学员管理", self.open_students))
            return
        if not self._selected_lesson():
            self._render_empty(
                ["这个班还没有课次",
                 "假期班的上课日期不固定，先按实际日期建课次（一次课一条）"],
                button=("＋ 新建课次", self.add_lesson))
            return

        self._show_hint([])
        default_hour = self._default_hour()
        for idx, stu in enumerate(self.students):
            sid = str(stu.sstu_id)
            record = self._signs.get(stu.sstu_id)
            signed = bool(record) and str(record.sign_state) == "1"
            self._state[sid] = "1" if signed else "0"
            hour = default_hour
            if record is not None and record.sign_hour not in (None, ""):
                try:
                    hour = float(record.sign_hour)
                except (TypeError, ValueError):
                    hour = default_hour
            self._hours[sid] = hour
            self._insert_row(sid, stu.stu_name or "—", idx, signed)

    def _insert_row(self, sid, name, idx, signed):
        if signed:
            tag = "signed"
        else:
            tag = "even" if idx % 2 else "odd"
        self.tree.insert(
            "", tk.END, iid=sid,
            values=(name, fmt_hour(self._hours.get(sid, self._default_hour())),
                    "已签到" if signed else "未签到",
                    "✕  撤销" if signed else "✓  签到"),
            tags=(tag,))

    def _apply_row(self, sid):
        """按内存状态刷新一行（不重排、不重建，保持滚动位置）。"""
        stu = next((s for s in self.students if str(s.sstu_id) == sid), None)
        if stu is None:
            return
        signed = self._state.get(sid) == "1"
        idx = self.students.index(stu)
        tag = "signed" if signed else ("even" if idx % 2 else "odd")
        self.tree.item(
            sid,
            values=(stu.stu_name or "—", fmt_hour(self._hours.get(sid, "")),
                    "已签到" if signed else "未签到",
                    "✕  撤销" if signed else "✓  签到"),
            tags=(tag,))

    def _update_labels(self):
        total = len(self.students)
        signed = sum(1 for v in self._state.values() if v == "1")
        self.stu_count_label.config(text=str(total))
        self.lesson_count_label.config(text=str(len(self.lessons)))
        self.signed_count_label.config(text=str(signed))

        lesson = self._selected_lesson()
        if lesson is None:
            self.context_label.config(text="")
            return
        idx = self.lessons.index(lesson) + 1 if lesson in self.lessons else 0
        bits = ["第 %d 次课" % idx, date_of(lesson)]
        if (lesson.lesson_time or "").strip():
            bits.append(lesson.lesson_time.strip())
        if (lesson.topic or "").strip():
            bits.append("内容：" + lesson.topic.strip())
        bits.append("已签到 %d / %d" % (signed, total))
        self.context_label.config(text="    ·    ".join(bits))

    def _set_tools_enabled(self, enabled):
        self.all_btn.set_enabled(enabled)
        # 主按钮禁用时底色也要退成灰，否则蓝底灰字看着像还能点
        if enabled:
            self.all_btn.set_colors(COLORS["primary"], "#FFFFFF")
        else:
            self.all_btn.set_colors(COLORS["border"], COLORS["subtle"])
        self.export_btn.set_enabled(enabled)
        try:
            self.lesson_box.configure(state="readonly" if enabled else "disabled")
            self.class_box.configure(state="readonly" if enabled else "disabled")
        except tk.TclError:
            pass

    def _flash_saved(self, text=None):
        self.save_label.config(
            text=text or ("已保存 %s" % datetime.now().strftime("%H:%M:%S")),
            fg=COLORS["success"])

    # ------------------------------------------------------------ 交互
    def on_class_change(self, _event=None):
        cid = self._cid_by_label.get(self.var_class.get())
        if cid is None:
            return
        self._store["class_id"] = cid
        # 换班后原来选的课次不属于这个班，交给 auto pick
        self._store["lesson_id"] = None
        self._load_lessons(cid)
        self._load_students(cid)
        self._render_rows()
        self._update_labels()
        self.save_label.config(text="")

    def on_lesson_change(self, _event=None):
        lesson = self._selected_lesson()
        if lesson is None:
            return
        self._store["lesson_id"] = lesson.lesson_id
        self._load_signs(lesson.lesson_id)
        self._render_rows()
        self._update_labels()
        self.save_label.config(text="")

    def _persist(self, sid, prev_state=None):
        """把一行写库；失败时回滚内存状态并提示，不静默丢数据。"""
        lesson = self._selected_lesson()
        cls = self._selected_class()
        if lesson is None or cls is None:
            return False
        stu = next((s for s in self.students if str(s.sstu_id) == sid), None)
        if stu is None:
            return False
        try:
            SeasonSignMapper().upsert(
                lesson.lesson_id, cls.sclass_id, stu.sstu_id, stu.stu_name,
                fmt_hour(self._hours.get(sid, self._default_hour())),
                self._state.get(sid, "0"),
                datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
            self._flash_saved()
            return True
        except Exception as e:
            logging.exception("保存假期签到失败")
            if prev_state is not None:
                self._state[sid] = prev_state
                self._apply_row(sid)
            self.save_label.config(text="保存失败：%s" % e, fg=COLORS["danger"])
            return False

    def toggle_sign_in(self, event):
        sid = self.tree.identify_row(event.y)
        if not sid:
            return
        column = self.tree.identify_column(event.x)
        try:
            if self.tree.heading(column)["text"] != "操作":
                return
        except tk.TclError:
            return
        if not self._selected_lesson():
            return
        prev = self._state.get(sid, "0")
        self._state[sid] = "0" if prev == "1" else "1"
        self._apply_row(sid)
        self._persist(sid, prev_state=prev)
        self._update_labels()

    def sign_all(self):
        lesson = self._selected_lesson()
        cls = self._selected_class()
        if lesson is None or cls is None or not self.students:
            return
        rows, now = [], datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        for stu in self.students:
            sid = str(stu.sstu_id)
            self._state[sid] = "1"
            rows.append((lesson.lesson_id, cls.sclass_id, stu.sstu_id,
                         stu.stu_name, fmt_hour(self._hours.get(sid,
                                                               self._default_hour())),
                         "1", now))
        try:
            SeasonSignMapper().upsert_batch(rows)
        except Exception as e:
            logging.exception("批量签到失败")
            self.save_label.config(text="保存失败：%s" % e, fg=COLORS["danger"])
            self._render_rows()
            self._update_labels()
            return
        for stu in self.students:
            self._apply_row(str(stu.sstu_id))
        self._flash_saved("已全部签到并保存")
        self._update_labels()

    def on_double_click_hour(self, event):
        if self.tree.identify_region(event.x, event.y) != "cell":
            return
        if self.tree.identify_column(event.x) != "#2":
            return
        sid = self.tree.identify_row(event.y)
        if not sid:
            return
        bbox = self.tree.bbox(sid, "#2")
        if not bbox:
            return
        x, y, width, height = bbox

        entry = ttk.Entry(self.tree, width=6, justify="center", font=font(11))
        entry.place(x=x, y=y, width=width, height=height)
        entry.insert(0, fmt_hour(self._hours.get(sid, self._default_hour())))
        entry.focus_set()
        entry.select_range(0, tk.END)
        state = {"done": False}

        def commit(_event=None):
            if state["done"]:
                return "break"
            state["done"] = True
            text = entry.get().strip()
            try:
                number = float(text)
                if number <= 0 or number > 999:
                    raise ValueError
            except ValueError:
                entry.destroy()
                self.save_label.config(text="课时要填 0～999 的数字，已取消修改",
                                       fg=COLORS["warn"])
                return "break"
            self._hours[sid] = number
            entry.destroy()
            self._apply_row(sid)
            self._persist(sid)
            return "break"

        def cancel(_event=None):
            state["done"] = True
            entry.destroy()
            return "break"

        entry.bind("<Return>", commit)
        entry.bind("<FocusOut>", commit)
        entry.bind("<Escape>", cancel)

    def _on_tree_motion(self, event):
        try:
            column = self.tree.identify_column(event.x)
            in_operation = self.tree.heading(column)["text"] == "操作"
        except (tk.TclError, KeyError):
            in_operation = False
        self.tree.configure(cursor="hand2" if in_operation else "")

    # ------------------------------------------------------------ 导出
    def export_record(self):
        lesson = self._selected_lesson()
        cls = self._selected_class()
        if lesson is None or cls is None:
            messagebox.showinfo("没有可导出的课次", "请先选择课次",
                                parent=self.app.master)
            return
        signed = [s for s in self.students if self._state.get(str(s.sstu_id)) == "1"]
        unsigned = [s for s in self.students if self._state.get(str(s.sstu_id)) != "1"]
        try:
            now = datetime.now()
            safe_name = re.sub(r'[\\/:*?"<>|]', "_", "%s_%s" % (
                cls.class_name or "假期班", date_of(lesson)))
            try:
                from service.dp import data_dir
                out_dir = data_dir()
            except Exception:
                out_dir = app_dir()
            path = os.path.join(out_dir, "假期签到_%s_%s.txt" % (
                safe_name, now.strftime("%Y%m%d_%H%M%S")))
            with open(path, "w", encoding="utf-8") as f:
                f.write("===== %s签到记录 =====\n" % self.title)
                f.write("班级：%s\n" % (cls.class_name or ""))
                if (cls.class_type or "").strip():
                    f.write("课程：%s\n" % cls.class_type.strip())
                idx = self.lessons.index(lesson) + 1 if lesson in self.lessons else 0
                f.write("课次：第 %d 次  %s  %s\n" % (
                    idx, date_of(lesson), (lesson.lesson_time or "").strip()))
                if (lesson.topic or "").strip():
                    f.write("内容：%s\n" % lesson.topic.strip())
                f.write("导出时间：%s\n" % now.strftime("%Y年%m月%d日 %H:%M:%S"))
                f.write("总人数：%d  已到：%d  未到：%d\n"
                        % (len(self.students), len(signed), len(unsigned)))
                f.write("\n【已签到】\n")
                for s in signed:
                    f.write("- %s（%s 课时）\n" % (
                        s.stu_name, fmt_hour(self._hours.get(str(s.sstu_id), ""))))
                f.write("\n【未签到】\n")
                for s in unsigned:
                    f.write("- %s\n" % s.stu_name)
            messagebox.showinfo("导出成功", "签到记录已保存至：\n%s" % path,
                                parent=self.app.master)
        except Exception as e:
            logging.exception("导出假期签到失败")
            messagebox.showerror("导出失败", str(e), parent=self.app.master)

    # ------------------------------------------------------------ 管理入口
    def add_class(self):
        from ui_season_admin import open_class_form
        return open_class_form(self.app, self.season, on_done=self._after_admin)

    def open_students(self):
        from ui_season_admin import open_season_admin
        cls = self._selected_class()
        return open_season_admin(self.app, self.season, page="stu",
                                 class_id=cls.sclass_id if cls else None,
                                 on_done=self._after_admin)

    def open_lessons(self):
        from ui_season_admin import open_season_admin
        cls = self._selected_class()
        return open_season_admin(self.app, self.season, page="lesson",
                                 class_id=cls.sclass_id if cls else None,
                                 on_done=self._after_admin)

    def add_lesson(self):
        from ui_season_admin import open_lesson_form
        cls = self._selected_class()
        if cls is None:
            return self.add_class()
        return open_lesson_form(self.app, self.season, cls.sclass_id,
                                on_done=self._after_admin)

    def _after_admin(self):
        """管理弹窗改完数据后：重建当前页（选择存在 app.season_last，不会跳回第一个班）。"""
        self.app.refresh_current_page()
