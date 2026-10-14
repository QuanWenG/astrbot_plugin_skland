"""Optional public integration; this module never imports the support plugin."""


async def import_support_after_login(context, *, owner_id: str, group_umo: str,
                                     platform_id: str, group_id: str,
                                     group_name: str, member_nickname: str) -> str | dict | None:
    if not group_id:
        return None
    metadata = context.get_registered_star("astrbot_plugin_arksupport")
    if metadata is None or not getattr(metadata, "activated", False):
        return None
    plugin = getattr(metadata, "star_cls", None)
    callback = getattr(plugin, "import_skland_after_login_receipt", None)
    structured = callable(callback)
    if not structured:
        callback = getattr(plugin, "import_skland_after_login", None)
    if not callable(callback):
        return None
    result = await callback(
        owner_id=owner_id, group_umo=group_umo, platform_id=platform_id,
        group_id=group_id, group_name=group_name, member_nickname=member_nickname,
    )
    if result is None:
        return None
    if structured:
        if (not isinstance(result, dict) or result.get("schema_version") != 1
                or isinstance(result.get("role_count"), bool) or not isinstance(result.get("role_count"), int)
                or result["role_count"] < 0 or not isinstance(result.get("text"), str)
                or not isinstance(result.get("short_text"), str) or not isinstance(result.get("images"), list)
                or any(not isinstance(image, bytes) for image in result["images"])):
            raise ValueError("助战导入回执契约不兼容")
    elif not isinstance(result, str):
        raise ValueError("助战导入文字回执无效")
    return result
