def calculate_next_page(current_page: object, direction: object) -> int:
    """
    Calculates the target page given the current page and a direction ('prec' or 'succ').
    Returns the target page number.
    Raises ValueError for invalid direction or non-integer page.

    `current_page` arriva dal payload di una `cl.Action`, cioe' DAL CLIENT:
    prima del 25/08 un `page` non intero non era un rifiuto ma un TypeError non
    gestito dentro il callback (`"5" - 1`), e un `page` booleano (`True - 1`)
    passava silenziosamente come pagina 0. Qui si valida il TIPO; il limite
    inferiore (pagina >= 1) resta al chiamante, che e' anche l'unico a sapere
    se c'e' qualcosa da renderizzare — `ensure_page_render` gia' rifiuta le
    pagine fuori range e `tests/ui/test_pagination.py` fissa da sempre
    `calculate_next_page(1, "prec") == 0` come risultato aritmetico legittimo.
    """
    # `bool` e' sottotipo di `int`: escluso a mano, non e' un numero di pagina.
    if isinstance(current_page, bool) or not isinstance(current_page, int):
        raise ValueError(f"Invalid page: {current_page!r}")
    if direction == "prec":
        return current_page - 1
    elif direction == "succ":
        return current_page + 1
    else:
        raise ValueError(f"Invalid direction: {direction}")
