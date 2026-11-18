# -*- coding: utf-8 -*-
"""积分管理：自定义快捷积分按键的配置、校验与运行时绑定。

配置存在数据目录下的 points_shortcuts.json（跟 ui_config.json 一样，
绿色版在程序目录、安装版在 %APPDATA%，换电脑时一起拷走即可）。

一个「槽位」= 按键 + 分值 + 备注：
  {"key": "<F1>", "delta": "5", "note": "课堂积极回答问题"}
按键留空 = 这行留白，不生效也不绑定；分值为正加分、为负扣分。

按键用 Tk 的绑定序列存储（如 "<Control-1>" / "<F2>" / "<a>"），
界面上显示成 "Ctrl+1" / "F2" / "A"，两种写法都能在设置框里手输。
模块只依赖标准库 + service.dp，不导入任何界面模块，方便单独测试。
"""

import json
import logging
import os
import re
import tkinter as tk

from service.dp import data_dir

CONFIG_NAME = "points_shortcuts.json"
SLOT_COUNT = 5              # 默认 5 行；配置文件里更多行也照读
MAX_SLOTS = 12              # 设置界面最多加到 12 行
MAX_DELTA = 9999
MAX_NOTE = 200              # 与 mapper.add_record 的截断长度保持一致

# 这些组合交给系统 / 输入框 / 已有功能，不允许被快捷键占用
RESERVED_SEQUENCES = {
    # 只管真正会被输入框 / 系统抢走的组合；Ctrl+Q、Ctrl+S 这类本程序没有
    # 绑定，放开给老师自己用（不然想去掉误加的 Alt 反而保存不了）
    "<Control-a>", "<Control-c>", "<Control-v>", "<Control-x>",
    "<Control-z>",
    "<Control-F4>", "<Alt-F4>", "<Alt-Tab>", "<Control-Tab>",
    "<Control-Escape>", "<Control-Shift-Escape>",
}

_MOD_ALIASES = {
    "ctrl": "Control", "control": "Control", "ctl": "Control",
    "alt": "Alt", "option": "Alt",
    "shift": "Shift",
}
_MOD_ORDER = ("Control", "Alt", "Shift")
_MOD_NAMES = frozenset(_MOD_ORDER)
_FUNC_KEY = re.compile(r"^F([1-9]|1[0-2])$", re.IGNORECASE)
_F_ONLY = re.compile(r"^<(?:Control-|Alt-|Shift-)*F(?:[1-9]|1[0-2])>$")


def config_path():
    return os.path.join(data_dir(), CONFIG_NAME)


def empty_slot():
    return {"key": "", "delta": "", "note": ""}


def empty_slots(count=SLOT_COUNT):
    return [empty_slot() for _ in range(max(SLOT_COUNT, int(count or 0)))]


def normalize_key(text):
    """把用户输入的按键文本规范成 Tk 绑定序列。

    返回 (seq, error)：出错时 seq 为 ""。接受 "F1" / "<F1>" / "Ctrl+A" /
    "<Control-a>" / "a" / "1" 等写法；只开放字母、数字、F1~F12 与
    Ctrl / Alt / Shift 修饰组合，避免把回车、方向键这类导航键抢走。
    """
    raw = str(text or "").strip()
    if not raw:
        return "", ""
    if raw.startswith("<") and raw.endswith(">"):
        raw = raw[1:-1].strip()
    if not raw:
        return "", ""
    # 兼容三种写法：Ctrl+1 / Control-1 / <Control-1>（后者是落盘格式，
    # 读回来时必须能再解析，否则重启后组合键会莫名其妙失效）
    parts = [p.strip() for p in re.split(r"[+-]", raw) if p.strip()]
    if not parts:
        return "", ""

    mods, keys = set(), []
    for part in parts:
        low = part.lower()
        if low in _MOD_ALIASES:
            mods.add(_MOD_ALIASES[low])
        elif part in _MOD_NAMES:
            mods.add(part)
        else:
            keys.append(part)
    if len(keys) != 1:
        return "", "快捷键只能包含一个主按键（如 F1、Ctrl+1、a）"

    key = keys[0]
    match = _FUNC_KEY.match(key)
    if match:
        key = "F%d" % int(match.group(1))
    elif len(key) == 1 and key.isalnum() and key.isascii():
        key = key.lower() if key.isalpha() else key
    else:
        return "", "只支持字母、数字、F1~F12，以及 Ctrl / Alt / Shift 组合键"

    if mods and not (key.isdigit() or key.isalpha() or _FUNC_KEY.match(key)):
        return "", "组合键的主按键只能是字母、数字或 F1~F12"
    # Shift + 数字在 Tk 里会变成符号键，绑不上；只允许 Shift + 字母 / F 键
    if "Shift" in mods and key.isdigit():
        return "", "Shift+数字无法可靠识别，请换 F 键或 Ctrl / Alt 组合"

    seq = "<" + "-".join([m for m in _MOD_ORDER if m in mods] + [key]) + ">"
    if seq in RESERVED_SEQUENCES:
        return "", "%s 是系统 / 编辑常用组合，请换一个按键" % key_display(seq)
    return seq, ""


