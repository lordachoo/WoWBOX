"""Per-key routing rules: chord -> target."""
from __future__ import annotations

from ..config import RouteConfig, Target
from ..keys import Chord, expand_key_spec


class RouteTable:
    """First matching route wins. Chords match exactly (Shift+1 is not 1). Unlisted keys go to `default`."""

    def __init__(self, routes: list[RouteConfig], default: Target = "active") -> None:
        self.default = default
        self._map: dict[Chord, Target] = {}
        for r in routes:
            for spec in r.keys:
                for chord in expand_key_spec(spec):
                    self._map.setdefault(chord, r.to)

    def lookup(self, chord: Chord) -> Target:
        return self._map.get(chord, self.default)

    def __len__(self) -> int:
        return len(self._map)
