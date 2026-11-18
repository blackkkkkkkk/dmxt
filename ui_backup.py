"""数据备份与恢复面板。

数据都在一个 dmxt.db 文件里，这里提供一键备份、从备份恢复、删除旧备份。
恢复是覆盖操作，会先弹确认框，并在恢复前自动把当前数据另存一份。
"""

import logging
import os
import tkinter as tk
from tkinter import messagebox
from tkinter import ttk

from service import backup as bk
from ui_theme import COLORS, RoundedButton, font
from ui_widgets import center_on, form_label


def open_backup_dialog(app):
    return BackupDialog(app)


class BackupDialog:
    def __init__(self, app):
        self.app = app
        win = tk.Toplevel(app.master)
        self.win = win
        win.title("备份与恢复")
        win.configure(bg=COLORS["bg"])
        win.resizable(False, False)
        win.transient(app.master)

        root = tk.Frame(win, bg=COLORS["bg"])
        root.pack(fill=tk.BOTH, expand=True, padx=22, pady=(18, 16))

        form_label(root, "数据备份与恢复", 15, True).pack(anchor="w")
        form_label(
            root,
            "所有数据都在一个数据库文件里，备份就是复制它。\n"
            "建议每次大改动或结课后备份一次，以防误删。",
            9, color=COLORS["muted"]).pack(anchor="w", pady=(4, 12))

        # 数据库位置提示
        self.loc = form_label(root, "", 9, color=COLORS["muted"])
        self.loc.pack(anchor="w", pady=(0, 10))
        self._refresh_loc()

        # 顶部操作按钮
        btn_row = tk.Frame(root, bg=COLORS["bg"])
        btn_row.pack(fill=tk.X)
        RoundedButton(
            btn_row, text="立即备份", command=self.do_backup,
            width=110, height=36, radius=10, parent_bg=COLORS["bg"],
            font_obj=font(10)).pack(side=tk.LEFT)
        RoundedButton(
            btn_row, text="打开备份目录", command=self.open_dir,
            width=130, height=36, radius=10, bg=COLORS["card"], fg="#334155",
            hover_bg="#F8FAFC", border=COLORS["border"], parent_bg=COLORS["bg"],
            font_obj=font(10)).pack(side=tk.LEFT, padx=(10, 0))

        # 备份列表
        form_label(root, "已有备份（双击恢复，右键删除）", 10, True).pack(
            anchor="w", pady=(16, 6))
        list_frame = tk.Frame(root, bg=COLORS["card"],
                              highlightbackground=COLORS["border"],
                              highlightthickness=1)
        list_frame.pack(fill=tk.BOTH, expand=True)

        self.tree = ttk.Treeview(
            list_frame, columns=("时间", "大小"), show="headings",
            style="Data.Treeview", height=8)
        self.tree.heading("时间", text="备份时间", anchor="w")
        self.tree.heading("大小", text="大小", anchor="center")
        self.tree.column("时间", anchor="w", width=250)
        self.tree.column("大小", anchor="center", width=90)
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=6, pady=6)
        self.tree.bind("<Double-1>", self.on_restore)
        self.tree.bind("<Button-3>", self.on_delete)

        # 底部按钮
        foot = tk.Frame(root, bg=COLORS["bg"])
        foot.pack(fill=tk.X, pady=(14, 0))
        RoundedButton(
            foot, text="关闭", command=self.close,
            width=92, height=36, radius=10, bg=COLORS["card"], fg="#334155",
            hover_bg="#F8FAFC", border=COLORS["border"], parent_bg=COLORS["bg"],
            font_obj=font(10)).pack(side=tk.RIGHT)

        self.refresh_list()

        center_on(win, app.master)
        try:
            win.grab_set()
        except tk.TclError:
            pass

    def _refresh_loc(self):
        try:
            from service import dp
            self.loc.config(text="数据文件：%s" % dp.db_path())
        except Exception:
            pass

    def refresh_list(self):
        self.tree.delete(*self.tree.get_children())
        for path, name, size, mtime in bk.list_backups():
            import time as _time
            stamp = _time.strftime("%Y-%m-%d %H:%M", _time.localtime(mtime))
            self.tree.insert("", tk.END, iid=path,
                             values=(stamp, bk.human_size(size)))

    def _selected_path(self):
        sel = self.tree.selection()
        if not sel:
            return None
        return sel[0]

    def do_backup(self):
        path = bk.create_backup()
        if path is None:
            messagebox.showwarning(
                "备份", "当前还没有数据库文件（可能尚未保存过任何数据）。\n"
                        "先正常使用、保存几次签到后再备份。")
            return
        self.refresh_list()
        messagebox.showinfo("备份成功", "已备份到：\n%s" % path)

    def open_dir(self):
        d = bk.backup_dir()
        try:
            os.makedirs(d, exist_ok=True)
            os.startfile(d)  # noqa: E999
        except Exception:
            messagebox.showinfo("备份目录", "备份目录：\n%s" % d)

    def on_restore(self, _event=None):
        path = self._selected_path()
        if not path:
            return
        name = os.path.basename(path)
        if not messagebox.askyesno(
                "恢复确认",
                "将用备份「%s」覆盖当前数据。\n\n"
                "当前数据会先自动另存一份，不会被直接丢弃。\n\n"
                "确定要恢复吗？" % name):
            return
        ok, msg = bk.restore_backup(path)
        if ok:
            messagebox.showinfo("恢复完成", msg)
            self.refresh_list()
            # 让主界面立即用恢复后的数据
            self._reload_main()
        else:
            messagebox.showerror("恢复失败", msg)

    def on_delete(self, _event=None):
        path = self._selected_path()
        if not path:
            return
        name = os.path.basename(path)
        if not messagebox.askyesno("删除备份", "确定删除备份「%s」吗？" % name):
            return
        ok, msg = bk.delete_backup(path)
        if ok:
            self.refresh_list()
        else:
            messagebox.showerror("删除失败", msg)

    def _reload_main(self):
        """恢复后让主界面重新读库，界面跟着变。"""
        try:
            # 清掉内存里的今日签到缓存，强制重读
            self.app.today_sign = {}
            self.app.today_sign_loaded = False
            self.app.load_student_data()
            # 恢复的是旧备份时，库里可能还没有积分流水表：强制补建一次，
            # 否则本进程内 _schema_ready 仍是 True，进积分页会报「no such table」
            try:
                from mapper.t_points_mapper import ensure_points_schema
                ensure_points_schema(force=True)
            except Exception:
                logging.exception("恢复后补建积分表失败")
            # 恢复的备份可能早于积分商城：商品表/兑换记录表也要补建
            try:
                from mapper.t_shop_mapper import ensure_shop_schema
                ensure_shop_schema(force=True)
            except Exception:
                logging.exception("恢复后补建积分商城表失败")
            # 恢复的可能是旧结构备份：学生映射表也要补建并重新合并旧数据
            try:
                from mapper.t_stu_mapper import ensure_stu_map
                ensure_stu_map(force=True)
            except Exception:
                logging.exception("恢复后整理学生映射表失败")
            self.app.load_today_sign(force=True)
            self.app.sign_in_status = self.app.init_sign_in_status()
            self.app.db_error = ""
            self.app.update_db_status()
            self.app.refresh_current_page()
        except Exception as exc:  # noqa: BLE001
            messagebox.showwarning(
                "提示", "数据已恢复，但界面刷新失败，请重启程序查看：%s" % exc)

    def close(self):
        try:
            self.win.destroy()
        except tk.TclError:
            pass
