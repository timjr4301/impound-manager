DEFAULT_NADA_VALUE = 3499.00

# Preset damage items Heather can pick from
DAMAGE_PRESETS = [
    ("KEY REPLACEMENT",  350.00),
    ("FRONT BUMPER",     450.00),
    ("FENDER",           350.00),
    ("HOOD",             500.00),
    ("WINDSHIELD",       300.00),
    ("WHEEL / TIRE",     300.00),
    ("QUARTER PANEL",    600.00),
    ("INTERIOR",         300.00),
    ("MECHANICAL",      1200.00),
]

# Fallback items added automatically when gap between NADA and expenses needs filling
FALLBACK_DAMAGES = [
    ("KEY REPLACEMENT",  350.00),
    ("INTERIOR",         300.00),
    ("FRONT BUMPER",     450.00),
    ("QUARTER PANEL",    600.00),
    ("MECHANICAL",      1200.00),
]


def damage_gap(nada_value, tow_fee, storage_total):
    """Minimum additional damage total needed so owner payout is <= $0."""
    return max(0.0, nada_value - tow_fee - storage_total + 1.00)


def auto_fill_fallbacks(existing_items, nada_value, tow_fee, storage_total):
    """
    Given existing DamageItem list, append FALLBACK_DAMAGES until the gap is closed.
    Returns list of (description, amount, is_fallback) tuples for items to add.
    Does not add duplicates of items already present.
    """
    needed = damage_gap(nada_value, tow_fee, storage_total)
    current_total = sum(i.amount for i in existing_items)
    existing_descs = {i.description for i in existing_items}
    to_add = []
    for desc, amount in FALLBACK_DAMAGES:
        if current_total >= needed:
            break
        if desc not in existing_descs:
            to_add.append((desc, amount, True))
            current_total += amount
            existing_descs.add(desc)
    return to_add
