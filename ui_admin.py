"""管理员设置：班级与学生管理，带口令保护。

班级信息同时被点名页（下拉框、默认课时）和课程页（周课表）使用，所以每次改完
都通过 app.reload_after_admin() 重载数据并重建当前页，不需要用户手动刷新。

口令用 PBKDF2-SHA256 加盐存到程序目录的 admin.json，不落明文；文件不存在时
视为默认口令 admin，首次登录后会提示尽快修改。
"""

import hashlib
import hmac
import json
import os
import re
import sys
import tkinter as tk
from tkinter import messagebox, ttk

from entity.TClass import TClass
from mapper.t_class_mapper import TClassMapper
from mapper.t_stu_mapper import TStuMapper
from ui_admin_import import ImportDialog
from ui_admin_stu import StudentPanel
from ui_theme import COLORS, RoundedButton, button_width, font
from ui_widgets import center_on as _center_on
from ui_widgets import form_entry as _entry
from ui_widgets import form_label as _label

WEEKDAYS = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")
DEFAULT_PASSWORD = "admin"
PBKDF2_ROUNDS = 120000
MIN_PASSWORD_LEN = 4

# ---------------------------------------------------------------- 口令存储


def _app_dir():
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def admin_config_path():
    try:
        from service.dp import data_dir
        return os.path.join(data_dir(), "admin.json")
    except Exception:
        return os.path.join(_app_dir(), "admin.json")


def _read_store():
    path = admin_config_path()
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict) and data.get("hash") and data.get("salt"):
            return data
    except Exception:
        pass
    return None


def _hash_password(password, salt=None, rounds=PBKDF2_ROUNDS):
    salt = salt or os.urandom(16).hex()
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"),
                                bytes.fromhex(salt), rounds)
    return {"salt": salt, "hash": digest.hex(), "rounds": rounds}


def verify_password(password):
    """口令校验；还没设置过口令时用默认口令 admin。"""
    store = _read_store()
    if store is None:
        return password == DEFAULT_PASSWORD
    try:
        rounds = int(store.get("rounds", PBKDF2_ROUNDS))
    except (TypeError, ValueError):
        rounds = PBKDF2_ROUNDS
    candidate = _hash_password(password, store["salt"], rounds)
    return hmac.compare_digest(candidate["hash"], store["hash"])


def set_admin_password(password):
    """保存新口令（哈希+盐），返回写入的文件路径。"""
    store = _hash_password(password)
    path = admin_config_path()
    with open(path, "w", encoding="utf-8") as f:
        json.dump(store, f, ensure_ascii=False, indent=2)
    return path


def uses_default_password():
    return _read_store() is None


# ---------------------------------------------------------------- 口令输入框


class PasswordPrompt:
    """登录小窗：口令正确才继续，回车即可提交。"""

    def __init__(self, app):
        self.ok = False
        self.win = tk.Toplevel(app.master)
        self.win.title("管理员验证")
        self.win.configure(bg=COLORS["bg"])
        self.win.resizable(False, False)
        self.win.transient(app.master)

        root = tk.Frame(self.win, bg=COLORS["bg"])
        root.pack(fill=tk.BOTH, expand=True, padx=24, pady=(20, 18))

        _label(root, "管理员验证", 16, True).pack(anchor="w")
        _label(root, "请输入管理员口令后再修改班级信息",
               9, color=COLORS["muted"]).pack(anchor="w", pady=(3, 14))

        self.var = tk.StringVar()
        self.entry = _entry(root, self.var, width=26, show="*")
        self.entry.pack(fill=tk.X, ipady=6)

        self.hint = _label(root, "", 9, color=COLORS["danger"])
        self.hint.pack(anchor="w", pady=(6, 0))
        if uses_default_password():
            self.hint.config(text="当前为默认口令 admin，登录后请尽快修改",
                             fg=COLORS["warn"])

        foot = tk.Frame(root, bg=COLORS["bg"])
        foot.pack(fill=tk.X, pady=(14, 0))
        RoundedButton(foot, text="进入", command=self.submit, width=92, height=36,
                      radius=10, parent_bg=COLORS["bg"],
                      font_obj=font(10)).pack(side=tk.RIGHT)
        RoundedButton(foot, text="取消", command=self.cancel, width=92, height=36,
                      bg=COLORS["card"], fg="#334155", hover_bg="#F8FAFC",
                      border=COLORS["border"], radius=10, parent_bg=COLORS["bg"],
                      font_obj=font(10)).pack(side=tk.RIGHT, padx=(0, 10))

        self.win.bind("<Return>", lambda _e: self.submit())
        self.win.bind("<Escape>", lambda _e: self.cancel())
        self.win.protocol("WM_DELETE_WINDOW", self.cancel)

        _center_on(self.win, app.master)
        try:
            self.win.grab_set()
        except tk.TclError:
            pass
        self.entry.focus_set()

    def submit(self):
        password = self.var.get()
        if verify_password(password):
            self.ok = True
            self._close()
            return
        self.hint.config(text="口令不正确，请重新输入", fg=COLORS["danger"])
        self.var.set("")
        self.entry.focus_set()

    def cancel(self):
        self.ok = False
        self._close()

    def _close(self):
        try:
            self.win.grab_release()
        except tk.TclError:
            pass
        self.win.destroy()


