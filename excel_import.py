# -*- coding: utf-8 -*-
"""Excel 批量导入：班级与学员。

分两步，便于界面「先预览、再导入」：
- parse_classes / parse_students 只读表 + 校验，返回逐行结果（新增/更新/跳过/
  错误 + 一句说明），界面拿它渲染预览表；
- import_classes / import_students 再按预览结果逐行写库，单行失败不影响其他行。

列识别不依赖顺序：按表头文字定位（去空格、去 *），多余列直接忽略。表头可以
写「班级编号」也可以写「班级号」「编号」；学员表的「班级」列填多个班时用
、 , 空格 / 分隔都行。

校验口径与手工表单保持一致：课时 0～24（学员 0～999）、星期只认周一～周日、
状态只认正常/停课/结课；不一致时把行号一并报出来，用户改一行重导即可。
"""

import re

from entity.TClass import TClass
from entity.TStu import TStu
from excel_io import ExcelReadError, read_table, write_xlsx
from mapper.t_class_mapper import TClassMapper
from mapper.t_stu_mapper import DEFAULT_STATE, DELETED_STATE, TStuMapper

CLASS_HEADERS = ("班级编号", "班级名称", "课程类型", "星期", "上课时间", "默认课时")
STUDENT_HEADERS = ("姓名", "剩余课时", "手机号", "状态", "班级")

# 表头别名：同一列用户可能这么写
CLASS_ALIASES = {
    "班级编号": ("班级编号", "班级号", "班级id", "编号"),
    "班级名称": ("班级名称", "班级名"),
    "课程类型": ("课程类型", "课程"),
    "星期": ("星期", "上课星期", "周几"),
    "上课时间": ("上课时间", "时间段", "时间"),
    "默认课时": ("默认课时", "课时", "默认课时数"),
}
STUDENT_ALIASES = {
    "姓名": ("姓名", "学生姓名", "学员姓名", "名字"),
    "剩余课时": ("剩余课时", "课时", "剩余", "剩余课时数"),
    "手机号": ("手机号", "手机", "电话", "联系电话"),
    "状态": ("状态", "学生状态"),
    "班级": ("班级", "所在班级", "所属班级", "班级编号", "班级列表"),
}

WEEKDAYS = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")
_WEEKDAY_NAMES = {}
for _i, _day in enumerate(WEEKDAYS):
    _cn = "一二三四五六日"[_i]
    for _alias in ("周" + _cn, "星期" + _cn, "礼拜" + _cn,
                   "周%d" % (_i + 1), "星期%d" % (_i + 1), str(_i + 1), _cn):
        _WEEKDAY_NAMES[_alias] = _day

STATE_ALIASES = {
    "1": ("1", "正常", "在读", "上课"),
    "2": ("2", "停课", "暂停", "休学"),
    "3": ("3", "结课", "已结课", "毕业"),
}

DEFAULT_CLASS_HOUR = 2.5
DEFAULT_STU_HOUR = 2.5

_PHONE_RE = re.compile(r"[0-9+\- ]{3,20}")


def cell_text(value):
    """单元格值 → 干净的字符串（2.0 显示成 2，None 显示成空）。"""
    if value is None:
        return ""
    if isinstance(value, float) and value == int(value):
        return str(int(value))
    return str(value).strip()


def _is_deleted(stu):
    return str(getattr(stu, "stu_state", DEFAULT_STATE) or "") == DELETED_STATE


def _header_key(text):
    return re.sub(r"[\s*＊:：]", "", cell_text(text)).lower()


def find_header(rows, anchor):
    """找表头行，返回 (在 rows 里的下标, 真实行号, {表头文字: 列号})。

    anchor 可以是单个列名，也可以是同一列的多种写法（别名），命中任意一个即算
    找到表头——用户把「班级编号」写成「班级号」「编号」，导入照样能用。
    """
    anchors = (anchor,) if isinstance(anchor, str) else tuple(anchor)
    keys = {_header_key(a) for a in anchors}
    for pos, (line, cells) in enumerate(rows):
        mapping = {}
        for index, cell in enumerate(cells):
            text = _header_key(cell)
            if text and text not in mapping:
                mapping[text] = index
        if keys & set(mapping):
            return pos, line, mapping
    raise ExcelReadError(
        "没找到表头：请把第一行写成列名，且至少有「%s」这一列" % anchors[0])


