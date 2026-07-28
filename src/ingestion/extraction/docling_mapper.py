from __future__ import annotations
import re
from decimal import Decimal
from src.ingestion.extraction.schema import RigaGrezza
from src.ingestion.codes import base_cleanup
from src.ingestion.extraction.text_table import _to_decimal

DOCLING_CODE_RE = re.compile(
    r"^(?:"
    r"(?=.*[A-Z])(?=(?:.*\d){4,})[A-Z0-9][A-Z0-9.\-]{4,}[A-Z0-9S]"
    r"|"
    r"[0-9]{6,}"
    r")$"
)

class DoclingMapper:
    def map_page(self, grid: list[list[str]], *, doc_stem: str, page: int) -> list[RigaGrezza]:
        """Maps a docling grid to a list of RigaGrezza. Column-aware."""
        out = []
        seen = set()
        
        for row in grid:
            code_indices = []
            
            for i, cell in enumerate(row):
                t = cell.strip()
                t_nospace = t.replace(" ", "")
                if not t:
                    continue
                if DOCLING_CODE_RE.match(t) or DOCLING_CODE_RE.match(t_nospace):
                    code_indices.append(i)
                else:
                    # Try prefix logic: maybe the code is fused with the price (e.g. "V0 92750 68,00")
                    # We split by space and check if the first parts form a code
                    tokens = t.split()
                    for j in range(1, len(tokens)):
                        prefix_t = " ".join(tokens[:j])
                        prefix_nospace = prefix_t.replace(" ", "")
                        if DOCLING_CODE_RE.match(prefix_t) or DOCLING_CODE_RE.match(prefix_nospace):
                            code_indices.append(i)
                            break
                            
            if not code_indices:
                continue
                
            for k, start_idx in enumerate(code_indices):
                end_idx = code_indices[k+1] if k + 1 < len(code_indices) else len(row)
                block_cells = row[start_idx:end_idx]
                
                code_raw = block_cells[0].strip()
                t_nospace = code_raw.replace(" ", "")
                
                m = DOCLING_CODE_RE.match(code_raw)
                m2 = DOCLING_CODE_RE.match(t_nospace)
                
                code_str = None
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
                            
                dedup_desc = []
                for d in desc_tokens:
                    if d not in dedup_desc:
                        dedup_desc.append(d)
                        
                desc = " ".join(dedup_desc)
                desc = base_cleanup(desc) if desc else None
                
                key = (doc_stem, page, code_str)
                try:
                    riga = RigaGrezza(codice_originale=code_str, descrizione=desc, prezzo=prezzo)
                    # We store it in a dict to keep the LAST occurrence for each code
                    # (since DB upsert would overwrite and keep the last one anyway)
                    # But we don't have a dict yet, let's just append it.
                    # We will do deduplication after the loop.
                    out.append(riga)
                except ValueError:
                    pass
        
        # Deduplicate per (doc, page, codice) keeping the LAST occurrence
        final_out = {}
        for r in out:
            final_out[r.codice_originale] = r
            
        return list(final_out.values())
