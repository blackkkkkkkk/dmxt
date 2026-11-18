import calendar
import json
import logging
import os
import re
import sys
import tkinter as tk
from tkinter import ttk, messagebox
from datetime import date, datetime, time, timedelta

from entity.TSign import TSign
from mapper.t_class_mapper import TClassMapper
from mapper.t_sign_mapper import TSignMapper
from mapper.t_stu_mapper import TStuMapper

from ui_background import DEFAULT_CONFIG as BG_DEFAULT
from ui_background import BackgroundCanvas
from ui_theme import COLORS, RoundedButton, apply_ttk_style, font


def app_dir():
    """程序所在目录：源码运行取项目根，PyInstaller 打包后取 exe 所在目录。"""
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def data_dir():
    """用户数据目录（数据库/日志/配置/导出）。统一走 service.dp.data_dir()。"""
    from service.dp import data_dir as _data_dir
    return _data_dir()


LOG_PATH = os.path.join(data_dir(), "dmxt_error.log")
try:
    logging.basicConfig(
        filename=LOG_PATH,
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        encoding="utf-8",
    )
except Exception:
    # 目录不可写时退回控制台日志，至少不阻断启动
    logging.basicConfig(level=logging.INFO)


def enable_dpi_awareness():
    """Windows 高 DPI 下让界面按物理像素渲染，避免整窗口被位图拉伸发虚。"""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        try:
            import ctypes
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            logging.info("DPI 感知设置失败，按系统默认缩放渲染")


# 单实例互斥量句柄，进程存活期间持有，进程退出时系统自动释放
_single_instance_handle = None


def acquire_single_instance():
    """尝试获取单实例锁，返回 (是否成功, 已有窗口句柄或 0)。

    用 Windows 命名互斥量判断是否已有实例在运行。成功后返回的句柄要一直持有
    到进程结束（存到模块级变量），否则互斥量会被提前释放。
    """
    global _single_instance_handle
    if sys.platform != "win32":
        # 非 Windows 平台先不限制（本程序面向 Windows）
        return True, 0
    try:
        import ctypes
        from ctypes import wintypes
        kernel32 = ctypes.windll.kernel32
        mutex_name = "Global\\QuanTongQuanXing_RollCall_SingleInstance"
        _single_instance_handle = kernel32.CreateMutexW(None, False, mutex_name)
        if not _single_instance_handle:
            return True, 0
        last_error = kernel32.GetLastError()
        # ERROR_ALREADY_EXISTS = 183
        if last_error == 183:
            # 找到已有窗口并置前
            user32 = ctypes.windll.user32
            hwnd = user32.FindWindowW(None, "全童全星上课点名系统")
            if hwnd:
                try:
                    user32.ShowWindow(hwnd, 9)      # SW_RESTORE
                    user32.SetForegroundWindow(hwnd)
                except Exception:
                    pass
            return False, hwnd
        return True, 0
    except Exception:
        # 拿锁失败不应阻断启动（兼容性兜底）
        return True, 0


def resource_path(*parts):
    """取打包进 exe 的静态资源：源码运行取项目根，PyInstaller 运行取临时解包目录。"""
    base = getattr(sys, "_MEIPASS", None) or app_dir()
    return os.path.join(base, *parts)


def apply_window_icon(win):
    """把 assets/app.ico 设成窗口图标：标题栏左上角、任务栏、Alt+Tab 都用它。

    Windows 上优先用 .ico（里面已经放好 16~256 各档，系统按 DPI 挑最合适的一档），
    取不到再退回 PNG。Toplevel 弹窗会自动继承这个默认图标。
    """
    ico = resource_path("assets", "app.ico")
    try:
        if os.path.exists(ico):
            win.iconbitmap(default=ico)
            return True
    except Exception:
        logging.info("设置 ico 窗口图标失败：%s", ico, exc_info=True)
    png = resource_path("assets", "logo.png")
    try:
        if os.path.exists(png):
            photo = tk.PhotoImage(file=png)
            win.iconphoto(True, photo)
            win._icon_photo = photo      # PhotoImage 不挂引用会被 GC 掉
            return True
    except Exception:
        logging.info("设置 png 窗口图标失败：%s", png, exc_info=True)
    return False


WEEKDAYS = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")

# 上课时间在库里是 "08.20-10.20" 这种「点号」写法，也兼容 "19:00-21:00"；
# 连字符位置还见过 ~ 和中文破折号，一并放宽，脏数据解析失败会返回 None。
_TIME_RANGE_RE = re.compile(
    r"(\d{1,2})\s*[.:：]\s*(\d{2})\s*[-~—–－至]\s*(\d{1,2})\s*[.:：]\s*(\d{2})")


def parse_time_range(text):
    """把 class_time 解析成 (开始 time, 结束 time)；解析不出来返回 None。"""
    m = _TIME_RANGE_RE.search(str(text or ""))
    if not m:
        return None
    try:
        h1, m1, h2, m2 = (int(g) for g in m.groups())
        start, end = time(h1, m1), time(h2, m2)
    except (TypeError, ValueError):
        return None
    if end <= start:
        return None
    return start, end


def weekday_text(moment):
    """datetime → '周一' … '周日'（与 t_class.class_day 的取值一致）。"""
    return WEEKDAYS[moment.weekday()]


_DATE_FORMATS = ("%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d", "%Y年%m月%d日")
_SHORT_DATE_FORMATS = ("%m-%d", "%m/%d", "%m月%d日")


def normalize_date(value):
    """把日期输入规范成 YYYY-MM-DD；也接受 2026/11/13、11-13 这类写法。"""
    if isinstance(value, (datetime, date)):
        return value.strftime("%Y-%m-%d")
    text = str(value or "").strip()
    if not text:
        raise ValueError("请填写点名日期，例如 2026-11-13。")
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).strftime("%Y-%m-%d")
        except ValueError:
            pass
    for fmt in _SHORT_DATE_FORMATS:
        try:
            parsed = datetime.strptime(text, fmt)
            return date(date.today().year, parsed.month, parsed.day).strftime("%Y-%m-%d")
        except ValueError:
            pass
    raise ValueError("日期「%s」不是有效日期，请写成 2026-11-13 这样的格式。" % text)


