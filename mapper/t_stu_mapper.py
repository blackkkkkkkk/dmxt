# mapper/t_stu_mapper.py
"""学生数据访问层：学生主档 + 学生-班级映射。

设计（2026-11-13 起）：
- t_stu 是「学生主档」：一名学生全库只有一条记录（按姓名判唯一），
  姓名 / 剩余课时 / 手机号 / 状态都是学生本人的属性，不再跟班级绑定。
- t_stu_class 是映射表：一个学生可以映射到多个班级；班级名单全部
  从这张表读。t_stu.stu_class_id 这一列保留但只作历史参考，不再参与
  名单查询。
- 旧库升级是自动的：首次使用时把「同一姓名在多个班各有一行」的旧
  数据合并成一条主档 + 多条映射，签到记录与积分流水一并归并。

有效标志沿用 stu_state：0 已删除（软删，可恢复）、1 正常、2 停课、3 结课。
"""

from contextlib import contextmanager

from mapper.base_mapper import BaseMapper
from entity.TStu import TStu

# stu_state 在库里是 NOT NULL 且无默认值，严格模式下漏写会报 1364
DEFAULT_STATE = "1"

# 有效标志：'0' 表示已删除（不在名单里，但记录仍保留，可恢复）
DELETED_STATE = "0"

# 所有「活学生」查询都带这个条件。t_stu 没有单独的 is_valid 列，
# 就借 stu_state 表达：0 已删除、1 正常、2 停课、3 结课。
# 不写死的表别名，方便直接拼到 WHERE / 子句后面。
ALIVE_CLAUSE = "(stu_state IS NULL OR stu_state NOT IN ('%s'))" % DELETED_STATE


def _alive(alias=""):
    """带表别名的活学生条件，如 _alive('s.') → (s.stu_state IS NULL OR ...)。"""
    return ("(%sstu_state IS NULL OR %sstu_state NOT IN ('%s'))"
            % (alias, alias, DELETED_STATE))

# ---------------------------------------------------------------- 映射表 DDL

