def format_timestamp(seconds: float) -> str:
    """Format a duration used by a lazily evaluated ArkCard property."""
    seconds = max(0, int(seconds))
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours:
        return f"{hours}小时{minutes}分钟"
    if minutes:
        return f"{minutes}分钟{seconds}秒"
    return f"{seconds}秒"
