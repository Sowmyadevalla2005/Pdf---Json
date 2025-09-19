#!/usr/bin/env python3
"""
pdf_to_json.py
Usage:
    python pdf_to_json.py input.pdf output.json [--ocr] [--debug]
"""
import fitz              # PyMuPDF
import pdfplumber
import camelot
import pytesseract
from pdf2image import convert_from_path
from PIL import Image
import io, json, argparse, re, os, statistics

def gather_font_sizes(doc):
    """Return sorted unique font sizes found in the document (descending)."""
    sizes = []
    for page in doc:
        blocks = page.get_text("dict").get("blocks", [])
        for b in blocks:
            if b.get("type") != 0:  # 0 == text
                continue
            for line in b.get("lines", []):
                for span in line.get("spans", []):
                    sizes.append(round(span.get("size", 0), 1))
    sizes = sorted(set(sizes), reverse=True)
    return sizes

def is_numbered_heading(text):
    """Detect numbering patterns like '1. ', '1.1 ', '2.3.1 ' etc."""
    if not text:
        return None
    m = re.match(r'^\s*(\d+(?:\.\d+)*)(?:\s+|-)\s*(.*)', text)
    if m:
        numbering = m.group(1)
        rest = m.group(2).strip()
        level = numbering.count('.')  # 0 => top-level (1), 1 => sub-level (2)
        return {"numbering": numbering, "title": rest, "level": level+1}
    return None

def clean_line(text):
    return ' '.join(text.replace('\xa0', ' ').split())

def extract_paragraphs_and_headings_from_page(page, section_size=None, subsection_size=None, tol=0.5, debug=False):
    """
    Using PyMuPDF page.get_text('dict') parse lines, detect headings using font-size and numbering,
    and accumulate paragraph content under sections/subsections.
    """
    page_dict = page.get_text("dict")
    blocks = page_dict.get("blocks", [])
    current_section = None
    current_sub = None
    content = []
    paragraph_buffer = []

    def flush_paragraph():
        nonlocal paragraph_buffer
        if paragraph_buffer:
            text = clean_line(' '.join(paragraph_buffer))
            content.append({
                "type": "paragraph",
                "section": current_section,
                "sub_section": current_sub,
                "text": text
            })
            paragraph_buffer = []

    for b in blocks:
        btype = b.get("type")
        if btype == 0:  # text block
            for line in b.get("lines", []):
                spans = line.get("spans", [])
                if not spans:
                    continue
                # aggregate line text and compute max span size and fonts present
                line_text = ''.join([s.get("text","") for s in spans]).strip()
                if not line_text:
                    continue
                max_size = max((round(s.get("size",0),1) for s in spans))
                fonts = [s.get("font","") for s in spans]
                is_bold = any('Bold' in f or 'bold' in f.lower() for f in fonts)

                # numbering check
                num_info = is_numbered_heading(line_text)
                heading_level = None
                if num_info:
                    heading_level = 1 if num_info["level"] == 1 else 2
                    heading_text = num_info["title"] or line_text
                else:
                    heading_text = line_text

                # heuristics on font-size and boldness
                if section_size and max_size >= (section_size - tol):
                    # treat as section heading
                    flush_paragraph()
                    current_section = heading_text
                    current_sub = None
                    content.append({
                        "type": "heading",
                        "level": 1,
                        "text": current_section
                    })
                    continue
                if subsection_size and max_size >= (subsection_size - tol):
                    flush_paragraph()
                    current_sub = heading_text
                    content.append({
                        "type": "heading",
                        "level": 2,
                        "text": current_sub
                    })
                    continue
                # numbered heading overrides sizes
                if num_info:
                    flush_paragraph()
                    if heading_level == 1:
                        current_section = heading_text
                        current_sub = None
                        content.append({"type":"heading","level":1,"text":current_section})
                    else:
                        current_sub = heading_text
                        content.append({"type":"heading","level":2,"text":current_sub})
                    continue

                # if bold and short, treat as possible heading (heuristic)
                if is_bold and len(line_text) < 120 and max_size > (statistics.mean([subsection_size or section_size or max_size]) if subsection_size else max_size):
                    flush_paragraph()
                    # prefer subsection if subsection_size exists and this size < section_size
                    # fallback to subsection
                    current_sub = line_text
                    content.append({"type":"heading","level":2,"text":current_sub})
                    continue

                # otherwise body text
                paragraph_buffer.append(line_text)
        elif btype == 1:  # image block
            # treat image as chart placeholder in-flow
            flush_paragraph()
            content.append({
                "type": "chart",
                "section": current_section,
                "description": "image block detected (image data handled separately)",
                "chart_data": None
            })
        else:
            # other block types: ignore or flush
            flush_paragraph()

    flush_paragraph()
    if debug:
        print("Page content items:", len(content))
    return content

