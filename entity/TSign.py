from datetime import datetime
from typing import Optional

class TSign:
    def __init__(
        self,
        dm_id: Optional[int] = None,
        stu_name: Optional[str] = None,
        stu_class_id: Optional[str] = None,
        sign_hour: Optional[str] = None,
        sign_state: Optional[str] = None,
        bz: Optional[str] = None,
        update_time: Optional[datetime] = None,
        create_time: Optional[datetime] = None,
        sign_time: Optional[str] = None
    ):
        self.dm_id = dm_id
        self.stu_name = stu_name
        self.stu_class_id = stu_class_id
        self.sign_hour = sign_hour
        self.sign_state = sign_state
        self.bz = bz
        self.update_time = update_time
        self.create_time = create_time
        self.sign_time = sign_time

    def __repr__(self):
        return (
            f"TSign(dm_id={self.dm_id!r}, "
            f"stu_name={self.stu_name!r}, "
            f"stu_class_id={self.stu_class_id!r}, "
            f"sign_hour={self.sign_hour!r}, "
            f"sign_state={self.sign_state!r}, "
            f"bz={self.bz!r}, "
            f"update_time={self.update_time!r}, "
            f"create_time={self.create_time!r}, "
            f"sign_time={self.sign_time!r})"
        )