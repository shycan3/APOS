def active_titles(items: list[dict[str, object]]) -> list[str]:
    return [str(item["title"]) for item in items]
