from typing import Optional

class TStu:
    def __init__(
        self,
        stu_id: Optional[int] = None,
        stu_name: Optional[str] = None,
        stu_class_id: Optional[str] = None,
        stu_hour: Optional[float] = None,
        phone: Optional[str] = None,
        stu_state: Optional[str] = None,
    ):
        self.stu_id = stu_id
        self.stu_name = stu_name
        self.stu_class_id = stu_class_id
        self.stu_hour = stu_hour
        self.phone = phone
        self.stu_state = stu_state

    def __repr__(self):
        return (
            f"TStu(stu_id={self.stu_id!r}, "
            f"stu_name={self.stu_name!r}, "
            f"stu_class_id={self.stu_class_id!r}, "
            f"stu_hour={self.stu_hour!r}, "
            f"phone={self.phone!r},"
            f"stu_state={self.stu_state!r})"
        )