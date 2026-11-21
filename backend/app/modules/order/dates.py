"""订单排期的安全日期计算；日期越界是参数错误，不能变成服务器异常。"""
from datetime import date, timedelta
from app.core.errors import AppError, ErrorCode


def shift_date(value: date, days: int) -> date:
    try:
        return value + timedelta(days=days)
    except OverflowError:
        raise AppError(ErrorCode.PARAM_ERROR,
                       "交期与提前天数计算出的日期超出有效范围，请调整交期或提前天数", 422) from None