def resolve_columns(mapping, aliases):
    """按别名把列名映射到列号；找不到的列不出现在结果里。"""
    out = {}
    for canon, names in aliases.items():
        for name in names:
            key = _header_key(name)
            if key in mapping:
                out[canon] = mapping[key]
                break
    return out


def normalize_weekday(value):
    """各种写法 → 周一…周日；认不出返回 None。"""
    raw = re.sub(r"\s", "", cell_text(value))
    return _WEEKDAY_NAMES.get(raw)


def normalize_state(value):
    """状态文本 → '1'/'2'/'3'；空值按正常；认不出返回 None。"""
    raw = re.sub(r"\s", "", cell_text(value))
    if not raw:
        return DEFAULT_STATE
    for code, names in STATE_ALIASES.items():
        if raw in names:
            return code
    return None


def split_classes(value):
    """「01、02 / 03」→ ['01', '02', '03']（去重、保序）。"""
    raw = cell_text(value)
    if not raw:
        return []
    out = []
    for part in re.split(r"[、,，;；/／\\|\s]+", raw):
        part = part.strip()
        if part and part not in out:
            out.append(part)
    return out


def _cell(row, columns, name):
    index = columns.get(name)
    if index is None or index >= len(row):
        return ""
    return cell_text(row[index])


def _parse_hour(text, default, minimum, maximum, label):
    raw = cell_text(text)
    if not raw:
        return default, ""
    try:
        value = float(raw)
    except ValueError:
        return None, "%s要填数字，例如 2.5" % label
    if not (minimum < value <= maximum):
        return None, "%s应在 %g～%g 之间" % (label, minimum, maximum)
    return value, ""


def parse_classes(rows, existing_ids=(), update_existing=False):
    """解析班级表，返回逐行结果（不改库）。"""
    pos, _header_line, mapping = find_header(rows, CLASS_ALIASES["班级编号"])
    columns = resolve_columns(mapping, CLASS_ALIASES)
    if "班级编号" not in columns:
        raise ExcelReadError("表头里缺少「班级编号」列")
    existing = {str(x) for x in existing_ids}
    seen = {}

    items = []
    for line, row in rows[pos + 1:]:
        cid = _cell(row, columns, "班级编号")
        name = _cell(row, columns, "班级名称")
        kind = _cell(row, columns, "课程类型")
        day_text = _cell(row, columns, "星期")
        time_text = _cell(row, columns, "上课时间")
        hour_text = _cell(row, columns, "默认课时")
        if not any((cid, name, kind, day_text, time_text, hour_text)):
            continue

        item = {
            "line": line,
            "class_id": cid,
            "class_name": name,
            "class_type": kind,
            "class_day": day_text,
            "class_time": time_text,
            "class_hour": None,
            "status": "",
            "message": "",
            "summary": "",
        }

        if not cid:
            item["status"] = "错误"
            item["message"] = "班级编号为空"
        elif re.search(r"\s", cid):
            item["status"] = "错误"
            item["message"] = "班级编号不能包含空格"
        elif len(cid) > 20:
            item["status"] = "错误"
            item["message"] = "班级编号最多 20 个字符"

        if not item["status"]:
            day = normalize_weekday(day_text)
            if day is None:
                item["status"] = "错误"
                item["message"] = ("星期「%s」无效，请填 周一～周日"
                                   % day_text if day_text else "星期为空，请填 周一～周日")
            else:
                item["class_day"] = day

        if not item["status"]:
            hour, error = _parse_hour(hour_text, DEFAULT_CLASS_HOUR, 0, 24, "默认课时")
            if error:
                item["status"] = "错误"
                item["message"] = error
            else:
                item["class_hour"] = hour

        if not item["status"]:
            first = seen.get(cid)
            if first is not None:
                item["status"] = "跳过"
                item["message"] = "与第 %d 行的班级编号重复" % first
            else:
                seen[cid] = line
                if cid in existing:
                    if update_existing:
                        item["status"] = "更新"
                        item["message"] = "编号已存在，按表格内容更新"
                    else:
                        item["status"] = "跳过"
                        item["message"] = "编号已存在（可勾选更新）"
                else:
                    item["status"] = "新增"
                    item["message"] = "新班级"

        item["summary"] = " · ".join(part for part in (
            item["class_id"] or "（无编号）",
            item["class_day"],
            item["class_time"],
            ("%g 课时" % item["class_hour"]) if item["class_hour"] is not None else "",
        ) if part)
        items.append(item)
    return items


