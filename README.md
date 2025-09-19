# 📄 PDF to Structured JSON Extractor

This project parses a PDF file and extracts its content into a **well-structured JSON format**.  
The JSON preserves **page hierarchy** and identifies **paragraphs, tables, and charts/images**, along with **sections and sub-sections** where possible.

---

## ⚙️ Installation

### Local Setup
```bash
# 1. Create and activate virtual environment
python -m venv venv
source venv/bin/activate     # Linux / Mac
venv\Scripts\activate        # Windows

# 2. Install Python dependencies
pip install pymupdf pdfplumber camelot-py[cv] pdf2image pytesseract pandas pillow

# 3. Install system dependencies
# Ubuntu / Debian
sudo apt-get install -y poppler-utils ghostscript tesseract-ocr

# Windows
# - Install Poppler for Windows and add poppler/bin to PATH
# - Install Ghostscript and add to PATH
# - Install Tesseract OCR (optional, for scanned PDFs)

```
### Usage
``` bash
# Command Line

python pdf_to_json.py input.pdf output.json

```
### Output Format

``` bash

# The program generates a JSON file like this:

{
  "pages": [
    {
      "page_number": 1,
      "content": [
        {
          "type": "heading",
          "level": 1,
          "text": "Introduction"
        },
        {
          "type": "paragraph",
          "section": "Introduction",
          "sub_section": "Background",
          "text": "This is an example paragraph extracted from the PDF..."
        },
        {
          "type": "table",
          "section": "Financial Data",
          "description": null,
          "table_data": [
            ["Year", "Revenue", "Profit"],
            ["2022", "$10M", "$2M"],
            ["2023", "$12M", "$3M"]
          ]
        },
        {
          "type": "chart",
          "section": "Performance Overview",
          "description": "Chart or image detected",
          "chart_data": null
        }
      ]
    }
  ]
}

