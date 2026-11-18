"""管理员设置 · 学生管理：学生主档 + 学生-班级映射。

2026-11-13 起学生全局唯一（一人一条主档，按姓名判唯一），班级归属走
t_stu_class 映射表，一个学生可以同时在多个班：

- 「全部学生」视图：查看/新增/编辑/删除学生主档。新增学生只是建档，
  还不属于任何班级；选「＋ 新增学员」到具体班级里把现有学生加进去。
- 「具体班级」视图：本班学员名单。新增学员只能从现有学生里挑，
  不能在这里凭空造学生；「移出本班」只解除映射，学生和签到记录都保留。

删除学生主档是软删除（stu_state 置 0）：学生从所有班级名单里消失，
映射关系保留，勾「显示已删除」可恢复；改名会同步更新全部班级的签到记录。
"""

import re
import tkinter as tk
from tkinter import messagebox, ttk

from entity.TStu import TStu
from mapper.t_class_mapper import TClassMapper
from mapper.t_sign_mapper import TSignMapper
from mapper.t_stu_mapper import DEFAULT_STATE, DELETED_STATE, TStuMapper
from ui_theme import COLORS, RoundedButton, button_width, font
from ui_widgets import (center_on, field_row, fit_dialog_to_screen,
                        form_entry, form_label)

STATE_LABELS = (("1", "正常"), ("2", "停课"), ("3", "结课"))
STATE_TEXT = dict(STATE_LABELS)
STATE_TEXT[DELETED_STATE] = "已删除"
# 表单里只能选正常/停课/结课，'0' 是删除后的有效标志，不给人手动选
STATE_ORDER = [text for _, text in STATE_LABELS]
STATE_VALUE = {text: value for value, text in STATE_LABELS}

DEFAULT_HOUR = "2.5"

# 「全部学生」视图的下拉项（不属于任何班级，cid 用空串表示）
ALL_LABEL = "◎ 全部学生"


def state_text(value):
    """库里没写或写了奇怪值时，统一按「正常」显示，避免空白列。"""
    return STATE_TEXT.get(str(value or DEFAULT_STATE), "正常")


def is_deleted(stu):
    """有效标志为 0 即已删除：记录仍在库里，可以恢复。"""
    return str(getattr(stu, "stu_state", DEFAULT_STATE) or "") == DELETED_STATE