def parse_students(rows, known_classes=(), existing_names=(), deleted_names=(),
                   update_existing=False):
    """解析学员表，返回逐行结果（不改库）。"""
    pos, _header_line, mapping = find_header(rows, STUDENT_ALIASES["姓名"])
    columns = resolve_columns(mapping, STUDENT_ALIASES)
    if "姓名" not in columns:
        raise ExcelReadError("表头里缺少「姓名」列")
    known = {str(x) for x in known_classes}
    alive = {str(x).strip() for x in existing_names}
    deleted = {str(x).strip() for x in deleted_names}
    seen = {}

    items = []
    for line, row in rows[pos + 1:]:
        name = _cell(row, columns, "姓名")
        hour_text = _cell(row, columns, "剩余课时")
        phone = _cell(row, columns, "手机号")
        state_text = _cell(row, columns, "状态")
        class_text = _cell(row, columns, "班级")
        if not any((name, hour_text, phone, state_text, class_text)):
            continue

        item = {
            "line": line,
            "name": name,
            "stu_hour": None,
            "phone": phone,
            "stu_state": DEFAULT_STATE,
            "classes": [],
            "status": "",
            "message": "",
            "summary": "",
        }

        if not name:
            item["status"] = "错误"
            item["message"] = "姓名为空"
        elif len(name) > 50:
            item["status"] = "错误"
            item["message"] = "姓名最多 50 个字符"

        if not item["status"]:
            hour, error = _parse_hour(hour_text, DEFAULT_STU_HOUR, -0.0001, 999,
                                      "剩余课时")
            if error:
                item["status"] = "错误"
                item["message"] = error
            else:
                item["stu_hour"] = hour

        if not item["status"] and phone and not _PHONE_RE.fullmatch(phone):
            item["status"] = "错误"
            item["message"] = "手机号只能含数字 + - 空格（3～20 位）"

        if not item["status"]:
            code = normalize_state(state_text)
            if code is None:
                item["status"] = "错误"
                item["message"] = "状态「%s」无效，请填 正常/停课/结课" % state_text
            else:
                item["stu_state"] = code

        if not item["status"]:
            classes = split_classes(class_text)
            unknown = [cid for cid in classes if cid not in known]
            if unknown:
                item["status"] = "错误"
                item["message"] = ("班级「%s」不存在，请先建班" % "、".join(unknown))
            else:
                item["classes"] = classes

        if not item["status"]:
            first = seen.get(name)
            if first is not None:
                item["status"] = "跳过"
                item["message"] = "与第 %d 行的姓名重复" % first
            else:
                seen[name] = line
                if name in deleted:
                    item["status"] = "跳过"
                    item["message"] = "同名已删除，请先恢复再导入"
                elif name in alive:
                    if update_existing:
                        item["status"] = "更新"
                        item["message"] = "已有学生，按表格更新课时/手机号/状态"
                    else:
                        item["status"] = "已有"
                        item["message"] = ("已有学生，只补充班级归属"
                                           if item["classes"] else "已有学生，无班级变化")
                else:
                    item["status"] = "新增"
                    item["message"] = "新学生"

        item["summary"] = " · ".join(part for part in (
            item["name"] or "（无姓名）",
            ("%g 课时" % item["stu_hour"]) if item["stu_hour"] is not None else "",
            "" if item["stu_state"] == DEFAULT_STATE else
            {"1": "正常", "2": "停课", "3": "结课"}.get(item["stu_state"], ""),
            ("、".join(item["classes"])) if item["classes"] else "未分班",
        ) if part)
        items.append(item)
    return items


