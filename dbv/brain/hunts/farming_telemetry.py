"""DBVictory experience curve shared by hunt reports and statistics."""


def experience_for_level(level):
    """Cumulative XP at the start of a DBVictory level."""
    return 50 * (level**3 - 6*level**2 + 17*level - 12) // 3
