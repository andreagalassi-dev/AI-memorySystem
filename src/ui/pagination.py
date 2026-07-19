def calculate_next_page(current_page: int, direction: str) -> int:
    """
    Calculates the target page given the current page and a direction ('prec' or 'succ').
    Returns the target page number.
    Raises ValueError for invalid direction.
    """
    if direction == "prec":
        return current_page - 1
    elif direction == "succ":
        return current_page + 1
    else:
        raise ValueError(f"Invalid direction: {direction}")
