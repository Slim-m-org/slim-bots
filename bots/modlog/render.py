"""Human wording for mod log posts: titles, names and diffs; no ids, event types or routes."""

PERMISSION_LABELS = {
    0: "Administrator",
    1: "View channels",
    2: "Send messages",
    3: "Manage messages",
    4: "Manage channels",
    5: "Manage roles",
    6: "Kick members",
    7: "Ban members",
    8: "Create invites",
    9: "Add reactions",
    10: "Attach files",
    11: "Connect to voice",
    12: "Speak",
    13: "Use the canvas",
    14: "Manage the canvas",
    15: "Manage the server",
    16: "Mention everyone",
    17: "Run code",
    18: "View moderation history",
    19: "Move members",
}

# The client's role palette, in order; a role with no colour takes one derived from its id.
COLOUR_NAMES = ["Gold", "Sky", "Slate", "Violet", "Sage", "Clay", "Red", "Orange", "Pink", "Teal"]


def permission_names(bits):
    return [label for bit, label in PERMISSION_LABELS.items() if bits & (1 << bit)]


def permission_diff(before, after):
    """`+ Manage messages, - Kick members`, or None when nothing changed."""
    added = permission_names(after & ~before)
    removed = permission_names(before & ~after)
    parts = [f"+ {name}" for name in added] + [f"- {name}" for name in removed]
    return ", ".join(parts) or None


def colour_name(index):
    if isinstance(index, int) and 0 <= index < len(COLOUR_NAMES):
        return COLOUR_NAMES[index]
    return "Automatic"


def on_off(value):
    return "on" if value else "off"


def role_look_diff(before, after):
    """(label, "old -> new") for each member-visible attribute that differs; empty for a create or delete."""
    if not before or not after:
        return []
    changes = []
    if before.get("name") != after.get("name"):
        changes.append(("Name", f"{before.get('name')} -> {after.get('name')}"))
    if before.get("color") != after.get("color"):
        changes.append(("Colour", f"{colour_name(before.get('color'))} -> {colour_name(after.get('color'))}"))
    if bool(before.get("hoist")) != bool(after.get("hoist")):
        changes.append(("Listed separately", f"{on_off(before.get('hoist'))} -> {on_off(after.get('hoist'))}"))
    if bool(before.get("mentionable")) != bool(after.get("mentionable")):
        changes.append(("Anyone can mention", f"{on_off(before.get('mentionable'))} -> {on_off(after.get('mentionable'))}"))
    return changes


def person(member, fallback="Unknown member"):
    """`Nick (@nick)`."""
    if member is None:
        return fallback
    return f"{member.display_name} (@{member.username})"


def plain_text(title, fields):
    """The one-line fallback an older server shows instead of the embed."""
    if not fields:
        return title
    return title + ": " + "; ".join(f"{name.lower()} {value}" for name, value in fields)