def open_date_picker(parent, current, on_pick, today=None):
    """轻量月历弹窗：选中某天回调 on_pick('YYYY-MM-DD') 并关闭。

    只用标准库 calendar，避免为了一个日期框给安装包多加第三方依赖。
    """
    today_obj = date.today()
    try:
        today_obj = datetime.strptime(today, "%Y-%m-%d").date() if today else today_obj
    except (TypeError, ValueError):
        pass
    try:
        selected = datetime.strptime(current, "%Y-%m-%d").date()
    except (TypeError, ValueError):
        selected = today_obj

    win = tk.Toplevel(parent)
    win.title("选择点名日期")
    win.configure(bg=COLORS["card"])
    win.resizable(False, False)
    try:
        win.transient(parent.winfo_toplevel())
    except tk.TclError:
        pass
    state = {"y": selected.year, "m": selected.month, "picked": selected}

    def small_button(master, text, command, width=3):
        return tk.Button(master, text=text, command=command, width=width,
                         bg=COLORS["card"], fg=COLORS["text"], font=font(10),
                         bd=0, relief=tk.FLAT, highlightthickness=0,
                         activebackground=COLORS["primary_soft"],
                         cursor="hand2")

    head = tk.Frame(win, bg=COLORS["card"])
    head.pack(fill=tk.X, padx=14, pady=(12, 4))
    title = tk.Label(head, text="", bg=COLORS["card"], fg=COLORS["text"],
                     font=font(12, True))
    title.pack(side=tk.LEFT, expand=True)
    body = tk.Frame(win, bg=COLORS["card"])
    body.pack(padx=14, pady=(0, 8))
    for col, wd in enumerate(WEEKDAYS):
        tk.Label(body, text=wd[-1], width=4, bg=COLORS["card"],
                 fg=COLORS["muted"], font=font(10)).grid(
            row=0, column=col, padx=2, pady=(2, 6))
    cells = []
    for row in range(6):
        line = []
        for col in range(7):
            btn = tk.Button(body, text="", width=4, font=font(10), bd=0,
                            relief=tk.FLAT, highlightthickness=0,
                            cursor="hand2", bg=COLORS["card"],
                            fg=COLORS["text"],
                            activebackground=COLORS["primary_soft"])
            btn.grid(row=row + 1, column=col, padx=2, pady=2)
            line.append(btn)
        cells.append(line)

    def choose(day_obj):
        state["picked"] = day_obj
        try:
            on_pick(day_obj.strftime("%Y-%m-%d"))
        finally:
            win.destroy()

    def draw():
        title.config(text="%d 年 %d 月" % (state["y"], state["m"]))
        weeks = calendar.Calendar(firstweekday=0).monthdatescalendar(
            state["y"], state["m"])
        for r in range(6):
            week = weeks[r] if r < len(weeks) else None
            for c in range(7):
                btn = cells[r][c]
                if week is None:
                    btn.config(text="", state=tk.DISABLED, bg=COLORS["card"])
                    continue
                day_obj = week[c]
                bg, fg = COLORS["card"], COLORS["text"]
                if day_obj == state["picked"]:
                    bg, fg = COLORS["primary"], "#FFFFFF"
                elif day_obj == today_obj:
                    bg, fg = COLORS["primary_soft"], COLORS["primary"]
                elif day_obj.month != state["m"]:
                    fg = COLORS["subtle"]
                btn.config(text=str(day_obj.day), bg=bg, fg=fg,
                           activebackground=COLORS["primary_soft"],
                           activeforeground=COLORS["primary"],
                           state=tk.NORMAL,
                           command=lambda d=day_obj: choose(d))

    def shift_month(delta):
        month = state["m"] + delta
        year = state["y"]
        while month < 1:
            month += 12
            year -= 1
        while month > 12:
            month -= 12
            year += 1
        state["y"], state["m"] = year, month
        draw()

    small_button(head, "‹", lambda: shift_month(-1)).pack(side=tk.LEFT)
    small_button(head, "›", lambda: shift_month(1)).pack(side=tk.LEFT)
    small_button(head, "今天", lambda: choose(today_obj), width=5).pack(
        side=tk.LEFT, padx=(10, 0))
    small_button(head, "关闭", win.destroy, width=5).pack(side=tk.LEFT, padx=(4, 0))

    foot = tk.Label(win, text="点日期即可切换；‹ › 翻月，Esc 关闭",
                    bg=COLORS["card"], fg=COLORS["muted"], font=font(9))
    foot.pack(pady=(0, 10))
    draw()
    win.bind("<Escape>", lambda _e: win.destroy())
    win.update_idletasks()
    try:
        px, py = parent.winfo_rootx(), parent.winfo_rooty()
        pw, ph = parent.winfo_width(), parent.winfo_height()
        w, h = win.winfo_width(), win.winfo_height()
        x = max(0, px + max(pw - w, 0) // 2)
        y = max(0, py + max(ph - h, 0) // 3)
    except tk.TclError:
        x, y = 120, 120
    win.geometry("+%d+%d" % (x, y))
    try:
        win.grab_set()
    except tk.TclError:
        pass
    return win


def pick_class_for_moment(classes, now):
    """按「今天是周几 + 现在几点」挑出最该点名的那一个班。

    返回 (班级, 依据)，依据取值：
      ongoing  —— 正在上的这一节
      upcoming —— 今天还没开始、最近的一节
      finished —— 今天的课都上完了，取最后一节
      no_time  —— 今天有班，但上课时间都是脏数据解析不出来
      no_day   —— 今天这个星期几压根没排课
    """
    today = weekday_text(now)
    same_day = [c for c in (classes or []) if getattr(c, "class_day", "") == today]
    if not same_day:
        return None, "no_day"

    timed, untimed = [], []
    for cls in same_day:
        span = parse_time_range(getattr(cls, "class_time", ""))
        if span:
            timed.append((cls, span))
        else:
            untimed.append(cls)

    if not timed:
        # 今天只有脏数据：至少别让用户看到空列表
        return (untimed[0], "no_time") if untimed else (None, "no_day")

    cur = now.time()
    for cls, (start, end) in timed:
        if start <= cur <= end:
            return cls, "ongoing"

    later = [(cls, span) for cls, span in timed if span[0] > cur]
    if later:
        return min(later, key=lambda item: item[1][0])[0], "upcoming"

    return max(timed, key=lambda item: item[1][1])[0], "finished"


class RollCallSystemUI:
    def __init__(self, master):
        self.master = master
        master.title("全童全星上课点名系统")
        master.geometry("1450x860")
        master.minsize(1180, 720)
        master.state('zoomed')
        master.configure(bg=COLORS['bg'])
        # 标题栏左上角 / 任务栏 / Alt+Tab 的图标
        self.icon_ok = apply_window_icon(master)

        # 初始化数据：数据库不可用时不让界面崩掉，改为在页面上提示
        self.student_data = {}
        self.student_ids = {}
        self.class_all = []
        self.course_data = {}
        self.db_error = ""
        self.sign_in_status_by_class = {}
        # 每个班「最近一次从数据库读到的签到状态」（按 stu_id）。
        # 用来区分：界面上的勾选是数据库带出来的，还是老师手动点了还没保存。
        # 数据库记录被改/删时，只回退前者，手动点的不会被刷新冲掉。
        self.sign_in_baseline_by_class = {}
        # 点名日期：默认今天，可切到往日补点名。
        # sign_date_manual=False 时跨天自动跟随系统日期，保证第二天不带昨天状态；
        # 老师主动挑过日期后不再被跨天逻辑抢走，直到点「今天」。
        self.sign_date = self._today_text()
        self.sign_date_manual = False
        # 每个日期一份界面草稿：没保存的勾选/课时/临时学生按日期隔离，切换不串
        self.sign_drafts = {}
        self.sign_date_var = None
        # 今天已录入的签到（启动时从数据库读回，用于界面回显）
        self.today_sign = {}
        self.today_sign_date = ""
        self.today_sign_loaded = False
        self.today_sign_error = ""
        self.today_sign_last = ""
        # 老版本用 MySQL，新版默认 SQLite：首次运行且 SQLite 为空时，
        # 尝试把旧 MySQL 里的数据一次性迁进来（失败不影响启动）。
        try:
            from service import migrate
            migrated, summary = migrate.migrate_if_needed()
            if migrated:
                logging.info("旧数据迁移完成：%s", summary)
        except Exception as e:
            logging.info("数据迁移检查跳过：%s", e)
        try:
            # 学生主档 + 学生-班级映射表：建表并把旧的按班重复学生合并掉
            from mapper.t_stu_mapper import ensure_stu_map
            ensure_stu_map()
        except Exception as e:
            logging.exception("学生映射表整理失败：%s", e)
        try:
            self.student_data = self.load_student_data()
            self.course_data = self.init_course_json()
            self.load_today_sign(force=True)
        except Exception as e:
            self.db_error = str(e)
            logging.exception("启动时加载数据库数据失败")

        self.current_class = list(self.student_data.keys())[0] if self.student_data else ""
        self.hour_overrides = {}      # 班级 → {stu_id: 已改课时}，重建页面时不丢
        # 临时上课学生：仅当天、当前班有效，不进正式名单，但能点名并保存到签到记录。
        # 结构：{class_id: [姓名, ...]}，纯内存；iid 用 temp_N 命名空间与 stu_id 区分。
        self.temp_students = {}
        # 临时学生按班级保存签到状态，切班回来时保留
        self.temp_sign_in_status = {}
        # 必须在 hour_overrides 之后：建状态时会把数据库里今天的课时回填进来
        self.sign_in_status = self.init_sign_in_status()
        self.season_last = {}         # 暑假/寒假 → {class_id, lesson_id}，切页后保持选择
        self.class_choice_manual = False   # 用户手动选过班后，不再自动改他的选择
        self.class_hint_source = ""         # 工具条下方说明的前缀：自动选中/手动选择…
        self.highlighted_item = None
        self.nav_buttons = {}
        self.current_page = None
        self.ui_config = self.load_ui_config()
        # 积分管理自定义快捷键：绑在主窗口上，只在积分页且选中学生时生效。
        # 绑一次就够，改设置时由管理器自己解绑旧键、重绑新键。
        try:
            from points_shortcuts import ShortcutManager
            self.shortcuts = ShortcutManager(self)
        except Exception:
            logging.exception("积分快捷键初始化失败")
            self.shortcuts = None

        # 样式配置（表格 / 下拉框 / 滚动条）
        self.style = apply_ttk_style(master)

        self.create_widgets()
        # 程序启动时默认显示点名页面
        self.show_roll_call()

    # ---------------------- 数据加载/初始化 ----------------------
    def init_course_json(self):
        # json_path = "course_data.json"
        # demo_data = {
        #     "周五": [{"time": "18:40-20:40", "content": "C++"}],
        #     "周六": [
        #         {"time": "08:10-10:10", "content": "C++"},
        #         {"time": "10:10-12:10", "content": "C++"},
        #         {"time": "14:00-16:00", "content": "C++"},
        #         {"time": "16:00-18:00", "content": "C++"}
        #     ],
        #     "周日": [
        #         {"time": "08:10-10:10", "content": "C++"},
        #         {"time": "10:10-12:10", "content": "C++"},
        #         {"time": "14:00-16:00", "content": "C++"},
        #         {"time": "16:00-18:00", "content": "竞赛/考级"}
        #     ]
        # }
        # with open(json_path, 'w', encoding='utf-8') as f:
        #     json.dump(demo_data, f, ensure_ascii=False, indent=4)

        tcs = self.class_all
        if not tcs:
            tcs = TClassMapper().select_all()
            self.class_all = tcs
        class_dict = {"周一": [], "周二": [], "周三": [], "周四": [], "周五": [], "周六": [], "周日": []}
        for t in tcs:
            # class_day 出现脏数据时跳过该行，避免整个课程页 KeyError
            if t.class_day not in class_dict:
                logging.warning("班级 %s 的 class_day=%r 不在周一..周日内，已跳过", t.class_id, t.class_day)
                continue
            class_dict[t.class_day].append({"time": t.class_time, "content": t.class_type})

        for c in list(class_dict.keys()):
            if not class_dict[c]:
                class_dict.pop(c)
        return class_dict

    def load_student_data(self):
        tcMapper = TClassMapper()
        tsMapper = TStuMapper()
        tcs = tcMapper.select_all()
        stu_dict = {}
        id_dict = {}
        for t in tcs:
            st = tsMapper.select_by_class_id(t.class_id)
            stu_dict[t.class_id] = [s.stu_name for s in st]
            # 签到状态用 stu_id 作键，同班同名也不会互相覆盖
            id_dict[t.class_id] = [str(s.stu_id) for s in st]

        self.class_all = tcs
        self.student_ids = id_dict
        return stu_dict

    # ---------------------- 今日签到（数据库回显） ----------------------
    @staticmethod
    def _is_signed_state(state):
        """数据库里的签到状态归一：老数据有 ''、NULL、'1'、'已签到' 几种写法。"""
        return str(state if state is not None else "").strip() in (
            "已签到", "1", "已到", "签到", "true", "True")

    @staticmethod
    def _today_text(moment=None):
        return (moment or datetime.now()).strftime("%Y-%m-%d")

    def load_today_sign(self, force=False):
        """从数据库读「当前点名日期」的签到记录 → {班级: {学生姓名: 记录}}。

        - 默认读今天；点名页选了历史日期就改读那一天（补点名）；
        - 只有日期变了或 force=True 才查库，平时走内存缓存，切班/重绘不刷库；
        - 跨天 / 换日期自动清掉上一天的勾选与课时，不会把状态带过去；
        - 数据库不可用时把错误留在 today_sign_error，界面照常按未签到显示。
        """
        today = self._today_text()
        if not getattr(self, "sign_date_manual", False):
            # 没手动挑日期时，跨天自动跟到新的一天
            self.sign_date = today
        day = getattr(self, "sign_date", "") or today
        cached = getattr(self, "today_sign_date", "")
        if (not force and cached == day
                and getattr(self, "today_sign_loaded", False)):
            return getattr(self, "today_sign", {})

        if cached and cached != day:
            # 隔天 / 切换点名日期：上一天的勾选、改的课时、基线全部作废
            self.sign_in_status_by_class = {}
            self.sign_in_baseline_by_class = {}
            if hasattr(self, "hour_overrides"):
                self.hour_overrides.clear()
            if hasattr(self, "temp_sign_in_status"):
                self.temp_sign_in_status.clear()

        self.today_sign = {}
        self.today_sign_date = day
        self.today_sign_loaded = False
        self.today_sign_error = ""
        self.today_sign_last = ""
        try:
            mapper = TSignMapper()
            rows = (mapper.select_today_all() if day == today
                    else mapper.select_by_date(day))
        except Exception as e:
            self.today_sign_error = str(e)
            logging.exception("读取点名日期签到记录失败")
            return self.today_sign

        for row in rows:
            class_id = (row.stu_class_id or "").strip()
            name = (row.stu_name or "").strip()
            if not class_id or not name:
                continue
            bucket = self.today_sign.setdefault(class_id, {})
            if name in bucket:
                # 老数据有重复：只认最新那条（SQL 已按 dm_id 倒序）
                bucket[name]["dups"] += 1
                continue
            bucket[name] = {
                "signed": self._is_signed_state(row.sign_state),
                "state": row.sign_state or "",
                "hour": row.sign_hour,
                "time": row.sign_time or "",
                "dm_id": row.dm_id,
                "dups": 0,
            }

        if rows:
            newest = rows[0].create_time or rows[0].update_time
            if newest:
                self.today_sign_last = newest.strftime("%H:%M")
        self.today_sign_loaded = True
        return self.today_sign

    # ---------------------- 点名日期（补点名） ----------------------
    def sign_date_text(self):
        """当前点名日期 YYYY-MM-DD；默认今天。"""
        return getattr(self, "sign_date", "") or self._today_text()

    def _sync_sign_date_from_entry(self, strict=False):
        """把日期输入框里「还没按回车」的内容同步成内部点名日期。

        只改日期字段、刷新提示，不重读库也不动表格勾选：保存、导出这类
        写数据的动作在动手前先调一次，避免「框里写着 13 号、却按 14 号写库」。
        strict=True 时遇到非法日期会提示并返回 False，调用方应中止写库。
        """
        var = getattr(self, "sign_date_var", None)
        text = (var.get() or "").strip() if var is not None else ""
        if not text:
            return True
        try:
            day = normalize_date(text)
        except ValueError as exc:
            if strict:
                messagebox.showwarning("日期格式不对", str(exc))
                self.render_sign_date_line()
                return False
            # 老师还在输入途中，静默忽略，别用弹窗打断
            return True
        if day != getattr(self, "sign_date", ""):
            self.sign_date = day
            self.sign_date_manual = (day != self._today_text())
            self.render_sign_date_line()
        return True

    def _on_sign_date_entry_blur(self):
        """输入框失焦时正式切换日期，连带刷新那一天的签到状态。"""
        try:
            var = getattr(self, "sign_date_var", None)
            text = (var.get() or "").strip() if var is not None else ""
            if not text:
                self.render_sign_date_line()
                return
            try:
                day = normalize_date(text)
            except ValueError:
                return                  # 还没输完，不打断
            if day != self.sign_date_text():
                self.set_sign_date(day)
            else:
                # 顺便把 2026/11/13 这类写法规范成 2026-11-13
                self.render_sign_date_line()
        except tk.TclError:
            pass                        # 窗口正在关闭，控件已销毁

    def _sign_moment(self):
        """班级定位/提示用的「点名日 + 现在时刻」；默认路径与 datetime.now() 等价。"""
        now = datetime.now()
        if not getattr(self, "sign_date_manual", False):
            return now
        try:
            day = datetime.strptime(self.sign_date_text(), "%Y-%m-%d").date()
            return datetime.combine(day, now.time())
        except ValueError:
            return now

    def _sign_day_prefix(self):
        """文案前缀：默认「今天周六」，补点名时「2026-11-13 周五」。"""
        wd = weekday_text(self._sign_moment())
        if not getattr(self, "sign_date_manual", False):
            return "今天" + wd
        return "%s %s" % (self.sign_date_text(), wd)

    def _snapshot_sign_state(self):
        """把「当前日期」的界面状态存成草稿（含未保存的勾选/课时/临时学生）。"""
        if self.current_class:
            self.sign_in_status_by_class[self.current_class] = dict(self.sign_in_status)
        self._sync_temp_status_from_sign()
        self._capture_hours()
        return {
            "status": {k: dict(v) for k, v in self.sign_in_status_by_class.items()},
            "hours": {k: dict(v) for k, v in self.hour_overrides.items()},
            "baseline": {k: dict(v) for k, v in self.sign_in_baseline_by_class.items()},
            "temp": {k: dict(v) for k, v in self.temp_sign_in_status.items()},
        }

    def _restore_sign_state(self, draft):
        self.sign_in_status_by_class = {
            k: dict(v) for k, v in draft.get("status", {}).items()}
        self.hour_overrides = {k: dict(v) for k, v in draft.get("hours", {}).items()}
        self.sign_in_baseline_by_class = {
            k: dict(v) for k, v in draft.get("baseline", {}).items()}
        self.temp_sign_in_status = {k: dict(v) for k, v in draft.get("temp", {}).items()}

    def set_sign_date(self, value, _quiet=False):
        """切换点名日期：重读那一天的签到记录，按库重算勾选、课时与统计。"""
        try:
            day = normalize_date(value)
        except ValueError as exc:
            if not _quiet:
                messagebox.showwarning("日期格式不对", str(exc))
            self.render_sign_date_line()
            return False
        old = self.sign_date_text()
        if day == old:
            self.render_sign_date_line()
            return True
        # 1) 旧日期没保存的勾选/课时按日期收好，切回来还在
        self.sign_drafts[old] = self._snapshot_sign_state()
        self.sign_date = day
        self.sign_date_manual = (day != self._today_text())
        # 2) 按新日期重读数据库；load 会顺手清掉跨日期的内存状态
        self.load_today_sign(force=True)
        # 3) 新日期之前留过草稿就恢复，否则完全以数据库为准
        draft = self.sign_drafts.get(day)
        if draft:
            self._restore_sign_state(draft)
        # 4) 没手动选过班时，按新日期的星期重新定位班级；选过班就只改说明里的日期
        if not getattr(self, "class_choice_manual", False):
            self.auto_select_class()
        else:
            self.class_hint_source = "%s（已手动选班）" % self._sign_day_prefix()
        self.sign_in_status = self.init_sign_in_status()
        self._rebuild_sign_status_for_temp()
        combo = getattr(self, "class_combobox", None)
        if combo is not None:
            try:
                combo.set(self.current_class)
            except tk.TclError:
                pass
        self.render_sign_date_line()
        self._refresh_roll_call_view()
        logging.info("点名日期切换：%s → %s", old, day)
        return True

    def shift_sign_date(self, days):
        """工具条的 ‹ ›：往前/往后一天。"""
        self._sync_sign_date_from_entry()
        try:
            base = datetime.strptime(self.sign_date_text(), "%Y-%m-%d").date()
        except ValueError:
            base = date.today()
        return self.set_sign_date((base + timedelta(days=days)).strftime("%Y-%m-%d"))

    def open_sign_date_picker(self):
        self._sync_sign_date_from_entry()
        return open_date_picker(self.master, self.sign_date_text(),
                                self.set_sign_date, today=self._today_text())

    def _sign_date_hint_text(self):
        # 默认不重复「今天」按钮上的文字，只在补点名时给出醒目提示
        return "● 补点名中" if getattr(self, "sign_date_manual", False) else ""

    def render_sign_date_line(self):
        """日期输入框与「补点名」提示跟着当前日期走；控件不在就静默跳过。"""
        var = getattr(self, "sign_date_var", None)
        if var is not None:
            try:
                var.set(self.sign_date_text())
            except tk.TclError:
                pass
        label = getattr(self, "sign_date_hint_label", None)
        if label is None:
            return
        try:
            if not label.winfo_exists():
                return
            manual = getattr(self, "sign_date_manual", False)
            label.config(text=self._sign_date_hint_text(),
                         fg=COLORS["warn"] if manual else COLORS["muted"])
        except tk.TclError:
            pass

    def today_sign_of(self, class_id=None):
        """某班今天的签到记录字典（姓名 → 记录），没有则空字典。"""
        class_id = class_id if class_id is not None else self.current_class
        return getattr(self, "today_sign", {}).get(class_id, {})

    def _apply_today_hours(self, class_id):
        """把数据库里今天的课时回填到表格；内存里改过还没保存的优先。"""
        db = self.today_sign_of(class_id)
        if not db:
            return
        if not hasattr(self, "hour_overrides"):
            self.hour_overrides = {}
        bucket = self.hour_overrides.setdefault(class_id, {})
        names = self.student_data.get(class_id, [])
        ids = self.student_ids.get(class_id, [])
        for name, sid in zip(names, ids):
            rec = db.get(name)
            if not rec or sid in bucket:
                continue
            hour = rec.get("hour")
            if hour in (None, ""):
                continue
            try:
                num = float(hour)
                hour = int(num) if num == int(num) else num
            except (TypeError, ValueError):
                pass
            bucket[sid] = hour

    def init_sign_in_status(self):
        """按 stu_id 建当前班的签到状态：以数据库为准，但保住没保存的手动勾选。

        老版是「内存里存过就一律用内存」，结果历史页删掉记录后，点名页还是
        显示已签到，连点「刷新数据」都盖不掉（刷新前又把界面状态存回了内存）。
        现在拿「上次从数据库读到的状态」当基线：
        - 界面值与基线一致 → 这条勾选是数据库带出来的，跟着数据库变；
        - 界面值与基线不同 → 老师手动点过还没保存，保留，别把人家的点名冲掉。
        """
        cls = self.current_class
        saved = getattr(self, "sign_in_status_by_class", {}).get(cls, {}) or {}
        db = self.today_sign_of(cls) or {}
        baselines = getattr(self, "sign_in_baseline_by_class", None)
        if baselines is None:
            baselines = self.sign_in_baseline_by_class = {}
        prior = baselines.get(cls, {}) or {}
        names = self.student_data.get(cls, [])
        ids = self.student_ids.get(cls, [])
        status = {}
        baseline = {}
        for name, sid in zip(names, ids):
            rec = db.get(name)
            db_signed = bool(rec and rec["signed"])
            user_signed = saved.get(sid)
            if (sid in prior and user_signed is not None
                    and bool(user_signed) != bool(prior[sid])):
                status[sid] = bool(user_signed)     # 手动改过，还没保存
            else:
                status[sid] = db_signed             # 一律以数据库为准
            baseline[sid] = db_signed
        # ids 比 names 多时（理论上不会）剩下的按未签到处理
        for sid in ids[len(names):]:
            status[sid] = bool(saved.get(sid, False))
        baselines[cls] = baseline
        self._apply_today_hours(cls)
        return status

    def resync_sign_status(self, class_ids=None):
        """签到记录被别处改动（历史页改/删、别的窗口改）后，按库重算界面状态。

        class_ids 里的班级会连「未保存勾选」的缓存一起丢掉：记录都被删了，
        再留着旧勾选只会让人以为没删干净。
        """
        self.load_today_sign(force=True)
        for cid in (class_ids or ()):
            self.sign_in_status_by_class.pop(cid, None)
            self.hour_overrides.pop(cid, None)
            self.sign_in_baseline_by_class.pop(cid, None)
        self.sign_in_status = self.init_sign_in_status()
        self._rebuild_sign_status_for_temp()
        self._refresh_roll_call_view()

    def _refresh_roll_call_view(self):
        """当前正停在点名页时，把表格/统计/提示重画一遍；别的页面不用管。"""
        if getattr(self, "current_page", "") != "roll_call":
            return
        tree = getattr(self, "roll_call_tree", None)
        if tree is None:
            return
        try:
            if not tree.winfo_exists():
                return
            self.update_roll_call_table()
            self.update_total_student_count()
            self.update_sign_in_log()
            self.render_class_hint()
            self.render_sign_date_line()
            self.render_today_sign_line()
        except tk.TclError:
            pass

    # ---------------------- 临时上课学生 ----------------------
    @staticmethod
    def _temp_iid(index):
        """临时学生的 iid，独立命名空间避免与正式 stu_id 冲突。"""
        return "temp_%d" % index

    def temp_names(self, class_id=None):
        """某个班的临时学生名单（姓名列表）。"""
        class_id = class_id if class_id is not None else self.current_class
        return getattr(self, "temp_students", {}).get(class_id, [])

    def add_temp_students(self, names):
        """往当前班添加临时学生（去重、去空），返回实际新增的姓名列表。"""
        cls = self.current_class
        bucket = self.temp_students.setdefault(cls, [])
        existing = set(bucket) | set(self.student_data.get(cls, []))
        added = []
        for raw in names:
            name = str(raw).strip()
            if not name:
                continue
            if name in existing:
                continue
            bucket.append(name)
            existing.add(name)
            added.append(name)
        if added:
            self._rebuild_sign_status_for_temp()
            self.update_roll_call_table()
            self.update_total_student_count()
            self.update_sign_in_log()
        return added

    def remove_temp_student(self, name):
        """从当前班移除一个临时学生；若是临时学生返回 True，正式学生返回 False。"""
        cls = self.current_class
        bucket = self.temp_students.get(cls, [])
        if name not in bucket:
            return False
        bucket.remove(name)
        # 清掉它的签到状态（iid 会随重建变化，直接按名字清）
        self._rebuild_sign_status_for_temp()
        self.update_roll_call_table()
        self.update_total_student_count()
        self.update_sign_in_log()
        return True

    def add_temp_student_dialog(self):
        """弹出「添加临时学生」小窗：一次可填多个名字，也能看到/删除已加的。"""
        from ui_widgets import center_on, form_label

        win = tk.Toplevel(self.master)
        win.title("添加临时学生")
        win.configure(bg=COLORS["bg"])
        win.resizable(False, False)
        win.transient(self.master)

        root = tk.Frame(win, bg=COLORS["bg"])
        root.pack(fill=tk.BOTH, expand=True, padx=22, pady=(18, 16))

        form_label(root, "添加临时学生", 15, True).pack(anchor="w")
        form_label(
            root,
            "临时学生仅本次打开、当前班有效，可正常点名并保存到记录；\n"
            "不会进入正式名单。多个名字用换行、逗号或空格隔开。",
            9, color=COLORS["muted"]).pack(anchor="w", pady=(4, 10))

        # 输入框
        self._temp_entry_var = tk.StringVar()
        entry = tk.Text(root, width=36, height=5, bg=COLORS["card"],
                        fg=COLORS["text"], font=font(11), relief="flat",
                        highlightthickness=1, highlightbackground=COLORS["border"],
                        highlightcolor=COLORS["primary"], insertbackground=COLORS["text"],
                        wrap="word")
        entry.pack(fill=tk.X)
        entry.focus_set()

        # 已有临时学生列表
        temp_names = self.temp_names(self.current_class)
        if temp_names:
            form_label(root, "当前班的临时学生（双击移除）", 10, True).pack(
                anchor="w", pady=(14, 6))
            list_frame = tk.Frame(root, bg=COLORS["card"],
                                  highlightbackground=COLORS["border"],
                                  highlightthickness=1)
            list_frame.pack(fill=tk.BOTH)
            tree = ttk.Treeview(list_frame, columns=("姓名",), show="headings",
                                style="Data.Treeview", height=min(len(temp_names), 6))
            tree.heading("姓名", text="姓名", anchor="w")
            tree.column("姓名", anchor="w", width=280)
            tree.pack(fill=tk.BOTH, expand=True, padx=6, pady=6)
            for name in temp_names:
                tree.insert("", tk.END, values=(name,))

            def _on_remove(_event):
                sel = tree.selection()
                if not sel:
                    return
                name = tree.item(sel[0])["values"][0]
                self.remove_temp_student(name)
                tree.delete(sel[0])
                if not tree.get_children():
                    self._temp_list_empty = True

            tree.bind("<Double-1>", _on_remove)

        # 底部按钮
        foot = tk.Frame(root, bg=COLORS["bg"])
        foot.pack(fill=tk.X, pady=(14, 0))

        def _on_confirm():
            text = entry.get("1.0", "end").strip()
            if not text:
                win.destroy()
                return
            names = [n for n in re.split(r"[\n,，、\s]+", text) if n.strip()]
            added = self.add_temp_students(names)
            win.destroy()
            if added:
                messagebox.showinfo(
                    "已添加", "已添加 %d 名临时学生：\n%s" % (len(added), "、".join(added)))
            else:
                messagebox.showinfo("提示", "没有新增（名字为空或已存在）")

        RoundedButton(foot, text="添加", command=_on_confirm, width=92, height=36,
                      radius=10, parent_bg=COLORS["bg"],
                      font_obj=font(10)).pack(side=tk.RIGHT)
        RoundedButton(foot, text="取消", command=win.destroy, width=92, height=36,
                      bg=COLORS["card"], fg="#334155", hover_bg="#F8FAFC",
                      border=COLORS["border"], radius=10, parent_bg=COLORS["bg"],
                      font_obj=font(10)).pack(side=tk.RIGHT, padx=(0, 10))

        win.bind("<Escape>", lambda _e: win.destroy())
        center_on(win, self.master)
        try:
            win.grab_set()
        except tk.TclError:
            pass
        return win

    def _rebuild_sign_status_for_temp(self):
        """把临时学生的签到状态并入当前 sign_in_status（临时学生 iid 与正式学生并存）。

        临时学生状态单独存在 temp_sign_in_status[class_id][name]，
        这里统一到 sign_in_status 里用 temp_N 作键，供表格与保存共用。
        """
        cls = self.current_class
        saved = self.temp_sign_in_status.setdefault(cls, {})
        # 先清掉旧临时键，避免重复累加
        status = {k: v for k, v in self.sign_in_status.items()
                  if not str(k).startswith("temp_")}
        names = self.temp_names(cls)
        for idx, name in enumerate(names):
            status[self._temp_iid(idx)] = bool(saved.get(name, False))
        self.sign_in_status = status

    def _sync_temp_status_from_sign(self):
        """把 sign_in_status 里临时学生的签到状态回写到 temp_sign_in_status。"""
        cls = self.current_class
        saved = self.temp_sign_in_status.setdefault(cls, {})
        names = self.temp_names(cls)
        for idx, name in enumerate(names):
            tiid = self._temp_iid(idx)
            if tiid in self.sign_in_status:
                saved[name] = bool(self.sign_in_status[tiid])

    def _today_sign_text(self):
        """工具条下方的小字：当前点名日期从数据库读到的签到情况。"""
        manual = getattr(self, "sign_date_manual", False)
        scope = self.sign_date_text() if manual else "今日"
        if getattr(self, "today_sign_error", ""):
            return "%s签到记录读取失败：%s" % (scope, self.today_sign_error[:60])
        if not getattr(self, "today_sign_loaded", False):
            return ""
        db = self.today_sign_of(self.current_class)
        if not db:
            return "%s尚无签到记录（数据库），点「保存」后才写入" % scope
        signed = sum(1 for r in db.values() if r["signed"])
        total = len(db)
        if manual:
            parts = ["数据库在 %s 已有 %d 条记录：已签到 %d、未签到 %d" % (
                scope, total, signed, total - signed),
                "补点名：保存会写入这一天"]
        else:
            parts = ["数据库今日已有 %d 条记录：已签到 %d、未签到 %d" % (
                total, signed, total - signed)]
        if getattr(self, "today_sign_last", ""):
            parts.append("最后录入 %s" % self.today_sign_last)
        dups = sum(r.get("dups", 0) for r in db.values())
        if dups:
            parts.append("含 %d 条重复，保存时自动合并" % dups)
        return " · ".join(parts)

    def render_today_sign_line(self):
        """刷新工具条里的「今日签到」说明；页面不在了就静默跳过。"""
        label = getattr(self, "today_sign_label", None)
        if label is None:
            return
        try:
            label.config(text=self._today_sign_text())
        except tk.TclError:
            pass

    # ---------------------- 外观配置 ----------------------
    def ui_config_path(self):
        return os.path.join(data_dir(), "ui_config.json")

    def load_ui_config(self):
        """读取 ui_config.json；缺失或损坏时回落到默认外观。"""
        cfg = dict(BG_DEFAULT)
        path = self.ui_config_path()
        if os.path.isfile(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    for key in ("type", "preset", "image", "overlay",
                                "points_split", "shop_split"):
                        if key in data:
                            cfg[key] = data[key]
            except Exception:
                logging.warning("ui_config.json 读取失败，使用默认外观", exc_info=True)
        return cfg

    def save_ui_config(self, cfg):
        try:
            with open(self.ui_config_path(), "w", encoding="utf-8") as f:
                json.dump(cfg, f, ensure_ascii=False, indent=2)
        except Exception:
            logging.exception("保存外观配置失败")
            messagebox.showerror("保存失败", f"外观配置无法写入：\n{self.ui_config_path()}")

    def apply_background(self, cfg, persist=False):
        """应用外观：更新画布、按需落盘、重绘当前页（标题颜色可能变化）。"""
        self.ui_config = dict(cfg)
        self.bg.apply_config(cfg)
        if persist:
            self.save_ui_config(self.ui_config)
        self.refresh_current_page()

    def open_appearance(self):
        from ui_settings import open_appearance_dialog
        return open_appearance_dialog(self)

    # ---------------------- 管理员设置 ----------------------
    def open_admin(self):
        """管理员设置：班级与学生增删改（需口令）。"""
        from ui_admin import open_admin_dialog
        return open_admin_dialog(self)

    def open_backup(self):
        """数据备份与恢复。"""
        from ui_backup import open_backup_dialog
        return open_backup_dialog(self)

    def reload_after_admin(self):
        """管理员改完班级后静默重载：数据、下拉框、当前页一起更新。"""
        try:
            self._capture_hours()
            self.student_data = self.load_student_data()
            self.course_data = self.init_course_json()
            if self.current_class not in self.student_data:
                self.current_class = next(iter(self.student_data), "")

            # 被删掉的班级，其签到状态与临时课时一并清掉，避免越积越多
            valid = set(self.student_data)
            for bucket in (self.sign_in_status_by_class, self.hour_overrides):
                for key in list(bucket.keys()):
                    if key not in valid:
                        bucket.pop(key, None)

            self.load_today_sign(force=True)
            self.sign_in_status = self.init_sign_in_status()
            self._rebuild_sign_status_for_temp()   # 临时学生并回当前班
            self.db_error = ""
            self.update_db_status()
            self.refresh_current_page()
            logging.info("管理员修改后已重载班级数据，当前班级＝%s", self.current_class)
            return True
        except Exception as e:
            self.db_error = str(e)
            self.update_db_status()
            logging.exception("管理员修改后重载数据失败")
            messagebox.showerror("重载失败", f"无法重新读取班级数据：{e}\n详情见 {LOG_PATH}")
            return False

    def _capture_hours(self):
        """把表格里已改但未保存的课时记到内存，重建页面/切班时不丢。"""
        tree = getattr(self, "roll_call_tree", None)
        if tree is None:
            return
        try:
            if not tree.winfo_exists():
                return
            if not hasattr(self, "hour_overrides"):
                self.hour_overrides = {}
            bucket = self.hour_overrides.setdefault(self.current_class, {})
            for item in tree.get_children():
                values = tree.item(item)["values"]
                if len(values) >= 2:
                    bucket[str(item)] = values[1]
        except tk.TclError:
            pass

    def refresh_current_page(self):
        self._capture_hours()
        page = self.current_page or "roll_call"
        if page == "course":
            self.show_course_content()
        elif page in ("summer", "winter"):
            self.show_season(page)
        elif page == "points":
            self.show_points()
        elif page == "shop":
            self.show_shop()
        else:
            self.show_roll_call()

    # ---------------------- 刷新数据功能 ----------------------
    def refresh_student_data(self):
        try:
            # 刷新前先收好当前界面状态：老师手动点过、还没保存的勾选不能丢。
            # 哪些是「数据库带出来的」由 init_sign_in_status 按基线判断——
            # 库里已被删掉的记录不会被这份缓存复活。
            if self.current_class:
                self.sign_in_status_by_class[self.current_class] = dict(self.sign_in_status)
            self.student_data = self.load_student_data()
            if self.current_class not in self.student_data:
                self.current_class = next(iter(self.student_data), "")
            # 重新读一遍今天的签到记录：别的窗口/别的机器录过的也能看到
            self.load_today_sign(force=True)
            # 没手动选过班时，刷新后按新的排课表重新定位（可能刚好到点了）
            if not getattr(self, "class_choice_manual", False):
                self.auto_select_class()
            if hasattr(self, "class_combobox"):
                self.class_combobox['values'] = list(self.student_data.keys())
                self.class_combobox.set(self.current_class)
            self.sign_in_status = self.init_sign_in_status()
            self._rebuild_sign_status_for_temp()   # 临时学生并回当前班
            self.db_error = ""
            self.update_db_status()
            # 只在点名页更新表格；课程页的 tree 已销毁，交给页面重建
            if getattr(self, "current_page", "roll_call") == "roll_call":
                if hasattr(self, "roll_call_tree"):
                    try:
                        if self.roll_call_tree.winfo_exists():
                            self.update_roll_call_table()
                    except tk.TclError:
                        pass
                self.update_total_student_count()
                self.update_sign_in_log()
                self.render_class_hint()
                self.render_today_sign_line()
            else:
                if getattr(self, "current_page", "") == "course":
                    self.show_course_content()
            messagebox.showinfo("刷新成功", "已重新加载班级与学生数据")
        except Exception as e:
            self.db_error = str(e)
            logging.exception("刷新数据失败")
            self.update_db_status()
            messagebox.showerror("刷新失败", f"无法从数据库读取数据：{e}\n详情见 {LOG_PATH}")

    # ---------------------- 界面创建 ----------------------
    def create_widgets(self):
        # ---------- 左侧导航 ----------
        self.sidebar = tk.Frame(self.master, bg=COLORS['sidebar'], width=232)
        self.sidebar.pack(side=tk.LEFT, fill=tk.Y)
        self.sidebar.pack_propagate(False)

        brand = tk.Frame(self.sidebar, bg=COLORS['sidebar'])
        brand.pack(fill=tk.X, padx=24, pady=(30, 20))
        tk.Label(brand, text="全童全星", bg=COLORS['sidebar'], fg="#FFFFFF",
                 font=font(19, True)).pack(anchor="w")
        tk.Label(brand, text="上课点名系统", bg=COLORS['sidebar'],
                 fg=COLORS['sidebar_muted'], font=font(10)).pack(anchor="w", pady=(4, 0))

        tk.Frame(self.sidebar, bg=COLORS['sidebar_line'], height=1).pack(
            fill=tk.X, padx=24, pady=(0, 18))

        tk.Label(self.sidebar, text="功能菜单", bg=COLORS['sidebar'],
                 fg=COLORS['sidebar_muted'], font=font(9)).pack(anchor="w", padx=28, pady=(0, 8))

        nav_items = (
            ("roll_call", "◉", "课堂点名", self.show_roll_call),
            ("history", "◷", "点名记录", self.show_history),
            ("points", "★", "积分管理", self.show_points),
            ("shop", "◆", "积分商城", self.show_shop),
            ("course", "▤", "课程内容", self.show_course_content),
            ("summer", "☀", "暑假课程", lambda: self.show_season("summer")),
            ("winter", "❄", "寒假课程", lambda: self.show_season("winter")),
        )
        for key, icon, text, cmd in nav_items:
            btn = RoundedButton(
                self.sidebar, text=text, icon=icon, command=cmd,
                width=184, height=46, bg=COLORS['sidebar'], fg=COLORS['sidebar_text'],
                hover_bg=COLORS['sidebar_hover'], radius=10, anchor="w", padding=20,
                parent_bg=COLORS['sidebar'], font_obj=font(12),
            )
            btn.pack(fill=tk.X, padx=24, pady=3)
            self.nav_buttons[key] = btn

        # ---------- 侧边栏底部：外观设置、日期与数据库状态 ----------
        footer = tk.Frame(self.sidebar, bg=COLORS['sidebar'])
        footer.pack(side=tk.BOTTOM, fill=tk.X, padx=24, pady=22)

        self.btn_admin = RoundedButton(
            footer, text="管理员设置", icon="⚙", command=self.open_admin,
            width=184, height=38, bg=COLORS['sidebar'], fg=COLORS['sidebar_text'],
            hover_bg=COLORS['sidebar_hover'], radius=10, anchor="w", padding=20,
            parent_bg=COLORS['sidebar'], font_obj=font(10),
        )
        self.btn_admin.pack(fill=tk.X, pady=(0, 8))

        self.btn_backup = RoundedButton(
            footer, text="备份与恢复", icon="▣", command=self.open_backup,
            width=184, height=38, bg=COLORS['sidebar'], fg=COLORS['sidebar_text'],
            hover_bg=COLORS['sidebar_hover'], radius=10, anchor="w", padding=20,
            parent_bg=COLORS['sidebar'], font_obj=font(10),
        )
        self.btn_backup.pack(fill=tk.X, pady=(0, 8))

        self.btn_appearance = RoundedButton(
            footer, text="外观设置", icon="◐", command=self.open_appearance,
            width=184, height=38, bg=COLORS['sidebar'], fg=COLORS['sidebar_text'],
            hover_bg=COLORS['sidebar_hover'], radius=10, anchor="w", padding=20,
            parent_bg=COLORS['sidebar'], font_obj=font(10),
        )
        self.btn_appearance.pack(fill=tk.X, pady=(0, 14))

        tk.Frame(footer, bg=COLORS['sidebar_line'], height=1).pack(fill=tk.X, pady=(0, 12))
        today = datetime.now()
        weekday = "周" + "一二三四五六日"[today.weekday()]
        tk.Label(footer, text=f"{today.strftime('%Y-%m-%d')}  {weekday}",
                 bg=COLORS['sidebar'], fg=COLORS['sidebar_muted'],
                 font=font(9)).pack(anchor="w")
        self.db_status_label = tk.Label(
            footer, text="", bg=COLORS['sidebar'],
            fg=COLORS['sidebar_muted'], font=font(9))
        self.db_status_label.pack(anchor="w", pady=(6, 0))
        self.update_db_status()

        # ---------- 右侧内容区：画布本身承载背景，页面块浮在上面 ----------
        self.bg = BackgroundCanvas(self.master, config=self.ui_config)
        self.bg.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        # 兼容旧引用：页面根容器即背景画布
        self.right_frame = self.bg

    # ---------------------- 通用 UI 小部件 ----------------------
    def _new_page(self):
        """开一页新画布，返回布局器；上一页的控件会被销毁。"""
        return self.bg.new_page(padx=30, pady=24)

    def _card(self):
        """白色卡片容器，尺寸交给画布 window item 控制。"""
        return tk.Frame(self.bg, bg=COLORS['card'],
                        highlightbackground=COLORS['border'], highlightthickness=1)

    def _stat_card(self, title, accent):
        card = self._card()
        tk.Frame(card, bg=accent, width=4).pack(side=tk.LEFT, fill=tk.Y)
        body = tk.Frame(card, bg=COLORS['card'])
        body.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=18, pady=13)
        tk.Label(body, text=title, bg=COLORS['card'], fg=COLORS['muted'],
                 font=font(10)).pack(anchor="w")
        value = tk.Label(body, text="0", bg=COLORS['card'], fg=accent,
                         font=font(22, True))
        value.pack(anchor="w", pady=(2, 0))
        return card, value

    def _banner_widget(self, text, bg, fg, border):
        banner = tk.Frame(self.bg, bg=bg, highlightbackground=border,
                          highlightthickness=1)
        tk.Label(banner, text=text, bg=bg, fg=fg, font=font(10),
                 justify="left", wraplength=860).pack(anchor="w", padx=14, pady=9)
        return banner

    # ---------------------- 通用 UI 小部件（旧签名保留） ----------------------
    def set_active_nav(self, key):
        self.current_page = key
        for k, btn in self.nav_buttons.items():
            btn.set_selected(k == key)

    def update_db_status(self):
        if not hasattr(self, "db_status_label"):
            return
        ok = not self.db_error
        if ok:
            try:
                from service.dp import is_sqlite
                text = "● 本地数据库就绪" if is_sqlite() else "● 数据库已连接"
            except Exception:
                text = "● 数据库已连接"
        else:
            text = "● 数据库未连接"
        self.db_status_label.config(
            text=text,
            fg=COLORS['success'] if ok else "#F87171",
        )

    # ---------------------- 按星期与时段自动选班 ----------------------
    def _find_class(self, class_id):
        for cls in self.class_all:
            if getattr(cls, "class_id", None) == class_id:
                return cls
        return None

    @staticmethod
    def _format_span(span, fallback=""):
        """(start, end) → '08:20-10:20'；解析不出来时退回原始字符串。"""
        if not span:
            return str(fallback or "")
        return "%02d:%02d-%02d:%02d" % (
            span[0].hour, span[0].minute, span[1].hour, span[1].minute)

    def auto_select_class(self):
        """按今天周几 + 当前时段定位班级；返回说明前缀（自动选中 / 没排课…）。

        用户手动选过班后不再调用，避免把他选好的班抢走。
        """
        now = self._sign_moment()
        if not hasattr(self, "class_hint_source"):
            self.class_hint_source = ""
        candidates = [c for c in self.class_all if c.class_id in self.student_data]
        if not candidates:
            candidates = list(self.class_all)
        picked, reason = pick_class_for_moment(candidates, now)
        prefix = self._sign_day_prefix()

        if picked is None:
            self.class_hint_source = "%s没有排课，保留上次选择" % prefix
            return self.class_hint_source

        if picked.class_id != self.current_class:
            # 先把上一个班的签到状态收好，切回来不丢
            if self.current_class:
                self.sign_in_status_by_class[self.current_class] = dict(self.sign_in_status)
            self.current_class = picked.class_id
            self.sign_in_status = self.init_sign_in_status()

        if reason == "ongoing":
            self.class_hint_source = "%s，已自动选中当前时段" % prefix
        elif reason == "upcoming":
            self.class_hint_source = "%s，已自动选中接下来的班" % prefix
        elif reason == "finished":
            self.class_hint_source = "%s的课已上完，选中最后一节" % prefix
        else:
            self.class_hint_source = "%s有班，但上课时间填得不规范，已选中第一个" % prefix
        return self.class_hint_source

    def _class_hint_text(self):
        """工具条下面那行小字：说明为什么是现在这个班 + 现在处于什么时段。"""
        cls = self._find_class(self.current_class)
        if cls is None:
            return ""
        now = self._sign_moment()
        today = weekday_text(now)
        day = getattr(cls, "class_day", "") or "未设星期"
        span = parse_time_range(getattr(cls, "class_time", ""))
        when = self._format_span(span, getattr(cls, "class_time", ""))
        # 就是今天的班时省掉星期，避免「今天周日 · 周日 14:10」重复一遍
        slot = when if day == today else "%s %s" % (day, when)
        source = getattr(self, "class_hint_source", "") or "当前班级"
        parts = ["%s · %s" % (source, slot)]
        course = getattr(cls, "class_type", "")
        if course:
            parts.append(course)

        manual_date = getattr(self, "sign_date_manual", False)
        if span and day == today:
            cur = now.time()
            if span[0] <= cur <= span[1]:
                parts.append("正在上课")
            elif cur < span[0]:
                minutes = (span[0].hour * 60 + span[0].minute) - (cur.hour * 60 + cur.minute)
                parts.append("%d 分钟后开课" % minutes if minutes < 120
                             else ("该日稍后上课" if manual_date else "今天稍后上课"))
            else:
                parts.append("该日已下课" if manual_date else "今天已下课")
        elif day != today:
            if manual_date:
                parts.append("不是该日（%s）" % self._sign_day_prefix())
            else:
                parts.append("不是今天（今天%s）" % today)
        return " · ".join(parts)

    def render_class_hint(self):
        """把说明写到标签上；标签还没建或已销毁时静默跳过。"""
        label = getattr(self, "class_hint_label", None)
        if label is None:
            return
        try:
            if not label.winfo_exists():
                return
            label.config(text=self._class_hint_text())
        except tk.TclError:
            pass

    def relocate_current_slot(self):
        """手动选过班后，想回到「按今天排课自动定位」的状态。"""
        self.class_choice_manual = False
        self.show_roll_call()

    # ---------------------- 班级切换功能 ----------------------
    def switch_class(self, event):
        # 切换前保存当前班的签到状态与已改课时，切回来时不丢
        if self.current_class:
            self.sign_in_status_by_class[self.current_class] = dict(self.sign_in_status)
            self._sync_temp_status_from_sign()
            self._capture_hours()
        self.current_class = self.class_combobox.get()
        self.load_today_sign()          # 换班后要读这个班今天在数据库里的状态
        self.sign_in_status = self.init_sign_in_status()
        self._rebuild_sign_status_for_temp()   # 把目标班的临时学生状态并回来
        # 用户自己点过下拉后，就不再被自动选班覆盖
        self.class_choice_manual = True
        self.class_hint_source = "已手动选择"
        self.update_roll_call_table()
        self.update_sign_in_log()
        self.update_total_student_count()
        self.render_class_hint()
        self.render_today_sign_line()

    # ---------------------- 课程内容功能 ----------------------
    def toggle_highlight(self, event):
        item = event.widget.identify_row(event.y)
        if not item:
            return
        if self.highlighted_item == item:
            event.widget.item(item, tags=())
            self.highlighted_item = None
        else:
            if self.highlighted_item:
                event.widget.item(self.highlighted_item, tags=())
            event.widget.item(item, tags=('highlight',))
            event.widget.tag_configure('highlight', background=COLORS['primary_soft'],
                                       foreground=COLORS['primary'])
            self.highlighted_item = item

    def show_course_content(self):
        self.set_active_nav("course")
        page = self._new_page()

        days = len(self.course_data)
        lessons = sum(len(v) for v in self.course_data.values())

        page.header("课程内容", "按星期展示排课，单击行可高亮标记",
                    links=[("刷新数据", self.refresh_student_data)])

        c1, self.course_day_label = self._stat_card("上课天数", COLORS['primary'])
        c2, self.course_count_label = self._stat_card("课程节次", COLORS['success'])
        page.stat_row([c1, c2])
        self.course_day_label.config(text=str(days))
        self.course_count_label.config(text=str(lessons))

        # 课程表
        table_card = self._card()
        shell = tk.Frame(table_card, bg=COLORS['card'])
        shell.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)

        course_tree = ttk.Treeview(
            shell,
            columns=('星期', '上课时间', '课程内容'),
            show='headings',
            style='Data.Treeview'
        )
        course_tree.bind("<Button-1>", self.toggle_highlight)

        course_tree.heading('星期', text='星期', anchor='w')
        course_tree.heading('上课时间', text='上课时间', anchor='w')
        course_tree.heading('课程内容', text='课程内容', anchor='w')
        course_tree.column('星期', anchor='w', stretch=False, width=170)
        course_tree.column('上课时间', anchor='w', stretch=False, width=240)
        course_tree.column('课程内容', anchor='w', stretch=True, width=400)

        for idx, day in enumerate(self.course_data):
            for t in self.course_data[day]:
                course_tree.insert('', tk.END, values=(day, t['time'], t['content']),
                                   tags=('even',) if idx % 2 else ('odd',))
        course_tree.tag_configure('odd', background=COLORS['card'], foreground=COLORS['text'])
        course_tree.tag_configure('even', background=COLORS['row_alt'], foreground=COLORS['text'])

        scrollbar = ttk.Scrollbar(shell, orient="vertical", command=course_tree.yview,
                                  style="Slim.Vertical.TScrollbar")
        course_tree.configure(yscrollcommand=scrollbar.set)

        course_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y, padx=(4, 0))

        if not self.course_data:
            tk.Label(table_card, text="暂无排课数据，请检查 t_class 表", bg=COLORS['card'],
                     fg=COLORS['muted'], font=font(12)).place(relx=0.5, rely=0.55, anchor="center")

        page.block(table_card, gap=14, expand=True)
        page.layout()

    def show_season(self, season):
        """暑假 / 寒假课程：班级与学员自定义，课次按实际日期一条条排，按课次点名。"""
        from ui_season import SeasonPage
        try:
            self.season_page = SeasonPage(self, season)
        except Exception as e:
            self.db_error = str(e)
            self.update_db_status()
            logging.exception("打开 %s 页失败", season)
            messagebox.showerror(
                "打开失败",
                "假期课程页面加载失败：%s\n详情见 %s" % (e, LOG_PATH))

    def show_history(self):
        """点名记录：常规班 + 假期班的历史点名，可查、可汇总、可导出。"""
        from ui_history import HistoryPage
        try:
            self.history_page = HistoryPage(self)
        except Exception as e:
            self.db_error = str(e)
            self.update_db_status()
            logging.exception("打开点名记录页失败")
            messagebox.showerror(
                "打开失败",
                "点名记录页面加载失败：%s\n详情见 %s" % (e, LOG_PATH))

    def show_points(self):
        """学员积分：按班级分类、加减分、备注与流水查询。"""
        from ui_points import PointsPage
        try:
            self.points_page = PointsPage(self)
        except Exception as e:
            self.db_error = str(e)
            self.update_db_status()
            logging.exception("打开积分管理页失败")
            messagebox.showerror(
                "打开失败",
                "积分管理页面加载失败：%s\n详情见 %s" % (e, LOG_PATH))

    def show_shop(self):
        """积分商城：商品管理、积分兑换与兑换记录。"""
        from ui_shop import ShopPage
        try:
            self.shop_page = ShopPage(self)
        except Exception as e:
            self.db_error = str(e)
            self.update_db_status()
            logging.exception("打开积分商城页失败")
            messagebox.showerror(
                "打开失败",
                "积分商城页面加载失败：%s\n详情见 %s" % (e, LOG_PATH))

    # ---------------------- 导出签到记录功能 ----------------------
    def export_sign_in_record(self):
        self._sync_sign_date_from_entry()
        try:
            current_time = datetime.now().strftime("%Y%m%d_%H%M%S")
            record_time = datetime.now().strftime("%Y年%m月%d日 %H:%M:%S")
            # 去掉文件名非法字符，并写到用户数据目录（安装目录可能只读）
            safe_class = re.sub(r'[\\/:*?"<>|]', "_", str(self.current_class))
            file_path = os.path.join(data_dir(), f"签到记录_{safe_class}_{current_time}.txt")

            names = self.student_data.get(self.current_class, [])
            ids = self.student_ids.get(self.current_class, [])
            # 临时学生也一并导出（用 temp_N 键，导出时只按姓名）
            temp_names = self.temp_names(self.current_class)
            temp_ids = [self._temp_iid(i) for i in range(len(temp_names))]
            total = len(names) + len(temp_names)
            signed = sum(self.sign_in_status.values())
            unsigned = total - signed

            with open(file_path, 'w', encoding='utf-8') as f:
                f.write(f"===== 签到记录 =====\n")
                f.write(f"记录时间：{record_time}\n")
                f.write(f"点名日期：{self.sign_date_text()}\n")
                f.write(f"班级：{self.current_class}\n")
                f.write(f"总人数：{total}  已到人数：{signed}  未到人数：{unsigned}\n")
                f.write(f"\n【已签到学生】\n")
                for name, sid in list(zip(names, ids)) + list(zip(temp_names, temp_ids)):
                    if self.sign_in_status.get(sid):
                        f.write(f"- {name}\n")
                f.write(f"\n【未签到学生】\n")
                for name, sid in list(zip(names, ids)) + list(zip(temp_names, temp_ids)):
                    if not self.sign_in_status.get(sid):
                        f.write(f"- {name}\n")

            messagebox.showinfo("导出成功", f"签到记录已保存至：\n{file_path}")
        except Exception as e:
            logging.exception("导出签到记录失败")
            messagebox.showerror("导出失败", f"{e}\n详情见 {LOG_PATH}")

    def get_table_data(self):
        data = []
        for item in self.roll_call_tree.get_children():
            values = self.roll_call_tree.item(item)['values']
            data.append(values)
        return data
    # ---------------------- 保存签到记录mysql ----------------------
    def save_sign(self):
        """把当前班的点名结果写库（按当前点名日期）。

        规则：一个学生在同一个班、同一天只留一条记录（再点保存就是更新）；
        选了历史日期时写入那一天，历史页按该日期显示，不影响今天的记录。
        """
        try:
            # 老师手输日期后可能没按回车就点「保存」：先采纳输入框里的内容，
            # 保证写入的日期与界面显示的日期一致，非法日期则中止保存。
            if not self._sync_sign_date_from_entry(strict=True):
                return
            day = self.sign_date_text()
            is_today = (day == self._today_text())
            now = datetime.now()
            record_time = "%s年%s月%s日 %s" % (
                day[:4], day[5:7], day[8:10], now.strftime("%H:%M:%S"))
            create_stamp = "%s %s" % (day, now.strftime("%H:%M:%S"))
            tsMapper = TSignMapper()
            tbDatas = self.get_table_data()

            # 一次把本班这一天的记录取回来，避免逐行查库、也不会被课时差异干扰
            existing = {}
            rows = (tsMapper.select_by_class_today(self.current_class) if is_today
                    else tsMapper.select_by_class_date(self.current_class, day))
            for rec in rows:
                name = (rec.stu_name or "").strip()
                existing.setdefault(name, []).append(rec)

            inTSigns = []
            updates = 0
            merged = 0
            handled = set()      # 同班重名只处理第一行，避免一次保存插出两条
            for stu in tbDatas:
                name, hour, status = stu[0], stu[1], stu[2]
                name = str(name).strip()
                if name in handled:
                    continue
                handled.add(name)

                recs = existing.get(name) or []
                if recs:                     # 今天已经点过名 → 更新
                    keep = recs[0]
                    if keep.sign_state != status or str(keep.sign_hour) != str(hour):
                        tsMapper.update_state(keep.dm_id, status, str(hour))
                        updates += 1
                    # 老数据一天多条：保留最新一条，其余合并掉
                    for extra in recs[1:]:
                        if is_today:
                            merged += tsMapper.delete_duplicate_today(
                                self.current_class, name, keep.dm_id)
                        else:
                            merged += tsMapper.delete_duplicate_on_date(
                                self.current_class, name, keep.dm_id, day)
                else:                        # 今天第一次点名 → 插入
                    inTSigns.append(TSign(
                        stu_name=name,
                        stu_class_id=self.current_class,
                        sign_hour=str(hour),
                        sign_state=status,
                        sign_time=record_time,
                    ))
            if inTSigns:
                tsMapper.insert_batch(
                    inTSigns, create_time=None if is_today else create_stamp)

            # 保存后以数据库为准刷新「今日签到」缓存与提示
            self.sign_in_status_by_class[self.current_class] = dict(self.sign_in_status)
            # 界面状态此刻已全部写进库，旧的基线作废，下次重建以库为准
            self.sign_in_baseline_by_class.pop(self.current_class, None)
            # 把临时学生的签到状态也记住，切班再切回来不丢
            self._sync_temp_status_from_sign()
            date_changed = (getattr(self, "today_sign_date", "") != day)
            hours_before = ({k: dict(v) for k, v in self.hour_overrides.items()}
                            if date_changed else None)
            self.load_today_sign(force=True)
            if date_changed:
                # 日期刚从输入框同步过来，load 会按「换了一天」清掉内存状态；
                # 这里把刚写进库的勾选 / 课时补回去，界面不会突然回退。
                self.sign_in_status_by_class[self.current_class] = dict(self.sign_in_status)
                self._sync_temp_status_from_sign()
                if hours_before:
                    self.hour_overrides = hours_before
            # 当前日期的草稿同步成刚保存的状态：切到别的日期再切回来不会退回去
            self.sign_drafts[day] = self._snapshot_sign_state()
            self.render_today_sign_line()
            self.db_error = ""
            self.update_db_status()

            detail = "新增 %d 条，更新 %d 条" % (len(inTSigns), updates)
            if merged:
                detail += "，合并重复 %d 条" % merged
            scope = "今日" if is_today else day
            tip = "" if is_today else "\n\n到「点名记录」页按 %s 查询即可看到。" % day
            messagebox.showinfo(
                "保存成功",
                "%s 的签到记录已保存至数据库中（%s）%s" % (scope, detail, tip))
        except Exception as e:
            self.db_error = str(e)
            self.update_db_status()
            logging.exception("保存签到失败")
            messagebox.showerror("保存失败", f"写入数据库失败：{e}\n详情见 {LOG_PATH}")

    # ---------------------- 课堂点名功能 ----------------------
    # def update_roll_call_table(self):
    #     for item in self.roll_call_tree.get_children():
    #         self.roll_call_tree.delete(item)
    #     for name in self.student_data.get(self.current_class, []):
    #         status_text = "已签到" if self.sign_in_status.get(name, False) else "未签到"
    #         tags = ('signed',) if self.sign_in_status.get(name, False) else ()
    #         self.roll_call_tree.insert('', tk.END, values=(name, status_text, "点击签到"), tags=tags)
    #     self.roll_call_tree.tag_configure('signed', foreground='#007BFF', font=("微软雅黑", 16, "bold"))
    def update_roll_call_table(self):
        # 清空表格
        for item in self.roll_call_tree.get_children():
            self.roll_call_tree.delete(item)

        # 默认课时数
        default_hour = 2.5
        for c in self.class_all:
            if c.class_id == self.current_class:
                default_hour = c.class_hour


        names = self.student_data.get(self.current_class, [])
        ids = self.student_ids.get(self.current_class, [])
        saved_hours = getattr(self, "hour_overrides", {}).get(self.current_class, {})
        for idx, (sid, name) in enumerate(zip(ids, names)):
            signed = self.sign_in_status.get(sid, False)
            status_text = "已签到" if signed else "未签到"
            if signed:
                tags = ('signed',)
            else:
                tags = ('even',) if idx % 2 else ('odd',)

            # 顺序必须和 columns 完全一致；iid 用 stu_id，避免同班同名互相覆盖
            self.roll_call_tree.insert(
                '',
                tk.END,
                iid=sid,
                values=(
                    name,                                      # 第1列：学生姓名
                    saved_hours.get(sid, default_hour),        # 第2列：课时数
                    status_text,                               # 第3列：签到状态
                    "✕  撤销" if signed else "✓  签到"          # 第4列：操作
                ),
                tags=tags
            )

        # 临时学生追加在正式学生后面（浅蓝底区分），同样可签到、可改课时
        temp_names = self.temp_names(self.current_class)
        for idx, name in enumerate(temp_names):
            tiid = self._temp_iid(idx)
            signed = self.sign_in_status.get(tiid, False)
            status_text = "已签到" if signed else "未签到"
            self.roll_call_tree.insert(
                '',
                tk.END,
                iid=tiid,
                values=(
                    name,
                    saved_hours.get(tiid, default_hour),
                    status_text,
                    "✕  撤销" if signed else "✓  签到",
                ),
                tags=('temp_signed',) if signed else ('temp',)
            )

        # 行样式：已签到浅绿，未签到白/浅灰交替
        self.roll_call_tree.tag_configure('signed', background=COLORS['success_soft'],
                                          foreground='#15803D')
        self.roll_call_tree.tag_configure('odd', background=COLORS['card'],
                                          foreground=COLORS['text'])
        self.roll_call_tree.tag_configure('even', background=COLORS['row_alt'],
                                          foreground=COLORS['text'])
        # 临时学生：浅蓝底，与正式学生区分
        self.roll_call_tree.tag_configure('temp', background=COLORS['primary_soft'],
                                          foreground=COLORS['text'])
        self.roll_call_tree.tag_configure('temp_signed', background=COLORS['success_soft'],
                                          foreground='#15803D')

    def _current_total(self):
        """当前班总人数（正式学生 + 临时学生）。"""
        return (len(self.student_data.get(self.current_class, []))
                + len(self.temp_names(self.current_class)))

    def update_total_student_count(self):
        total = self._current_total()
        if hasattr(self, "total_count_label"):
            self.total_count_label.config(text=str(total))
        self._refresh_stat_cards()

    def _refresh_stat_cards(self):
        """同步「已签到 / 未签到」卡片；页面未渲染时静默跳过。"""
        total = self._current_total()
        signed = sum(1 for v in self.sign_in_status.values() if v)
        if getattr(self, "signed_count_label", None) is not None:
            try:
                self.signed_count_label.config(text=str(signed))
                self.unsigned_count_label.config(text=str(max(total - signed, 0)))
            except tk.TclError:
                pass

    # def toggle_sign_in(self, event):
    #     item = self.roll_call_tree.identify_row(event.y)
    #     column = self.roll_call_tree.identify_column(event.x)
    #     if not item or column != '#3':
    #         return
    #
    #     name = self.roll_call_tree.item(item)['values'][0]
    #     self.sign_in_status[name] = not self.sign_in_status.get(name, False)
    #     status_text = "已签到" if self.sign_in_status[name] else "未签到"
    #     if self.sign_in_status[name]:
    #         self.roll_call_tree.item(item, values=(name, status_text, "点击签到"), tags=('signed',))
    #     else:
    #         self.roll_call_tree.item(item, values=(name, status_text, "点击签到"), tags=())
    #     self.update_sign_in_log()
    def toggle_sign_in(self, event):
        item = self.roll_call_tree.identify_row(event.y)
        column = self.roll_call_tree.identify_column(event.x)

        if not item:
            return

        # ✅ 按列名判断，最稳
        col_name = self.roll_call_tree.heading(column)['text']
        if col_name != '操作':
            return

        values = self.roll_call_tree.item(item)['values']
        # 列顺序必须与 columns 一致：(学生姓名, 课时数, 签到状态, 操作)
        name = values[0]   # 学生姓名
        hour = values[1]   # 课时数

        # 切换签到状态：iid 就是 stu_id（临时学生是 temp_N），同名不会串
        self.sign_in_status[item] = not self.sign_in_status.get(item, False)
        signed = self.sign_in_status[item]
        status_text = "已签到" if signed else "未签到"
        op_text = "✕  撤销" if signed else "✓  签到"
        is_temp = str(item).startswith("temp_")
        if is_temp:
            new_tags = ('temp_signed',) if signed else ('temp',)
        else:
            new_tags = ('signed',) if signed else ()

        self.roll_call_tree.item(
            item,
            values=(name, hour, status_text, op_text),
            tags=new_tags,
        )

        self.update_sign_in_log()

    def update_sign_in_log(self):
        total = self._current_total()
        signed = sum(1 for v in self.sign_in_status.values() if v)
        if hasattr(self, "log_label"):
            try:
                self.log_label.config(text=f"签到统计：{total}/{signed}（总人数/已到人数）")
            except tk.TclError:
                pass
        self._refresh_stat_cards()

    def on_double_click_hour(self, event):
        region = self.roll_call_tree.identify_region(event.x, event.y)
        if region != "cell":
            return

        column = self.roll_call_tree.identify_column(event.x)
        row = self.roll_call_tree.identify_row(event.y)

        # 只响应「课时数」列（第2列）
        if column != "#2":
            return

        item = self.roll_call_tree.item(row)
        values = list(item["values"])
        current_hour = values[1]

        # bbox 坐标相对 treeview，因此输入框挂在 treeview 上，避免位置偏移
        x, y, width, height = self.roll_call_tree.bbox(row, column)

        entry = ttk.Entry(self.roll_call_tree, width=6, justify="center", font=font(11))
        entry.place(x=x, y=y, width=width, height=height)
        entry.insert(0, current_hour)
        entry.focus_set()
        entry.select_range(0, tk.END)

        state = {"done": False}

        def save_edit(event=None):
            if state["done"]:
                return "break"
            state["done"] = True
            try:
                new_hour = float(entry.get())
                if new_hour <= 0:
                    new_hour = 2.5
            except ValueError:
                new_hour = 2.5

            values[1] = new_hour
            self.roll_call_tree.item(row, values=values)
            # 记到内存，切换班级或重建页面后仍保留这个课时
            if not hasattr(self, "hour_overrides"):
                self.hour_overrides = {}
            self.hour_overrides.setdefault(self.current_class, {})[str(row)] = new_hour
            entry.destroy()

            # ✅ 这里可以同步数据库
            # self.update_hour_to_db(values[0], new_hour)
            return "break"

        def cancel_edit(event=None):
            state["done"] = True
            entry.destroy()
            return "break"

        entry.bind("<Return>", save_edit)
        entry.bind("<FocusOut>", save_edit)
        entry.bind("<Escape>", cancel_edit)
    def _on_tree_motion(self, event):
        """鼠标移到「操作」列时显示手型，提示可点击。"""
        try:
            region = self.roll_call_tree.identify_region(event.x, event.y)
            column = self.roll_call_tree.identify_column(event.x)
        except Exception:
            return
        if region == "cell" and column == "#4":
            self.roll_call_tree.configure(cursor="hand2")
        else:
            self.roll_call_tree.configure(cursor="")

    def show_roll_call(self):
        self.set_active_nav("roll_call")
        # 进页面强制重读一次今天的签到：历史页刚改/删的记录、别的窗口录的
        # 都要能看到，不能只靠启动时的缓存（跨天也会自动重读）。
        self.load_today_sign(force=True)
        # 打开页面的这一刻按「今天周几 + 现在几点」自动定位班级；
        # 一旦用户自己从下拉里选过班，就不再抢占他的选择。
        if not getattr(self, "class_choice_manual", False):
            self.auto_select_class()
        page = self._new_page()
        # 按库重算勾选：删掉的记录在这里变回「未签到」；
        # 老师手动点过、还没保存的由基线保护，切页回来不会白点。
        self._sync_temp_status_from_sign()
        self.sign_in_status = self.init_sign_in_status()
        self._rebuild_sign_status_for_temp()
        names = self.student_data.get(self.current_class, [])
        self.class_hint_label = None      # 上一页的标签已随画布销毁

        page.header("课堂点名",
                    "点击「操作」列完成签到；双击课时数可修改，改完点「保存」写回数据库",
                    links=[("定位当前时段", self.relocate_current_slot),
                           ("刷新数据", self.refresh_student_data)])

        # 三个统计卡片
        c1, self.total_count_label = self._stat_card("本班人数", COLORS['primary'])
        c2, self.signed_count_label = self._stat_card("已签到", COLORS['success'])
        c3, self.unsigned_count_label = self._stat_card("未签到", COLORS['warn'])
        page.stat_row([c1, c2, c3])

        # 工具条：班级切换 + 操作按钮
        toolbar = self._card()
        bar = tk.Frame(toolbar, bg=COLORS['card'])
        bar.pack(fill=tk.X, padx=16, pady=12)

        tk.Label(bar, text="班级", bg=COLORS['card'], fg=COLORS['muted'],
                 font=font(10)).pack(side=tk.LEFT, padx=(0, 10))

        self.class_combobox = ttk.Combobox(
            bar,
            values=list(self.student_data.keys()),
            style='Class.TCombobox',
            state='readonly',
            width=16
        )
        self.class_combobox.set(self.current_class)
        self.class_combobox.bind("<<ComboboxSelected>>", self.switch_class)
        self.class_combobox.pack(side=tk.LEFT)

        # 点名日期单独占一行：跟右侧一排操作按钮挤在一起会把文字压没
        # 可手输、‹ › 前后一天、日历弹窗、一键回今天（往日补点名）
        date_box = tk.Frame(toolbar, bg=COLORS['card'])
        date_box.pack(fill=tk.X, padx=16, pady=(0, 6))
        tk.Label(date_box, text="点名日期", bg=COLORS['card'], fg=COLORS['muted'],
                 font=font(10)).pack(side=tk.LEFT, padx=(0, 6))
        self.sign_date_var = tk.StringVar(value=self.sign_date_text())
        date_entry = ttk.Entry(date_box, textvariable=self.sign_date_var,
                               width=13, justify="center", font=font(10))
        date_entry.pack(side=tk.LEFT)
        date_entry.bind("<Return>",
                        lambda _e: self.set_sign_date(self.sign_date_var.get()))
        date_entry.bind("<KP_Enter>",
                        lambda _e: self.set_sign_date(self.sign_date_var.get()))
        # 手输日期后直接点别处（不按回车）也要生效，签到状态跟着那天走
        date_entry.bind("<FocusOut>", lambda _e: self._on_sign_date_entry_blur())
        small = dict(height=34, radius=8, parent_bg=COLORS['card'],
                     bg=COLORS['card'], fg="#334155", hover_bg="#F8FAFC",
                     border=COLORS['border'], font_obj=font(10), padding=4)
        RoundedButton(date_box, text="<", command=lambda: self.shift_sign_date(-1),
                      width=30, **small).pack(side=tk.LEFT, padx=(4, 0))
        RoundedButton(date_box, text=">", command=lambda: self.shift_sign_date(1),
                      width=30, **small).pack(side=tk.LEFT, padx=(4, 0))
        RoundedButton(date_box, text="日历", command=self.open_sign_date_picker,
                      width=60, **small).pack(side=tk.LEFT, padx=(6, 0))
        RoundedButton(date_box, text="今天",
                      command=lambda: self.set_sign_date(self._today_text()),
                      width=60, **small).pack(side=tk.LEFT, padx=(4, 0))
        self.sign_date_hint_label = tk.Label(
            date_box, text="", bg=COLORS['card'], fg=COLORS['muted'],
            font=font(9), anchor="w")
        self.sign_date_hint_label.pack(side=tk.LEFT, padx=(10, 0))
        tk.Label(date_box, text="切换日期会同步更新签到状态",
                 bg=COLORS['card'], fg=COLORS['subtle'],
                 font=font(9)).pack(side=tk.LEFT, padx=(16, 0))
        self.render_sign_date_line()

        # 右侧按钮（side=RIGHT 的先后即从右到左）：保存 → 导出 → 刷新 → 加临时学生
        RoundedButton(bar, text="保存", command=self.save_sign, width=88, height=38,
                      radius=10, parent_bg=COLORS['card'],
                      font_obj=font(10)).pack(side=tk.RIGHT)

        RoundedButton(bar, text="导出记录", command=self.export_sign_in_record, width=104,
                      height=38, bg=COLORS['card'], fg="#334155", hover_bg="#F8FAFC",
                      border=COLORS['border'], radius=10, parent_bg=COLORS['card'],
                      font_obj=font(10)).pack(side=tk.RIGHT, padx=(0, 8))

        RoundedButton(bar, text="刷新数据", command=self.refresh_student_data, width=104,
                      height=38, bg=COLORS['card'], fg="#334155", hover_bg="#F8FAFC",
                      border=COLORS['border'], radius=10, parent_bg=COLORS['card'],
                      font_obj=font(10)).pack(side=tk.RIGHT, padx=(0, 8))

        RoundedButton(bar, text="＋ 临时学生", command=self.add_temp_student_dialog,
                      width=116, height=38, bg=COLORS['primary_soft'], fg=COLORS['primary'],
                      hover_bg="#D6E4FF", border=COLORS['primary'], radius=10,
                      parent_bg=COLORS['card'], font_obj=font(10)).pack(side=tk.RIGHT, padx=(0, 8))

        # 工具条第二行：为什么不选别的班（今天周几、这一节几点、现在什么状态）
        hint = self._class_hint_text()
        if hint:
            self.class_hint_label = tk.Label(
                toolbar, text=hint, bg=COLORS['card'], fg=COLORS['muted'],
                font=font(10), anchor="w", justify="left")
            self.class_hint_label.pack(fill=tk.X, padx=18, pady=(0, 10))

        # 工具条第三行：当前点名日期在数据库里已录入的签到情况
        # 始终建这个标签：切日期后文字可能从空变成有内容，重建页面不划算
        self.today_sign_label = tk.Label(
            toolbar, text=self._today_sign_text(), bg=COLORS['card'],
            fg=COLORS['success'] if not self.today_sign_error else COLORS['danger'],
            font=font(9), anchor="w", justify="left")
        self.today_sign_label.pack(fill=tk.X, padx=18,
                                   pady=(0, 10) if hint else (10, 10))

        page.block(toolbar, gap=14)

        if self.db_error:
            page.block(self._banner_widget(
                f"数据库不可用：{self.db_error[:80]}    详情见 {LOG_PATH}",
                COLORS['danger_soft'], COLORS['danger'], "#FECACA"), gap=12)

        # 点名表格
        table_card = self._card()
        shell = tk.Frame(table_card, bg=COLORS['card'])
        shell.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)

        self.roll_call_tree = ttk.Treeview(
            shell,
            columns=('学生姓名', '课时数', '签到状态', '操作'),
            show='headings',
            style='Data.Treeview'
        )
        self.roll_call_tree.heading('学生姓名', text='学生姓名', anchor='w')
        self.roll_call_tree.heading('课时数', text='课时数', anchor='center')
        self.roll_call_tree.heading('签到状态', text='签到状态', anchor='center')
        self.roll_call_tree.heading('操作', text='操作', anchor='center')
        self.roll_call_tree.column('学生姓名', anchor='w', stretch=True, width=360)
        self.roll_call_tree.column('课时数', anchor='center', stretch=False, width=160)
        self.roll_call_tree.column('签到状态', anchor='center', stretch=False, width=170)
        self.roll_call_tree.column('操作', anchor='center', stretch=False, width=150)

        self.update_roll_call_table()
        self.roll_call_tree.bind("<Button-1>", self.toggle_sign_in)
        self.roll_call_tree.bind("<Double-1>", self.on_double_click_hour)
        self.roll_call_tree.bind("<Motion>", self._on_tree_motion)

        scrollbar = ttk.Scrollbar(shell, orient="vertical",
                                  command=self.roll_call_tree.yview,
                                  style="Slim.Vertical.TScrollbar")
        self.roll_call_tree.configure(yscrollcommand=scrollbar.set)

        self.roll_call_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y, padx=(4, 0))

        if not names:
            hint = ("数据库未连接，无法读取学生名单" if self.db_error
                    else "当前班级暂无学生，请检查 t_stu 表")
            tk.Label(table_card, text=hint, bg=COLORS['card'], fg=COLORS['muted'],
                     font=font(12)).place(relx=0.5, rely=0.55, anchor="center")

        page.block(table_card, gap=14, expand=True)
        page.layout()
        self.update_total_student_count()

        self.update_total_student_count()
        self.update_sign_in_log()


if __name__ == "__main__":
    enable_dpi_awareness()
    # 单实例：已有窗口在运行时，把已有窗口置前并静默退出，避免双开写同一个库。
    # acquire_single_instance 内部已经把已有窗口恢复并置前，这里直接退出即可。
    ok, _existing = acquire_single_instance()
    if not ok:
        raise SystemExit(0)
    root = tk.Tk()
    try:
        app = RollCallSystemUI(root)
    except Exception as e:
        logging.exception("界面初始化失败")
        messagebox.showerror("启动失败", f"{e}\n详情见 {LOG_PATH}")
        root.destroy()
        raise SystemExit(1)
    root.mainloop()