class StudentPanel(tk.Frame):
    """学生页：全部学生 / 按班级两种视图 + 学生表格。"""

    # 列宽按 200% DPI 下的表头实测值给足：四字表头（剩余课时/签到记录）
    # 在 2 倍缩放下约 80px，加上 ttk heading 的内边距需要 120px 以上，
    # 写 92 会把「时」「录」裁掉。
    COLUMNS = (
        ("姓名", 170, "w"),
        ("所在班级", 130, "w"),
        ("剩余课时", 130, "center"),
        ("手机号", 140, "w"),
        ("状态", 100, "center"),
        ("签到记录", 130, "center"),
    )

    def __init__(self, master, admin):
        super().__init__(master, bg=COLORS["bg"])
        self.admin = admin
        self._classes = {}
        self._label_to_cid = {}
        self._students = []
        self._by_id = {}
        self._form = None
        self._picker = None
        self._sign_counts = {}
        self._class_names_by_id = {}   # 全部学生视图：sid → 班级串

        self.var_class = tk.StringVar()
        self.var_filter = tk.StringVar()
        self.var_show_deleted = tk.BooleanVar(value=False)

        self._build()

    # ---------- 布局 ----------
    def _build(self):
        bar = tk.Frame(self, bg=COLORS["bg"])
        bar.pack(fill=tk.X, pady=(0, 10))

        # 工具栏左右两组都要塞进 980px 的窗口；高 DPI 下每个控件都会变宽，
        # 左侧下拉/输入框必须收窄，否则最后 pack 的「＋ 新增学生」会被挤成一条。
        RoundedButton(bar, text="← 班级", command=self._back_to_class,
                      width=78, height=38, bg=COLORS["card"], fg="#334155",
                      hover_bg="#F8FAFC", border=COLORS["border"], radius=10,
                      parent_bg=COLORS["bg"], font_obj=font(10)).pack(
            side=tk.LEFT, padx=(0, 8))

        form_label(bar, "视图", 10, color=COLORS["muted"], bg=COLORS["bg"]).pack(
            side=tk.LEFT)
        self.class_box = ttk.Combobox(
            bar, textvariable=self.var_class, state="readonly",
            style="Class.TCombobox", width=15, values=[])
        self.class_box.pack(side=tk.LEFT, padx=(6, 8))
        self.class_box.bind("<<ComboboxSelected>>", lambda _e: self.refresh_students())

        form_label(bar, "筛选", 10, color=COLORS["muted"], bg=COLORS["bg"]).pack(
            side=tk.LEFT)
        entry_filter = form_entry(bar, self.var_filter, width=7)
        entry_filter.pack(side=tk.LEFT, padx=(6, 8), ipady=4)
        entry_filter.bind("<KeyRelease>", lambda _e: self._render_rows())

        self.del_btn = RoundedButton(
            bar, text="删除", command=self.on_remove_click, width=78, height=38,
            bg=COLORS["card"], fg=COLORS["danger"], hover_bg=COLORS["danger_soft"],
            border=COLORS["border"], radius=10, parent_bg=COLORS["bg"],
            font_obj=font(10))
        self.del_btn.pack(side=tk.RIGHT)

        self.edit_btn = RoundedButton(
            bar, text="编辑", command=self.edit_student, width=78, height=38,
            bg=COLORS["card"], fg="#334155", hover_bg="#F8FAFC",
            border=COLORS["border"], radius=10, parent_bg=COLORS["bg"],
            font_obj=font(10))
        self.edit_btn.pack(side=tk.RIGHT, padx=(0, 8))

        # 移出本班：只解除学生与本班的映射，主档/签到都保留
        drop_text = "移出本班"
        self.drop_btn = RoundedButton(
            bar, text=drop_text, command=self.remove_member,
            width=button_width(drop_text, font(10), 96), height=38,
            bg=COLORS["card"], fg="#B45309", hover_bg=COLORS["warn_soft"],
            border=COLORS["border"], radius=10, parent_bg=COLORS["bg"],
            font_obj=font(10))
        self.drop_btn.pack(side=tk.RIGHT, padx=(0, 8))

        self.add_btn = RoundedButton(
            bar, text="＋ 新增学生", command=self.on_add_click,
            width=button_width("＋ 新增学员", font(10), 118),
            height=38, radius=10, parent_bg=COLORS["bg"], font_obj=font(10))
        self.add_btn.pack(side=tk.RIGHT, padx=(0, 8))

        card = tk.Frame(self, bg=COLORS["card"],
                        highlightbackground=COLORS["border"], highlightthickness=1)
        card.pack(fill=tk.BOTH, expand=True)
        shell = tk.Frame(card, bg=COLORS["card"])
        shell.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)

        self.tree = ttk.Treeview(
            shell, columns=[c[0] for c in self.COLUMNS], show="headings",
            height=8, style="Data.Treeview", selectmode="browse")
        for name, width, anchor in self.COLUMNS:
            self.tree.heading(name, text=name, anchor=anchor)
            self.tree.column(name, anchor=anchor, width=width,
                             stretch=name in ("姓名", "所在班级"), minwidth=70)
        self.tree.tag_configure("odd", background=COLORS["card"],
                                foreground=COLORS["text"])
        self.tree.tag_configure("even", background=COLORS["row_alt"],
                                foreground=COLORS["text"])
        self.tree.tag_configure("paused", foreground=COLORS["muted"])
        self.tree.tag_configure("finished", foreground=COLORS["subtle"])
        self.tree.tag_configure("deleted", foreground=COLORS["subtle"],
                                background=COLORS["row_alt"])
        self.tree.bind("<<TreeviewSelect>>", lambda _e: self._sync_buttons())
        self.tree.bind("<Double-1>", lambda _e: self.edit_student())

        scrollbar = ttk.Scrollbar(shell, orient="vertical", command=self.tree.yview,
                                  style="Slim.Vertical.TScrollbar")
        self.tree.configure(yscrollcommand=scrollbar.set)
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y, padx=(4, 0))

        foot = tk.Frame(self, bg=COLORS["bg"])
        foot.pack(fill=tk.X, pady=(10, 0))
        self.status = form_label(foot, "", 9, color=COLORS["muted"], bg=COLORS["bg"])
        self.status.pack(side=tk.LEFT, anchor="w")

        # 删除只是把有效标志置 0，勾上这个才看得到被删掉的学生，也才能恢复
        self.chk_deleted = tk.Checkbutton(
            foot, text="显示已删除", variable=self.var_show_deleted,
            command=self.refresh_students, bg=COLORS["bg"], fg=COLORS["muted"],
            activebackground=COLORS["bg"], activeforeground=COLORS["text"],
            selectcolor=COLORS["card"], font=font(9), bd=0,
            highlightthickness=0, cursor="hand2")
        self.chk_deleted.pack(side=tk.RIGHT)

        form_label(self, "学生全局唯一：新增学生建档（暂不属于任何班），到具体班级视图用「＋ 新增学员」从现有学生里挑选",
                   9, color=COLORS["subtle"], bg=COLORS["bg"]).pack(anchor="w", pady=(2, 0))
        form_label(self, "「移出本班」只解除班级归属，学生和签到记录都保留；双击一行可直接编辑，改名会同步全部班级的签到",
                   9, color=COLORS["subtle"], bg=COLORS["bg"]).pack(anchor="w")

    # ---------- 数据 ----------
    def refresh_all(self):
        """重新读班级列表与学生，切换回本页时调用。"""
        if not self._load_classes():
            return False
        return self.refresh_students()

    def _load_classes(self):
        try:
            classes = TClassMapper().select_all()
        except Exception as e:
            self.status.config(text="读取班级失败：%s" % e, fg=COLORS["danger"])
            self._classes = {}
            self._label_to_cid = {ALL_LABEL: ""}
            self.class_box.configure(values=[ALL_LABEL])
            self.var_class.set(ALL_LABEL)
            self._render_rows()
            self._set_tools_enabled(False)
            return False

        self._classes = {str(c.class_id): c for c in classes}
        self._label_to_cid = {ALL_LABEL: ""}
        labels = [ALL_LABEL]
        for cid, c in self._classes.items():
            label = self._class_label(c)
            labels.append(label)
            self._label_to_cid[label] = cid
        self.class_box.configure(values=labels)

        current = self.current_class_id()
        if self.var_class.get() not in self._label_to_cid or not self.var_class.get():
            self.var_class.set(ALL_LABEL)
        return True

    @staticmethod
    def _class_label(c):
        """下拉框标签：编号 · 星期 课程。比完整班级名短，编号保证不歧义。"""
        cid = str(c.class_id)
        bits = [b for b in ((c.class_day or "").strip(),
                            (c.class_type or "").strip()) if b]
        if not bits:
            return cid
        text = "%s · %s" % (cid, " ".join(bits))
        return text if len(text) <= 18 else text[:17] + "…"

    def _back_to_class(self):
        self.admin.show_page("class")

    def is_all_view(self):
        """当前是否「全部学生」视图。"""
        return self.current_class_id() == ""

    def select_class(self, cid):
        """切到指定班级：从班级页「管理该班学生」跳进来时用它锁定班级。"""
        for label, value in self._label_to_cid.items():
            if value == cid:
                self.var_class.set(label)
                return self.refresh_students()
        return False

    def current_class_id(self):
        value = self._label_to_cid.get(self.var_class.get(), "")
        return value if value != ALL_LABEL else ""

    def current_class(self):
        return self._classes.get(self.current_class_id())

    def refresh_students(self):
        cid = self.current_class_id()
        self._students = []
        self._by_id = {}
        self._sign_counts = {}
        mapper = TStuMapper()
        include_deleted = self.var_show_deleted.get()
        try:
            if cid:
                # 班级视图：映射表里本班的学员
                self._students = mapper.select_by_class_id(
                    cid, include_deleted=include_deleted)
                for s in self._students:
                    self._sign_counts[str(s.stu_id)] = TSignMapper().count_by_stu(
                        s.stu_name, cid)
            else:
                # 全部学生视图：学生主档
                self._students = mapper.select_all(include_deleted=include_deleted)
                for s in self._students:
                    self._sign_counts[str(s.stu_id)] = TSignMapper().count_by_stu_name(
                        s.stu_name)
        except Exception as e:
            self._render_rows()
            self._set_tools_enabled(False)
            self.status.config(text="读取学生失败：%s" % e, fg=COLORS["danger"])
            return False

        # 所在班级列：全部学生视图需要，班级视图隐藏
        self._class_names_by_id = {}
        if not cid:
            for s in self._students:
                ids = mapper.classes_of(s.stu_id, include_deleted=True)
                self._class_names_by_id[str(s.stu_id)] = "、".join(ids) or "（未分班）"

        self._render_rows()
        self._set_tools_enabled(True)
        return True

    def _render_rows(self):
        self.tree.delete(*self.tree.get_children())
        self._by_id = {}
        keyword = self.var_filter.get().strip()
        shown = 0
        cid = self.current_class_id()
        # 已删除的排在最后：先看活学生，删掉的只作参考
        ordered = ([s for s in self._students if not is_deleted(s)]
                   + [s for s in self._students if is_deleted(s)])
        for s in ordered:
            sid = str(s.stu_id)
            name = s.stu_name or "—"
            if keyword and keyword not in name:
                continue
            self._by_id[sid] = s
            tag = "odd" if shown % 2 == 0 else "even"
            state_text_value = state_text(s.stu_state)
            if is_deleted(s):
                tag = "deleted"
            elif state_text_value == "停课":
                tag = "paused"
            elif state_text_value == "结课":
                tag = "finished"
            classes_text = self._class_names_by_id.get(sid, cid or "")
            self.tree.insert(
                "", tk.END, iid=sid,
                values=(name, classes_text, self._fmt_hour(s.stu_hour),
                        s.phone or "—", state_text_value,
                        self._sign_counts.get(sid, 0)),
                tags=(tag,),
            )
            shown += 1

        total = len([s for s in self._students if not is_deleted(s)])
        deleted = len(self._students) - total
        scope = "全部学生" if not cid else "%s 班" % cid
        if keyword:
            text = "筛选「%s」：%d / %d 人（%s）" % (keyword, shown, len(self._students), scope)
        elif deleted:
            text = "%s 共 %d 名（另有 %d 名已删除）" % (scope, total, deleted)
        else:
            text = "%s 共 %d 名" % (scope, total)
        self.status.config(text=text, fg=COLORS["muted"])
        self._apply_view()
        self._sync_buttons()

    def _apply_view(self):
        """按当前视图调整列与按钮文案。"""
        all_view = self.is_all_view()
        columns = [c[0] for c in self.COLUMNS]
        if all_view:
            self.tree["displaycolumns"] = columns
            self.tree.heading("所在班级", text="所在班级", anchor="w")
            self.tree.column("所在班级", width=130, minwidth=70, stretch=True)
            self.add_btn.set_text("＋ 新增学生")
        else:
            # 班级视图不需要「所在班级」列，直接隐藏而不是留一条 0 宽空列
            self.tree["displaycolumns"] = [c for c in columns if c != "所在班级"]
            self.tree.heading("所在班级", text="", anchor="w")
            self.tree.column("所在班级", width=0, minwidth=0, stretch=False)
            self.add_btn.set_text("＋ 新增学员")

    @staticmethod
    def _fmt_hour(value):
        if value in (None, ""):
            return "—"
        try:
            number = float(value)
        except (TypeError, ValueError):
            return str(value)
        return str(int(number)) if number == int(number) else ("%g" % number)

    def _selected(self):
        selection = self.tree.selection()
        return selection[0] if selection else None

    def selected_student(self):
        sid = self._selected()
        return self._by_id.get(sid) if sid else None

    def _sync_buttons(self):
        stu = self.selected_student()
        deleted = stu is not None and is_deleted(stu)
        self.edit_btn.set_enabled(stu is not None and not deleted)
        self.del_btn.set_enabled(stu is not None)
        # 同一个按钮两种身份：活学生是「删除」，已删除的学生是「恢复」
        if deleted:
            self.del_btn.set_text("恢复")
            self.del_btn.set_colors(COLORS["card"], COLORS["success"],
                                    COLORS["success_soft"], border=COLORS["border"])
        else:
            self.del_btn.set_text("删除")
            self.del_btn.set_colors(COLORS["card"], COLORS["danger"],
                                    COLORS["danger_soft"], border=COLORS["border"])
        # 移出本班只在班级视图、且选中了活学生时可用
        self.drop_btn.set_enabled(bool(self.current_class_id())
                                  and stu is not None and not deleted)

    def _set_tools_enabled(self, enabled):
        self.add_btn.set_enabled(enabled)
        if not enabled:
            self.edit_btn.set_enabled(False)
            self.del_btn.set_enabled(False)
            self.drop_btn.set_enabled(False)

    # ---------- 增删改 ----------
    def on_add_click(self):
        """「＋」按钮两种身份：全部学生视图新增学生主档，班级视图从现有学生里挑。"""
        if self._form_open() or self._picker_open():
            return
        if self.is_all_view():
            self.add_student()
        else:
            current = self.current_class()
            if current is None:
                messagebox.showinfo("请先选择班级", "请先在上方选择一个班级",
                                    parent=self.winfo_toplevel())
                return
            self._picker = MemberPickerDialog(self, current)

    def add_student(self):
        if self._form_open():
            return
        self._form = StudentFormDialog(self, None)

    def edit_student(self):
        if self._form_open():
            return
        stu = self.selected_student()
        if stu is None:
            messagebox.showinfo("请先选择", "请先在列表里选中一名学生",
                                parent=self.winfo_toplevel())
            return
        if is_deleted(stu):
            messagebox.showinfo(
                "该学生已删除",
                "「%s」已被删除，先点「恢复」把它启用后才能编辑。" % stu.stu_name,
                parent=self.winfo_toplevel())
            return
        self._form = StudentFormDialog(self, stu)

    def remove_member(self):
        """把选中学生移出当前班级：只删映射，学生主档与签到记录保留。"""
        stu = self.selected_student()
        if stu is None:
            messagebox.showinfo("请先选择", "请先在列表里选中一名学生",
                                parent=self.winfo_toplevel())
            return
        cid = self.current_class_id()
        if not cid:
            return
        if not messagebox.askyesno(
                "移出本班",
                "确定把「%s」移出 %s 班吗？\n\n"
                "学生记录与其他班级的归属都保留，历史签到记录不变；\n"
                "之后仍可在「＋ 新增学员」里重新加回来。" % (stu.stu_name, cid),
                parent=self.winfo_toplevel()):
            return
        try:
            TStuMapper().remove_from_class(stu.stu_id, cid)
        except Exception as e:
            messagebox.showerror("移出失败", str(e), parent=self.winfo_toplevel())
            return
        self.on_changed("已把 %s 移出 %s 班（学生记录保留）" % (stu.stu_name, cid))

    def on_remove_click(self):
        """「删除 / 恢复」是同一个按钮：选中的是已删除学生时就执行恢复。"""
        stu = self.selected_student()
        if stu is not None and is_deleted(stu):
            return self.restore_student()
        return self.delete_student()

    def restore_student(self):
        stu = self.selected_student()
        if stu is None:
            messagebox.showinfo("请先选择", "请先在列表里选中一名学生",
                                parent=self.winfo_toplevel())
            return
        if not messagebox.askyesno(
                "恢复学生",
                "确定恢复「%s」吗？\n\n恢复后状态回到「正常」，会重新出现在"
                "之前所在的所有班级名单里，历史签到记录照旧。" % stu.stu_name,
                parent=self.winfo_toplevel()):
            return
        revived = TStu(
            stu_id=stu.stu_id,
            stu_name=stu.stu_name,
            stu_hour=stu.stu_hour,
            phone=stu.phone,
            stu_state=DEFAULT_STATE,
        )
        try:
            TStuMapper().restore(revived)
        except Exception as e:
            messagebox.showerror("恢复失败", str(e), parent=self.winfo_toplevel())
            return
        self.on_changed("已恢复学生 %s" % stu.stu_name)

    def delete_student(self):
        stu = self.selected_student()
        if stu is None:
            messagebox.showinfo("请先选择", "请先在列表里选中一名学生",
                                parent=self.winfo_toplevel())
            return
        if is_deleted(stu):
            return self.restore_student()
        try:
            # 学生已全局唯一：签到记录按姓名统计（全部班级加起来）
            sign_count = TSignMapper().count_by_stu_name(stu.stu_name)
        except Exception as e:
            messagebox.showerror("读取失败", str(e), parent=self.winfo_toplevel())
            return

        if sign_count == 0:
            if not messagebox.askyesno(
                    "删除学生",
                    "确定删除「%s」吗？\n\n学生将从所有班级名单里消失（记录保留，"
                    "勾选「显示已删除」可随时恢复）。" % stu.stu_name,
                    icon="warning", default="no", parent=self.winfo_toplevel()):
                return
            purge = False
        else:
            dialog = DeleteStudentDialog(self, stu, sign_count)
            dialog.wait()
            if dialog.choice is None:
                return
            purge = dialog.choice == "purge"

        try:
            if purge:
                TSignMapper().delete_by_stu_name(stu.stu_name)
            TStuMapper().delete(stu.stu_id)
        except Exception as e:
            messagebox.showerror("删除失败", str(e), parent=self.winfo_toplevel())
            return

        message = "已删除学生 %s（记录保留，可恢复）" % stu.stu_name
        if purge and sign_count:
            message = "已删除学生 %s，并清除全部班级的 %d 条签到记录" % (
                stu.stu_name, sign_count)
        self.on_changed(message)

    def on_changed(self, message=""):
        """写操作成功后：刷新本页、同步班级页人数、让主界面重载。"""
        keep = self.var_class.get()
        self.refresh_students()
        if keep in self._label_to_cid:
            self.var_class.set(keep)
        else:
            self.var_class.set(ALL_LABEL)
        if message:
            self.status.config(text=message, fg=COLORS["success"])
        self.admin.refresh()
        self.admin.app.reload_after_admin()

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

    def _picker_open(self):
        picker = self._picker
        if picker is None:
            return False
        try:
            if picker.win.winfo_exists():
                picker.win.lift()
                picker.win.focus_force()
                return True
        except tk.TclError:
            pass
        self._picker = None
        return False