# ---------------------------------------------------------------- 入口


def open_admin_dialog(app):
    """先验口令，再打开管理员设置；重复点击只把已有窗口提到前面。"""
    existing = getattr(app, "_admin_dialog", None)
    if existing is not None:
        try:
            if existing.win.winfo_exists():
                existing.win.deiconify()
                existing.win.lift()
                existing.win.focus_force()
                return existing
        except tk.TclError:
            pass
        app._admin_dialog = None

    prompt = PasswordPrompt(app)
    app.master.wait_window(prompt.win)
    if not prompt.ok:
        return None

    dialog = AdminDialog(app)
    app._admin_dialog = dialog
    return dialog


# ---------------------------------------------------------------- 班级管理


class AdminDialog:
    """班级列表 + 增删改。点关闭不影响已保存的数据。"""

    COLUMNS = (
        ("班级编号", 110, "w"),
        ("班级名称", 300, "w"),
        ("课程", 100, "w"),
        ("星期", 76, "w"),
        ("上课时间", 118, "w"),
        ("默认课时", 110, "center"),
        ("学生", 62, "center"),
    )

    def __init__(self, app):
        self.app = app
        self._classes = {}
        self._counts = {}
        self._form = None
        self._import_dialog = None
        self._page = "class"

        self.win = tk.Toplevel(app.master)
        self.win.title("管理员设置")
        self.win.configure(bg=COLORS["bg"])
        self.win.geometry("1000x760")
        self.win.minsize(880, 600)
        self.win.transient(app.master)

        self._build()
        _center_on(self.win, app.master, ratio=4)
        self.win.protocol("WM_DELETE_WINDOW", self.close)
        self.win.bind("<Escape>", lambda _e: self.close())
        try:
            self.win.grab_set()
        except tk.TclError:
            pass
        self.refresh()

    # ---------- 布局 ----------
    def _build(self):
        root = tk.Frame(self.win, bg=COLORS["bg"])
        root.pack(fill=tk.BOTH, expand=True, padx=22, pady=(18, 16))

        head = tk.Frame(root, bg=COLORS["bg"])
        head.pack(fill=tk.X)
        _label(head, "管理员设置", 16, True).pack(anchor="w")
        _label(head, "班级与学生的改动会立即同步到点名页与课程页；删除班级不会删除学生",
               9, color=COLORS["muted"]).pack(anchor="w", pady=(3, 0))

        # 分段导航 + 全局操作
        nav = tk.Frame(root, bg=COLORS["bg"])
        nav.pack(fill=tk.X, pady=(14, 12))

        self.nav_class = RoundedButton(
            nav, text="班级管理", command=lambda: self.show_page("class"),
            width=112, height=36, bg=COLORS["card"], fg="#334155",
            hover_bg="#F8FAFC", border=COLORS["border"], radius=10,
            parent_bg=COLORS["bg"], font_obj=font(10))
        self.nav_class.pack(side=tk.LEFT)

        self.nav_stu = RoundedButton(
            nav, text="学生管理", command=lambda: self.show_page("stu"),
            width=112, height=36, bg=COLORS["card"], fg="#334155",
            hover_bg="#F8FAFC", border=COLORS["border"], radius=10,
            parent_bg=COLORS["bg"], font_obj=font(10))
        self.nav_stu.pack(side=tk.LEFT, padx=(8, 0))

        # Excel 批量导入：班级和学员共用一个入口，对话框里再切模式
        self.nav_import = RoundedButton(
            nav, text="⇩ Excel 导入", command=self.import_from_current_page,
            width=button_width("⇩ Excel 导入", font(10), 124), height=36,
            bg=COLORS["card"], fg="#334155", hover_bg="#F8FAFC",
            border=COLORS["border"], radius=10, parent_bg=COLORS["bg"],
            font_obj=font(10))
        self.nav_import.pack(side=tk.LEFT, padx=(8, 0))

        RoundedButton(nav, text="关闭", command=self.close, width=88, height=36,
                      bg=COLORS["card"], fg="#334155", hover_bg="#F8FAFC",
                      border=COLORS["border"], radius=10, parent_bg=COLORS["bg"],
                      font_obj=font(10)).pack(side=tk.RIGHT)

        RoundedButton(nav, text="修改口令", command=self.change_password, width=104,
                      height=36, bg=COLORS["card"], fg="#334155", hover_bg="#F8FAFC",
                      border=COLORS["border"], radius=10, parent_bg=COLORS["bg"],
                      font_obj=font(10)).pack(side=tk.RIGHT, padx=(0, 10))

        self.body = tk.Frame(root, bg=COLORS["bg"])
        self.body.pack(fill=tk.BOTH, expand=True)

        self.class_page = tk.Frame(self.body, bg=COLORS["bg"])
        self._build_class_page(self.class_page)
        self.student_page = StudentPanel(self.body, self)
        self.show_page("class")

    def _build_class_page(self, page):
        bar = tk.Frame(page, bg=COLORS["bg"])
        bar.pack(fill=tk.X, pady=(0, 10))

        RoundedButton(bar, text="＋ 新增班级", command=self.add_class, width=126,
                      height=38, radius=10, parent_bg=COLORS["bg"],
                      font_obj=font(10)).pack(side=tk.LEFT)

        self.import_btn = RoundedButton(
            bar, text="导入 Excel", command=lambda: self.open_import("class"),
            width=button_width("导入 Excel", font(10), 108), height=38,
            bg=COLORS["card"], fg="#334155", hover_bg="#F8FAFC",
            border=COLORS["border"], radius=10, parent_bg=COLORS["bg"],
            font_obj=font(10))
        self.import_btn.pack(side=tk.LEFT, padx=(10, 0))

        self.edit_btn = RoundedButton(
            bar, text="编辑", command=self.edit_class, width=88, height=38,
            bg=COLORS["card"], fg="#334155", hover_bg="#F8FAFC",
            border=COLORS["border"], radius=10, parent_bg=COLORS["bg"],
            font_obj=font(10))
        self.edit_btn.pack(side=tk.LEFT, padx=(10, 0))

        self.del_btn = RoundedButton(
            bar, text="删除", command=self.delete_class, width=88, height=38,
            bg=COLORS["card"], fg=COLORS["danger"], hover_bg=COLORS["danger_soft"],
            border=COLORS["border"], radius=10, parent_bg=COLORS["bg"],
            font_obj=font(10))
        self.del_btn.pack(side=tk.LEFT, padx=(10, 0))

        stu_label = "管理该班学生 →"
        self.stu_btn = RoundedButton(
            bar, text=stu_label, command=self.manage_students,
            width=button_width(stu_label, font(10), 134),
            height=38, bg=COLORS["card"], fg="#334155", hover_bg="#F8FAFC",
            border=COLORS["border"], radius=10, parent_bg=COLORS["bg"],
            font_obj=font(10))
        self.stu_btn.pack(side=tk.LEFT, padx=(10, 0))

        # 表格卡片
        card = tk.Frame(page, bg=COLORS["card"],
                        highlightbackground=COLORS["border"], highlightthickness=1)
        card.pack(fill=tk.BOTH, expand=True)
        shell = tk.Frame(card, bg=COLORS["card"])
        shell.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)

        self.tree = ttk.Treeview(
            shell, columns=[c[0] for c in self.COLUMNS], show="headings",
            height=8, style="Data.Treeview", selectmode="browse")
        for name, width, anchor in self.COLUMNS:
            self.tree.heading(name, text=name, anchor=anchor)
            stretch = name in ("班级名称", "上课时间")
            self.tree.column(name, anchor=anchor, width=width,
                             stretch=stretch, minwidth=60)
        self.tree.tag_configure("odd", background=COLORS["card"],
                                foreground=COLORS["text"])
        self.tree.tag_configure("even", background=COLORS["row_alt"],
                                foreground=COLORS["text"])
        self.tree.bind("<<TreeviewSelect>>", lambda _e: self._sync_buttons())
        self.tree.bind("<Double-1>", lambda _e: self.edit_class())

        scrollbar = ttk.Scrollbar(shell, orient="vertical", command=self.tree.yview,
                                  style="Slim.Vertical.TScrollbar")
        self.tree.configure(yscrollcommand=scrollbar.set)
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y, padx=(4, 0))

        self.status = _label(page, "", 9, color=COLORS["muted"])
        self.status.pack(anchor="w", pady=(10, 0))
        _label(page, "提示：双击一行可直接编辑；「班级编号」就是点名页下拉框里显示的编号",
               9, color=COLORS["subtle"]).pack(anchor="w", pady=(2, 0))
        _label(page, "班级和学员都能用「导入 Excel」批量创建：先下载模板，填好后预览再导入",
               9, color=COLORS["subtle"]).pack(anchor="w")

    def show_page(self, name, cid=None):
        """切换班级 / 学生页；学生页每次进入都重读班级，避免看到过期的下拉项。

        cid 用于从班级页直达：跳到学生页后自动锁定这个班，不用再手动选。
        """
        if name == self._page and getattr(self, "_page_ready", False):
            if name == "stu":
                self.student_page.refresh_all()
                if cid:
                    self.student_page.select_class(cid)
            return
        self._page = name
        if name == "class":
            self.student_page.pack_forget()
            self.class_page.pack(fill=tk.BOTH, expand=True)
        else:
            self.class_page.pack_forget()
            self.student_page.pack(fill=tk.BOTH, expand=True)
            self.student_page.refresh_all()
            if cid:
                self.student_page.select_class(cid)
        self.nav_class.set_selected(name == "class")
        self.nav_stu.set_selected(name == "stu")
        self._page_ready = True

    # ---------- 数据 ----------
    def load_rows(self):
        """读班级与学生数；返回 (classes, counts)，失败时抛异常。"""
        classes = TClassMapper().select_all()
        counts = TStuMapper().counts_by_class()
        return classes, counts

    def refresh(self):
        self.tree.delete(*self.tree.get_children())
        self._classes = {}
        self._counts = {}
        try:
            classes, counts = self.load_rows()
        except Exception as e:
            self.status.config(text="读取班级失败：%s" % e, fg=COLORS["danger"])
            self.edit_btn.set_enabled(False)
            self.del_btn.set_enabled(False)
            self.stu_btn.set_enabled(False)
            return False

        for idx, c in enumerate(classes):
            cid = str(c.class_id)
            self._classes[cid] = c
            self._counts[cid] = counts.get(cid, 0)
            self.tree.insert(
                "", tk.END, iid=cid,
                values=(cid, c.class_name or "—", c.class_type or "—",
                        c.class_day or "—", c.class_time or "—",
                        self._fmt_hour(c.class_hour), self._counts[cid]),
                tags=("even",) if idx % 2 else ("odd",),
            )
        self.status.config(
            text="共 %d 个班级、%d 名学生" % (len(classes), sum(self._counts.values())),
            fg=COLORS["muted"])
        self._sync_buttons()
        return True

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

    def _sync_buttons(self):
        has_selection = self._selected() is not None
        self.edit_btn.set_enabled(has_selection)
        self.del_btn.set_enabled(has_selection)
        self.stu_btn.set_enabled(has_selection)

    def selected_class(self):
        cid = self._selected()
        return self._classes.get(cid) if cid else None

    # ---------- 增删改 ----------
    def add_class(self):
        if self._form_open():
            return
        self._form = ClassFormDialog(self)

    def open_import(self, mode="class"):
        """打开 Excel 导入窗口；已经开着就切到对应模式并提到前面。"""
        dialog = self._import_dialog
        if dialog is not None:
            try:
                if dialog.win.winfo_exists():
                    dialog.set_mode(mode)
                    dialog.win.deiconify()
                    dialog.win.lift()
                    dialog.win.focus_force()
                    return dialog
            except tk.TclError:
                pass
            self._import_dialog = None
        self._import_dialog = ImportDialog(self, mode)
        return self._import_dialog

    def import_from_current_page(self):
        """顶部入口跟着当前页走：在班级页就导班级，在学生页就导学员。"""
        return self.open_import("class" if self._page == "class" else "stu")

    def edit_class(self):
        if self._form_open():
            return
        current = self.selected_class()
        if current is None:
            messagebox.showinfo("请先选择", "请先在列表里选中一个班级", parent=self.win)
            return
        self._form = ClassFormDialog(self, current)

    def delete_class(self):
        current = self.selected_class()
        if current is None:
            messagebox.showinfo("请先选择", "请先在列表里选中一个班级", parent=self.win)
            return
        cid = str(current.class_id)
        count = self._counts.get(cid, 0)
        if count:
            question = ("班级 %s 还有 %d 名学生。\n\n"
                        "删除后班级会从列表消失，学生记录仍保留在数据库中，"
                        "但不再属于任何班级。\n\n确定删除吗？") % (cid, count)
        else:
            question = "确定删除班级 %s 吗？" % cid
        if not messagebox.askyesno("删除班级", question, icon="warning",
                                   default="no", parent=self.win):
            return
        try:
            TClassMapper().delete(cid)
        except Exception as e:
            messagebox.showerror("删除失败", str(e), parent=self.win)
            return
        self.on_changed("已删除班级 %s" % cid)

    def manage_students(self):
        """从班级页直达该班学生列表，省得切页后再找一遍班级。"""
        current = self.selected_class()
        if current is None:
            messagebox.showinfo("请先选择", "请先在列表里选中一个班级", parent=self.win)
            return
        self.show_page("stu", cid=str(current.class_id))

    def on_changed(self, message=""):
        """任何写操作成功后：刷新列表 + 让主界面重载数据。"""
        self.refresh()
        if message:
            self.status.config(text=message, fg=COLORS["success"])
        self.app.reload_after_admin()

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

    def change_password(self):
        ChangePasswordDialog(self)

    # ---------- 关闭 ----------
    def close(self):
        try:
            self.win.grab_release()
        except tk.TclError:
            pass
        self.win.destroy()
        setattr(self.app, "_admin_dialog", None)


