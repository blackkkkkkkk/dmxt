# mapper/t_sign_mapper.py
from mapper.base_mapper import BaseMapper
from entity.TSign import TSign


def _now():
    """当前本地时间字符串。MySQL 下 create_time 有 DEFAULT CURRENT_TIMESTAMP，
    但 SQLite 需要显式写入；统一在这里显式写入，两种引擎行为一致。"""
    from service.dp import is_sqlite, now_text
    return now_text()


class TSignMapper(BaseMapper):

    def select_by_id(self, dm_id):
        sql = "SELECT * FROM t_sign WHERE dm_id = %s"
        with self._cursor() as (cursor, _):
            cursor.execute(sql, (dm_id,))
            return self._fetch_one(cursor, TSign)

    def select_by_class_id(self, stu_class_id):
        sql = "SELECT * FROM t_sign WHERE stu_class_id = %s"
        with self._cursor() as (cursor, _):
            cursor.execute(sql, (stu_class_id,))
            return self._fetch_all(cursor, TSign)

    def select_by_today(self, tsign: TSign):
        # 基础SQL语句
        sql = "SELECT * FROM t_sign WHERE DATE(create_time) = CURDATE()"
        params = []

        # 检查TSign对象的各个属性，如果有值则添加到查询条件中
        if tsign.dm_id is not None:
            sql += " AND dm_id = %s"
            params.append(tsign.dm_id)

        if tsign.stu_name is not None:
            sql += " AND stu_name = %s"
            params.append(tsign.stu_name)

        if tsign.stu_class_id is not None:
            sql += " AND stu_class_id = %s"
            params.append(tsign.stu_class_id)

        if tsign.sign_hour is not None:
            sql += " AND sign_hour = %s"
            params.append(tsign.sign_hour)

        if tsign.sign_state is not None:
            sql += " AND sign_state = %s"
            params.append(tsign.sign_state)

        if tsign.bz is not None:
            sql += " AND bz = %s"
            params.append(tsign.bz)

        if tsign.update_time is not None:
            sql += " AND update_time = %s"
            params.append(tsign.update_time)

        if tsign.create_time is not None:
            sql += " AND create_time = %s"
            params.append(tsign.create_time)

        if tsign.sign_time is not None:
            sql += " AND sign_time = %s"
            params.append(tsign.sign_time)

        with self._cursor() as (cursor, _):
            cursor.execute(sql, tuple(params))
            return self._fetch_all(cursor, TSign)

    def select_today_all(self):
        """今天（按 create_time 的日期）已录入的全部签到记录。

        启动时一次性读回来，用于把今天已签到的学生回显到界面上。
        同一班同一学生理论上只有一条，老数据可能有重复，按 dm_id 倒序返回最新的在前。
        """
        sql = ("SELECT * FROM t_sign WHERE DATE(create_time) = CURDATE() "
               "ORDER BY dm_id DESC")
        with self._cursor() as (cursor, _):
            cursor.execute(sql)
            return self._fetch_all(cursor, TSign)

    def select_by_date(self, day):
        """指定日期（YYYY-MM-DD）的全部签到记录，按 dm_id 倒序。

        点名页可以选历史日期补点名：日期一律按 create_time 判定，
        与「点名记录」页的口径一致。MySQL / SQLite 都支持 DATE(col) = 字面量。
        """
        sql = ("SELECT * FROM t_sign WHERE DATE(create_time) = %s "
               "ORDER BY dm_id DESC")
        with self._cursor() as (cursor, _):
            cursor.execute(sql, (day,))
            return self._fetch_all(cursor, TSign)

    def select_by_class_today(self, stu_class_id):
        """某个班今天的签到记录，按 dm_id 倒序（第一条即最新）。"""
        sql = ("SELECT * FROM t_sign WHERE stu_class_id = %s "
               "AND DATE(create_time) = CURDATE() ORDER BY dm_id DESC")
        with self._cursor() as (cursor, _):
            cursor.execute(sql, (stu_class_id,))
            return self._fetch_all(cursor, TSign)

    def select_by_class_date(self, stu_class_id, day):
        """某个班在指定日期的签到记录，按 dm_id 倒序（保存时用来定位已有行）。"""
        sql = ("SELECT * FROM t_sign WHERE stu_class_id = %s "
               "AND DATE(create_time) = %s ORDER BY dm_id DESC")
        with self._cursor() as (cursor, _):
            cursor.execute(sql, (stu_class_id, day))
            return self._fetch_all(cursor, TSign)

    def delete_duplicate_today(self, stu_class_id, stu_name, keep_id):
        """删掉「今天 + 本班 + 该学生」除 keep_id 之外的重复记录，返回删除条数。

        一天一班一学生只应留一条；老版本保存逻辑可能插出重复行，
        保存时顺手合并，保证「当天只能签到一次」。
        """
        sql = ("DELETE FROM t_sign WHERE stu_class_id = %s AND stu_name = %s "
               "AND DATE(create_time) = CURDATE() AND dm_id <> %s")
        with self._cursor() as (cursor, conn):
            cursor.execute(sql, (stu_class_id, stu_name, keep_id))
            affected = cursor.rowcount
            conn.commit()
            return affected

    def delete_duplicate_on_date(self, stu_class_id, stu_name, keep_id, day):
        """删掉「指定日期 + 本班 + 该学生」除 keep_id 之外的重复记录。"""
        sql = ("DELETE FROM t_sign WHERE stu_class_id = %s AND stu_name = %s "
               "AND DATE(create_time) = %s AND dm_id <> %s")
        with self._cursor() as (cursor, conn):
            cursor.execute(sql, (stu_class_id, stu_name, day, keep_id))
            affected = cursor.rowcount
            conn.commit()
            return affected

    def insert(self, sign: TSign, create_time=None):
        """create_time 可指定（补点名写历史日期），格式 'YYYY-MM-DD HH:MM:SS'。"""
        sql = """
        INSERT INTO t_sign(stu_name, stu_class_id, sign_hour, sign_state, bz, sign_time, create_time)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        """
        with self._cursor() as (cursor, conn):
            cursor.execute(sql, (
                sign.stu_name,
                sign.stu_class_id,
                sign.sign_hour,
                sign.sign_state,
                sign.bz,
                sign.sign_time,
                str(create_time) if create_time else _now(),
            ))
            conn.commit()

    def insert_batch(self, signs: list[TSign], create_time=None):
        """批量写入；create_time 指定时整批归到那一天（补点名用）。"""
        if not signs:
            return
        sql = """
        INSERT INTO t_sign (
            stu_name, stu_class_id, sign_hour, sign_state, bz, sign_time, create_time
        ) VALUES (%s, %s, %s, %s, %s, %s, %s)
        """
        stamp = str(create_time) if create_time else _now()
        with self._cursor() as (cursor, conn):
            cursor.executemany(
                sql,
                [
                    (
                        s.stu_name,
                        s.stu_class_id,
                        s.sign_hour,
                        s.sign_state,
                        s.bz,
                        s.sign_time,
                        stamp,
                    )
                    for s in signs
                ]
            )
            conn.commit()
    def update_state(self, dm_id, sign_state,sign_hour):
        sql = "UPDATE t_sign SET sign_state = %s,sign_hour = %s WHERE dm_id = %s"
        with self._cursor() as (cursor, conn):
            cursor.execute(sql, (sign_state,sign_hour, dm_id))
            conn.commit()

    def count_by_stu(self, stu_name, stu_class_id):
        """某个学生（按姓名+班级定位）的签到记录数，用于删除前提示。"""
        sql = "SELECT COUNT(*) AS n FROM t_sign WHERE stu_name = %s AND stu_class_id = %s"
        with self._cursor() as (cursor, _):
            cursor.execute(sql, (stu_name, stu_class_id))
            row = cursor.fetchone()
            return int(row["n"]) if row else 0

    def count_by_stu_name(self, stu_name):
        """某名学生全部班级的签到记录总数（学生已全局唯一，按姓名统计）。"""
        sql = "SELECT COUNT(*) AS n FROM t_sign WHERE stu_name = %s"
        with self._cursor() as (cursor, _):
            cursor.execute(sql, (stu_name,))
            row = cursor.fetchone()
            return int(row["n"]) if row else 0

    def delete_by_stu_name(self, stu_name):
        """删除某名学生全部班级的签到记录（学生已全局唯一，按姓名删除）。"""
        sql = "DELETE FROM t_sign WHERE stu_name = %s"
        with self._cursor() as (cursor, conn):
            cursor.execute(sql, (stu_name,))
            affected = cursor.rowcount
            conn.commit()
            return affected

    def delete_by_stu(self, stu_name, stu_class_id):
        """删除某个学生的全部签到记录（按姓名+班级定位）。"""
        sql = "DELETE FROM t_sign WHERE stu_name = %s AND stu_class_id = %s"
        with self._cursor() as (cursor, conn):
            cursor.execute(sql, (stu_name, stu_class_id))
            affected = cursor.rowcount
            conn.commit()
            return affected