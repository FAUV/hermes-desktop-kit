"""File-level diff engine (SCAFFOLD).

created / modified / deleted semantics against adjacent/current state.
Text files get readable unified diffs (stdlib difflib); binary/unsupported
and secret-shaped files get metadata-only treatment — never fake diffs,
never emit secret values. Implementation milestone.
"""