def preview_classes(path_or_rows, update_existing=False):
    """界面用：读文件 + 带上库里已有编号后解析，不改库。"""
    rows = (path_or_rows if isinstance(path_or_rows, list)
            else read_table(path_or_rows)[0])
    existing = {str(c.class_id) for c in TClassMapper().select_all()}
    return parse_classes(rows, existing, update_existing)


def preview_students(path_or_rows, update_existing=False):
    """界面用：读文件 + 带上已有学生/班级后解析，不改库。"""
    rows = (path_or_rows if isinstance(path_or_rows, list)
            else read_table(path_or_rows)[0])
    class_ids = {str(c.class_id) for c in TClassMapper().select_all()}
    everyone = TStuMapper().select_all(include_deleted=True)
    alive = {str(s.stu_name or "").strip() for s in everyone if not _is_deleted(s)}
    deleted = {str(s.stu_name or "").strip() for s in everyone if _is_deleted(s)}
    return parse_students(rows, class_ids, alive, deleted, update_existing)


class ImportReport:
    """导入结果：界面用它拼提示，回归脚本用它断言。"""

    def __init__(self, kind):
        self.kind = kind
        self.total = 0
        self.created = 0
        self.updated = 0
        self.existing = 0
        self.skipped = 0
        self.failed = 0
        self.rows = []          # [(行号, 结果, 说明)]

    def add(self, line, status, message):
        self.rows.append((line, status, message))

    def summary(self):
        bits = ["共处理 %d 行" % self.total]
        if self.created:
            bits.append("新增 %d" % self.created)
        if self.updated:
            bits.append("更新 %d" % self.updated)
        if self.existing:
            bits.append("已有学生 %d" % self.existing)
        if self.skipped:
            bits.append("跳过 %d" % self.skipped)
        if self.failed:
            bits.append("失败 %d" % self.failed)
        return "，".join(bits)

    def error_lines(self, limit=8):
        bad = [(line, text) for line, status, text in self.rows
               if status in ("错误", "失败")]
        text = "\n".join("第 %d 行：%s" % pair for pair in bad[:limit])
        if len(bad) > limit:
            text += "\n……另有 %d 行，详见导出/控制台" % (len(bad) - limit)
        return text


def import_classes(path_or_rows, update_existing=False):
    """导入班级。返回 ImportReport。"""
    items = preview_classes(path_or_rows, update_existing)
    mapper = TClassMapper()

    report = ImportReport("班级")
    for item in items:
        report.total += 1
        if item["status"] == "错误":
            report.failed += 1
            report.add(item["line"], "错误", item["message"])
            continue
        if item["status"] == "跳过":
            report.skipped += 1
            report.add(item["line"], "跳过", item["message"])
            continue

        old = mapper.select_by_id(item["class_id"]) if item["status"] == "更新" else None
        t_class = TClass(
            class_id=item["class_id"],
            class_type=item["class_type"] or None,
            class_time=item["class_time"] or None,
            class_name=item["class_name"] or None,
            class_hour=item["class_hour"],
            class_day=item["class_day"],
            class_val=(1 if old is None or old.class_val in (None, "")
                       else old.class_val),
        )
        try:
            if item["status"] == "更新" and old is not None:
                mapper.update(t_class, str(old.class_id))
                report.updated += 1
                report.add(item["line"], "已更新", "%s 已按表格更新" % t_class.class_id)
            else:
                mapper.insert(t_class)
                report.created += 1
                report.add(item["line"], "已新增", "已新增班级 %s" % t_class.class_id)
        except Exception as e:
            report.failed += 1
            report.add(item["line"], "失败", str(e))
    return report