# ---------------------------------------------------------------- 班级表单


class ClassFormDialog:
    """新增 / 编辑班级。编辑时允许改编号，改动会迁移学生与签到记录。"""

    def __init__(self, admin, original=None):
        self.admin = admin
        self.original = original
        self.is_new = original is None

        self.win = tk.Toplevel(admin.win)
        self.win.title("新增班级" if self.is_new else "编辑班级 %s" % original.class_id)
        self.win.configure(bg=COLORS["bg"])
        self.win.resizable(False, False)
        self.win.transient(admin.win)

        self.var_id = tk.StringVar(value="" if self.is_new else str(original.class_id))
        self.var_name = tk.StringVar(value="" if self.is_new else (original.class_name or ""))
        self.var_type = tk.StringVar(value="" if self.is_new else (original.class_type or ""))
        self.var_day = tk.StringVar(value="周六" if self.is_new else (original.class_day or ""))
        self.var_time = tk.StringVar(value="" if self.is_new else (original.class_time or ""))
        self.var_hour = tk.StringVar(
            value="2.5" if self.is_new else self._hour_text(original.class_hour))

        self._build()
        _center_on(self.win, admin.win, ratio=3)
        self.win.protocol("WM_DELETE_WINDOW", self.cancel)
        self.win.bind("<Escape>", lambda _e: self.cancel())
        self.win.bind("<Return>", lambda _e: self.save())
        try:
            self.win.grab_set()
        except tk.TclError:
            pass
        self.entries["班级编号 *"].focus_set()

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

        _label(root, "新增班级" if self.is_new else "编辑班级", 15, True).pack(anchor="w")
        _label(root, "带 * 的为必填项", 9, color=COLORS["muted"]).pack(
            anchor="w", pady=(3, 12))

        form = tk.Frame(root, bg=COLORS["bg"])
        form.pack(fill=tk.X)
        form.grid_columnconfigure(0, weight=1)

        self.entries = {}
        row = 0
        entry_id = _entry(form, self.var_id, width=34)
        self.entries["班级编号 *"] = entry_id
        row = self._field(form, row, "班级编号 *", entry_id,
                          "点名页下拉框显示的就是它，建议用 00、01 这样的编号")

        entry_name = _entry(form, self.var_name, width=34)
        self.entries["班级名称"] = entry_name
        row = self._field(form, row, "班级名称", entry_name,
                          "如 01_周六 8.20-10.20 python")

        entry_type = _entry(form, self.var_type, width=34)
        self.entries["课程类型"] = entry_type
        row = self._field(form, row, "课程类型", entry_type, "如 python / scratch")

        self.day_box = ttk.Combobox(form, textvariable=self.var_day, values=WEEKDAYS,
                                    state="readonly", style="Class.TCombobox",
                                    width=34)
        row = self._field(form, row, "星期 *", self.day_box, "")

        entry_time = _entry(form, self.var_time, width=34)
        self.entries["上课时间"] = entry_time
        row = self._field(form, row, "上课时间", entry_time, "如 08.20-10.20")

        entry_hour = _entry(form, self.var_hour, width=34)
        self.entries["默认课时 *"] = entry_hour
        row = self._field(form, row, "默认课时 *", entry_hour,
                          "点名表格里每名学生的初始课时，如 2.5")

        if not self.is_new:
            _label(root, "改「班级编号」会把这个班的学生和签到记录一起迁移到新编号",
                   9, color=COLORS["warn"]).pack(anchor="w", pady=(10, 0))

        self.error = _label(root, "", 9, color=COLORS["danger"])
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

    def _field(self, parent, row, label_text, widget, hint=""):
        """一行字段：标签在上、控件在下；提示文字挂在右侧。返回下一行行号。"""
        _label(parent, label_text, 10, color=COLORS["muted"]).grid(
            row=row, column=0, sticky="w", pady=(0, 3))
        widget.grid(row=row + 1, column=0, sticky="we", ipady=6, pady=(0, 2))
        if hint:
            _label(parent, hint, 8, color=COLORS["subtle"]).grid(
                row=row + 1, column=1, sticky="w", padx=(12, 0))
        return row + 2

    # ---------- 保存 ----------
    def _fail(self, message, field=None):
        self.error.config(text=message)
        entry = self.entries.get(field) if field else None
        if entry is not None:
            entry.focus_set()
        return False

    def _collect(self):
        cid = self.var_id.get().strip()
        if not cid:
            return self._fail("请填写班级编号", "班级编号 *")
        if re.search(r"\s", cid):
            return self._fail("班级编号不能包含空格", "班级编号 *")
        if len(cid) > 20:
            return self._fail("班级编号最多 20 个字符", "班级编号 *")
        if self.is_new and cid in self.admin._classes:
            return self._fail("班级编号 %s 已存在" % cid, "班级编号 *")

        day = self.var_day.get().strip()
        if day not in WEEKDAYS:
            return self._fail("请在「星期」里选一个有效值（周一～周日）")

        hour_text = self.var_hour.get().strip()
        try:
            hour = float(hour_text)
        except ValueError:
            return self._fail("默认课时要填数字，例如 2.5", "默认课时 *")
        if not (0 < hour <= 24):
            return self._fail("默认课时应在 0～24 之间", "默认课时 *")

        keep_val = getattr(self.original, "class_val", None) if self.original else None
        return TClass(
            class_id=cid,
            class_type=self.var_type.get().strip() or None,
            class_time=self.var_time.get().strip() or None,
            class_name=self.var_name.get().strip() or None,
            class_hour=hour,
            class_val=1 if keep_val in (None, "") else keep_val,
            class_day=day,
        )

    def save(self):
        t_class = self._collect()
        if t_class is False:
            return
        mapper = TClassMapper()
        try:
            if self.is_new:
                mapper.insert(t_class)
            else:
                mapper.update(t_class, str(self.original.class_id))
        except Exception as e:
            text = str(e)
            if "Duplicate entry" in text:
                return self._fail("班级编号 %s 已存在" % t_class.class_id, "班级编号 *")
            self.error.config(text="保存失败：%s" % text)
            return

        message = ("已新增班级 %s" if self.is_new else "已更新班级 %s") % t_class.class_id
        self._close()
        self.admin._form = None
        self.admin.on_changed(message)

    def cancel(self):
        self._close()
        self.admin._form = None

    def _close(self):
        try:
            self.win.grab_release()
        except tk.TclError:
            pass
        self.win.destroy()


