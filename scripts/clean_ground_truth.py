#!/usr/bin/env python3
"""
Clean up ground truth expected.csv files according to the rules.
"""

import csv
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent / "data" / "ground_truth"

def clean_mamoli_p231():
    """Process MAMOLI_P231_IT: remove ** from codes, split multi-value cells."""
    input_path = BASE / "MAMOLI_P231_IT" / "expected.csv"
    output_path = BASE / "MAMOLI_P231_IT" / "expected.csv.clean"
    
    rows = []
    with open(input_path, newline='', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            source = row.get('_source_cells (bozza da baseline — CORREGGERE)', '').strip()
            if not source or source.startswith('CODICE/CODE') or source.startswith('pz x co'):
                continue
            
            parts = source.split(' ┃ ')
            if len(parts) >= 4:
                code_raw = parts[0].strip()
                desc = parts[1].strip()
                qty_raw = parts[2].strip()
                price_raw = parts[3].strip()
                
                # Clean code: remove ** and <br>
                code = code_raw.replace('**', '').replace('<br>', '').strip()
                
                # Clean description: remove <br>
                desc = desc.replace('<br>', ' ').strip()
                
                # Clean qty: extract digits (e.g., "r<br>1" -> "1")
                qty = qty_raw.replace('<br>', ' ').strip()
                qty_digits = ''.join(c for c in qty if c.isdigit())
                qty = qty_digits if qty_digits else qty
                
                # Clean price: keep numeric format with comma
                price = price_raw.replace('<br>', ' ').strip()
                
                # Assign pos sequentially
                pos = len(rows) + 1
                
                rows.append({
                    'pos': pos,
                    'codice': code,
                    'descrizione': desc,
                    'quantita': qty,
                    'prezzo': price
                })
    
    # Write cleaned CSV
    with open(output_path, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=['pos', 'codice', 'descrizione', 'quantita', 'prezzo'])
        writer.writeheader()
        writer.writerows(rows)
    
    print(f"MAMOLI_P231_IT: {len(rows)} rows cleaned")
    return len(rows)

def clean_nordcap_p003():
    """Process NORDCAP_P003_DE: clean structure, keep original rows."""
    input_path = BASE / "NORDCAP_P003_DE" / "expected.csv"
    output_path = BASE / "NORDCAP_P003_DE" / "expected.csv.clean"
    
    rows = []
    with open(input_path, newline='', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            source = row.get('_source_cells (bozza da baseline — CORREGGERE)', '').strip()
            if not source or source.startswith('Bild'):
                continue
            
            parts = source.split(' ┃ ')
            if len(parts) >= 3:
                pos = parts[0].strip()
                code = parts[1].strip()
                desc = parts[2].strip()
                
                rows.append({
                    'pos': pos,
                    'codice': code,
                    'descrizione': desc,
                    'quantita': '',
                    'prezzo': ''
                })
    
    with open(output_path, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=['pos', 'codice', 'descrizione', 'quantita', 'prezzo'])
        writer.writeheader()
        writer.writerows(rows)
    
    print(f"NORDCAP_P003_DE: {len(rows)} rows cleaned")
    return len(rows)

def clean_nordcap_p004():
    """Process NORDCAP_P004_DE: fix broken headers and multi-line cells."""
    input_path = BASE / "NORDCAP_P004_DE" / "expected.csv"
    output_path = BASE / "NORDCAP_P004_DE" / "expected.csv.clean"
    
    rows = []
    with open(input_path, newline='', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            source = row.get('_source_cells (bozza da baseline — CORREGGERE)', '').strip()
            if not source or source.startswith('2551-P0485-1'):
                continue
            # Skip the header artifact line
            if source.startswith('rtikelnr'):
                continue
            
            # The ┃ separator was also inserted where <br> was, causing spurious splits.
            # We need to reconstruct the original 3-column structure:
            #   col0 = code/part-number
            #   col1 = secondary desc fragment (broken header artifact)
            #   col2 = main description (Artikelbezeichnung)
            # The <br> within col2 was turned into ┃, so we rejoin parts 2+ as needed.
            
            parts = source.split(' ┃ ')
            if len(parts) >= 3:
                code = parts[0].strip().replace('<br>', ' ')
                # If there are >3 parts, parts[2:] should be rejoined into the description
                desc_parts = [p.strip().replace('<br>', ' ') for p in parts[2:]]
                desc = ' '.join(desc_parts)
                # Clean up description: remove double spaces
                desc = ' '.join(desc.split())
                
                # Use sequential pos
                pos = str(len(rows) + 1)
                
                rows.append({
                    'pos': pos,
                    'codice': code,
                    'descrizione': desc,
                    'quantita': '',
                    'prezzo': ''
                })
    
    with open(output_path, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=['pos', 'codice', 'descrizione', 'quantita', 'prezzo'])
        writer.writeheader()
        writer.writerows(rows)
    
    print(f"NORDCAP_P004_DE: {len(rows)} rows cleaned")
    return len(rows)

def clean_silca_p003():
    """Process SILCA_P003: split code from description_it, keep multilingual descriptions."""
    input_path = BASE / "SILCA_P003" / "expected.csv"
    output_path = BASE / "SILCA_P003" / "expected.csv.clean"
    
    def extract_code_desc(raw):
        """Extract code and description from complex raw cell.
        Handles cases like:
          '**D938511ZR**<br>CINGHIA' -> ('D938511ZR', 'CINGHIA')
          ',<br>**D915059ZR**<br>SENSORE ASSE X' -> ('D915059ZR', 'SENSORE ASSE X')
        """
        # Remove ** markers
        clean = raw.replace('**', '')
        # Remove leading comma+br artifacts
        if clean.startswith(',<br>'):
            clean = clean[5:]
        elif clean.startswith(','):
            clean = clean[1:]
        if '<br>' in clean:
            # Find first position of alphanumeric after <br> removal
            parts = clean.split('<br>', 1)
            code = parts[0].strip()
            desc_it = parts[1].strip()
        else:
            code = clean.strip()
            desc_it = ''
        return code, desc_it
    
    def clean_desc(raw):
        """Clean description from leading comma+br artifacts."""
        clean = raw.replace('<br>', ' ').strip()
        if clean.startswith(', '):
            clean = clean[2:]
        elif clean.startswith(','):
            clean = clean[1:]
        return clean.strip()
    
    rows = []
    with open(input_path, newline='', encoding='utf-8') as f:
        reader = csv.reader(f)
        next(reader)  # skip header
        for row in reader:
            # The data is in the last column (index 7)
            if len(row) >= 8:
                source = row[7].strip()
            else:
                continue
                
            if not source or source.startswith('**Ref.**'):
                continue
            
            parts = source.split(' ┃ ')
            if len(parts) >= 6:
                ref = parts[0].strip()
                code_raw = parts[1].strip()
                desc_en = clean_desc(parts[2]) if len(parts) > 2 else ''
                desc_de = clean_desc(parts[3]) if len(parts) > 3 else ''
                desc_fr = clean_desc(parts[4]) if len(parts) > 4 else ''
                desc_es = clean_desc(parts[5]) if len(parts) > 5 else ''
                
                # Extract code and description_it from combined field
                code, desc_it = extract_code_desc(code_raw)
                
                # Clean leading comma in ref
                if ref.startswith(','):
                    ref = ref[1:]
                
                rows.append({
                    'ref': ref,
                    'codice': code,
                    'descrizione_it': desc_it,
                    'descrizione_en': desc_en,
                    'descrizione_de': desc_de,
                    'descrizione_fr': desc_fr,
                    'descrizione_es': desc_es
                })
    
    with open(output_path, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=['ref', 'codice', 'descrizione_it', 'descrizione_en', 'descrizione_de', 'descrizione_fr', 'descrizione_es'])
        writer.writeheader()
        writer.writerows(rows)
    
    print(f"SILCA_P003: {len(rows)} rows cleaned")
    return len(rows)

def clean_silca_p004():
    """Process SILCA_P004: continuation of SILCA_P003."""
    input_path = BASE / "SILCA_P004" / "expected.csv"
    output_path = BASE / "SILCA_P004" / "expected.csv.clean"
    
    def extract_code_desc(raw):
        clean = raw.replace('**', '')
        if clean.startswith(',<br>'):
            clean = clean[5:]
        elif clean.startswith(','):
            clean = clean[1:]
        if '<br>' in clean:
            parts = clean.split('<br>', 1)
            code = parts[0].strip()
            desc_it = parts[1].strip()
        else:
            code = clean.strip()
            desc_it = ''
        return code, desc_it
    
    def clean_desc(raw):
        clean = raw.replace('<br>', ' ').strip()
        if clean.startswith(', '):
            clean = clean[2:]
        elif clean.startswith(','):
            clean = clean[1:]
        return clean.strip()
    
    rows = []
    with open(input_path, newline='', encoding='utf-8') as f:
        reader = csv.reader(f)
        next(reader)  # skip header
        for row in reader:
            # The data is in the last column (index 7)
            if len(row) >= 8:
                source = row[7].strip()
            else:
                continue
                
            if not source or source.startswith('**Ref.**'):
                continue
            
            parts = source.split(' ┃ ')
            if len(parts) >= 6:
                ref = parts[0].strip()
                code_raw = parts[1].strip()
                desc_en = clean_desc(parts[2]) if len(parts) > 2 else ''
                desc_de = clean_desc(parts[3]) if len(parts) > 3 else ''
                desc_fr = clean_desc(parts[4]) if len(parts) > 4 else ''
                desc_es = clean_desc(parts[5]) if len(parts) > 5 else ''
                
                # Extract code and description_it from combined field
                code, desc_it = extract_code_desc(code_raw)
                
                # Clean leading comma in ref
                if ref.startswith(','):
                    ref = ref[1:]
                
                rows.append({
                    'ref': ref,
                    'codice': code,
                    'descrizione_it': desc_it,
                    'descrizione_en': desc_en,
                    'descrizione_de': desc_de,
                    'descrizione_fr': desc_fr,
                    'descrizione_es': desc_es
                })
    
    with open(output_path, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=['ref', 'codice', 'descrizione_it', 'descrizione_en', 'descrizione_de', 'descrizione_fr', 'descrizione_es'])
        writer.writeheader()
        writer.writerows(rows)
    
    print(f"SILCA_P004: {len(rows)} rows cleaned")
    return len(rows)

def clean_electrolux_en():
    """Process ELECTROLUX_EN: verify and align column names."""
    input_path = BASE / "ELECTROLUX_EN" / "expected.csv"
    output_path = BASE / "ELECTROLUX_EN" / "expected.csv.clean"
    
    rows = []
    with open(input_path, newline='', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            pos = row.get('pos', '').strip()
            code = row.get('codice', '').strip()
            desc = row.get('descrizione', '').strip()
            qty = row.get('quantita', '').strip()
            price = row.get('prezzo', '').strip()
            
            if not pos and not code and not desc:
                continue
            if not code and not desc:
                continue
            
            rows.append({
                'pos': pos,
                'codice': code,
                'descrizione': desc,
                'quantita': qty,
                'prezzo': price
            })
    
    with open(output_path, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=['pos', 'codice', 'descrizione', 'quantita', 'prezzo'])
        writer.writeheader()
        writer.writerows(rows)
    
    print(f"ELECTROLUX_EN: {len(rows)} rows cleaned")
    return len(rows)

def clean_electrolux_es():
    """Process ELECTROLUX_ES: verify and align column names."""
    input_path = BASE / "ELECTROLUX_ES" / "expected.csv"
    output_path = BASE / "ELECTROLUX_ES" / "expected.csv.clean"
    
    rows = []
    with open(input_path, newline='', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            pos = row.get('pos', '').strip()
            code = row.get('codice', '').strip()
            desc = row.get('descrizione', '').strip()
            qty = row.get('quantita', '').strip()
            price = row.get('prezzo', '').strip()
            
            if not pos and not code and not desc:
                continue
            if not code and not desc:
                continue
            
            rows.append({
                'pos': pos,
                'codice': code,
                'descrizione': desc,
                'quantita': qty,
                'prezzo': price
            })
    
    with open(output_path, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=['pos', 'codice', 'descrizione', 'quantita', 'prezzo'])
        writer.writeheader()
        writer.writerows(rows)
    
    print(f"ELECTROLUX_ES: {len(rows)} rows cleaned")
    return len(rows)

def clean_delitools_en():
    """Process DELITOOLS_EN: use pos as key when Part Number is empty."""
    input_path = BASE / "DELITOOLS_EN" / "expected.csv"
    output_path = BASE / "DELITOOLS_EN" / "expected.csv.clean"
    
    rows = []
    with open(input_path, newline='', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            pos = row.get('pos', '').strip()
            code = row.get('codice', '').strip()
            desc = row.get('descrizione', '').strip()
            qty = row.get('quantita', '').strip()
            price = row.get('prezzo', '').strip()
            
            if not pos and not code and not desc:
                continue
            
            rows.append({
                'pos': pos,
                'codice': code,
                'descrizione': desc,
                'quantita': qty,
                'prezzo': price
            })
    
    with open(output_path, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=['pos', 'codice', 'descrizione', 'quantita', 'prezzo'])
        writer.writeheader()
        writer.writerows(rows)
    
    print(f"DELITOOLS_EN: {len(rows)} rows cleaned")
    return len(rows)

def main():
    print("=" * 60)
    print("Cleaning ground truth expected.csv files")
    print("=" * 60)
    
    clean_mamoli_p231()
    clean_nordcap_p003()
    clean_nordcap_p004()
    clean_silca_p003()
    clean_silca_p004()
    clean_electrolux_en()
    clean_electrolux_es()
    clean_delitools_en()
    
    print("=" * 60)
    print("Done! Cleaned files saved as expected.csv.clean")
    print("=" * 60)

if __name__ == "__main__":
    main()