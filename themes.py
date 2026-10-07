"""Shared UI palettes, kept separate from monitoring and animation."""
THEMES = {
    "midnight": {"bg": "#131b28", "panel": "#192333", "border": "#334158",
                 "text": "#f1f5fb", "muted": "#a0b0c6", "accent": "#83dbaf"},
    "forest": {"bg": "#111f1d", "panel": "#1b302b", "border": "#3c5a4b",
               "text": "#edf8ee", "muted": "#acbeb2", "accent": "#d2df83"},
    "plum": {"bg": "#201727", "panel": "#302238", "border": "#584366",
             "text": "#fff0fc", "muted": "#beabc9", "accent": "#e8a7d8"},
}
PET_PALETTES = {
    "mint": ["#256b6a", "#7ce5bf", "#b9ffe3", "#4db6a0", "#79d4b8", "#3c8e84", "#a2f3da", "#367f78", "#62cfac"],
    "sky": ["#345b9a", "#8acbff", "#dbf5ff", "#649ddb", "#89c3f5", "#5576bd", "#bcf0ff", "#466698", "#6ca6e0"],
    "amber": ["#985b35", "#ffca76", "#fff0c6", "#dfa55c", "#f4c17f", "#b17a40", "#ffe4a1", "#956543", "#e8b46a"],
    "rose": ["#87517c", "#eeb0d7", "#ffe4f6", "#c887b9", "#e6a8cc", "#a968a0", "#ffd0eb", "#9b638f", "#d896c3"],
}


def palette(settings):
    return THEMES.get(settings["theme"], THEMES["midnight"])
