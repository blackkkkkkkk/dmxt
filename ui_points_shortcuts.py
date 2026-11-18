# -*- coding: utf-8 -*-
"""积分管理 →「快捷键设置」弹窗。

至少 5 行槽位，每行 = 自定义按键 + 自定义分值 + 自定义备注；
按键留空就是留白，不生效。按键可以点「录制」后直接按，也可以手输
F1 / Ctrl+1 / a。保存后交给 points_shortcuts.ShortcutManager 重新绑定。
"""

import ctypes
import sys
import tkinter as tk
from tkinter import messagebox

from points_shortcuts import (MAX_DELTA, MAX_NOTE, MAX_SLOTS, SLOT_COUNT,
                              empty_slot, key_display, load_slots,
                              normalize_key, save_slots)
from ui_theme import COLORS, RoundedButton, button_width, font
from ui_widgets import fit_dialog_to_screen, form_entry

_HINT = (
    "· 至少 5 行，可全部留白；按键留空的行不生效。\n"
    "· 按键：点「录制」后直接按，或手输 F1 / Ctrl+1 / a（可配 Ctrl / Alt / Shift）。\n"
    "· 分值正加负减（±%d），备注随流水保存，可留空（最多 %d 字）。\n"
    "· 只对积分页当前选中的学员生效；光标在输入框时，字母 / 数字快捷键不触发。"
) % (MAX_DELTA, MAX_NOTE)

_MODIFIER_KEYS = frozenset((
    "Control_L", "Control_R", "Shift_L", "Shift_R", "Alt_L", "Alt_R",
    "Meta_L", "Meta_R", "Caps_Lock", "Num_Lock", "Super_L", "Super_R"))

# Windows 的 Tk 里 event.state 的 0x8 是 NumLock（不是 Alt），Alt 是 0x20000；
# X11 下才是 Mod1=0x8。早先两平台共用 (0x8|0x20000) 判断，结果只要开着
# 数字锁，录 F1 就会变成 Alt+F1。
_ALT_STATE_WIN = 0x20000
_VK_MENU = 0x12


def _alt_down(state):
    """录制按键时判断 Alt 是否真的被按住。"""
    if state & _ALT_STATE_WIN:
        return True
    if sys.platform == "win32":
        # 少数键盘布局 / 输入法下 state 不带 Alt 位，再问一次物理键
        try:
            return bool(ctypes.windll.user32.GetAsyncKeyState(_VK_MENU) & 0x8000)
        except Exception:
            return False
    return bool(state & 0x8)


def open_shortcut_settings(app, on_saved=None):
    return ShortcutSettingsDialog(app, on_saved=on_saved)


