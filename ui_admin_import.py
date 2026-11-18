# -*- coding: utf-8 -*-
"""管理员设置 · Excel 导入：班级 / 学员。

一个对话框两种模式，流程都是「下载模板 → 选择文件 → 预览校验结果 → 开始导入」：
预览表先按行号列出每一行会被怎么处理（新增 / 更新 / 已有 / 跳过 / 错误），
用户确认无误再写库，避免表格里某个错别字悄悄带进数据库。

导入只做数据落库，不碰口令与签到；导入完成后由调用方（管理员设置）统一刷新
班级列表、学生列表和主界面。
"""

import os
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import excel_import
from excel_io import FILE_TYPES, ExcelReadError
from ui_theme import COLORS, RoundedButton, button_width, font
from ui_widgets import (center_on, check_box, fit_dialog_to_screen, form_label)

MODE_TITLES = {"class": "导入班级", "stu": "导入学员"}

HINTS = {
    "class": "必填：班级编号、星期。选填：班级名称、课程类型、上课时间、默认课时。"
             "首行须为列名，顺序不限。",
    "stu": "必填：姓名。选填：剩余课时、手机号、状态、班级（多个用「、」分隔）。"
           "首行须为列名，顺序不限。",
}

UPDATE_LABELS = {
    "class": "编号已存在时更新资料",
    "stu": "姓名已存在时更新资料（课时/手机号/状态）",
}

TEMPLATE_NAMES = {"class": "班级导入模板.xlsx", "stu": "学员导入模板.xlsx"}

RESULT_TAGS = {
    "新增": "new",
    "更新": "update",
    "已有": "update",
    "跳过": "skip",
    "错误": "bad",
}