# ---------------------------------------------------------------- 学生表单


class StudentFormDialog:
    """新增 / 编辑学生主档。

    学生全局唯一：这里只管姓名/课时/手机号/状态，不管班级归属；
    想把学生加进班级，到具体班级视图用「＋ 新增学员」。
    同名（含已删除）时不再新建，引导先处理现有学生。
    """

    def __init__(self, panel, original=None):
        self.panel = panel
        self.original = original
        self.is_new = original is None

        self.win = tk.Toplevel(panel.winfo_toplevel())
        self.win.title("新增学生" if self.is_new else "编辑学生 %s" % original.stu_name)
        self.win.configure(bg=COLORS["bg"])
        self.win.transient(panel.winfo_toplevel())

        self.var_name = tk.StringVar(value="" if self.is_new else (original.stu_name or ""))
        self.var_hour = tk.StringVar(
            value=DEFAULT_HOUR if self.is_new else self._hour_text(original.stu_hour))
        self.var_phone = tk.StringVar(
            value="" if self.is_new else (original.phone or ""))
        self.var_state = tk.StringVar(
            value=state_text(DEFAULT_STATE if self.is_new else original.stu_state))

        self._build()
        fit_dialog_to_screen(self.win)
        center_on(self.win, panel.winfo_toplevel(), ratio=3)
        self.win.protocol("WM_DELETE_WINDOW", self.cancel)
        self.win.bind("<Escape>", lambda _e: self.cancel())
        self.win.bind("<Return>", lambda _e: self.save())
        try:
            self.win.grab_set()
        except tk.TclError:
            pass
        self.entries["姓名 *"].focus_set()

    @staticmethod
    def _hour_text(value):
        if value in (None, ""):
            return ""
        try:
            number = float(value)
        except (TypeError, ValueError):
            return str(value)
        return str(int(number)) if number == int(number) else ("%g" % number)

    def _build(self):
        root = tk.Frame(self.win, bg=COLORS["bg"])
        root.pack(fill=tk.BOTH, expand=True, padx=24, pady=(20, 16))

        form_label(root, "新增学生" if self.is_new else "编辑学生", 15, True,
                   bg=COLORS["bg"]).pack(anchor="w")
        if self.is_new:
            form_label(root, "学生全局唯一；建档后到具体班级用「＋ 新增学员」把他加进班级",
                       9, color=COLORS["muted"], bg=COLORS["bg"]).pack(
                anchor="w", pady=(3, 12))
        else:
            form_label(root, "班级归属不在这里改：加入/移出班级到具体班级视图操作",
                       9, color=COLORS["muted"], bg=COLORS["bg"]).pack(
                anchor="w", pady=(3, 12))

        form = tk.Frame(root, bg=COLORS["bg"])
        form.pack(fill=tk.X)
        form.grid_columnconfigure(0, weight=1)

        self.entries = {}
        row = 0
        entry_name = form_entry(form, self.var_name, width=34)
        self.entries["姓名 *"] = entry_name
        row = field_row(form, row, "姓名 *", entry_name,
                        "全库唯一：与现有学生重名时不能新建")

        entry_hour = form_entry(form, self.var_hour, width=34)
        self.entries["剩余课时 *"] = entry_hour
        row = field_row(form, row, "剩余课时 *", entry_hour,
                        "点名表格里的初始课时，如 2.5")

        entry_phone = form_entry(form, self.var_phone, width=34)
        self.entries["手机号"] = entry_phone
        row = field_row(form, row, "手机号", entry_phone, "可留空")

        state_box = ttk.Combobox(form, textvariable=self.var_state, values=STATE_ORDER,
                                 state="readonly", style="Class.TCombobox", width=34)
        row = field_row(form, row, "状态", state_box,
                        "停课/结课的学生仍显示在点名表里，但颜色变淡")

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

    # ---------- 保存 ----------
    def _fail(self, message, field=None):
        self.error.config(text=message)
        entry = self.entries.get(field) if field else None
        if entry is not None:
            entry.focus_set()
        return False

    def _collect(self):
        name = self.var_name.get().strip()
        if not name:
            return self._fail("请填写学生姓名", "姓名 *")
        if len(name) > 50:
            return self._fail("姓名最多 50 个字符", "姓名 *")

        mapper = TStuMapper()
        # 学生全局唯一：不允许与任何现有学生重名（含已删除的，先恢复再改名）
        try:
            existing = mapper.select_by_name(name, include_deleted=True)
        except Exception:
            existing = None
        if existing is not None:
            if self.is_new or existing.stu_id != self.original.stu_id:
                extra = ("该学生已存在，可直接到班级里用「＋ 新增学员」加入"
                         if is_deleted(existing) is False else
                         "该姓名有一条已删除的记录，请先在「显示已删除」里恢复它")
                return self._fail("已有同名学生 %s：%s" % (name, extra), "姓名 *")

        hour_text = self.var_hour.get().strip()
        try:
            hour = float(hour_text)
        except ValueError:
            return self._fail("剩余课时要填数字，例如 2.5", "剩余课时 *")
        if not (0 <= hour <= 999):
            return self._fail("剩余课时应在 0～999 之间", "剩余课时 *")

        phone = self.var_phone.get().strip()
        if phone and not re.fullmatch(r"[0-9+\- ]{3,20}", phone):
            return self._fail("手机号只能包含数字、+、- 和空格（3～20 位）", "手机号")

        state = STATE_VALUE.get(self.var_state.get(), DEFAULT_STATE)
        stu = TStu(
            stu_id=None if self.is_new else self.original.stu_id,
            stu_name=name,
            stu_hour=hour,
            phone=phone,
            stu_state=state,
        )
        return stu

    def save(self):
        stu = self._collect()
        if stu is False:
            return
        mapper = TStuMapper()
        try:
            if self.is_new:
                mapper.insert(stu)
                message = ("已新增学生 %s（暂未分班，到班级视图用「＋ 新增学员」加入）"
                           % stu.stu_name)
            else:
                mapper.update(stu, old_name=self.original.stu_name)
                message = "已更新学生 %s" % stu.stu_name
        except Exception as e:
            self.error.config(text="保存失败：%s" % e)
            return

        self._close()
        self.panel._form = None
        self.panel.on_changed(message)

    def cancel(self):
        self._close()
        self.panel._form = None

    def _close(self):
        try:
            self.win.grab_release()
        except tk.TclError:
            pass
        self.win.destroy()


