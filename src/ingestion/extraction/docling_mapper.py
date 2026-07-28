from __future__ import annotations
import re
from decimal import Decimal
from src.ingestion.extraction.schema import RigaGrezza
from src.ingestion.codes import base_cleanup
from src.ingestion.extraction.text_table import _to_decimal

SHORT_CODE_RE = re.compile(r"^[0-9]{3,}[A-Z0-9]*$")

DOCLING_CODE_RE = re.compile(
    r"^(?:"
    r"(?=.*[A-Z])(?=(?:.*\d){4,})[A-Z0-9][A-Z0-9.\-]{4,}[A-Z0-9S]"
    r"|"
    r"[0-9]{6,}"
    r")$"
)

class DoclingMapper:
    def map_page(self, grid: list[list[str]], doc_stem: str, page: int, stats: dict = None) -> list[RigaGrezza]:
        """Esegue il chunking orizzontale e decodifica i blocchi."""
        out = []
        seen = set()
        
        for row in grid:
            code_indices = []
            
            for i, cell in enumerate(row):
                t = cell.strip()
                t_nospace = t.replace(" ", "")
                if not t:
                    continue
                matched_docling = False
                if DOCLING_CODE_RE.match(t) or DOCLING_CODE_RE.match(t_nospace):
                    code_indices.append(i)
                    matched_docling = True
                else:
                    tokens = t.split()
                    for j in range(1, len(tokens)):
                        prefix_t = " ".join(tokens[:j])
                        prefix_nospace = prefix_t.replace(" ", "")
                        if DOCLING_CODE_RE.match(prefix_t) or DOCLING_CODE_RE.match(prefix_nospace):
                            code_indices.append(i)
                            matched_docling = True
                            break
                            
                if not matched_docling and stats is not None:
                    if SHORT_CODE_RE.match(t) or SHORT_CODE_RE.match(t_nospace):
                        stats.setdefault("short_code", []).append((t, doc_stem, page, str(row)))
                            
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
