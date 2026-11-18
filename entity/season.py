"""暑假 / 寒假课程的实体。

假期班和常规班是两套东西：常规班按「星期 + 固定时间」每周重复，假期班上课
日期不固定，一次课一个时间，所以单独建表，互不影响。

- 假期班级：老师自己起名，指定课程、每次课默认课时、起止日期
- 假期学员：直接挂在假期班级下，和常规班的 t_stu 没有关系（可以从常规班导进来）
- 假期课次：一条记录就是「某天某个时间段的一次课」，点名按课次走
- 假期签到：一条记录就是「某个学员在某次课的出勤」，按 (课次, 学员) 唯一
"""

from typing import Optional


class SeasonClass:
    def __init__(
        self,
        sclass_id: Optional[int] = None,
        season: Optional[str] = None,
        class_name: Optional[str] = None,
        class_type: Optional[str] = None,
        class_hour: Optional[float] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        note: Optional[str] = None,
        state: Optional[str] = None,
        create_time=None,
    ):
        self.sclass_id = sclass_id
        self.season = season
        self.class_name = class_name
        self.class_type = class_type
        self.class_hour = class_hour
        self.start_date = start_date
        self.end_date = end_date
        self.note = note
        self.state = state
        self.create_time = create_time

    def __repr__(self):
        return ("SeasonClass(sclass_id=%r, season=%r, class_name=%r, class_type=%r, "
                "class_hour=%r, start_date=%r, end_date=%r, state=%r)"
                % (self.sclass_id, self.season, self.class_name, self.class_type,
                   self.class_hour, self.start_date, self.end_date, self.state))


class SeasonStu:
    def __init__(
        self,
        sstu_id: Optional[int] = None,
        sclass_id: Optional[int] = None,
        stu_name: Optional[str] = None,
        phone: Optional[str] = None,
        note: Optional[str] = None,
        state: Optional[str] = None,
        create_time=None,
    ):
        self.sstu_id = sstu_id
        self.sclass_id = sclass_id
        self.stu_name = stu_name
        self.phone = phone
        self.note = note
        self.state = state
        self.create_time = create_time

    def __repr__(self):
        return ("SeasonStu(sstu_id=%r, sclass_id=%r, stu_name=%r, phone=%r, state=%r)"
                % (self.sstu_id, self.sclass_id, self.stu_name, self.phone, self.state))


class SeasonLesson:
    def __init__(
        self,
        lesson_id: Optional[int] = None,
        sclass_id: Optional[int] = None,
        lesson_date: Optional[str] = None,
        lesson_time: Optional[str] = None,
        topic: Optional[str] = None,
        note: Optional[str] = None,
        create_time=None,
    ):
        self.lesson_id = lesson_id
        self.sclass_id = sclass_id
        self.lesson_date = lesson_date
        self.lesson_time = lesson_time
        self.topic = topic
        self.note = note
        self.create_time = create_time

    def __repr__(self):
        return ("SeasonLesson(lesson_id=%r, sclass_id=%r, lesson_date=%r, "
                "lesson_time=%r, topic=%r)"
                % (self.lesson_id, self.sclass_id, self.lesson_date,
                   self.lesson_time, self.topic))


class SeasonSign:
    def __init__(
        self,
        sign_id: Optional[int] = None,
        lesson_id: Optional[int] = None,
        sclass_id: Optional[int] = None,
        sstu_id: Optional[int] = None,
        stu_name: Optional[str] = None,
        sign_hour: Optional[str] = None,
        sign_state: Optional[str] = None,
        sign_time: Optional[str] = None,
        bz: Optional[str] = None,
        update_time=None,
    ):
        self.sign_id = sign_id
        self.lesson_id = lesson_id
        self.sclass_id = sclass_id
        self.sstu_id = sstu_id
        self.stu_name = stu_name
        self.sign_hour = sign_hour
        self.sign_state = sign_state
        self.sign_time = sign_time
        self.bz = bz
        self.update_time = update_time

    def __repr__(self):
        return ("SeasonSign(sign_id=%r, lesson_id=%r, sstu_id=%r, stu_name=%r, "
                "sign_hour=%r, sign_state=%r)"
                % (self.sign_id, self.lesson_id, self.sstu_id, self.stu_name,
                   self.sign_hour, self.sign_state))
