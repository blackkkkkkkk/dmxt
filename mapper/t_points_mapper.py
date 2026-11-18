"""学员积分：一张流水表 + 汇总查询。

设计取舍：
- 不给 t_stu 加 points 列。余额 = SUM(delta)，这样「加/减积分」天然留下
  一条不可改的记录（谁、哪个班、多少分、什么时候、为什么），也不会因为
  旧库缺列而要做 ALTER 迁移；新表由程序首次访问时自动建，老备份恢复后
  同样会自动补建空表，不会崩。
- 积分跟着 stu_id 走，学生改名、转班都不丢；流水里的 class_id / stu_name
  是「当时那一笔」的快照，学生被删后历史仍然读得出来。
- 流水只追加，不提供改和删：记错了就再记一笔反向分值并在备注里写明，
  账目才对得上。

SQLite 与 MySQL 各写一套建表 DDL；其余 SQL 都走 dp.translate_sql()，
用 %s 占位符即可跨两种库。
"""

import logging
from contextlib import contextmanager
from datetime import datetime

from service import dp

MYSQL_SCHEMA = """
CREATE TABLE IF NOT EXISTS t_point_record (
  record_id INT NOT NULL AUTO_INCREMENT COMMENT '流水主键',
  stu_id VARCHAR(32) NOT NULL COMMENT '学生 id（与 t_stu.stu_id 对应）',
  stu_name VARCHAR(50) NOT NULL COMMENT '当时姓名快照，学生删了也能读',
  class_id VARCHAR(32) NOT NULL COMMENT '当时的班级快照',
  delta INT NOT NULL COMMENT '变动分值，正数为加、负数为减',
  balance_after INT NOT NULL DEFAULT 0 COMMENT '本笔之后的余额（冗余，便于对账）',
  note VARCHAR(255) DEFAULT NULL COMMENT '备注/原因',
  create_time VARCHAR(32) NOT NULL COMMENT '发生时间 YYYY-MM-DD HH:MM:SS',
  PRIMARY KEY (record_id),
  KEY idx_point_stu (stu_id),
  KEY idx_point_class (class_id),
  KEY idx_point_time (create_time)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
"""

SQLITE_SCHEMA = """
CREATE TABLE IF NOT EXISTS t_point_record (
  record_id INTEGER PRIMARY KEY AUTOINCREMENT,
  stu_id TEXT NOT NULL,
  stu_name TEXT NOT NULL,
  class_id TEXT NOT NULL,
  delta INTEGER NOT NULL,
  balance_after INTEGER NOT NULL DEFAULT 0,
  note TEXT,
  create_time TEXT NOT NULL
)
"""

RECORD_LIMIT = 5000          # 一次最多读这么多条流水，避免拖慢界面

_schema_ready = False


def ensure_points_schema(force=False):
    """建表（幂等）。同进程内只执行一次；换库/恢复备份后可 force=True。"""
    global _schema_ready
    if _schema_ready and not force:
        return True
    statement = SQLITE_SCHEMA if dp.is_sqlite() else MYSQL_SCHEMA
    conn = dp.get_conn()
    try:
        with conn.cursor() as cursor:
            cursor.execute(statement)
        conn.commit()
    finally:
        conn.close()
    _schema_ready = True
    logging.info("积分流水表已就绪（t_point_record）")
    return True


def _now_text():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _range_clause(start=None, end=None):
    """按 create_time 的日期部分筛一段区间，返回 (SQL 片段, 参数)。

    create_time 存的是 'YYYY-MM-DD HH:MM:SS' 文本，SQLite / MySQL 的
    DATE() 都能取到前 10 位；start / end 留空表示这一头不限制。
    """
    sql, params = "", []
    if start:
        sql += " AND DATE(create_time) >= %s"
        params.append(str(start)[:10])
    if end:
        sql += " AND DATE(create_time) <= %s"
        params.append(str(end)[:10])
    return sql, params


@contextmanager
def _cursor():
    """与 BaseMapper._cursor 一致：自动确保常规表已建、用完关连接。"""
    if dp.is_sqlite():
        dp.ensure_schema_sqlite()
    conn = dp.get_conn()
    try:
        cursor = conn.cursor()
        yield cursor, conn
    finally:
        cursor.close()
        conn.close()


