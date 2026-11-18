"""暑假 / 寒假课程的数据访问层。

四张表都由程序在第一次打开假期页时自动建好（CREATE TABLE IF NOT EXISTS），
不需要手动执行 SQL；建表语句也同步到了 schema.sql，方便手工初始化。

为什么单独建表而不是复用 t_class / t_stu：常规班的逻辑是「按星期重复」，
点名记录按天写；假期班上课日期不固定，点名要按「课次」写。混在同一张表里
会让常规点名页的下拉框冒出假期班，也会让 DATE(create_time)=CURDATE() 的
「当天一条」判断误伤假期班的多次课。
"""

import logging

from entity.season import SeasonClass, SeasonLesson, SeasonSign, SeasonStu
from mapper.base_mapper import BaseMapper
from mapper.t_stu_mapper import TStuMapper

# 有效标志：1 有效、0 已删除（软删除，记录保留可恢复）
ALIVE = "1"
DELETED = "0"

SUMMER = "summer"
WINTER = "winter"
SEASONS = (SUMMER, WINTER)

# 建表语句顺序不能乱：其它表都要先有班级
SCHEMA_STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS t_season_class (
      sclass_id INT NOT NULL AUTO_INCREMENT COMMENT '假期班级主键',
      season VARCHAR(16) NOT NULL COMMENT 'summer 暑假 / winter 寒假',
      class_name VARCHAR(100) NOT NULL COMMENT '班级名称，老师自定义',
      class_type VARCHAR(100) DEFAULT NULL COMMENT '课程内容，如 C++ 集训',
      class_hour DOUBLE DEFAULT 2.5 COMMENT '每次课默认课时',
      start_date DATE DEFAULT NULL COMMENT '开班日期',
      end_date DATE DEFAULT NULL COMMENT '结班日期',
      note VARCHAR(255) DEFAULT NULL,
      state VARCHAR(8) NOT NULL DEFAULT '1' COMMENT '有效标志：0已删除(可恢复),1有效',
      create_time TIMESTAMP NULL DEFAULT CURRENT_TIMESTAMP,
      PRIMARY KEY (sclass_id),
      KEY idx_season_state (season, state)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
    """
    CREATE TABLE IF NOT EXISTS t_season_stu (
      sstu_id INT NOT NULL AUTO_INCREMENT,
      sclass_id INT NOT NULL COMMENT '所属假期班级',
      stu_name VARCHAR(50) NOT NULL,
      phone VARCHAR(30) DEFAULT NULL,
      note VARCHAR(255) DEFAULT NULL,
      state VARCHAR(8) NOT NULL DEFAULT '1' COMMENT '有效标志：0已删除(可恢复),1有效',
      create_time TIMESTAMP NULL DEFAULT CURRENT_TIMESTAMP,
      PRIMARY KEY (sstu_id),
      KEY idx_class_state (sclass_id, state)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
    """
    CREATE TABLE IF NOT EXISTS t_season_lesson (
      lesson_id INT NOT NULL AUTO_INCREMENT,
      sclass_id INT NOT NULL COMMENT '所属假期班级',
      lesson_date DATE NOT NULL COMMENT '上课日期',
      lesson_time VARCHAR(60) DEFAULT NULL COMMENT '上课时间段，如 14:00-16:00',
      topic VARCHAR(200) DEFAULT NULL COMMENT '本次课内容',
      note VARCHAR(255) DEFAULT NULL,
      create_time TIMESTAMP NULL DEFAULT CURRENT_TIMESTAMP,
      PRIMARY KEY (lesson_id),
      KEY idx_class_date (sclass_id, lesson_date)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
    """
    CREATE TABLE IF NOT EXISTS t_season_sign (
      sign_id INT NOT NULL AUTO_INCREMENT,
      lesson_id INT NOT NULL,
      sclass_id INT NOT NULL,
      sstu_id INT NOT NULL,
      stu_name VARCHAR(50) NOT NULL COMMENT '冗余姓名，导出的表格直接可读',
      sign_hour VARCHAR(20) DEFAULT NULL COMMENT '本次课计课时',
      sign_state VARCHAR(8) NOT NULL DEFAULT '0' COMMENT '1已签到 0未签到',
      sign_time VARCHAR(40) DEFAULT NULL,
      bz VARCHAR(255) DEFAULT NULL,
      update_time TIMESTAMP NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
      PRIMARY KEY (sign_id),
      UNIQUE KEY uk_lesson_stu (lesson_id, sstu_id),
      KEY idx_class (sclass_id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
)

# SQLite 专用的建表语句：SQLite 不支持 ENGINE/CHARSET/表级 KEY，
# 且 upsert（ON CONFLICT）需要 t_season_sign 上有 (lesson_id, sstu_id) 唯一约束。
# 这里直接写 SQLite 方言，避免依赖 dp.translate_sql 对复杂 DDL 的翻译。
SQLITE_SCHEMA_STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS t_season_class (
      sclass_id INTEGER PRIMARY KEY AUTOINCREMENT,
      season TEXT NOT NULL,
      class_name TEXT NOT NULL,
      class_type TEXT,
      class_hour REAL DEFAULT 2.5,
      start_date TEXT,
      end_date TEXT,
      note TEXT,
      state TEXT NOT NULL DEFAULT '1',
      create_time TEXT DEFAULT (datetime('now','localtime'))
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS t_season_stu (
      sstu_id INTEGER PRIMARY KEY AUTOINCREMENT,
      sclass_id INTEGER NOT NULL,
      stu_name TEXT NOT NULL,
      phone TEXT,
      note TEXT,
      state TEXT NOT NULL DEFAULT '1',
      create_time TEXT DEFAULT (datetime('now','localtime'))
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS t_season_lesson (
      lesson_id INTEGER PRIMARY KEY AUTOINCREMENT,
      sclass_id INTEGER NOT NULL,
      lesson_date TEXT NOT NULL,
      lesson_time TEXT,
      topic TEXT,
      note TEXT,
      create_time TEXT DEFAULT (datetime('now','localtime'))
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS t_season_sign (
      sign_id INTEGER PRIMARY KEY AUTOINCREMENT,
      lesson_id INTEGER NOT NULL,
      sclass_id INTEGER NOT NULL,
      sstu_id INTEGER NOT NULL,
      stu_name TEXT NOT NULL,
      sign_hour TEXT,
      sign_state TEXT NOT NULL DEFAULT '0',
      sign_time TEXT,
      bz TEXT,
      update_time TEXT,
      UNIQUE (lesson_id, sstu_id)
    )
    """,
)


def _is_sqlite():
    from service.dp import is_sqlite
    return is_sqlite()


_schema_ready = False


def ensure_schema(force=False):
    """建表（幂等）。第一次调用会执行四条 DDL，之后同进程内直接返回。"""
    global _schema_ready
    if _schema_ready and not force:
        return True
    from service.dp import get_conn

    statements = SQLITE_SCHEMA_STATEMENTS if _is_sqlite() else SCHEMA_STATEMENTS
    conn = get_conn()
    try:
        with conn.cursor() as cursor:
            for sql in statements:
                cursor.execute(sql)
        conn.commit()
    finally:
        conn.close()
    _schema_ready = True
    logging.info("假期课程表已就绪（t_season_*）")
    return True


def _date_text(value):
    """DATE 列取回来是 date 对象，统一转成 YYYY-MM-DD 字符串给界面用。"""
    if value in (None, ""):
        return ""
    if hasattr(value, "strftime"):
        return value.strftime("%Y-%m-%d")
    return str(value)[:10]


# ---------------------------------------------------------------- 假期班级


class SeasonClassMapper(BaseMapper):

    def select_all(self, season, include_deleted=False):
        sql = "SELECT * FROM t_season_class WHERE season = %s"
        params = [season]
        if not include_deleted:
            sql += " AND state <> %s"
            params.append(DELETED)
        sql += " ORDER BY sclass_id"
        with self._cursor() as (cursor, _):
            cursor.execute(sql, tuple(params))
            return self._fetch_all(cursor, SeasonClass)

    def select_by_id(self, sclass_id):
        sql = "SELECT * FROM t_season_class WHERE sclass_id = %s"
        with self._cursor() as (cursor, _):
            cursor.execute(sql, (sclass_id,))
            return self._fetch_one(cursor, SeasonClass)

    def select_by_name(self, season, class_name, exclude_id=None):
        """同名检查：同一假期里班级重名会让下拉框分不清。"""
        sql = "SELECT * FROM t_season_class WHERE season = %s AND class_name = %s"
        params = [season, class_name]
        if exclude_id is not None:
            sql += " AND sclass_id <> %s"
            params.append(exclude_id)
        with self._cursor() as (cursor, _):
            cursor.execute(sql, tuple(params))
            return self._fetch_one(cursor, SeasonClass)

    def insert(self, cls: SeasonClass):
        sql = """
        INSERT INTO t_season_class(season, class_name, class_type, class_hour,
                                   start_date, end_date, note, state)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        """
        with self._cursor() as (cursor, conn):
            cursor.execute(sql, (
                cls.season, cls.class_name, cls.class_type,
                cls.class_hour, cls.start_date or None, cls.end_date or None,
                cls.note, cls.state or ALIVE,
            ))
            new_id = cursor.lastrowid
            conn.commit()
            return new_id

    def update(self, cls: SeasonClass):
        sql = """
        UPDATE t_season_class
           SET class_name = %s, class_type = %s, class_hour = %s,
               start_date = %s, end_date = %s, note = %s, state = %s
         WHERE sclass_id = %s
        """
        with self._cursor() as (cursor, conn):
            cursor.execute(sql, (
                cls.class_name, cls.class_type, cls.class_hour,
                cls.start_date or None, cls.end_date or None,
                cls.note, cls.state or ALIVE, cls.sclass_id,
            ))
            affected = cursor.rowcount
            conn.commit()
            return affected

    def delete(self, sclass_id):
        """软删除：班级不再出现在下拉框里，学员/课次/签到全部保留。"""
        sql = "UPDATE t_season_class SET state = %s WHERE sclass_id = %s"
        with self._cursor() as (cursor, conn):
            cursor.execute(sql, (DELETED, sclass_id))
            affected = cursor.rowcount
            conn.commit()
            return affected

    def restore(self, sclass_id):
        sql = "UPDATE t_season_class SET state = %s WHERE sclass_id = %s"
        with self._cursor() as (cursor, conn):
            cursor.execute(sql, (ALIVE, sclass_id))
            affected = cursor.rowcount
            conn.commit()
            return affected

    def stats_by_class(self, season):
        """一次取回每个班的有效学员数与课次数：{sclass_id: (学员, 课次)}。"""
        sql = """
        SELECT c.sclass_id,
               (SELECT COUNT(*) FROM t_season_stu s
                 WHERE s.sclass_id = c.sclass_id AND s.state <> %s) AS stu_n,
               (SELECT COUNT(*) FROM t_season_lesson l
                 WHERE l.sclass_id = c.sclass_id) AS lesson_n
          FROM t_season_class c
         WHERE c.season = %s
        """
        with self._cursor() as (cursor, _):
            cursor.execute(sql, (DELETED, season))
            return {r["sclass_id"]: (int(r["stu_n"]), int(r["lesson_n"]))
                    for r in cursor.fetchall()}


# ---------------------------------------------------------------- 假期学员


class SeasonStuMapper(BaseMapper):

    def select_by_class(self, sclass_id, include_deleted=False):
        sql = "SELECT * FROM t_season_stu WHERE sclass_id = %s"
        if not include_deleted:
            sql += " AND state <> %s"
        sql += " ORDER BY sstu_id"
        params = (sclass_id, DELETED) if not include_deleted else (sclass_id,)
        with self._cursor() as (cursor, _):
            cursor.execute(sql, params)
            return self._fetch_all(cursor, SeasonStu)

    def select_by_id(self, sstu_id):
        sql = "SELECT * FROM t_season_stu WHERE sstu_id = %s"
        with self._cursor() as (cursor, _):
            cursor.execute(sql, (sstu_id,))
            return self._fetch_one(cursor, SeasonStu)

    def select_deleted_by_name(self, sclass_id, stu_name):
        """同班已删除的同名学员，新增时优先接手这条记录。"""
        sql = ("SELECT * FROM t_season_stu WHERE sclass_id = %s AND stu_name = %s "
               "AND state = %s ORDER BY sstu_id LIMIT 1")
        with self._cursor() as (cursor, _):
            cursor.execute(sql, (sclass_id, stu_name, DELETED))
            return self._fetch_one(cursor, SeasonStu)

    def insert(self, stu: SeasonStu):
        sql = ("INSERT INTO t_season_stu(sclass_id, stu_name, phone, note, state) "
               "VALUES (%s, %s, %s, %s, %s)")
        with self._cursor() as (cursor, conn):
            cursor.execute(sql, (stu.sclass_id, stu.stu_name, stu.phone,
                                 stu.note, stu.state or ALIVE))
            new_id = cursor.lastrowid
            conn.commit()
            return new_id

    def update(self, stu: SeasonStu, old_name=None):
        """改名时把该学员的历史签到姓名一并改掉，否则导出记录对不上人。"""
        sql = ("UPDATE t_season_stu SET stu_name = %s, phone = %s, note = %s, "
               "state = %s WHERE sstu_id = %s")
        with self._cursor() as (cursor, conn):
            try:
                cursor.execute(sql, (stu.stu_name, stu.phone, stu.note,
                                     stu.state or ALIVE, stu.sstu_id))
                if old_name and old_name != stu.stu_name:
                    cursor.execute(
                        "UPDATE t_season_sign SET stu_name = %s WHERE sstu_id = %s",
                        (stu.stu_name, stu.sstu_id))
                conn.commit()
            except Exception:
                conn.rollback()
                raise

    def delete(self, sstu_id):
        """软删除：有效标志置 0，学员不再出现在点名表里，签到记录保留。"""
        sql = "UPDATE t_season_stu SET state = %s WHERE sstu_id = %s"
        with self._cursor() as (cursor, conn):
            cursor.execute(sql, (DELETED, sstu_id))
            affected = cursor.rowcount
            conn.commit()
            return affected

    def restore(self, sstu_id):
        sql = "UPDATE t_season_stu SET state = %s WHERE sstu_id = %s"
        with self._cursor() as (cursor, conn):
            cursor.execute(sql, (ALIVE, sstu_id))
            affected = cursor.rowcount
            conn.commit()
            return affected

    def sign_counts(self, sclass_id):
        """每个学员在该班已签到的次数：{sstu_id: 次数}。"""
        sql = ("SELECT sstu_id, COUNT(*) AS n FROM t_season_sign "
               "WHERE sclass_id = %s AND sign_state = '1' GROUP BY sstu_id")
        with self._cursor() as (cursor, _):
            cursor.execute(sql, (sclass_id,))
            return {r["sstu_id"]: int(r["n"]) for r in cursor.fetchall()}

    def import_from_regular(self, sclass_id, regular_class_id):
        """把常规班的在读学生复制进假期班（按姓名去重）。

        假期班的学员常常就是从几个常规班凑起来的，手工一个个敲太费事。
        返回 (新增人数, 跳过的重名列表)。
        """
        regulars = TStuMapper().select_by_class_id(regular_class_id)
        existing = {(s.stu_name or "").strip()
                    for s in self.select_by_class(sclass_id, include_deleted=True)}
        added, skipped = 0, []
        for stu in regulars:
            name = (stu.stu_name or "").strip()
            if not name:
                continue
            if name in existing:
                skipped.append(name)
                continue
            self.insert(SeasonStu(sclass_id=sclass_id, stu_name=name,
                                  phone=stu.phone, note="", state=ALIVE))
            existing.add(name)
            added += 1
        return added, skipped


# ---------------------------------------------------------------- 假期课次


class SeasonLessonMapper(BaseMapper):

    def select_by_class(self, sclass_id):
        """按上课日期 + 时间段排序，第几次课就按这个顺序数。"""
        sql = ("SELECT * FROM t_season_lesson WHERE sclass_id = %s "
               "ORDER BY lesson_date, lesson_time, lesson_id")
        with self._cursor() as (cursor, _):
            cursor.execute(sql, (sclass_id,))
            return self._fetch_all(cursor, SeasonLesson)

    def select_by_id(self, lesson_id):
        sql = "SELECT * FROM t_season_lesson WHERE lesson_id = %s"
        with self._cursor() as (cursor, _):
            cursor.execute(sql, (lesson_id,))
            return self._fetch_one(cursor, SeasonLesson)

    def insert(self, lesson: SeasonLesson):
        sql = ("INSERT INTO t_season_lesson(sclass_id, lesson_date, lesson_time, "
               "topic, note) VALUES (%s, %s, %s, %s, %s)")
        with self._cursor() as (cursor, conn):
            cursor.execute(sql, (lesson.sclass_id, lesson.lesson_date,
                                 lesson.lesson_time, lesson.topic, lesson.note))
            new_id = cursor.lastrowid
            conn.commit()
            return new_id

    def update(self, lesson: SeasonLesson):
        sql = ("UPDATE t_season_lesson SET lesson_date = %s, lesson_time = %s, "
               "topic = %s, note = %s WHERE lesson_id = %s")
        with self._cursor() as (cursor, conn):
            cursor.execute(sql, (lesson.lesson_date, lesson.lesson_time,
                                 lesson.topic, lesson.note, lesson.lesson_id))
            affected = cursor.rowcount
            conn.commit()
            return affected

    def sign_count(self, lesson_id):
        sql = "SELECT COUNT(*) AS n FROM t_season_sign WHERE lesson_id = %s"
        with self._cursor() as (cursor, _):
            cursor.execute(sql, (lesson_id,))
            row = cursor.fetchone()
            return int(row["n"]) if row else 0

    def delete(self, lesson_id):
        """删课次连同它的签到一起删——课次是排课记录，留着孤立的签到没有意义。

        界面上会先告诉用户有多少条签到会一起消失，所以这里不再做软删除。
        """
        with self._cursor() as (cursor, conn):
            try:
                cursor.execute("DELETE FROM t_season_sign WHERE lesson_id = %s",
                               (lesson_id,))
                removed = cursor.rowcount
                cursor.execute("DELETE FROM t_season_lesson WHERE lesson_id = %s",
                               (lesson_id,))
                conn.commit()
                return removed
            except Exception:
                conn.rollback()
                raise

    def attended_counts(self, sclass_id):
        """每个课次已签到人数：{lesson_id: 人数}。"""
        sql = ("SELECT lesson_id, COUNT(*) AS n FROM t_season_sign "
               "WHERE sclass_id = %s AND sign_state = '1' GROUP BY lesson_id")
        with self._cursor() as (cursor, _):
            cursor.execute(sql, (sclass_id,))
            return {r["lesson_id"]: int(r["n"]) for r in cursor.fetchall()}


# ---------------------------------------------------------------- 假期签到


class SeasonSignMapper(BaseMapper):

    def select_by_lesson(self, lesson_id):
        """{sstu_id: SeasonSign}，点名页据此还原上次保存的状态。"""
        sql = "SELECT * FROM t_season_sign WHERE lesson_id = %s"
        with self._cursor() as (cursor, _):
            cursor.execute(sql, (lesson_id,))
            return {s.sstu_id: s for s in self._fetch_all(cursor, SeasonSign)}

    def upsert(self, lesson_id, sclass_id, sstu_id, stu_name,
               sign_hour, sign_state, sign_time=None):
        """点一次写一次：同一课次同一学员只保留一行，重复点名就是更新。"""
        sql = """
        INSERT INTO t_season_sign(lesson_id, sclass_id, sstu_id, stu_name,
                                  sign_hour, sign_state, sign_time)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
            stu_name = VALUES(stu_name),
            sign_hour = VALUES(sign_hour),
            sign_state = VALUES(sign_state),
            sign_time = VALUES(sign_time)
        """
        with self._cursor() as (cursor, conn):
            cursor.execute(sql, (lesson_id, sclass_id, sstu_id, stu_name,
                                 str(sign_hour), sign_state, sign_time))
            conn.commit()

    def upsert_batch(self, rows):
        """rows: [(lesson_id, sclass_id, sstu_id, stu_name, hour, state, time)]。"""
        if not rows:
            return 0
        sql = """
        INSERT INTO t_season_sign(lesson_id, sclass_id, sstu_id, stu_name,
                                  sign_hour, sign_state, sign_time)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
            stu_name = VALUES(stu_name),
            sign_hour = VALUES(sign_hour),
            sign_state = VALUES(sign_state),
            sign_time = VALUES(sign_time)
        """
        with self._cursor() as (cursor, conn):
            cursor.executemany(sql, [
                (r[0], r[1], r[2], r[3], str(r[4]), r[5], r[6]) for r in rows
            ])
            conn.commit()
            return len(rows)

    def delete_by_stu(self, sstu_id):
        sql = "DELETE FROM t_season_sign WHERE sstu_id = %s"
        with self._cursor() as (cursor, conn):
            cursor.execute(sql, (sstu_id,))
            affected = cursor.rowcount
            conn.commit()
            return affected

    def count_by_stu(self, sstu_id):
        sql = "SELECT COUNT(*) AS n FROM t_season_sign WHERE sstu_id = %s"
        with self._cursor() as (cursor, _):
            cursor.execute(sql, (sstu_id,))
            row = cursor.fetchone()
            return int(row["n"]) if row else 0

    def select_by_stu(self, sstu_id):
        """某个学员的全部出勤，按课次日期排（用于「该生记录」）。"""
        sql = """
        SELECT g.*, l.lesson_date, l.lesson_time
          FROM t_season_sign g
          LEFT JOIN t_season_lesson l ON l.lesson_id = g.lesson_id
         WHERE g.sstu_id = %s
         ORDER BY l.lesson_date, l.lesson_time
        """
        with self._cursor() as (cursor, _):
            cursor.execute(sql, (sstu_id,))
            return cursor.fetchall()