def extract_tables(pdf_path, page_no, flavor_first='stream', debug=False):
    """Use Camelot to extract tables from the specified page. Returns list of dicts."""
    tables_out = []
    try:
        # Camelot expects 1-based page index as string
        tables = camelot.read_pdf(pdf_path, pages=str(page_no), flavor=flavor_first)
        if not tables or len(tables) == 0:
            # try the other flavor
            other = 'lattice' if flavor_first == 'stream' else 'stream'
            tables = camelot.read_pdf(pdf_path, pages=str(page_no), flavor=other)
        for t in tables:
            # df -> 2D list
            tables_out.append({
                "type": "table",
                "section": None,
                "description": None,
                "table_data": t.df.values.tolist()
            })
    except Exception as e:
        if debug:
            print(f"camelot error on page {page_no}: {e}")
    return tables_out

def extract_images_and_ocr(doc, page, enable_ocr=False, debug=False):
    """Extract images on a page. Optionally OCR them to provide a short description."""
    images_data = []
    image_list = page.get_images(full=True)
    for img in image_list:
        xref = img[0]
        try:
            base_image = doc.extract_image(xref)
            image_bytes = base_image.get("image")
            img_ext = base_image.get("ext", "png")
            pil_img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
            desc = None
            if enable_ocr:
                try:
                    desc = pytesseract.image_to_string(pil_img).strip()
                except Exception as e:
                    if debug:
                        print("OCR fail for image:", e)
                    desc = None
            images_data.append({
                "type": "chart",
                "section": None,
                "description": desc or "image detected",
                "chart_data": None
            })
        except Exception as e:
            if debug:
                print("Failed to extract image xref", xref, e)
            images_data.append({
                "type": "chart",
                "section": None,
                "description": "image detected (failed to extract)",
                "chart_data": None
            })
    return images_data

def ocr_page_image(pdf_path, page_no, poppler_path=None):
    """
    Fallback: render single page to image with pdf2image and OCR with pytesseract.
    Returns OCRed string.
    """
    try:
        if poppler_path:
            images = convert_from_path(pdf_path, first_page=page_no, last_page=page_no, poppler_path=poppler_path)
        else:
            images = convert_from_path(pdf_path, first_page=page_no, last_page=page_no)
        if images:
            return pytesseract.image_to_string(images[0])
    except Exception as e:
        print("OCR render error:", e)
    return ""

def parse_pdf(pdf_path, output_path="output.json", enable_ocr=False, debug=False, poppler_path=None):
    result = {"pages": []}
    doc = fitz.open(pdf_path)
    # Compute font sizes across document for heuristics
    sizes = gather_font_sizes(doc)
    if debug:
        print("Font sizes (descending):", sizes)
    section_size = sizes[0] if len(sizes) >= 1 else None
    subsection_size = sizes[1] if len(sizes) >= 2 else None

    # Also open pdfplumber (used later if needed)
    with pdfplumber.open(pdf_path) as pp:
        for i in range(len(doc)):
            page_number = i + 1
            page = doc[i]
            content_items = []

            # Get paragraphs + headings
            # If page has no text spans, we may fallback to OCR
            page_text = page.get_text("text")
            if not page_text or page_text.strip() == "":
                if enable_ocr:
                    ocr_text = ocr_page_image(pdf_path, page_number, poppler_path=poppler_path)
                    if ocr_text and ocr_text.strip():
                        # Put OCR text as paragraph (no headings)
                        content_items.append({
                            "type": "paragraph",
                            "section": None,
                            "sub_section": None,
                            "text": clean_line(ocr_text)
                        })
                # Still try to detect images
                content_items.extend(extract_images_and_ocr(doc, page, enable_ocr=enable_ocr, debug=debug))
            else:
                # normal path
                content_items.extend(extract_paragraphs_and_headings_from_page(page, section_size, subsection_size, debug=debug))
                # tables extracted separately
                tables = extract_tables(pdf_path, page_number, debug=debug)
                if tables:
                    content_items.extend(tables)
                # images/charts
                imgs = extract_images_and_ocr(doc, page, enable_ocr=enable_ocr, debug=debug)
                if imgs:
                    content_items.extend(imgs)

            result["pages"].append({
                "page_number": page_number,
                "content": content_items
            })

    # write JSON
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)
    if debug:
        print("Wrote output to", output_path)

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("pdf", help="Input PDF file path")
    parser.add_argument("output", nargs="?", default="output.json", help="Output JSON file path")
    parser.add_argument("--ocr", action="store_true", help="Enable OCR fallback for scanned pages and images")
    parser.add_argument("--debug", action="store_true", help="Print debug info")
    parser.add_argument("--poppler-path", default=None, help="Optional poppler path for pdf2image (Windows)")
    args = parser.parse_args()
    parse_pdf(args.pdf, args.output, enable_ocr=args.ocr, debug=args.debug, poppler_path=args.poppler_path)