def key_display(seq):
    """绑定序列 → 界面友好文字："<Control-1>" → "Ctrl+1"。"""
    raw = str(seq or "").strip()
    if not raw:
        return ""
    if raw.startswith("<") and raw.endswith(">"):
        raw = raw[1:-1]
    parts = [p for p in raw.split("-") if p]
    names = {"Control": "Ctrl", "Alt": "Alt", "Shift": "Shift"}
    out = [names.get(p, p) for p in parts[:-1]]
    last = parts[-1] if parts else ""
    out.append(last.upper() if len(last) == 1 and last.isalpha() else last)
    return "+".join(out)


def _delta_value(raw):
    """把分值文本转成规范字符串；不合法返回 None。"""
    text = str(raw or "").strip()
    if not text:
        return None
    try:
        value = int(text)
    except (TypeError, ValueError):
        return None
    if value == 0 or abs(value) > MAX_DELTA:
        return None
    return str(value)


def validate_slots(rows):
    """校验设置界面提交的若干行，返回 (slots, errors)。

    - 按键留空 = 留白行，整行保留但不生效，分值/备注都不校验；
    - 设了按键就必须填合法分值（非零、±9999 内），备注可空、最多 200 字；
    - 按键不能重复，也不能占用系统 / 编辑常用组合。
    """
    errors, slots, seen = [], [], {}
    rows = list(rows or [])
    for index in range(max(SLOT_COUNT, len(rows))):
        raw = rows[index] if index < len(rows) else {}
        if not isinstance(raw, dict):
            raw = {}
        note = str(raw.get("note", "") or "").strip()
        key_text = str(raw.get("key", "") or "").strip()
        seq, err = normalize_key(key_text)
        if err:
            errors.append("第 %d 行：%s" % (index + 1, err))
            seq = ""
        delta_text = str(raw.get("delta", "") or "").strip()
        if seq:
            if seq in seen:
                errors.append("第 %d 行：按键 %s 与第 %d 行重复" %
                              (index + 1, key_display(seq), seen[seq] + 1))
            seen[seq] = index
            value = _delta_value(delta_text)
            if value is None:
                errors.append("第 %d 行：分值要填 ±1~%d 的非零整数" %
                              (index + 1, MAX_DELTA))
            if len(note) > MAX_NOTE:
                errors.append("第 %d 行：备注最多 %d 字" % (index + 1, MAX_NOTE))
            delta_text = value if value is not None else ""
        slots.append({"key": seq, "delta": delta_text,
                      "note": note[:MAX_NOTE]})
    return slots, errors