class ShortcutSettingsDialog:
    def __init__(self, app, on_saved=None):
        self.app = app
        self.on_saved = on_saved
        self._recording = None
        self.key_vars = []
        self.delta_vars = []
        self.note_vars = []
        self.rec_buttons = []

        manager = getattr(app, "shortcuts", None)
        slots = list(manager.slots) if manager is not None else load_slots()

        win = tk.Toplevel(app.master)
        self.win = win
        win.title("积分快捷键设置")
        win.configure(bg=COLORS["bg"])
        win.transient(app.master)
        win.resizable(True, True)
        win.bind("<Escape>", lambda _e: self.close())

        root = tk.Frame(win, bg=COLORS["bg"])
        root.pack(fill=tk.BOTH, expand=True, padx=18, pady=(14, 14))

        tk.Label(root, text="积分快捷键设置", bg=COLORS["bg"],
                 fg=COLORS["text"], font=font(16, True)).pack(anchor="w")
        tk.Label(root,
                 text="选中学生后按快捷键，立即按设定分值加 / 扣分，并写入对应备注",
                 bg=COLORS["bg"], fg=COLORS["muted"], font=font(9)).pack(
            anchor="w", pady=(3, 12))

        grid = tk.Frame(root, bg=COLORS["bg"])
        grid.pack(fill=tk.X)
        headers = (("序号", 0), ("快捷键", 1), ("", 2), ("分值", 3),
                   ("备注", 4), ("", 5))
        for text, col in headers:
            if text:
                tk.Label(grid, text=text, bg=COLORS["bg"], fg=COLORS["muted"],
                         font=font(9, True)).grid(row=0, column=col, sticky="w",
                                                 pady=(0, 6), padx=(0, 10))

        self.grid = grid
        grid.columnconfigure(4, weight=1)
        for index in range(max(SLOT_COUNT, len(slots))):
            self.add_row(slots[index] if index < len(slots) else empty_slot())

        self.hint = tk.Label(root, text=_HINT, bg=COLORS["bg"],
                             fg=COLORS["muted"], font=font(9), justify=tk.LEFT,
                             anchor="w", wraplength=680)
        self.hint.pack(fill=tk.X, anchor="w", pady=(14, 10))

        bar = tk.Frame(root, bg=COLORS["bg"])
        bar.pack(fill=tk.X)
        self.add_button = RoundedButton(
            bar, text="＋ 添加一行", command=self.add_row_clicked,
            width=button_width("＋ 添加一行", font(9), 96), height=30, radius=8,
            bg=COLORS["primary"], fg="#FFFFFF",
            hover_bg=COLORS["primary_dark"], border=COLORS["primary"],
            parent_bg=COLORS["bg"], font_obj=font(9))
        self.add_button.pack(side=tk.LEFT)
        if len(self.key_vars) >= MAX_SLOTS:
            self.add_button.set_enabled(False)
        RoundedButton(
            bar, text="保存", command=self.save,
            width=button_width("保存", font(10), 92), height=34, radius=9,
            bg=COLORS["primary"], fg="#FFFFFF",
            hover_bg=COLORS["primary_dark"], border=COLORS["primary"],
            parent_bg=COLORS["bg"], font_obj=font(10)).pack(side=tk.RIGHT)
        RoundedButton(
            bar, text="取消", command=self.close,
            width=button_width("取消", font(10), 80), height=34, radius=9,
            bg=COLORS["card"], fg=COLORS["muted"],
            hover_bg=COLORS["row_alt"], border=COLORS["border"],
            parent_bg=COLORS["bg"], font_obj=font(10)).pack(
            side=tk.RIGHT, padx=(0, 10))

        fit_dialog_to_screen(win, min_w=720, min_h=380)
        win.grab_set()
        win.focus_force()

    # ------------------------------------------------------------ 行操作
    def add_row(self, slot=None):
        """追加一行槽位；默认 5 行起步，点「＋ 添加一行」可以加到 12 行。"""
        slot = slot or empty_slot()
        grid = self.grid
        index = len(self.key_vars)
        row = index + 1
        key_var = tk.StringVar(value=key_display(slot.get("key", "")))
        delta_var = tk.StringVar(value=str(slot.get("delta", "") or ""))
        note_var = tk.StringVar(value=str(slot.get("note", "") or ""))
        self.key_vars.append(key_var)
        self.delta_vars.append(delta_var)
        self.note_vars.append(note_var)

        tk.Label(grid, text="%d" % row, bg=COLORS["bg"],
                 fg=COLORS["muted"], font=font(10)).grid(
            row=row, column=0, sticky="w", padx=(0, 10))
        form_entry(grid, textvariable=key_var, width=14).grid(
            row=row, column=1, sticky="w", ipady=3, padx=(0, 8), pady=3)
        rec = RoundedButton(
            grid, text="录制", command=lambda i=index: self.start_record(i),
            width=button_width("录制", font(9), 52), height=26, radius=7,
            bg=COLORS["card"], fg=COLORS["primary"],
            hover_bg=COLORS["primary_soft"], border=COLORS["border"],
            parent_bg=COLORS["bg"], font_obj=font(9))
        rec.grid(row=row, column=2, sticky="w", padx=(0, 10))
        self.rec_buttons.append(rec)
        form_entry(grid, textvariable=delta_var, width=8).grid(
            row=row, column=3, sticky="w", ipady=3, padx=(0, 10))
        form_entry(grid, textvariable=note_var, width=30).grid(
            row=row, column=4, sticky="we", ipady=3, padx=(0, 10))
        RoundedButton(
            grid, text="清空", command=lambda i=index: self.clear_row(i),
            width=button_width("清空", font(9), 48), height=26, radius=7,
            bg=COLORS["card"], fg=COLORS["text"],
            hover_bg=COLORS["row_alt"], border=COLORS["border"],
            parent_bg=COLORS["bg"], font_obj=font(9)).grid(
            row=row, column=5, sticky="w")
        return index

    def add_row_clicked(self):
        if len(self.key_vars) >= MAX_SLOTS:
            self.hint.config(text="最多 %d 行，够了：多余的槽位留白即可" % MAX_SLOTS,
                             fg=COLORS["danger"])
            return
        self.add_row()
        fit_dialog_to_screen(self.win, min_w=720, min_h=380)
        if len(self.key_vars) >= MAX_SLOTS:
            self.add_button.set_enabled(False)
        self.hint.config(text="已加一行：填按键、分值和备注，或让它留白",
                         fg=COLORS["primary"])

    def clear_row(self, index):
        self.stop_record("已清空第 %d 行" % (index + 1))
        self.key_vars[index].set("")
        self.delta_vars[index].set("")
        self.note_vars[index].set("")

    # ------------------------------------------------------------ 录制
    def start_record(self, index):
        self.stop_record()
        self._recording = index
        self.rec_buttons[index].set_text("按键…")
        self.hint.config(text="请按下想使用的快捷键…（Esc 取消；只按 Ctrl / Alt / Shift 会继续等待）",
                         fg=COLORS["primary"])
        self.win.focus_force()
        self.win.bind("<Key>", self.on_record_key)

    def stop_record(self, message=""):
        try:
            self.win.unbind("<Key>")
        except tk.TclError:
            pass
        if self._recording is not None:
            try:
                self.rec_buttons[self._recording].set_text("录制")
            except (IndexError, tk.TclError):
                pass
        self._recording = None
        self.hint.config(text=message or _HINT, fg=COLORS["muted"])

    def on_record_key(self, event):
        """把一次真实按键转成可保存的显示文字；也支持用伪事件单独测试。"""
        if self._recording is None:
            return "break"
        keysym = str(getattr(event, "keysym", "") or "")
        if keysym == "Escape":
            self.stop_record("已取消录制")
            return "break"
        if keysym in _MODIFIER_KEYS:
            return "break"          # 只按下修饰键，继续等主按键
        state = int(getattr(event, "state", 0) or 0)
        mods = []
        if state & 0x4:
            mods.append("Ctrl")
        if _alt_down(state):
            mods.append("Alt")
        if state & 0x1:
            mods.append("Shift")
        key = keysym
        if key.upper() in ("F%d" % n for n in range(1, 13)):
            key = key.upper()
        elif len(key) == 1 and key.isascii() and key.isalnum():
            key = key.lower() if key.isalpha() else key
        else:
            self.hint.config(
                text="这个按键不支持，请用字母 / 数字 / F1~F12（可配 Ctrl、Alt、Shift）",
                fg=COLORS["danger"])
            return "break"
        seq, err = normalize_key("+".join(mods + [key]))
        if err:
            self.hint.config(text=err, fg=COLORS["danger"])
            return "break"
        index = self._recording
        self.key_vars[index].set(key_display(seq))
        self.stop_record("第 %d 行已录制 %s，记得填分值" %
                         (index + 1, key_display(seq)))
        return "break"

    # ------------------------------------------------------------ 保存
    def collect_rows(self):
        rows = [{"key": self.key_vars[i].get(),
                 "delta": self.delta_vars[i].get(),
                 "note": self.note_vars[i].get()}
                for i in range(len(self.key_vars))]
        # 加过又清空的行不必写进配置，下次打开还是干净的 5 行
        while len(rows) > SLOT_COUNT and all(
                not str(value).strip()
                for value in rows[-1].values()):
            rows.pop()
        return rows

    def save(self):
        from points_shortcuts import ShortcutManager, validate_slots
        self.stop_record()
        slots, errors = validate_slots(self.collect_rows())
        if errors:
            messagebox.showerror("快捷键设置没保存", "\n".join(errors[:8]),
                                 parent=self.win)
            return False
        try:
            save_slots(slots)
        except Exception as exc:
            messagebox.showerror("保存失败", "配置写不进去：%s" % exc,
                                 parent=self.win)
            return False
        manager = getattr(self.app, "shortcuts", None)
        if manager is None:
            manager = ShortcutManager(self.app)
            self.app.shortcuts = manager
        manager.reload(slots)
        if self.on_saved:
            self.on_saved(slots)
        self.close()
        return True

    def close(self):
        self.stop_record()
        try:
            self.win.grab_release()
        except tk.TclError:
            pass
        try:
            self.win.destroy()
        except tk.TclError:
            pass