MYSQL_SCHEMA_STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS t_stu_class (
      map_id INT NOT NULL AUTO_INCREMENT,
      stu_id INT NOT NULL COMMENT '学生主档 t_stu.stu_id',
      class_id VARCHAR(64) NOT NULL COMMENT '班级编号 t_class.class_id',
      create_time TIMESTAMP NULL DEFAULT CURRENT_TIMESTAMP,
      PRIMARY KEY (map_id),
      UNIQUE KEY uk_stu_class (stu_id, class_id),
      KEY idx_class (class_id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
)

SQLITE_SCHEMA_STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS t_stu_class (
      map_id INTEGER PRIMARY KEY AUTOINCREMENT,
      stu_id INTEGER NOT NULL,
      class_id TEXT NOT NULL,
      create_time TEXT,
      UNIQUE (stu_id, class_id)
    )
    """,
)

# 进程内标记：建表 + 合并旧数据只跑一次；恢复备份后会强制重跑。
_SCHEMA_READY = False


def _table_exists(cursor, name):
    from service.dp import is_sqlite
    if is_sqlite():
        cursor.execute(
            "SELECT COUNT(*) AS n FROM sqlite_master WHERE type='table' AND name=%s",
            (name,))
    else:
        cursor.execute(
            "SELECT COUNT(*) AS n FROM information_schema.tables "
            "WHERE table_schema = DATABASE() AND table_name = %s", (name,))
    row = cursor.fetchone()
    return bool(row) and int(list(row.values())[0] or 0) > 0


def ensure_stu_map(force=False):
    """确保映射表存在，并把旧的「按班重复」学生数据合并成主档 + 映射。

    幂等：映射表已有数据（或 t_stu 本身为空）时只建表、不合并。
    恢复备份后用 force=True 强制重新检查一遍。
    """
    global _SCHEMA_READY
    if _SCHEMA_READY and not force:
        return
    from service.dp import get_conn, is_sqlite, ensure_schema_sqlite

    # 首次运行（全新安装、空数据目录）时，version2.0 会先调本函数再碰任何
    # mapper；此时 t_stu 可能还没建出来，下面的合并就会报
    # "no such table: t_stu"。先把常规三张表建好，保证本函数可独立调用。
    if is_sqlite():
        ensure_schema_sqlite()

    conn = get_conn()
    try:
        with conn.cursor() as cursor:
            for ddl in (SQLITE_SCHEMA_STATEMENTS if is_sqlite()
                        else MYSQL_SCHEMA_STATEMENTS):
                cursor.execute(ddl)
        conn.commit()
        with conn.cursor() as cursor:
            _merge_legacy_students(cursor)
        conn.commit()
        _SCHEMA_READY = True
    finally:
        conn.close()


def _merge_legacy_students(cursor):
    """把旧的按班重复学生合并成「一条主档 + 多条映射」。

    同名视为同一名学生（这是「学生在数据中唯一」的判定依据）：
    - 主档取该姓名下最小 stu_id 的「活」记录（都删了就取最小的那条）；
    - 每个旧行所在的班级都生成一条映射（整组都已删除时也保留，方便恢复）；
    - t_point_record 里挂在重复行上的积分全部归并到主档；
    - 多余的重复行物理删除（它们的班级信息已经转移到映射表里）。
    全程在一个事务里，失败整体回滚。
    """
    cursor.execute("SELECT COUNT(*) AS n FROM t_stu_class")
    row = cursor.fetchone()
    if row and int(list(row.values())[0] or 0) > 0:
        return          # 已经迁移过（或新结构写入过），不重复处理
    cursor.execute("SELECT * FROM t_stu ORDER BY stu_id")
    rows = [dict(r) for r in cursor.fetchall()]
    if not rows:
        return

    groups = {}
    for r in rows:
        groups.setdefault(str(r.get("stu_name") or "").strip(), []).append(r)

    now = None
    for name, members in groups.items():
        alive = [m for m in members
                 if str(m.get("stu_state") or "") != DELETED_STATE]
        master = (alive or members)[0]
        master_id = master["stu_id"]
        others = [m for m in members if m["stu_id"] != master_id]
        any_alive = bool(alive)

        # 先建映射：每个成员行所在的班级都映射到主档
        # （学生已删除且组里还有活记录时，被删那一行不建映射，
        #   旧语义：删除 = 从那个班移除）
        seen_classes = set()
        for m in members:
            cid = str(m.get("stu_class_id") or "").strip()
            if not cid or cid in seen_classes:
                continue
            member_alive = str(m.get("stu_state") or "") != DELETED_STATE
            if not any_alive or member_alive:
                seen_classes.add(cid)
                cursor.execute(
                    "INSERT INTO t_stu_class(stu_id, class_id, create_time) "
                    "VALUES (%s, %s, %s)",
                    (master_id, cid, now or _now_text()))

        if not others:
            continue
        # 主档缺手机号/课时而重复行里有 → 顺手补上
        for key in ("phone", "stu_hour"):
            fallback = next((m[key] for m in others
                             if m.get(key) not in (None, "")), None)
            if (master.get(key) in (None, "")) and fallback is not None:
                master[key] = fallback
                cursor.execute(
                    "UPDATE t_stu SET %s = %%s WHERE stu_id = %%s" % key,
                    (master[key], master_id))

        # 积分流水归并到主档
        if _table_exists(cursor, "t_point_record"):
            for m in others:
                cursor.execute(
                    "UPDATE t_point_record SET stu_id = %s WHERE stu_id = %s",
                    (master_id, m["stu_id"]))
        # 重复行物理删除
        for m in others:
            cursor.execute("DELETE FROM t_stu WHERE stu_id = %s", (m["stu_id"],))


def _now_text():
    from service.dp import now_text
    return now_text()


class TStuMapper(BaseMapper):

    @contextmanager
    def _cursor(self):
        # 首次使用时建映射表 + 合并旧数据（进程内只跑一次）
        ensure_stu_map()
        with super()._cursor() as pair:
            yield pair

    # ------------------------------ 查询 ------------------------------

    def select_by_id(self, stu_id):
        sql = "SELECT * FROM t_stu WHERE stu_id = %s"
        with self._cursor() as (cursor, _):
            cursor.execute(sql, (stu_id,))
            return self._fetch_one(cursor, TStu)

    def select_by_name(self, stu_name, include_deleted=False):
        """按姓名找学生主档（姓名全局唯一，去掉首尾空格后精确匹配）。"""
        sql = "SELECT * FROM t_stu WHERE TRIM(stu_name) = TRIM(%s)"
        if not include_deleted:
            sql += " AND " + ALIVE_CLAUSE
        sql += " ORDER BY stu_id LIMIT 1"
        with self._cursor() as (cursor, _):
            cursor.execute(sql, (stu_name,))
            return self._fetch_one(cursor, TStu)

    def select_by_class_id(self, stu_class_id, include_deleted=False):
        """班级名单：走 t_stu_class 映射，按进班先后（map_id）排序。"""
        sql = ("SELECT s.* FROM t_stu s "
               "JOIN t_stu_class m ON m.stu_id = s.stu_id "
               "WHERE m.class_id = %s")
        if not include_deleted:
            sql += " AND " + _alive("s.")
        sql += " ORDER BY m.map_id"
        with self._cursor() as (cursor, _):
            cursor.execute(sql, (stu_class_id,))
            return self._fetch_all(cursor, TStu)

    def select_all(self, include_deleted=False):
        """全部学生主档（不按班级过滤），按姓名排序。"""
        sql = "SELECT * FROM t_stu"
        if not include_deleted:
            sql += " WHERE " + _alive()
        sql += " ORDER BY stu_name, stu_id"
        with self._cursor() as (cursor, _):
            cursor.execute(sql)
            return self._fetch_all(cursor, TStu)

    def classes_of(self, stu_id, include_deleted=False):
        """某个学生映射到的班级编号列表（按进班先后）。"""
        sql = ("SELECT m.class_id AS class_id FROM t_stu_class m "
               "JOIN t_stu s ON s.stu_id = m.stu_id WHERE m.stu_id = %s")
        if not include_deleted:
            sql += " AND " + _alive("s.")
        sql += " ORDER BY m.map_id"
        with self._cursor() as (cursor, _):
            cursor.execute(sql, (stu_id,))
            return [str(r["class_id"]) for r in cursor.fetchall()]

    def select_not_in_class(self, class_id, keyword=""):
        """尚未加入某班的活学生（「新增学员」选择框的数据源），可按姓名筛选。"""
        sql = ("SELECT s.* FROM t_stu s "
               "WHERE " + _alive("s.") +
               " AND NOT EXISTS (SELECT 1 FROM t_stu_class m "
               "                 WHERE m.stu_id = s.stu_id AND m.class_id = %s)")
        params = [class_id]
        kw = (keyword or "").strip()
        if kw:
            sql += " AND s.stu_name LIKE %s"
            params.append("%" + kw + "%")
        sql += " ORDER BY s.stu_name"
        with self._cursor() as (cursor, _):
            cursor.execute(sql, tuple(params))
            return self._fetch_all(cursor, TStu)

    def counts_by_class(self, include_deleted=False):
        """各班学生数（按映射统计），一次查询取回：{class_id: 人数}。"""
        sql = ("SELECT m.class_id AS class_id, COUNT(*) AS n "
               "FROM t_stu_class m JOIN t_stu s ON s.stu_id = m.stu_id")
        if not include_deleted:
            sql += " WHERE " + _alive("s.")
        sql += " GROUP BY m.class_id"
        with self._cursor() as (cursor, _):
            cursor.execute(sql)
            return {str(r["class_id"]): int(r["n"]) for r in cursor.fetchall()}

    # ------------------------------ 写入 ------------------------------

    def insert(self, stu: TStu):
        """新增学生主档。stu_class_id 有值时顺手建一条映射（兼容旧调用方）。"""
        sql = """
        INSERT INTO t_stu(stu_name, stu_class_id, stu_hour, phone, stu_state)
        VALUES (%s, %s, %s, %s, %s)
        """
        with self._cursor() as (cursor, conn):
            cursor.execute(sql, (
                stu.stu_name,
                stu.stu_class_id,
                stu.stu_hour,
                stu.phone,
                stu.stu_state or DEFAULT_STATE
            ))
            stu_id = cursor.lastrowid
            cid = str(stu.stu_class_id or "").strip()
            if stu_id and cid:
                cursor.execute(
                    "INSERT INTO t_stu_class(stu_id, class_id, create_time) "
                    "VALUES (%s, %s, %s)", (stu_id, cid, _now_text()))
            conn.commit()
            return stu_id

    def add_to_class(self, stu_id, class_id):
        """把现有学生加入班级（映射表追加，重复加入自动忽略）。"""
        sql = ("INSERT INTO t_stu_class(stu_id, class_id, create_time) "
               "VALUES (%s, %s, %s)")
        with self._cursor() as (cursor, conn):
            from service.dp import is_sqlite
            if is_sqlite():
                cursor.execute(sql + " ON CONFLICT(stu_id, class_id) DO NOTHING",
                               (stu_id, class_id, _now_text()))
            else:
                cursor.execute(sql + " ON DUPLICATE KEY UPDATE stu_id = stu_id",
                               (stu_id, class_id, _now_text()))
            conn.commit()

    def remove_from_class(self, stu_id, class_id):
        """把学生移出班级（只删映射，学生主档与签到记录都保留）。"""
        sql = "DELETE FROM t_stu_class WHERE stu_id = %s AND class_id = %s"
        with self._cursor() as (cursor, conn):
            cursor.execute(sql, (stu_id, class_id))
            affected = cursor.rowcount
            conn.commit()
            return affected

    def update(self, stu: TStu, old_name=None, old_class_id=None):
        """更新学生主档资料。

        学生已全局唯一，改名时把 t_sign 里该姓名的签到记录全部改名
        （所有班级一起改，同名学生只有一个人，不会误伤）。
        班级归属不再在这里改：加入/移出班级走 add_to_class / remove_from_class。
        """
        sql = """
        UPDATE t_stu
           SET stu_name = %s, stu_hour = %s, phone = %s, stu_state = %s
         WHERE stu_id = %s
        """
        with self._cursor() as (cursor, conn):
            try:
                cursor.execute(sql, (
                    stu.stu_name,
                    stu.stu_hour,
                    stu.phone,
                    stu.stu_state or DEFAULT_STATE,
                    stu.stu_id,
                ))
                if old_name is not None and str(old_name).strip() != str(stu.stu_name or "").strip():
                    cursor.execute(
                        "UPDATE t_sign SET stu_name = %s WHERE stu_name = %s",
                        (stu.stu_name, old_name))
                conn.commit()
            except Exception:
                conn.rollback()
                raise

    def update_stu_hour(self, stu_id, stu_hour):
        sql = "UPDATE t_stu SET stu_hour = %s WHERE stu_id = %s"
        with self._cursor() as (cursor, conn):
            cursor.execute(sql, (stu_hour, stu_id))
            conn.commit()

    def delete(self, stu_id):
        """删除 = 把有效标志置 0（学生从所有班级名单里消失），行保留可恢复。

        映射关系保留：恢复学生时，之前所在的班级一起恢复。
        """
        sql = "UPDATE t_stu SET stu_state = %s WHERE stu_id = %s"
        with self._cursor() as (cursor, conn):
            cursor.execute(sql, (DELETED_STATE, stu_id))
            affected = cursor.rowcount
            conn.commit()
            return affected

    def purge(self, stu_id):
        """物理删除主档 + 全部映射。界面上不再提供，仅用于清理误录数据。"""
        with self._cursor() as (cursor, conn):
            cursor.execute("DELETE FROM t_stu_class WHERE stu_id = %s", (stu_id,))
            cursor.execute("DELETE FROM t_stu WHERE stu_id = %s", (stu_id,))
            affected = cursor.rowcount
            conn.commit()
            return affected

    def restore(self, stu: TStu):
        """恢复已删除的学生：有效标志改回正常，并按当前资料覆盖课时/手机号。"""
        sql = """
        UPDATE t_stu
           SET stu_name = %s, stu_hour = %s, phone = %s, stu_state = %s
         WHERE stu_id = %s
        """
        with self._cursor() as (cursor, conn):
            cursor.execute(sql, (
                stu.stu_name,
                stu.stu_hour,
                stu.phone,
                stu.stu_state or DEFAULT_STATE,
                stu.stu_id,
            ))
            conn.commit()

    def select_deleted_by_name(self, stu_name, stu_class_id=None):
        """同名的已删除学生（用于新增时接手旧记录）。stu_class_id 兼容旧签名。"""
        sql = ("SELECT * FROM t_stu WHERE TRIM(stu_name) = TRIM(%s) "
               "AND stu_state = %s ORDER BY stu_id LIMIT 1")
        with self._cursor() as (cursor, _):
            cursor.execute(sql, (stu_name, DELETED_STATE))
            return self._fetch_one(cursor, TStu)
