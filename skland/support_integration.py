"""Optional public integration; this module never imports the support plugin."""


async def import_support_after_login(context, *, owner_id: str, group_umo: str,
                                     platform_id: str, group_id: str,
                                     group_name: str, member_nickname: str) -> str | None:
    if not group_id:
        return None
    metadata = context.get_registered_star("astrbot_plugin_arksupport")
    if metadata is None or not getattr(metadata, "activated", False):
        return None
    callback = getattr(getattr(metadata, "star_cls", None), "import_skland_after_login", None)
    if not callable(callback):
        return None
    return await callback(
        owner_id=owner_id, group_umo=group_umo, platform_id=platform_id,
        group_id=group_id, group_name=group_name, member_nickname=member_nickname,
    )
