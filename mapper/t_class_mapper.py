# mapper/t_class_mapper.py
from mapper.base_mapper import BaseMapper
from entity.TClass import TClass


class TClassMapper(BaseMapper):

    def select_by_id(self, class_id):
        sql = "SELECT * FROM t_class WHERE class_id = %s"
        with self._cursor() as (cursor, _):
            cursor.execute(sql, (class_id,))
            return self._fetch_one(cursor, TClass)

    def select_all(self):
        sql = "SELECT * FROM t_class ORDER BY class_id"
        with self._cursor() as (cursor, _):
            cursor.execute(sql)
            return self._fetch_all(cursor, TClass)

    def insert(self, t_class: TClass):
        sql = """
        INSERT INTO t_class(class_id, class_type, class_time, class_name,
                            class_hour, class_val, class_day)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        """
        with self._cursor() as (cursor, conn):
            cursor.execute(sql, (
                t_class.class_id,
                t_class.class_type,
                t_class.class_time,
                t_class.class_name,
                t_class.class_hour,
                t_class.class_val,
                t_class.class_day,
            ))
            conn.commit()

    def update(self, t_class: TClass, old_class_id=None):
        """更新班级。

        old_class_id 与 t_class.class_id 不同时，在同一个事务里把 t_stu、t_sign
        中指向旧编号的记录一起改到新编号——否则改编号会留下无班级的孤儿数据。
        """
        old = old_class_id if old_class_id is not None else t_class.class_id
        sql = """
        UPDATE t_class
           SET class_id = %s, class_type = %s, class_time = %s,
               class_name = %s, class_hour = %s, class_val = %s, class_day = %s
         WHERE class_id = %s
        """
        with self._cursor() as (cursor, conn):
            try:
                cursor.execute(sql, (
                    t_class.class_id,
                    t_class.class_type,
                    t_class.class_time,
                    t_class.class_name,
                    t_class.class_hour,
                    t_class.class_val,
                    t_class.class_day,
                    old,
                ))
                if old != t_class.class_id:
                    cursor.execute(
                        "UPDATE t_stu SET stu_class_id = %s WHERE stu_class_id = %s",
                        (t_class.class_id, old))
                    # 学生-班级映射表一起改，班级名单才不会掉人
                    cursor.execute(
                        "UPDATE t_stu_class SET class_id = %s WHERE class_id = %s",
                        (t_class.class_id, old))
                    cursor.execute(
                        "UPDATE t_sign SET stu_class_id = %s WHERE stu_class_id = %s",
                        (t_class.class_id, old))
                conn.commit()
            except Exception:
                conn.rollback()
                raise

    def delete(self, class_id):
        """删除班级：顺带清掉该班的学生映射（学生主档与签到记录保留）。"""
        with self._cursor() as (cursor, conn):
            cursor.execute(
                "DELETE FROM t_stu_class WHERE class_id = %s", (class_id,))
            cursor.execute("DELETE FROM t_class WHERE class_id = %s", (class_id,))
            conn.commit()
