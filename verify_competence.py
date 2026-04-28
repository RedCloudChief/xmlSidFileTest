import os
import sys
from generate_d1_report import run_validation

# Test file
input_xml = r"c:\Users\Nuvola Rossa\Desktop\D1\Modelli D per Test\1\D3_125025_04186540656.xml"

def test_competence(code, output_suffix):
    with open(input_xml, 'r', encoding='ISO-8859-1') as f:
        content = f.read()
    
    # Replace the admin code
    # <Amministrazione_Competente>1212</Amministrazione_Competente>
    content = content.replace('<Amministrazione_Competente>1212</Amministrazione_Competente>', 
                              f'<Amministrazione_Competente>{code}</Amministrazione_Competente>')
    # Replace in reference too
    content = content.replace('<Amministrazione_Competente_Conc_Rif>1212</Amministrazione_Competente_Conc_Rif>',
                              f'<Amministrazione_Competente_Conc_Rif>{code}</Amministrazione_Competente_Conc_Rif>')
    
    tmp_xml = f"test_competence_{output_suffix}.xml"
    with open(tmp_xml, 'w', encoding='ISO-8859-1') as f:
        f.write(content)
    
    output_pdf = f"test_competence_{output_suffix}.pdf"
    print(f"Testing with code {code} -> {output_pdf}")
    run_validation(content.encode('ISO-8859-1'), output_pdf, tmp_xml)
    print(f"Done. Please check {output_pdf}")

if __name__ == "__main__":
    # 1. Test mismatch (1212)
    test_competence("1212", "mismatch")
    
    # 2. Test match (1199)
    test_competence("1199", "match")