def import_students(path_or_rows, update_existing=False):
    """导入学员：建档 + 按「班级」列补班级映射。返回 ImportReport。"""
    items = preview_students(path_or_rows, update_existing)
    mapper = TStuMapper()

    report = ImportReport("学员")
    for item in items:
        report.total += 1
        if item["status"] in ("错误", "跳过"):
            if item["status"] == "错误":
                report.failed += 1
                report.add(item["line"], "错误", item["message"])
            else:
                report.skipped += 1
                report.add(item["line"], "跳过", item["message"])
            continue

        name = item["name"]
        try:
            stu = mapper.select_by_name(name, include_deleted=True)
            if stu is None:
                stu_id = mapper.insert(TStu(
                    stu_name=name,
                    stu_class_id=None,
                    stu_hour=item["stu_hour"],
                    phone=item["phone"] or None,
                    stu_state=item["stu_state"],
                ))
                added = 0
                for cid in item["classes"]:
                    if stu_id:
                        mapper.add_to_class(stu_id, cid)
                        added += 1
                report.created += 1
                report.add(item["line"], "已新增",
                           "已建档%s" % ("，加入 %d 个班级" % added if added
                                        else "（未分班）"))
                continue

            if _is_deleted(stu):
                report.skipped += 1
                report.add(item["line"], "跳过", "同名学生已删除，未处理")
                continue

            current = set(mapper.classes_of(stu.stu_id, include_deleted=True))
            added = [cid for cid in item["classes"] if cid not in current]
            if update_existing:
                mapper.update(TStu(
                    stu_id=stu.stu_id,
                    stu_name=name,
                    stu_hour=item["stu_hour"],
                    phone=item["phone"] or None,
                    stu_state=item["stu_state"],
                ), old_name=name)
                report.updated += 1
                status = "已更新"
            else:
                report.existing += 1
                status = "已有学生"
            for cid in added:
                mapper.add_to_class(stu.stu_id, cid)
            detail = "补充班级 %s" % "、".join(added) if added else "班级无变化"
            report.add(item["line"], status, detail)
        except Exception as e:
            report.failed += 1
            report.add(item["line"], "失败", str(e))
    return report


# ---------------------------------------------------------------- 模板

def class_template_rows():
    rows = [list(CLASS_HEADERS),
            ["01", "01_周六 8.20-10.20 python", "python", "周六", "08.20-10.20", 2.5],
            ["02", "02_周日 10.30-12.30 scratch", "scratch", "周日", "10.30-12.30", 2]]
    notes = [
        ["列名", "是否必填", "填写说明"],
        ["班级编号", "必填", "点名页下拉框显示的就是它，建议 00、01 这样；不能有空格，最多 20 字"],
        ["班级名称", "选填", "如 01_周六 8.20-10.20 python"],
        ["课程类型", "选填", "如 python / scratch"],
        ["星期", "必填", "周一～周日；写「星期六」「周6」「6」也能认"],
        ["上课时间", "选填", "如 08.20-10.20"],
        ["默认课时", "选填", "数字，0～24，留空按 2.5"],
        ["", "", ""],
        ["说明", "", "首行必须是列名（顺序不限，多余列忽略）；已存在的编号默认跳过，"
                     "勾选「已存在时更新资料」则按表格覆盖"],
    ]
    return rows, notes


def student_template_rows():
    rows = [list(STUDENT_HEADERS),
            ["张三", 2.5, "13800000000", "正常", "01、02"],
            ["李四", 0, "", "停课", "01"]]
    notes = [
        ["列名", "是否必填", "填写说明"],
        ["姓名", "必填", "全库唯一；已有同名学生会补班级，不会重复建档"],
        ["剩余课时", "选填", "数字，0～999，留空按 2.5"],
        ["手机号", "选填", "数字、+、- 和空格，3～20 位"],
        ["状态", "选填", "正常 / 停课 / 结课，留空按正常"],
        ["班级", "选填", "班级编号，多个用、或空格分隔；班级必须先在「班级管理」里建好"],
        ["", "", ""],
        ["说明", "", "首行必须是列名（顺序不限，多余列忽略）；已存在的学生默认只补班级，"
                     "勾选「已存在时更新资料」则同时更新课时/手机号/状态"],
    ]
    return rows, notes


def write_class_template(path):
    rows, notes = class_template_rows()
    return write_xlsx(path, rows, widths=[14, 34, 14, 12, 16, 12], notes=notes,
                      sheet_name="班级", notes_sheet="填写说明")


def write_student_template(path):
    rows, notes = student_template_rows()
    return write_xlsx(path, rows, widths=[14, 12, 18, 12, 24], notes=notes,
                      sheet_name="学员", notes_sheet="填写说明")
