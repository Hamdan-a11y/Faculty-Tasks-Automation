import pathlib
import zipfile

def make_docx(path: pathlib.Path, document_xml: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(
            "[Content_Types].xml",
            """<?xml version="1.0" encoding="UTF-8"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
</Types>""",
        )
        zf.writestr(
            "_rels/.rels",
            """<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
</Relationships>""",
        )
        zf.writestr(
            "word/_rels/document.xml.rels",
            """<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"></Relationships>""",
        )
        zf.writestr("word/document.xml", document_xml.strip())


CLO_DOC = """<?xml version="1.0" encoding="UTF-8"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:body>
    <w:tbl>
      <w:tr>
        <w:tc><w:p><w:r><w:t>CLO1</w:t></w:r></w:p></w:tc>
        <w:tc><w:p><w:r><w:t>Demonstrate understanding of basic algorithms</w:t></w:r></w:p></w:tc>
      </w:tr>
      <w:tr>
        <w:tc><w:p><w:r><w:t>CLO2</w:t></w:r></w:p></w:tc>
        <w:tc><w:p><w:r><w:t>Apply object oriented programming principles</w:t></w:r></w:p></w:tc>
      </w:tr>
      <w:tr>
        <w:tc><w:p><w:r><w:t>CLO3</w:t></w:r></w:p></w:tc>
        <w:tc><w:p><w:r><w:t>Analyze data structures for efficiency</w:t></w:r></w:p></w:tc>
      </w:tr>
    </w:tbl>
    <w:p><w:r><w:t></w:t></w:r></w:p>
  </w:body>
</w:document>"""

QUESTIONS_DOC = """<?xml version="1.0" encoding="UTF-8"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:body>
    <w:p><w:r><w:t>Q1: What is a stack data structure?</w:t></w:r></w:p>
    <w:p><w:r><w:t>Q2. Explain encapsulation in OOP.</w:t></w:r></w:p>
    <w:p><w:r><w:t>Q3) Describe time complexity of binary search?</w:t></w:r></w:p>
  </w:body>
</w:document>"""


if __name__ == "__main__":
    base = pathlib.Path(__file__).resolve().parent / "fixtures"
    make_docx(base / "clo_example.docx", CLO_DOC)
    make_docx(base / "questions_example.docx", QUESTIONS_DOC)
    print("Fixtures created at", base)
