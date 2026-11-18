# service/backup.py
"""数据备份与恢复。

数据都在单个 dmxt.db 文件里，所以备份 = 复制这个文件；恢复 = 用备份文件替换。
这里提供几个纯函数，界面层调用即可：

- list_backups()：列出备份目录下所有备份文件（按时间倒序）
- create_backup()：把当前数据库复制成带时间戳的备份
- restore_backup(path)：校验后用备份覆盖当前数据库
- delete_backup(path)：删除某个备份

备份放在数据目录下的 backups\ 子目录，命名 dmxt_backup_YYYYMMDD_HHMMSS.db。
恢复是破坏性操作，界面层必须先向用户确认再调用。
"""

import os
import shutil
import sqlite3
import time

from service import dp

BACKUP_DIR_NAME = "backups"


def backup_dir():
    return os.path.join(dp.data_dir(), BACKUP_DIR_NAME)


def _ensure_dir():
    d = backup_dir()
    os.makedirs(d, exist_ok=True)
    return d


def _stamp():
    # 精确到毫秒，避免同一秒内连续备份时文件名碰撞互相覆盖
    return time.strftime("%Y%m%d_%H%M%S") + "_%03d" % (
        int(time.time() * 1000) % 1000)


def _unique_path(name):
    """返回 backups 目录下不冲突的路径。

    时间戳只到毫秒，连续两次备份（或恢复前自动备份）可能落在同一毫秒，
    此时旧写法会生成同名文件并把前一份覆盖掉；这里自动追加序号。
    """
    d = _ensure_dir()
    dst = os.path.join(d, name)
    if not os.path.exists(dst):
        return dst
    stem, ext = os.path.splitext(name)
    for i in range(2, 1000):
        candidate = os.path.join(d, "%s_%d%s" % (stem, i, ext))
        if not os.path.exists(candidate):
            return candidate
    return os.path.join(d, "%s_%d%s" % (stem, time.time_ns(), ext))


def list_backups():
    """返回 [(绝对路径, 文件名, 大小, 修改时间), ...]，按时间倒序。"""
    d = backup_dir()
    if not os.path.isdir(d):
        return []
    items = []
    for name in os.listdir(d):
        if not name.endswith(".db"):
            continue
        path = os.path.join(d, name)
        try:
            stat = os.stat(path)
        except OSError:
            continue
        items.append((path, name, stat.st_size, stat.st_mtime))
    items.sort(key=lambda x: x[3], reverse=True)
    return items


def create_backup():
    """复制当前数据库到备份目录，返回备份文件路径。

    数据库不存在或为空时返回 None（没有数据可备份）。
    """
    src = dp.db_path()
    if not os.path.exists(src):
        return None
    dst = _unique_path("dmxt_backup_%s.db" % _stamp())
    shutil.copy2(src, dst)
    return dst


def _is_valid_db(path):
    """校验文件是不是可读的 SQLite 数据库（且含我们的核心表）。"""
    if not os.path.exists(path):
        return False, "文件不存在"
    try:
        conn = sqlite3.connect(path)
        cur = conn.cursor()
        cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = {r[0] for r in cur.fetchall()}
        conn.close()
    except Exception as exc:  # noqa: BLE001
        return False, "不是有效的数据库文件：%s" % exc
    if "t_class" not in tables or "t_stu" not in tables or "t_sign" not in tables:
        return False, "缺少核心表（t_class/t_stu/t_sign），不是本程序的备份"
    return True, ""


def restore_backup(path):
    """用备份文件覆盖当前数据库。

    返回 (ok, 消息)。恢复前会把当前数据库另存一份「恢复前自动备份」，避免误操作。
    """
    ok, err = _is_valid_db(path)
    if not ok:
        return False, err

    dst = dp.db_path()
    # 恢复前先把当前库留个底，防止误操作
    pre_backup = None
    if os.path.exists(dst):
        pre_backup = _unique_path("dmxt_恢复前_%s.db" % _stamp())
        shutil.copy2(dst, pre_backup)

    try:
        shutil.copy2(path, dst)
    except Exception as exc:  # noqa: BLE001
        return False, "恢复失败：%s" % exc

    msg = "恢复成功"
    if pre_backup:
        msg += "（原数据已另存为 %s）" % os.path.basename(pre_backup)
    return True, msg


def delete_backup(path):
    try:
        os.remove(path)
        return True, ""
    except OSError as exc:
        return False, str(exc)


def human_size(num_bytes):
    """把字节数格式化成易读大小。"""
    try:
        n = float(num_bytes)
    except (TypeError, ValueError):
        return ""
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return ("%.0f %s" % (n, unit)) if unit == "B" else ("%.1f %s" % (n, unit))
        n /= 1024
    return ""