def clean_slots(data):
    """读配置文件时做容错：坏行降级成留白，不让它把运行时绑定弄崩。"""
    if not isinstance(data, list):
        return empty_slots()
    rows = []
    for item in data:
        if not isinstance(item, dict):
            rows.append(empty_slot())
            continue
        seq, err = normalize_key(item.get("key", ""))
        delta = _delta_value(item.get("delta", ""))
        note = str(item.get("note", "") or "").strip()[:MAX_NOTE]
        if err or (seq and delta is None):
            seq, delta = "", ""
        rows.append({"key": seq, "delta": (delta or "") if seq else "",
                     "note": note})
    if len(rows) < SLOT_COUNT:
        rows.extend([empty_slot() for _ in range(SLOT_COUNT - len(rows))])
    return rows


def load_slots():
    path = config_path()
    if not os.path.isfile(path):
        return empty_slots()
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        slots = data.get("slots") if isinstance(data, dict) else data
        return clean_slots(slots)
    except Exception as exc:
        logging.warning("积分快捷键配置读取失败，按留白处理：%s", exc)
        return empty_slots()


def save_slots(slots):
    """原子写入：先写同目录临时文件再替换，避免中途退出写坏配置。"""
    path = config_path()
    tmp = path + ".tmp"
    payload = {"version": 1, "slots": list(slots or [])}
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)
    return path


def active_slots(slots):
    """返回 [(序号, 槽位)]：按键和分值都有效、需要绑定的那些行。"""
    result = []
    for index, slot in enumerate(slots or []):
        if not isinstance(slot, dict):
            continue
        seq = str(slot.get("key", "") or "").strip()
        delta = _delta_value(slot.get("delta", ""))
        if seq and delta is not None:
            result.append((index, {"key": seq, "delta": int(delta),
                                   "note": str(slot.get("note", "") or "")}))
    return result


def is_text_focus(widget):
    """焦点是否在会吃掉字母/数字的输入控件里。"""
    try:
        return widget.winfo_class() in (
            "Entry", "TEntry", "Text", "TCombobox", "Spinbox", "TSpinbox")
    except (AttributeError, tk.TclError):
        return False


class ShortcutManager:
    """把配置里的按键绑到主窗口；只在积分管理页、且有选中学生时生效。

    绑定挂在 app.master 上（Tk 的 bindtags 会让所有子控件的按键经过它），
    切页或弹窗打开时由 _fire 自行放行；改设置时先解绑旧序列再重绑。
    """

    def __init__(self, app):
        self.app = app
        self._bound = []
        self.slots = []
        self.reload(load_slots())

    def reload(self, slots=None):
        if slots is not None:
            self.slots = clean_slots(slots)
        self._unbind()
        for index, slot in active_slots(self.slots):
            seq = slot["key"]
            try:
                self.app.master.bind(
                    seq, lambda event, i=index: self._fire(event, i))
                self._bound.append(seq)
            except tk.TclError:
                logging.warning("积分快捷键 %s 绑定失败", seq, exc_info=True)
        return len(self._bound)

    def _unbind(self):
        for seq in self._bound:
            try:
                self.app.master.unbind(seq)
            except (tk.TclError, AttributeError):
                pass
        self._bound = []

    def slot(self, index):
        """运行时用的槽位：分值已经是 int，留白行返回 None。"""
        try:
            item = self.slots[index]
        except (IndexError, TypeError):
            return None
        seq = str(item.get("key", "") or "").strip()
        delta = _delta_value(item.get("delta", ""))
        if not seq or delta is None:
            return None
        return {"key": seq, "delta": int(delta),
                "note": str(item.get("note", "") or "")}

    def _fire(self, event, index):
        # 只在积分页起作用，别在别的页面（或输入框打字时）偷偷记一笔分
        if getattr(self.app, "current_page", "") != "points":
            return None
        slot = self.slot(index)
        if slot is None:
            return None
        seq = slot["key"]
        if is_text_focus(getattr(event, "widget", None)) and not (
                "Control-" in seq or "Alt-" in seq or _F_ONLY.match(seq)):
            return None
        page = getattr(self.app, "points_page", None)
        if page is None:
            return None
        try:
            if not page.stu_tree.winfo_exists():
                return None
        except (AttributeError, tk.TclError):
            return None
        page.quick_adjust(index)
        return "break"
