from datetime import datetime


def get_current_datetime() -> str:
    """Return current date and time as formatted string."""
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def get_current_date() -> str:
    """Return current date as formatted string."""
    return datetime.now().strftime("%Y-%m-%d")


def get_current_time() -> str:
    """Return current time as formatted string."""
    return datetime.now().strftime("%H:%M:%S")


def get_day_of_week() -> str:
    """Return the current day of the week (e.g. Monday, Tuesday)."""
    return datetime.now().strftime("%A")


if __name__ == "__main__":
    print("Date & Time:", get_current_datetime())
    print("Date:       ", get_current_date())
    print("Time:       ", get_current_time())
    print("Day:        ", get_day_of_week())
