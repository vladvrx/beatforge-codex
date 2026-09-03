"""Release-candidate promotion is off in the local studio.

Set ``BEATFORGE_AI_RELEASE_ROUTE=1`` only when maps are routed through the
independent AI review workflow. Headset flags still record evidence; they
do not stamp ``release_candidate`` while this is off.
"""

from __future__ import annotations

import os


def ai_release_route_enabled() -> bool:
    return os.environ.get("BEATFORGE_AI_RELEASE_ROUTE", "").strip().casefold() in {"1", "true", "yes"}