class PointsMapper:
    """积分流水的读写；所有方法返回普通 dict，界面不碰 SQL。"""

    def __init__(self):
        ensure_points_schema()

    # ------------------------------------------------------------ 写
    def add_record(self, stu_id, stu_name, class_id, delta, note=""):
        """记一笔增减。delta 必须是非零整数；返回带余额的流水字典。

        当前余额由同一连接里的 SUM 算出，写入 balance_after；失败整体回滚。
        """
        try:
            delta = int(delta)
        except (TypeError, ValueError):
            raise ValueError("积分分值必须是整数")
        if delta == 0:
            raise ValueError("积分变动不能是 0")
        if not str(stu_id or "").strip():
            raise ValueError("没有选中学生")

        stu_id = str(stu_id).strip()
        stu_name = str(stu_name or "").strip() or "未命名"
        class_id = str(class_id or "").strip()
        note = str(note or "").strip()[:200]
        created = _now_text()

        with _cursor() as (cursor, conn):
            try:
                cursor.execute(
                    "SELECT COALESCE(SUM(delta), 0) AS bal FROM t_point_record "
                    "WHERE stu_id = %s", (stu_id,))
                row = cursor.fetchone()
                balance = int((row or {}).get("bal") or 0) + delta
                cursor.execute(
                    "INSERT INTO t_point_record "
                    "(stu_id, stu_name, class_id, delta, balance_after, note, create_time) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s)",
                    (stu_id, stu_name, class_id, delta, balance, note, created))
                record_id = cursor.lastrowid
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        return {
            "record_id": record_id,
            "stu_id": stu_id,
            "stu_name": stu_name,
            "class_id": class_id,
            "delta": delta,
            "balance_after": balance,
            "note": note,
            "create_time": created,
        }

    # ------------------------------------------------------------ 读
    def balance_map(self, start=None, end=None):
        """{stu_id: 积分}；给 start/end 时只统计这段日期内的流水。

        不传日期就是「全部 / 累计总积分」，也是加减分记账的依据；
        没记过任何一笔的学生不在字典里（视为 0）。
        """
        where, params = _range_clause(start, end)
        with _cursor() as (cursor, _):
            cursor.execute(
                "SELECT stu_id, COALESCE(SUM(delta), 0) AS bal "
                "FROM t_point_record WHERE 1 = 1" + where + " GROUP BY stu_id",
                tuple(params))
            rows = cursor.fetchall()
        return {str(r["stu_id"]): int(r["bal"] or 0) for r in rows}

    def gains_map(self, start=None, end=None):
        """{stu_id: (获得, 扣减正数)}；给 start/end 时只统计这段日期。"""
        where, params = _range_clause(start, end)
        with _cursor() as (cursor, _):
            cursor.execute(
                "SELECT stu_id, delta FROM t_point_record "
                "WHERE delta <> 0" + where, tuple(params))
            rows = cursor.fetchall()
        out = {}
        for r in rows:
            key = str(r["stu_id"])
            gain, loss = out.get(key, (0, 0))
            value = int(r["delta"] or 0)
            out[key] = (gain + value, loss) if value > 0 else (gain, loss - value)
        return out

    def query(self, class_id=None, stu_id=None, keyword=None,
              start=None, end=None, limit=RECORD_LIMIT):
        """按条件查流水，倒序（新的在前）。空条件表示不过滤。"""
        ensure_points_schema()
        sql = ("SELECT record_id, stu_id, stu_name, class_id, delta, "
               "balance_after, note, create_time FROM t_point_record WHERE 1 = 1")
        params = []
        if class_id:
            sql += " AND class_id = %s"
            params.append(str(class_id))
        if stu_id:
            sql += " AND stu_id = %s"
            params.append(str(stu_id))
        if keyword:
            sql += " AND stu_name LIKE %s"
            params.append("%%%s%%" % str(keyword).strip())
        date_where, date_params = _range_clause(start, end)
        sql += date_where
        params.extend(date_params)
        sql += " ORDER BY record_id DESC LIMIT %s"
        params.append(int(limit))
        with _cursor() as (cursor, _):
            cursor.execute(sql, tuple(params))
            rows = cursor.fetchall()
        return [dict(r) for r in rows]

    def totals(self, rows=None):
        """对一批流水算 (奖励合计, 扣减合计, 净变动)。"""
        if rows is None:
            rows = self.query()
        gain = sum(int(r["delta"]) for r in rows if int(r["delta"]) > 0)
        loss = -sum(int(r["delta"]) for r in rows if int(r["delta"]) < 0)
        return gain, loss, gain - loss