# ---------------------------------------------------------------- 新增学员


class MemberPickerDialog:
    """班级视图「＋ 新增学员」：从现有学生里挑人加入本班。

    单击行即勾选/取消，可一次挑多人；也支持 Ctrl / Shift 连选与「全选」。
    只能选已有学生——想凭空新建，请到「全部学生」视图新增学生主档。
    窗口默认按屏幕工作区定尺寸，四边都能用鼠标拖动缩放。
    """

    # 与主表格同理：高 DPI 下四字表头要 120px 以上，写 88 会被裁成「剩余课」
    COLUMNS = (
        ("选择", 56, "center"),
        ("姓名", 130, "w"),
        ("所在班级", 150, "w"),
        ("剩余课时", 130, "center"),
        ("状态", 100, "center"),
    )

    def __init__(self, panel, class_obj):
        self.panel = panel
        self.class_obj = class_obj
        self.cid = str(class_obj.class_id)
        self.checked = set()          # 已勾选的学生 id（跨筛选保留）
        self._by_id = {}

        self.win = tk.Toplevel(panel.winfo_toplevel())
        self.win.title("新增学员 · %s 班" % self.cid)
        self.win.configure(bg=COLORS["bg"])
        self.win.transient(panel.winfo_toplevel())

        self.var_filter = tk.StringVar()

        self._build()
        self.reload()
        fit_dialog_to_screen(self.win, tree=self.tree,
                             min_rows=3, max_ratio=0.82, min_w=520, min_h=360)
        center_on(self.win, panel.winfo_toplevel(), ratio=4)
        self.win.protocol("WM_DELETE_WINDOW", self.cancel)
        self.win.bind("<Escape>", lambda _e: self.cancel())
        try:
            self.win.grab_set()
        except tk.TclError:
            pass
        self.entry_filter.focus_set()

    def _build(self):
        root = tk.Frame(self.win, bg=COLORS["bg"])
        root.pack(fill=tk.BOTH, expand=True, padx=24, pady=(20, 16))

        form_label(root, "新增学员 · %s 班" % self.cid, 15, True,
                   bg=COLORS["bg"]).pack(anchor="w")
        form_label(root, "点击行勾选/取消（可多选），表头「全选」与右侧按钮可批量操作；窗口可拖动调整大小",
                   9, color=COLORS["muted"], bg=COLORS["bg"]).pack(
            anchor="w", pady=(3, 10))

        # 底部按钮与状态先占住位置：窗口被拖小时优先保住它们，列表自动收缩
        foot = tk.Frame(root, bg=COLORS["bg"])
        foot.pack(side=tk.BOTTOM, fill=tk.X, pady=(10, 0))
        add_text = "加入本班"
        RoundedButton(foot, text=add_text, command=self.confirm,
                      width=button_width(add_text, font(10), 100), height=38,
                      radius=10, parent_bg=COLORS["bg"],
                      font_obj=font(10)).pack(side=tk.RIGHT)
        RoundedButton(foot, text="取消", command=self.cancel, width=88, height=38,
                      bg=COLORS["card"], fg="#334155", hover_bg="#F8FAFC",
                      border=COLORS["border"], radius=10, parent_bg=COLORS["bg"],
                      font_obj=font(10)).pack(side=tk.RIGHT, padx=(0, 10))

        self.status = form_label(root, "", 9, color=COLORS["muted"], bg=COLORS["bg"])
        self.status.pack(side=tk.BOTTOM, anchor="w", pady=(8, 0))

        line = tk.Frame(root, bg=COLORS["bg"])
        line.pack(side=tk.TOP, fill=tk.X, pady=(0, 8))
        form_label(line, "筛选", 10, color=COLORS["muted"],
                   bg=COLORS["bg"]).pack(side=tk.LEFT)
        self.entry_filter = form_entry(line, self.var_filter, width=16)
        self.entry_filter.pack(side=tk.LEFT, padx=(8, 0), ipady=4)
        self.entry_filter.bind("<KeyRelease>", lambda _e: self.reload())
        small = dict(width=66, height=30, radius=8, parent_bg=COLORS["bg"],
                     bg=COLORS["card"], fg="#334155", hover_bg="#F8FAFC",
                     border=COLORS["border"], font_obj=font(9))
        RoundedButton(line, text="清空", command=self._clear_selection,
                      **small).pack(side=tk.RIGHT, padx=(6, 0))
        RoundedButton(line, text="全选", command=self._select_all,
                      **small).pack(side=tk.RIGHT)

        card = tk.Frame(root, bg=COLORS["card"],
                        highlightbackground=COLORS["border"], highlightthickness=1)
        card.pack(fill=tk.BOTH, expand=True)
        shell = tk.Frame(card, bg=COLORS["card"])
        shell.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)

        self.tree = ttk.Treeview(
            shell, columns=[c[0] for c in self.COLUMNS], show="headings",
            height=10, style="Data.Treeview", selectmode="extended")
        for name, width, anchor in self.COLUMNS:
            self.tree.heading(name, text=name, anchor=anchor)
            self.tree.column(name, anchor=anchor, width=width,
                             stretch=name in ("姓名", "所在班级"), minwidth=56)
        self.tree.heading("选择", text="全选", command=self._toggle_all)
        self.tree.tag_configure("odd", background=COLORS["card"],
                                foreground=COLORS["text"])
        self.tree.tag_configure("even", background=COLORS["row_alt"],
                                foreground=COLORS["text"])
        scrollbar = ttk.Scrollbar(shell, orient="vertical", command=self.tree.yview,
                                  style="Slim.Vertical.TScrollbar")
        self.tree.configure(yscrollcommand=scrollbar.set)
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y, padx=(4, 0))

        # 单击即勾选（累积多选）；Ctrl/Shift 连选、Ctrl+A 全选、空格切换也支持
        self.tree.bind("<ButtonRelease-1>", self._on_row_click)
        self.tree.bind("<Control-a>", lambda _e: (self._select_all(), "break")[1])
        self.tree.bind("<space>", self._on_space)

    # ---------- 多选 ----------
    def _paint_row(self, iid):
        values = list(self.tree.item(iid, "values"))
        if not values:
            return
        values[0] = "☑" if iid in self.checked else "☐"
        self.tree.item(iid, values=values)

    def _on_row_click(self, event):
        iid = self.tree.identify_row(event.y)
        if not iid:                      # 点的是表头/空白/滚动条
            return
        if iid in self.checked:
            self.checked.discard(iid)
        else:
            self.checked.add(iid)
        self._paint_row(iid)
        self._update_status()

    def _on_space(self, _event):
        for iid in self.tree.selection():
            if iid in self.checked:
                self.checked.discard(iid)
            else:
                self.checked.add(iid)
            self._paint_row(iid)
        self._update_status()
        return "break"

    def _select_all(self):
        ids = list(self.tree.get_children())
        self.checked.update(ids)
        for iid in ids:
            self._paint_row(iid)
        self._update_status()

    def _clear_selection(self):
        ids = list(self.tree.get_children())
        self.checked.difference_update(ids)
        for iid in ids:
            self._paint_row(iid)
        try:
            self.tree.selection_remove(self.tree.selection())
        except tk.TclError:
            pass
        self._update_status()

    def _toggle_all(self):
        ids = list(self.tree.get_children())
        if ids and all(iid in self.checked for iid in ids):
            self._clear_selection()
        else:
            self._select_all()

    def _update_status(self):
        total = len(self.tree.get_children())
        picked = len([i for i in self.checked if i in self._by_id])
        text = "共 %d 名学生可加入（已在 %s 班的不显示）" % (total, self.cid)
        if picked:
            text += " · 已勾选 %d 名" % picked
        self.status.config(text=text,
                           fg=COLORS["primary"] if picked else COLORS["muted"])

    def _selected_ids(self):
        ids = [i for i in self.checked if i in self._by_id]
        if not ids:
            # 兼容只用 Ctrl / Shift 高亮、没点勾选列的情况
            ids = [i for i in self.tree.selection() if i in self._by_id]
        return ids

    def reload(self):
        self.tree.delete(*self.tree.get_children())
        self._by_id = {}
        mapper = TStuMapper()
        try:
            students = mapper.select_not_in_class(self.cid,
                                                  keyword=self.var_filter.get())
        except Exception as e:
            self.status.config(text="读取学生失败：%s" % e, fg=COLORS["danger"])
            return
        for idx, s in enumerate(students):
            sid = str(s.stu_id)
            classes = mapper.classes_of(s.stu_id)
            self._by_id[sid] = s
            self.tree.insert(
                "", tk.END, iid=sid,
                values=("☑" if sid in self.checked else "☐",
                        s.stu_name, "、".join(classes) or "（未分班）",
                        self.panel._fmt_hour(s.stu_hour), state_text(s.stu_state)),
                tags=("odd" if idx % 2 == 0 else "even",))
        # 筛选后不存在的学生从勾选集合里去掉，但保留仍可见的勾选
        self.checked.intersection_update(self._by_id.keys())
        self._update_status()

    def confirm(self):
        selection = self._selected_ids()
        if not selection:
            self.status.config(text="请先勾选要加入的学生（可多选，或点「全选」）",
                               fg=COLORS["warn"])
            return
        mapper = TStuMapper()
        added = []
        try:
            for sid in selection:
                stu = self._by_id.get(sid)
                if stu is None:
                    continue
                mapper.add_to_class(stu.stu_id, self.cid)
                added.append(stu.stu_name)
        except Exception as e:
            self.status.config(text="加入失败：%s" % e, fg=COLORS["danger"])
            return
        message = "已把 %s 加入 %s 班" % ("、".join(added), self.cid)
        self._close()
        self.panel._picker = None
        self.panel.on_changed(message)

    def cancel(self):
        self._close()
        self.panel._picker = None

    def _close(self):
        try:
            self.win.grab_release()
        except tk.TclError:
            pass
        self.win.destroy()