# ---------------------------------------------------------------- 修改口令


class ChangePasswordDialog:
    def __init__(self, admin):
        self.admin = admin
        self.win = tk.Toplevel(admin.win)
        self.win.title("修改管理员口令")
        self.win.configure(bg=COLORS["bg"])
        self.win.resizable(False, False)
        self.win.transient(admin.win)

        root = tk.Frame(self.win, bg=COLORS["bg"])
        root.pack(fill=tk.BOTH, expand=True, padx=24, pady=(20, 16))

        _label(root, "修改管理员口令", 15, True).pack(anchor="w")
        _label(root, "口令以加盐哈希保存，忘记后删除程序目录的 admin.json 恢复默认",
               9, color=COLORS["muted"]).pack(anchor="w", pady=(3, 12))

        self.var_old = tk.StringVar()
        self.var_new = tk.StringVar()
        self.var_repeat = tk.StringVar()
        self.entries = {}
        for idx, (text, var) in enumerate((("当前口令", self.var_old),
                                          ("新口令", self.var_new),
                                          ("确认新口令", self.var_repeat))):
            _label(root, text, 10, color=COLORS["muted"]).pack(anchor="w",
                                                               pady=(0 if idx == 0 else 10, 3))
            entry = _entry(root, var, width=30, show="*")
            entry.pack(fill=tk.X, ipady=6)
            self.entries[text] = entry

        self.error = _label(root, "", 9, color=COLORS["danger"])
        self.error.pack(anchor="w", pady=(10, 0))

        foot = tk.Frame(root, bg=COLORS["bg"])
        foot.pack(fill=tk.X, pady=(12, 0))
        RoundedButton(foot, text="保存口令", command=self.save, width=104, height=38,
                      radius=10, parent_bg=COLORS["bg"],
                      font_obj=font(10)).pack(side=tk.RIGHT)
        RoundedButton(foot, text="取消", command=self._close, width=96, height=38,
                      bg=COLORS["card"], fg="#334155", hover_bg="#F8FAFC",
                      border=COLORS["border"], radius=10, parent_bg=COLORS["bg"],
                      font_obj=font(10)).pack(side=tk.RIGHT, padx=(0, 10))

        _center_on(self.win, admin.win, ratio=3)
        self.win.bind("<Escape>", lambda _e: self._close())
        self.win.bind("<Return>", lambda _e: self.save())
        self.win.protocol("WM_DELETE_WINDOW", self._close)
        try:
            self.win.grab_set()
        except tk.TclError:
            pass
        self.entries["当前口令"].focus_set()

    def save(self):
        old, new, repeat = self.var_old.get(), self.var_new.get(), self.var_repeat.get()
        if not verify_password(old):
            self.error.config(text="当前口令不正确")
            self.var_old.set("")
            self.entries["当前口令"].focus_set()
            return
        if len(new) < MIN_PASSWORD_LEN:
            self.error.config(text="新口令至少 %d 位" % MIN_PASSWORD_LEN)
            self.entries["新口令"].focus_set()
            return
        if new != repeat:
            self.error.config(text="两次输入的新口令不一致")
            self.var_repeat.set("")
            self.entries["确认新口令"].focus_set()
            return
        try:
            path = set_admin_password(new)
        except Exception as e:
            self.error.config(text="保存失败：%s" % e)
            return
        self._close()
        messagebox.showinfo("口令已更新",
                            "管理员口令已更新，下次进入需要输入新口令。\n\n配置文件：%s" % path,
                            parent=self.admin.win)

    def _close(self):
        try:
            self.win.grab_release()
        except tk.TclError:
            pass
        self.win.destroy()