class ImportDialog:
    """Excel 导入窗口：左上空一行，表格里逐行预览校验结果。"""

    COLUMNS = (
        ("行", 60, "center"),
        ("结果", 96, "center"),
        ("内容", 350, "w"),
        ("说明", 310, "w"),
    )

    def __init__(self, admin, mode="class"):
        self.admin = admin
        self.mode = "class"
        self.path = ""
        self.items = []
        self.report = None

        self.win = tk.Toplevel(admin.win)
        self.win.title("Excel 导入")
        self.win.configure(bg=COLORS["bg"])
        self.win.transient(admin.win)

        self.var_update = tk.BooleanVar(value=False)

        self._build()
        fit_dialog_to_screen(self.win, tree=self.tree, min_rows=6,
                             min_w=820, min_h=520)
        # 提示文字按实际宽度折行后可能多出一行，重新量一次高度，避免底部按钮被挤出窗口
        try:
            self.hint.config(wraplength=max(260, self.win.winfo_width() - 40))
            self.win.update_idletasks()
            fit_dialog_to_screen(self.win, tree=self.tree, min_rows=6,
                                 min_w=820, min_h=520)
        except tk.TclError:
            pass
        center_on(self.win, admin.win, ratio=3)
        self.win.protocol("WM_DELETE_WINDOW", self.close)
        self.win.bind("<Escape>", lambda _e: self.close())
        self.set_mode(mode)
        try:
            self.win.grab_set()
        except tk.TclError:
            pass

    # ---------- 布局 ----------
    def _build(self):
        root = tk.Frame(self.win, bg=COLORS["bg"])
        root.pack(fill=tk.BOTH, expand=True, padx=22, pady=(18, 16))

        form_label(root, "Excel 导入", 15, True, bg=COLORS["bg"]).pack(anchor="w")
        form_label(root, "从 Excel / WPS / CSV 批量导入班级或学员；先预览，确认无误再写入数据库",
                   9, color=COLORS["muted"], bg=COLORS["bg"]).pack(
            anchor="w", pady=(3, 12))

        # 模式切换
        modes = tk.Frame(root, bg=COLORS["bg"])
        modes.pack(fill=tk.X)
        self.btn_class = RoundedButton(
            modes, text="导入班级", command=lambda: self.set_mode("class"),
            width=104, height=36, bg=COLORS["card"], fg="#334155",
            hover_bg="#F8FAFC", border=COLORS["border"], radius=10,
            parent_bg=COLORS["bg"], font_obj=font(10))
        self.btn_class.pack(side=tk.LEFT)
        self.btn_stu = RoundedButton(
            modes, text="导入学员", command=lambda: self.set_mode("stu"),
            width=104, height=36, bg=COLORS["card"], fg="#334155",
            hover_bg="#F8FAFC", border=COLORS["border"], radius=10,
            parent_bg=COLORS["bg"], font_obj=font(10))
        self.btn_stu.pack(side=tk.LEFT, padx=(8, 0))

        RoundedButton(modes, text="下载模板", command=self.download_template,
                      width=button_width("下载模板", font(10), 96), height=36,
                      bg=COLORS["card"], fg="#334155", hover_bg="#F8FAFC",
                      border=COLORS["border"], radius=10, parent_bg=COLORS["bg"],
                      font_obj=font(10)).pack(side=tk.RIGHT)

        self.hint = form_label(root, "", 9, color=COLORS["muted"], bg=COLORS["bg"])
        self.hint.pack(anchor="w", fill=tk.X, pady=(10, 8))
        self.hint.config(justify="left")
        self.win.bind("<Configure>", self._on_resize)

        # 文件行
        line = tk.Frame(root, bg=COLORS["bg"])
        line.pack(fill=tk.X)
        self.var_path = tk.StringVar()
        self.entry_path = tk.Entry(
            line, textvariable=self.var_path, state="readonly",
            readonlybackground=COLORS["card"], fg=COLORS["text"],
            font=font(9), relief="flat", highlightthickness=1,
            highlightbackground=COLORS["border"])
        self.entry_path.pack(side=tk.LEFT, fill=tk.X, expand=True, ipady=6)
        RoundedButton(line, text="选择文件…", command=self.choose_file,
                      width=button_width("选择文件…", font(10), 104), height=36,
                      radius=10, parent_bg=COLORS["bg"], font_obj=font(10)).pack(
            side=tk.LEFT, padx=(8, 0))

        # 选项
        opt = tk.Frame(root, bg=COLORS["bg"])
        opt.pack(fill=tk.X, pady=(8, 6))
        self.chk_update = check_box(opt, UPDATE_LABELS["class"], self.var_update,
                                    command=self._reload_if_loaded)
        self.chk_update.pack(side=tk.LEFT)

        # 预览表
        card = tk.Frame(root, bg=COLORS["card"],
                        highlightbackground=COLORS["border"], highlightthickness=1)
        card.pack(fill=tk.BOTH, expand=True)
        shell = tk.Frame(card, bg=COLORS["card"])
        shell.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)

        self.tree = ttk.Treeview(
            shell, columns=[c[0] for c in self.COLUMNS], show="headings",
            height=9, style="Data.Treeview", selectmode="browse")
        for name, width, anchor in self.COLUMNS:
            self.tree.heading(name, text=name, anchor=anchor)
            self.tree.column(name, anchor=anchor, width=width,
                             stretch=name in ("内容", "说明"), minwidth=56)
        self.tree.tag_configure("odd", background=COLORS["card"],
                                foreground=COLORS["text"])
        self.tree.tag_configure("even", background=COLORS["row_alt"],
                                foreground=COLORS["text"])
        self.tree.tag_configure("new", foreground=COLORS["success"])
        self.tree.tag_configure("update", foreground=COLORS["primary"])
        self.tree.tag_configure("skip", foreground=COLORS["muted"])
        self.tree.tag_configure("bad", foreground=COLORS["danger"])

        scrollbar = ttk.Scrollbar(shell, orient="vertical", command=self.tree.yview,
                                  style="Slim.Vertical.TScrollbar")
        self.tree.configure(yscrollcommand=scrollbar.set)
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y, padx=(4, 0))

        self.status = form_label(root, "", 9, color=COLORS["muted"], bg=COLORS["bg"])
        self.status.pack(anchor="w", pady=(8, 0))
        form_label(root, "提示：预览里标红的行不会导入；改正后重新选文件即可。"
                         "导入不会删除任何现有数据。",
                   9, color=COLORS["subtle"], bg=COLORS["bg"]).pack(
            anchor="w", pady=(2, 0))

        foot = tk.Frame(root, bg=COLORS["bg"])
        foot.pack(fill=tk.X, pady=(10, 0))
        self.btn_import = RoundedButton(
            foot, text="开始导入", command=self.run_import, width=104, height=38,
            radius=10, parent_bg=COLORS["bg"], font_obj=font(10))
        self.btn_import.pack(side=tk.RIGHT)
        RoundedButton(foot, text="关闭", command=self.close, width=88, height=38,
                      bg=COLORS["card"], fg="#334155", hover_bg="#F8FAFC",
                      border=COLORS["border"], radius=10, parent_bg=COLORS["bg"],
                      font_obj=font(10)).pack(side=tk.RIGHT, padx=(0, 10))
        self.btn_import.set_enabled(False)

    # ---------- 模式 ----------
    def _on_resize(self, event):
        """窗口变宽变窄时同步提示文字的换行宽度，避免长句被裁掉。"""
        if event.widget is not self.win:
            return
        try:
            self.hint.config(wraplength=max(260, event.width - 40))
        except tk.TclError:
            pass

    def set_mode(self, mode):
        if mode not in MODE_TITLES:
            return
        if mode == self.mode and self.items:
            return
        self.mode = mode
        self.btn_class.set_selected(mode == "class")
        self.btn_stu.set_selected(mode == "stu")
        self.hint.config(text=HINTS[mode])
        self.chk_update.config(text=UPDATE_LABELS[mode])
        self.win.title("Excel 导入 · %s" % MODE_TITLES[mode])
        self._clear_preview()
        if self.path:
            self._load(self.path)

    def _clear_preview(self):
        self.tree.delete(*self.tree.get_children())
        self.items = []
        self.report = None
        self.btn_import.set_enabled(False)
        self.status.config(text="还没有选择文件：点右侧「选择文件…」或先「下载模板」",
                           fg=COLORS["muted"])

    # ---------- 选文件 / 预览 ----------
    def choose_file(self):
        path = filedialog.askopenfilename(
            parent=self.win, title="选择要导入的表格",
            filetypes=list(FILE_TYPES))
        if not path:
            return
        self._load(path)

    def _reload_if_loaded(self):
        if self.path:
            self._load(self.path)

    def _load(self, path):
        self.path = path
        self.var_path.set(path)
        try:
            if self.mode == "class":
                items = excel_import.preview_classes(path, self.var_update.get())
            else:
                items = excel_import.preview_students(path, self.var_update.get())
        except ExcelReadError as e:
            self._clear_preview()
            self.status.config(text="读取失败：%s" % e, fg=COLORS["danger"])
            messagebox.showerror("读取失败", str(e), parent=self.win)
            return
        except Exception as e:
            self._clear_preview()
            self.status.config(text="读取失败：%s" % e, fg=COLORS["danger"])
            messagebox.showerror("读取失败", "这个文件读不了：\n%s" % e, parent=self.win)
            return

        self.items = items
        self._render_items()
        if not items:
            self.status.config(text="表格里没有数据行（表头之后是空的）",
                               fg=COLORS["warn"])
            self.btn_import.set_enabled(False)
            return

        counts = {}
        for item in items:
            counts[item["status"]] = counts.get(item["status"], 0) + 1
        parts = ["%s %d" % (name, counts[name]) for name in
                 ("新增", "更新", "已有", "跳过", "错误") if counts.get(name)]
        importable = counts.get("新增", 0) + counts.get("更新", 0) + counts.get("已有", 0)
        self.status.config(
            text="共 %d 行：%s" % (len(items), "，".join(parts)),
            fg=COLORS["danger"] if counts.get("错误") else COLORS["muted"])
        self.btn_import.set_enabled(importable > 0)

    def _render_items(self):
        self.tree.delete(*self.tree.get_children())
        for index, item in enumerate(self.items):
            tag = RESULT_TAGS.get(item["status"], "odd")
            if tag in ("odd", "even"):
                tag = "odd" if index % 2 == 0 else "even"
            self.tree.insert("", tk.END,
                             values=(item["line"], item["status"],
                                     item["summary"], item["message"]),
                             tags=(tag,))

    # ---------- 模板 ----------
    def download_template(self):
        default = TEMPLATE_NAMES[self.mode]
        path = filedialog.asksaveasfilename(
            parent=self.win, title="保存导入模板", defaultextension=".xlsx",
            initialfile=default, filetypes=[("Excel 工作簿", "*.xlsx")])
        if not path:
            return
        try:
            if self.mode == "class":
                excel_import.write_class_template(path)
            else:
                excel_import.write_student_template(path)
        except Exception as e:
            messagebox.showerror("生成失败", str(e), parent=self.win)
            return
        messagebox.showinfo(
            "模板已生成",
            "模板已保存到：\n%s\n\n按表头填好数据后，点「选择文件…」导入。\n"
            "第二个工作表「填写说明」里写了每列的填法。" % path,
            parent=self.win)

    # ---------- 导入 ----------
    def run_import(self):
        if not self.path or not self.items:
            return
        importable = [i for i in self.items
                      if i["status"] in ("新增", "更新", "已有")]
        if not importable:
            messagebox.showinfo("没有可导入的行",
                                "当前表格里没有能导入的数据，请检查预览里的红色提示。",
                                parent=self.win)
            return
        update = bool(self.var_update.get())
        try:
            if self.mode == "class":
                report = excel_import.import_classes(self.path, update)
            else:
                report = excel_import.import_students(self.path, update)
        except ExcelReadError as e:
            messagebox.showerror("导入失败", str(e), parent=self.win)
            return
        except Exception as e:
            messagebox.showerror("导入失败", "写入数据库时出错：\n%s" % e,
                                 parent=self.win)
            return

        self.report = report
        self._refresh_admin()
        # 重新读一遍，让预览反映导入后的状态（已存在的行会变成「跳过/已有」）
        self._load(self.path)
        self.status.config(text="导入完成：%s" % report.summary(),
                           fg=COLORS["success"] if not report.failed
                           else COLORS["warn"])

        text = "%s 导入完成。\n\n%s" % (MODE_TITLES[self.mode], report.summary())
        errors = report.error_lines()
        if errors:
            text += "\n\n未导入的行：\n" + errors
        messagebox.showinfo("Excel 导入结果", text, parent=self.win)

    def _refresh_admin(self):
        admin = self.admin
        try:
            admin.refresh()
            page = getattr(admin, "student_page", None)
            if page is not None:
                page.refresh_all()
            admin.app.reload_after_admin()
        except Exception:
            pass

    # ---------- 关闭 ----------
    def close(self):
        try:
            self.win.grab_release()
        except tk.TclError:
            pass
        self.win.destroy()
        try:
            self.admin._import_dialog = None
        except Exception:
            pass
