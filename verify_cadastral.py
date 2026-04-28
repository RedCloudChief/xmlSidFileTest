import os
import sys
from generate_d1_report import run_validation

# Test file
input_xml = r"c:\Users\Nuvola Rossa\Desktop\D1\Modelli D per Test\1\D3_125025_04186540656.xml"

def test_cadasto(code, output_suffix):
    with open(input_xml, 'r', encoding='ISO-8859-1') as f:
        content = f.read()
    
    # Replace all occurrences of H703 with the target code
    content = content.replace('<Codice_Comune>H703</Codice_Comune>', 
                              f'<Codice_Comune>{code}</Codice_Comune>')
    
    tmp_xml = f"test_catasto_{output_suffix}.xml"
    with open(tmp_xml, 'w', encoding='ISO-8859-1') as f:
        f.write(content)
    
    output_pdf = f"test_catasto_{output_suffix}.pdf"
    print(f"Testing cadastral code {code} -> {output_pdf}")
    run_validation(content.encode('ISO-8859-1'), output_pdf, tmp_xml)
    print(f"Done. Please check {output_pdf}")

if __name__ == "__main__":
    # 1. Test mismatch (H703) - should show error in "Righe Elaborato"
    test_cadasto("H703", "mismatch")
    
    # 2. Test match (B895) - should NOT show error in "Righe Elaborato"
    test_cadasto("B895", "match")
