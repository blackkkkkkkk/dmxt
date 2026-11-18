from typing import Optional

class TClass:
    def __init__(
        self,
        class_id: str,
        class_type: Optional[str] = None,
        class_time: Optional[str] = None,
        class_name: Optional[str] = None,
        class_hour: Optional[str] = None,
        stu_state: Optional[str] = None,
        class_val: Optional[str] = None,
        class_day: Optional[str] = None,

    ):
        self.class_id = class_id
        self.class_type = class_type
        self.class_time = class_time
        self.class_name = class_name
        self.class_hour = class_hour
        self.stu_state = stu_state
        self.class_val = class_val
        self.class_day = class_day

    def __repr__(self):
        return (
            f"TClass(class_id={self.class_id!r}, "
            f"class_type={self.class_type!r}, "
            f"class_time={self.class_time!r},"
            f"class_name={self.class_name!r},"
            f"class_hour={self.class_hour!r},"
            f"stu_state={self.stu_state!r},"
            f"class_val={self.class_val!r},"
            f"class_day={self.class_day!r})"
        )