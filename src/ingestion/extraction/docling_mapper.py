from __future__ import annotations
import re
from decimal import Decimal
from src.ingestion.extraction.schema import RigaGrezza
from src.ingestion.codes import base_cleanup
from src.ingestion.extraction.text_table import _to_decimal

# Short numeric code candidate: 3-5 digits, optionally ending with a letter.
# Must be ≥3 digits to exclude POS numbers (1, 2, 10) and quantities.
SHORT_CODE_RE = re.compile(r"^[0-9]{3,5}[A-Z]?$")

DOCLING_CODE_RE = re.compile(
    r"^(?:"
    r"(?=.*[A-Z])(?=(?:.*\d){4,})[A-Z0-9][A-Z0-9.\-]{4,}[A-Z0-9S]"
    r"|"
    r"[0-9]{6,}"
    r")$"
)

# A cell is "descriptive" if it contains a word of ≥3 alphabetic chars.
_DESC_RE = re.compile(r"[a-zA-Z]{3,}")
# A cell is "numeric-only" if it's empty, a dash, or contains only digits/dots/commas/spaces.
_NUMERIC_ONLY_RE = re.compile(r"^[\s\d.,\-]*$")


def _is_short_numeric_code(row: list[str], idx: int) -> bool:
    """Determine if a short numeric token at `row[idx]` is a product code.

    The heuristic uses column context:
    - The token must match SHORT_CODE_RE (3-5 digits, optional trailing letter).
    - At least one cell AFTER the candidate must contain descriptive text
      (a word with ≥3 alphabetic characters). This distinguishes codes
      (followed by product descriptions) from dimensions/quantities
      (followed by more numbers).

    Examples:
        ['2', '8524', 'Cappuccio/Cap', '1', '14,00']
            idx=1 → True  (followed by descriptive 'Cappuccio/Cap')
            idx=0 → False ('2' doesn't match SHORT_CODE_RE, <3 digits)

        ['32220J', '-', '', '100', '180', '63', '1022.53']
            idx=4 → False (remaining cells '63', '1022.53' are all numeric)
    """
    cell = row[idx].strip()
    cell_nospace = cell.replace(" ", "")

    # Must match the short code pattern (3-5 digits + optional letter)
    if not (SHORT_CODE_RE.match(cell) or SHORT_CODE_RE.match(cell_nospace)):
        return False

    # Check cells AFTER the candidate for descriptive content.
    # At least one must contain a word with ≥3 alphabetic chars.
    has_description_after = False
    for subsequent in row[idx + 1:]:
        s = subsequent.strip()
        if _DESC_RE.search(s):
            has_description_after = True
            break

    if not has_description_after:
        return False

    # Additional guard: descriptive text must be in a "close" cell (within 2 positions)
    # to avoid false positives from header text far away in the row.
    for subsequent in row[idx + 1: idx + 3]:
        s = subsequent.strip()
        if _DESC_RE.search(s):
            return True

    return False


