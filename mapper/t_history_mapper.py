"""点名历史记录查询与维护。

常规班（t_sign，一天一条）和假期班（t_season_sign，一次课一条）分属两套表，
这里用 UNION ALL 合成一张「记录表」再统一筛选，页面只认一种行结构。
改和删也在这里做：每行带一个 rec_id（常规班是 t_sign.dm_id，假期班是
t_season_sign.sign_id），配合 source 就能落回各自的表。

几点约定：
- 常规班的状态存的是「已签到 / 未签到」，假期班存的是 1 / 0；老数据里还有
  空串和 NULL，统一按「未签到」处理，页面上不会出现空白状态。
- 日期一律取「点名发生的日期」：常规班用 create_time 的日期，假期班用课次的
  lesson_date —— 老师补录或提前点名时，看到的才是那次课的日子。
- 班级名优先取 t_class.class_name（如「00_周五 晚19:00-21:00 C++3」），
  JOIN 不到时退回 class_id，班级被删掉的历史记录也不会凭空消失。
"""

import logging

from mapper.base_mapper import BaseMapper
from mapper.t_season_mapper import ensure_schema

SOURCE_LABELS = {"regular": "常规班", "summer": "暑假班", "winter": "寒假班"}
SOURCES = ("regular", "summer", "winter")

# 明细最多取这么多条：一次查询几万行会把界面拖慢，老师也不会一屏翻完
DEFAULT_LIMIT = 5000

# 是否签到：兼容中文、1/0 与历史脏数据
_SIGNED_EXPR = "CASE WHEN {col} IN ('1', '已签到') THEN 1 ELSE 0 END"

_REGULAR_BLOCK = """
    SELECT
        s.dm_id                                   AS rec_id,
        DATE(COALESCE(s.create_time, s.update_time)) AS sign_date,
        s.stu_name                                AS stu_name,
        COALESCE(NULLIF(c.class_name, ''), s.stu_class_id, '未分类') AS class_name,
        COALESCE(s.stu_class_id, '')              AS class_key,
        'regular'                                 AS source,
        ''                                        AS lesson_time,
        s.sign_hour                               AS sign_hour,
        {signed}                                  AS signed,
        s.sign_time                               AS sign_time,
        s.bz                                      AS bz
    FROM t_sign s
    LEFT JOIN t_class c ON c.class_id = s.stu_class_id
""".format(signed=_SIGNED_EXPR.format(col="s.sign_state"))

_SEASON_BLOCK = """
    SELECT
        g.sign_id                                 AS rec_id,
        l.lesson_date                             AS sign_date,
        g.stu_name                                AS stu_name,
        COALESCE(NULLIF(sc.class_name, ''), '未命名假期班') AS class_name,
        CAST(g.sclass_id AS CHAR)                 AS class_key,
        sc.season                                 AS source,
        COALESCE(l.lesson_time, '')               AS lesson_time,
        g.sign_hour                               AS sign_hour,
        {signed}                                  AS signed,
        g.sign_time                               AS sign_time,
        g.bz                                      AS bz
    FROM t_season_sign g
    JOIN t_season_lesson l ON l.lesson_id = g.lesson_id
    JOIN t_season_class sc ON sc.sclass_id = g.sclass_id
""".format(signed=_SIGNED_EXPR.format(col="g.sign_state"))

_OUTER = """
SELECT
    t.rec_id,
    DATE_FORMAT(t.sign_date, '%%Y-%%m-%%d') AS sign_date,
    t.stu_name,
    t.class_name,
    t.class_key,
    t.source,
    t.lesson_time,
    t.sign_hour,
    t.signed,
    t.sign_time,
    t.bz
FROM (
{blocks}
) t
WHERE 1 = 1
"""


def detail_sql(include_season=True):
    """明细查询 SQL：include_season=False 时只查常规班（假期表建不出来时兜底）。"""
    blocks = [_REGULAR_BLOCK]
    if include_season:
        blocks.append(_SEASON_BLOCK)
    return _OUTER.format(blocks=" UNION ALL ".join(blocks))


def weekday_text(date_text):
    """2026-07-06 → 周一；解析不了就返回空串。"""
    from datetime import datetime

    text = (date_text or "").strip()
    if len(text) < 10:
        return ""
    try:
        moment = datetime.strptime(text[:10], "%Y-%m-%d")
    except ValueError:
        return ""
    return "周" + "一二三四五六日"[moment.weekday()]


def fmt_hour(value):
    """课时显示：2.0 → 2，2.5 → 2.5，空值 → 空串。"""
    if value in (None, ""):
        return ""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    return str(int(number)) if number == int(number) else ("%g" % number)


