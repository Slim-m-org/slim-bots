"""What Sonarr and Radarr share when adding: the quality profile and root folder to add into."""


async def resolve_defaults(api, name, profile_setting, folder_setting):
    """The quality profile id and root folder: the configured ones, else the first the service lists."""
    profiles = await api("GET", "/qualityprofile") or []
    folders = await api("GET", "/rootfolder") or []
    wanted = profile_setting.lower()
    profile = next((p for p in profiles if wanted and wanted in (str(p["id"]), p["name"].lower())), None) or (profiles[0] if profiles else None)
    folder = folder_setting or (folders[0]["path"] if folders else None)
    if profile is None or folder is None:
        raise RuntimeError(f"{name} has no quality profile or root folder to add into")
    return profile["id"], folder