# ---------------------------------------------------------------- 删除确认


class DeleteStudentDialog:
    """删除学生：说明是软删除（可恢复），并让用户选择签到记录是否一并清除。"""

    def __init__(self, panel, stu, sign_count):
        self.choice = None
        parent = panel.winfo_toplevel()

        self.win = tk.Toplevel(parent)
        self.win.title("删除学生")
        self.win.configure(bg=COLORS["bg"])
        self.win.resizable(False, False)
        self.win.transient(parent)

        root = tk.Frame(self.win, bg=COLORS["bg"])
        root.pack(fill=tk.BOTH, expand=True, padx=28, pady=(22, 18))

        form_label(root, "删除学生「%s」" % stu.stu_name, 15, True,
                   bg=COLORS["bg"]).pack(anchor="w")
        desc = form_label(
            root,
            "该学生全部班级共有 %d 条签到记录\n"
            "删除只是标记为无效：学生从所有班级名单里消失，"
            "记录仍留在库里，勾选「显示已删除」可以恢复。\n"
            "签到记录可保留或一并清除（全部班级），清除后不可恢复。"
            % sign_count,
            9, color=COLORS["muted"], bg=COLORS["bg"])
        desc.configure(justify="left", anchor="w", wraplength=580)
        desc.pack(anchor="w", pady=(8, 0))

        foot = tk.Frame(root, bg=COLORS["bg"])
        foot.pack(fill=tk.X, pady=(20, 0))
        RoundedButton(foot, text="取消", command=self.cancel, width=88, height=38,
                      bg=COLORS["card"], fg="#334155", hover_bg="#F8FAFC",
                      border=COLORS["border"], radius=10, parent_bg=COLORS["bg"],
                      font_obj=font(10)).pack(side=tk.RIGHT)

        purge_text = "连签到一起清除"
        RoundedButton(foot, text=purge_text, command=self.purge,
                      width=button_width(purge_text, font(10), 140),
                      height=38, bg=COLORS["card"], fg=COLORS["danger"],
                      hover_bg=COLORS["danger_soft"], border=COLORS["border"],
                      radius=10, parent_bg=COLORS["bg"],
                      font_obj=font(10)).pack(side=tk.RIGHT, padx=(0, 10))

        RoundedButton(foot, text="只删除学生", command=self.keep, width=126,
                      height=38, radius=10, parent_bg=COLORS["bg"],
                      font_obj=font(10)).pack(side=tk.RIGHT, padx=(0, 10))

        center_on(self.win, parent, ratio=3)
        self.win.protocol("WM_DELETE_WINDOW", self.cancel)
        self.win.bind("<Escape>", lambda _e: self.cancel())
        try:
            self.win.grab_set()
        except tk.TclError:
            pass

    def wait(self):
        """模态等待用户做出选择。"""
        self.win.wait_window(self.win)

    def keep(self):
        self.choice = "keep"
        self._close()

    def purge(self):
        self.choice = "purge"
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
