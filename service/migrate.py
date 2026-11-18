# service/migrate.py
"""MySQL → SQLite 一次性数据迁移。

背景：老版本依赖外部 MySQL，新版默认用内置 SQLite。为了让老用户升级后
数据不丢，这里在「SQLite 数据库还是空的、且能连上旧 MySQL」时，把旧库里的
全部数据一次性导入 SQLite，导入成功后写一个标记文件，之后不再重复导入。

迁移是只读 + 追加式的：
- 只从 MySQL 读，不删不改旧库；
- 只在 SQLite 为空时才导入（已有数据绝不覆盖）；
- 失败不阻断程序启动，只在日志里留记录，用户下次打开会重试。
"""

import json
import logging
import os
import sys

from service import dp


def _migration_marker_path():
    return os.path.join(dp.app_dir(), ".migrated_to_sqlite")


def _sqlite_is_empty():
    """SQLite 里常规三张表都是空的，才值得做迁移。"""
    dp.ensure_schema_sqlite()
    conn = dp.get_conn()
    try:
        with conn.cursor() as cursor:
            cursor.execute("SELECT (SELECT COUNT(*) FROM t_class)"
                           " + (SELECT COUNT(*) FROM t_stu)"
                           " + (SELECT COUNT(*) FROM t_sign) AS n")
            row = cursor.fetchone()
    finally:
        conn.close()
    if not row:
        return True
    return int(list(row.values())[0] or 0) == 0


def already_migrated():
    return os.path.exists(_migration_marker_path())


def _try_connect_mysql(cfg):
    """尝试连接旧 MySQL；连不上返回 None（说明没有旧库，跳过迁移）。"""
    import pymysql
    try:
        return pymysql.connect(
            host=cfg["host"], port=cfg["port"], user=cfg["user"],
            password=cfg["password"], database=cfg["database"],
            charset=cfg.get("charset", "utf8mb4"),
            cursorclass=pymysql.cursors.DictCursor,
            connect_timeout=4,
        )
    except Exception as exc:  # noqa: BLE001
        logging.info("未检测到旧 MySQL 数据库，跳过迁移：%s", exc)
        return None


def _copy_table(mysql_conn, sqlite_conn, table, columns, transform=None):
    """把 MySQL 某张表的全部行复制到 SQLite 同名表。

    columns: 有序列名列表；transform(row) 可选，返回规整后的值列表。
    """
    with mysql_conn.cursor() as mc:
        mc.execute("SELECT * FROM `%s`" % table)
        rows = mc.fetchall()
    if not rows:
        return 0
    placeholders = ", ".join(["?"] * len(columns))
    col_list = ", ".join(columns)
    sql = "INSERT INTO %s(%s) VALUES (%s)" % (table, col_list, placeholders)
    with sqlite_conn.cursor() as sc:
        for row in rows:
            if transform:
                values = transform(row)
            else:
                values = [row.get(c) for c in columns]
            sc.execute(sql, values)
    return len(rows)


def _as_text(value):
    if value is None:
        return None
    if hasattr(value, "strftime"):
        return value.strftime("%Y-%m-%d %H:%M:%S")
    return str(value)


def migrate_if_needed():
    """迁移入口：判断条件满足就迁移，返回 (是否执行了迁移, 摘要字符串)。"""
    cfg = dp.load_config()
    if cfg["engine"] == "mysql":
        # 当前就是 MySQL 模式，无需迁移
        return False, ""
    if already_migrated():
        return False, ""
    if not _sqlite_is_empty():
        # SQLite 已有数据（可能是新用户已在使用），不要覆盖
        return False, ""

    mysql_conn = _try_connect_mysql(cfg)
    if mysql_conn is None:
        return False, ""

    summary_parts = []
    try:
        # 确保 SQLite 常规表 + 假期表都建好
        from mapper.t_season_mapper import ensure_schema
        dp.ensure_schema_sqlite()
        ensure_schema(force=True)

        sqlite_conn = dp.get_conn()

        # 1. 常规班级
        n = _copy_table(mysql_conn, sqlite_conn, "t_class",
                        ["class_id", "class_type", "class_time", "class_name",
                         "class_hour", "class_val", "class_day"],
                        lambda r: [r.get("class_id"), r.get("class_type"),
                                   r.get("class_time"), r.get("class_name"),
                                   r.get("class_hour"), r.get("class_val"),
                                   r.get("class_day")])
        summary_parts.append("班级 %d" % n)

        # 2. 学生
        n = _copy_table(mysql_conn, sqlite_conn, "t_stu",
                        ["stu_id", "stu_name", "stu_class_id", "stu_hour",
                         "phone", "stu_state"],
                        lambda r: [r.get("stu_id"), r.get("stu_name"),
                                   r.get("stu_class_id"), r.get("stu_hour"),
                                   r.get("phone"), r.get("stu_state") or "1"])
        summary_parts.append("学生 %d" % n)

        # 3. 签到
        n = _copy_table(mysql_conn, sqlite_conn, "t_sign",
                        ["dm_id", "stu_name", "stu_class_id", "sign_hour",
                         "sign_state", "sign_time", "bz", "update_time",
                         "create_time"],
                        lambda r: [r.get("dm_id"), r.get("stu_name"),
                                   r.get("stu_class_id"), r.get("sign_hour"),
                                   r.get("sign_state"), r.get("sign_time"),
                                   r.get("bz"), _as_text(r.get("update_time")),
                                   _as_text(r.get("create_time"))])
        summary_parts.append("签到 %d" % n)

        # 4. 假期四张表（存在才迁）
        season_tables = {
            "t_season_class": ["sclass_id", "season", "class_name", "class_type",
                               "class_hour", "start_date", "end_date", "note",
                               "state", "create_time"],
            "t_season_stu": ["sstu_id", "sclass_id", "stu_name", "phone",
                             "note", "state", "create_time"],
            "t_season_lesson": ["lesson_id", "sclass_id", "lesson_date",
                                "lesson_time", "topic", "note", "create_time"],
            "t_season_sign": ["sign_id", "lesson_id", "sclass_id", "sstu_id",
                              "stu_name", "sign_hour", "sign_state",
                              "sign_time", "bz", "update_time"],
        }
        for table, cols in season_tables.items():
            try:
                n = _copy_table(mysql_conn, sqlite_conn, table, cols)
            except Exception as exc:  # noqa: BLE001
                logging.info("迁移 %s 跳过：%s", table, exc)
                continue
            summary_parts.append("%s %d" % (table, n))

        sqlite_conn.commit()
        sqlite_conn.close()

        # 写标记文件
        try:
            with open(_migration_marker_path(), "w", encoding="utf-8") as f:
                json.dump({
                    "migrated_at": dp.now_text(),
                    "summary": ", ".join(summary_parts),
                }, f, ensure_ascii=False, indent=2)
        except Exception as exc:  # noqa: BLE001
            logging.info("写迁移标记失败（不影响使用）：%s", exc)

        logging.info("已从旧 MySQL 迁移数据到 SQLite：%s", ", ".join(summary_parts))
        return True, ", ".join(summary_parts)
    finally:
        mysql_conn.close()