class DoclingMapper:
    def map_page(self, grid: list[list[str]], doc_stem: str, page: int, stats: dict = None) -> list[RigaGrezza]:
        """Esegue il chunking orizzontale e decodifica i blocchi."""
        out = []
        seen = set()
        
        for row in grid:
            code_indices = []
            code_sources = {}  # idx -> 'docling' | 'short'
            
            for i, cell in enumerate(row):
                t = cell.strip()
                t_nospace = t.replace(" ", "")
                if not t:
                    continue
                matched_docling = False
                if DOCLING_CODE_RE.match(t) or DOCLING_CODE_RE.match(t_nospace):
                    code_indices.append(i)
                    code_sources[i] = 'docling'
                    matched_docling = True
                else:
                    tokens = t.split()
                    for j in range(1, len(tokens)):
                        prefix_t = " ".join(tokens[:j])
                        prefix_nospace = prefix_t.replace(" ", "")
                        if DOCLING_CODE_RE.match(prefix_t) or DOCLING_CODE_RE.match(prefix_nospace):
                            code_indices.append(i)
                            code_sources[i] = 'docling'
                            matched_docling = True
                            break
                            
                if not matched_docling:
                    # Try short numeric code heuristic (context-based)
                    if _is_short_numeric_code(row, i):
                        code_indices.append(i)
                        code_sources[i] = 'short'
                        if stats is not None:
                            stats.setdefault("short_code_accepted", []).append(
                                (t, doc_stem, page, str(row))
                            )
                    elif stats is not None:
                        if SHORT_CODE_RE.match(t) or SHORT_CODE_RE.match(t_nospace):
                            stats.setdefault("short_code_rejected", []).append(
                                (t, doc_stem, page, str(row))
                            )
                            
            if not code_indices:
                continue
                
            for k, start_idx in enumerate(code_indices):
                end_idx = code_indices[k+1] if k + 1 < len(code_indices) else len(row)
                block_cells = row[start_idx:end_idx]
                
                code_raw = block_cells[0].strip()
                t_nospace = code_raw.replace(" ", "")
                
                code_str = None
                source = code_sources.get(start_idx, 'docling')

                if source == 'short':
                    # Short code: use the raw cell value directly
                    if SHORT_CODE_RE.match(code_raw):
                        code_str = code_raw
                    elif SHORT_CODE_RE.match(t_nospace):
                        code_str = t_nospace
                else:
                    # Standard DOCLING_CODE_RE matching
                    m = DOCLING_CODE_RE.match(code_raw)
                    m2 = DOCLING_CODE_RE.match(t_nospace)
                    
                    if m:
                        code_str = m.group()
                    elif m2:
                        code_str = m2.group()
                    else:
                        tokens = code_raw.split()
                        for j in range(1, len(tokens)):
                            prefix_t = " ".join(tokens[:j])
                            prefix_nospace = prefix_t.replace(" ", "")
                            if DOCLING_CODE_RE.match(prefix_t):
                                code_str = DOCLING_CODE_RE.match(prefix_t).group()
                                break
                            elif DOCLING_CODE_RE.match(prefix_nospace):
                                code_str = DOCLING_CODE_RE.match(prefix_nospace).group()
                                break
                            
                if not code_str:
                    continue
                
                prezzo = None
                desc_tokens = []
                
                # Lookahead for fused price in the next block's code cell
                if k + 1 < len(code_indices):
                    next_code_raw = row[code_indices[k+1]].strip()
                    next_tokens = next_code_raw.split()
                    for t in reversed(next_tokens):
                        p = _to_decimal(t)
                        if p is not None:
                            prezzo = p
                            if stats is not None:
                                stats.setdefault("lookahead", []).append((code_str, str(p), str(block_cells), page))
                            break
                            
                for cell in block_cells[1:]:
                    cell_text = cell.strip().replace('€', '').replace('$', '').strip()
                    if not cell_text:
                        continue
                        
                    tokens = cell_text.split()
                    cell_price = None
                    
                    for t in reversed(tokens):
                        p = _to_decimal(t)
                        if p is not None:
                            cell_price = p
                            tokens.remove(t)
                            break
                            
                    if cell_price is not None:
                        prezzo = cell_price
                        
                    if tokens:
                        remaining = " ".join(tokens)
                        if re.search(r'[a-zA-Z]{3,}', remaining):
                            desc_tokens.append(remaining)
                            
                # Heuristic for truncated Mamoli codes
                if len(code_str) == 9 and code_str.startswith("000000"):
                    if desc_tokens:
                        # Flatten desc_tokens into words
                        all_words = " ".join(desc_tokens).split()
                        if all_words and re.match(r'^\d{3}$', all_words[-1]):
                            code_str += all_words[-1]
                            all_words.pop()
                            desc_tokens = [" ".join(all_words)]
                            if stats is not None:
                                stats.setdefault("suffix", []).append((code_str, str(prezzo), str(block_cells), page))
                            
                dedup_desc = []
                for d in desc_tokens:
                    if d not in dedup_desc:
                        dedup_desc.append(d)
                        
                desc = " ".join(dedup_desc)
                desc = base_cleanup(desc) if desc else None
                
                key = (doc_stem, page, code_str)
                
                # Regola di precisione: uno short-code numerico (3-5 cifre) va scartato
                # se il record risultante ha descrizione NULL E prezzo NULL.
                if re.match(r"^[0-9]{3,5}$", code_str):
                    if desc is None and prezzo is None:
                        continue
                
                try:
                    riga = RigaGrezza(codice_originale=code_str, descrizione=desc, prezzo=prezzo)
                    out.append(riga)
                except ValueError:
                    pass
        
        # Deduplicate per (doc, page, codice) keeping the LAST occurrence
        final_out = {}
        for r in out:
            final_out[r.codice_originale] = r
            
        return list(final_out.values())