def _hour_text(value):
    """课时入库前的归一：2.0 → '2'、2.5 → '2.5'、空 → ''。

    两套表的 sign_hour 都是 VARCHAR，历史上就按字符串存，这里保持原样，
    免得出现 '2.0' 与 '2' 混排；写不进去的值原样返回，不吞老师填的内容。
    """
    if value in (None, ""):
        return ""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    return str(int(number)) if number == int(number) else ("%g" % number)


class SignHistoryMapper(BaseMapper):
    """历史点名记录：查、改、删。"""

    def query(self, start=None, end=None, source=None, class_key=None,
              keyword=None, only_absent=False, limit=DEFAULT_LIMIT,
              only_signed=False, class_keys=None):
        """按条件查明细。

        start / end 为 'YYYY-MM-DD'，闭区间；source 取 regular/summer/winter；
        class_key 与 source 配合定位具体班级；keyword 匹配学生姓名。
        class_keys 传 [(source, class_key), ...] 时按多个班级过滤（优先于
        class_key，界面多选班级用）。only_signed / only_absent 是互斥的快捷
        筛选（界面用开关按钮保证不会同时按下），万一都传了 True，以
        only_signed 为准。
        """
        include_season = True
        try:
            ensure_schema()
        except Exception:
            # 假期表建不出来（权限/连接问题）也不该让整个历史页打不开，
            # 至少常规班的记录要能看
            logging.exception("假期课程表检查失败，历史记录只查常规班")
            include_season = False

        sql = detail_sql(include_season)
        params = []

        # 日期条件单独括起来：老数据里 create_time / update_time 都为空的记录
        # 算不出日期，但也不能因为筛选就永远看不见，让它始终露出来（排最后）
        date_conds = []
        if start:
            date_conds.append("t.sign_date >= %s")
            params.append(start)
        if end:
            date_conds.append("t.sign_date <= %s")
            params.append(end)
        if date_conds:
            sql += " AND (t.sign_date IS NULL OR (" + " AND ".join(date_conds) + "))"
        if source in SOURCES:
            sql += " AND t.source = %s"
            params.append(source)
        selected = {}
        for item in (class_keys or []):
            try:
                src, key = item
            except (TypeError, ValueError):
                continue
            src, key = str(src or ""), str(key or "").strip()
            if src in SOURCES and key:
                selected.setdefault(src, []).append(key)
        if selected:
            # 同编号的班可能同时存在于常规班和假期班，必须带上来源一起比
            groups = []
            for src, keys in selected.items():
                marks = ", ".join(["%s"] * len(keys))
                groups.append("(t.source = %s AND t.class_key IN (%s))" %
                              ("%s", marks))
                params.append(src)
                params.extend(keys)
            sql += " AND (" + " OR ".join(groups) + ")"
        elif class_key not in (None, ""):
            sql += " AND t.class_key = %s"
            params.append(str(class_key))
        if keyword:
            sql += " AND t.stu_name LIKE %s"
            params.append("%" + str(keyword).strip() + "%")
        if only_signed:
            sql += " AND t.signed = 1"
        elif only_absent:
            sql += " AND t.signed = 0"

        # 日期未知的排最后，其余按日期倒序
        sql += (" ORDER BY t.sign_date IS NULL, t.sign_date DESC,"
                " t.class_name, t.stu_name, t.sign_time DESC")
        sql += " LIMIT %s"
        params.append(int(limit))

        with self._cursor() as (cursor, _):
            cursor.execute(sql, tuple(params))
            rows = cursor.fetchall()
        return [self._normalize(row) for row in rows]

    def count(self):
        """库里有多少条点名记录，用于「全部时间」时的提示。"""
        try:
            ensure_schema()
            with self._cursor() as (cursor, _):
                cursor.execute(
                    "SELECT (SELECT COUNT(*) FROM t_sign)"
                    " + (SELECT COUNT(*) FROM t_season_sign) AS n")
                row = cursor.fetchone()
        except Exception:
            logging.exception("统计点名记录条数失败")
            return 0
        if not row:
            return 0
        return int(list(row.values())[0] or 0)

    def class_options(self):
        """班级下拉的候选：{source: [(class_key, 显示名), ...]}。"""
        options = {key: [] for key in SOURCES}
        regular_sql = """
            SELECT DISTINCT s.stu_class_id AS class_key,
                   COALESCE(NULLIF(c.class_name, ''), s.stu_class_id) AS class_name
            FROM t_sign s
            LEFT JOIN t_class c ON c.class_id = s.stu_class_id
            WHERE s.stu_class_id IS NOT NULL AND s.stu_class_id <> ''
            ORDER BY class_name
        """
        try:
            with self._cursor() as (cursor, _):
                cursor.execute(regular_sql)
                options["regular"] = [(str(r["class_key"]), r["class_name"] or str(r["class_key"]))
                                      for r in cursor.fetchall()]
        except Exception:
            logging.exception("读取常规班列表失败")

        try:
            ensure_schema()
            season_sql = """
                SELECT DISTINCT c.season AS season,
                       CAST(c.sclass_id AS CHAR) AS class_key,
                       c.class_name AS class_name
                FROM t_season_class c
                WHERE EXISTS (SELECT 1 FROM t_season_sign g WHERE g.sclass_id = c.sclass_id)
                ORDER BY c.season, c.class_name
            """
            with self._cursor() as (cursor, _):
                cursor.execute(season_sql)
                for row in cursor.fetchall():
                    key = row["season"] or ""
                    if key not in options:
                        options[key] = []
                    options[key].append((str(row["class_key"]),
                                         row["class_name"] or ("%s班" % key)))
        except Exception:
            logging.exception("读取假期班列表失败")
        return options

    # ---------------------------------------------------------- 修改 / 删除
    def update_record(self, source, rec_id, signed, hour=None, note=None):
        """改一条点名记录：签到状态、课时、备注。返回受影响行数。

        两套表的状态写法本来就不一样（常规班存「已签到/未签到」，假期班存 1/0），
        这里按来源分别写，不去「统一」它 —— 改口径会连累点名页的回显逻辑。

        刻意不动 create_time / update_time：常规班的记录日期取的是 create_time，
        老数据里 create_time 为空时还会退回 update_time，改状态顺手盖上今天的时间戳
        会把「哪一天点的名」改错。
        """
        rec_id = int(rec_id)
        hour_text = _hour_text(hour)
        note_text = "" if note is None else str(note)

        if source == "regular":
            sql = ("UPDATE t_sign SET sign_state = %s, sign_hour = %s, bz = %s "
                   "WHERE dm_id = %s")
            state = "已签到" if signed else "未签到"
        else:
            ensure_schema()
            sql = ("UPDATE t_season_sign SET sign_state = %s, sign_hour = %s, "
                   "bz = %s WHERE sign_id = %s")
            state = "1" if signed else "0"

        with self._cursor() as (cursor, conn):
            cursor.execute(sql, (state, hour_text, note_text, rec_id))
            affected = cursor.rowcount
            conn.commit()
            return affected

    def delete_record(self, source, rec_id):
        """删一条点名记录，返回受影响行数。"""
        return self.delete_records([(source, rec_id)])

    def delete_records(self, items):
        """批量删除，items 为 [(source, rec_id), ...]，返回实际删除条数。

        一次批量操作放在同一个连接里，出错整体回滚：要么都删掉，要么一条没动，
        不会出现「删了一半、界面上的条数还对不上」的状态。
        """
        regular_ids, season_ids = [], []
        for source, rec_id in items:
            try:
                rid = int(rec_id)
            except (TypeError, ValueError):
                continue
            (regular_ids if source == "regular" else season_ids).append(rid)
        if not regular_ids and not season_ids:
            return 0
        if season_ids:
            ensure_schema()

        removed = 0
        with self._cursor() as (cursor, conn):
            try:
                for rid in regular_ids:
                    cursor.execute("DELETE FROM t_sign WHERE dm_id = %s", (rid,))
                    removed += max(cursor.rowcount, 0)
                for rid in season_ids:
                    cursor.execute(
                        "DELETE FROM t_season_sign WHERE sign_id = %s", (rid,))
                    removed += max(cursor.rowcount, 0)
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        return removed

    @staticmethod
    def _normalize(row):
        """行 → 界面直接可用的字典，状态统一成中文。"""
        date_text = row.get("sign_date") or ""
        signed = int(row.get("signed") or 0) == 1
        source = row.get("source") or ""
        return {
            "rec_id": int(row.get("rec_id") or 0),
            "date": date_text or "未知",
            "weekday": weekday_text(date_text),
            "class_name": row.get("class_name") or "",
            "class_key": row.get("class_key") or "",
            "student": row.get("stu_name") or "",
            "hour": fmt_hour(row.get("sign_hour")),
            "signed": signed,
            "state": "已签到" if signed else "未签到",
            "sign_time": row.get("sign_time") or "",
            "source": source,
            "source_label": SOURCE_LABELS.get(source, source),
            "lesson_time": row.get("lesson_time") or "",
            "note": row.get("bz") or "",
        }
