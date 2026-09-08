"""Render the markdown corpus in `corpus/` into multi-page PDFs.

The harness measures the real pipeline, and the real pipeline starts at
`extract_text_from_pdf`. Feeding it plain text would skip pypdf and quietly
change what the chunker sees, so the corpus is stored as markdown (readable,
diffable, editable) and rendered to PDF here.

Multi-page on purpose: page numbers are ground truth for item 1.3, and a
single-page corpus cannot tell a correct page number from a hardcoded 1.
"""

import argparse
import textwrap
from pathlib import Path


EVAL_DIR = Path(__file__).resolve().parent
CORPUS_DIR = EVAL_DIR / "corpus"
PDF_DIR = EVAL_DIR / "corpus_pdf"

# Helvetica 12pt on US Letter with 72pt margins: 468pt of usable width at
# roughly 6pt per character, and 46 lines between the top and bottom margin.
WRAP_COLUMNS = 78
LINES_PER_PAGE = 46
FONT_SIZE = 12
LEADING = 14
TOP_Y = 720
LEFT_X = 72


def _escape(line: str) -> bytes:
    """Escape one line of text for a PDF string literal."""
    escaped = (
        line.replace("\\", r"\\")
        .replace("(", r"\(")
        .replace(")", r"\)")
    )

    # The base-14 fonts are single-byte encoded; anything outside latin-1
    # would silently corrupt the extracted text rather than fail loudly.
    return escaped.encode("latin-1", errors="replace")


def layout(markdown: str) -> list[list[str]]:
    """Turn document source into pages of rendered lines.

    Markdown heading markers are dropped rather than rendered: the corpus is
    standing in for company PDFs, and a real one does not contain '##'.
    """
    lines: list[str] = []

    for raw in markdown.splitlines():
        stripped = raw.lstrip("#").strip() if raw.startswith("#") else raw.rstrip()

        if not stripped:
            lines.append("")
            continue

        lines.extend(textwrap.wrap(stripped, width=WRAP_COLUMNS) or [""])

    pages = [
        lines[start : start + LINES_PER_PAGE]
        for start in range(0, len(lines), LINES_PER_PAGE)
    ]

    return pages or [[""]]


def render_pdf(pages: list[list[str]]) -> bytes:
    """Build a multi-page PDF whose text pypdf can extract."""
    page_count = len(pages)

    # Object numbering: 1 catalog, 2 page tree, then one Page object per page,
    # then one content stream per page, then the shared font.
    first_page_obj = 3
    first_content_obj = first_page_obj + page_count
    font_obj = first_content_obj + page_count

    kids = " ".join(f"{first_page_obj + i} 0 R" for i in range(page_count))

    objects: list[bytes] = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        f"<< /Type /Pages /Kids [{kids}] /Count {page_count} >>".encode("ascii"),
    ]

    for index in range(page_count):
        objects.append(
            (
                f"<< /Type /Page /Parent 2 0 R "
                f"/MediaBox [0 0 612 792] "
                f"/Resources << /Font << /F1 {font_obj} 0 R >> >> "
                f"/Contents {first_content_obj + index} 0 R >>"
            ).encode("ascii")
        )

    for page_lines in pages:
        operators = [
            b"BT",
            f"/F1 {FONT_SIZE} Tf".encode("ascii"),
            f"{LEFT_X} {TOP_Y} Td".encode("ascii"),
            f"{LEADING} TL".encode("ascii"),
        ]

        for line in page_lines:
            # A blank line is drawn as a single space rather than an empty
            # string: pypdf emits nothing for an empty show operator, which
            # would collapse every paragraph break and leave the semantic
            # chunker of item 1.3 with no boundaries to split on.
            operators.append(b"(" + _escape(line or " ") + b") Tj")
            operators.append(b"T*")

        operators.append(b"ET")
        content = b"\n".join(operators)

        objects.append(
            b"<< /Length "
            + str(len(content)).encode("ascii")
            + b" >>\nstream\n"
            + content
            + b"\nendstream"
        )

    # /Encoding is not optional here. Without it a Type1 base font falls back
    # to Adobe StandardEncoding, in which byte 0x27 is `quoteright` - so every
    # apostrophe typed as ' comes back out of pypdf as U+2019 and no
    # hand-written answer span containing one ever matches.
    objects.append(
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica "
        b"/Encoding /WinAnsiEncoding >>"
    )

    out = bytearray(b"%PDF-1.4\n")
    offsets: list[int] = []

    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode("ascii")
        out += body
        out += b"\nendobj\n"

    xref_offset = len(out)
    size = len(objects) + 1

    out += f"xref\n0 {size}\n".encode("ascii")
    out += b"0000000000 65535 f \n"

    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode("ascii")

    out += b"trailer\n"
    out += f"<< /Size {size} /Root 1 0 R >>\n".encode("ascii")
    out += f"startxref\n{xref_offset}\n".encode("ascii")
    out += b"%%EOF\n"

    return bytes(out)


def build(destination: Path = PDF_DIR) -> list[Path]:
    destination.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []

    for source in sorted(CORPUS_DIR.glob("*.md")):
        pages = layout(source.read_text(encoding="utf-8"))
        target = destination / f"{source.stem}.pdf"
        target.write_bytes(render_pdf(pages))
        written.append(target)

        print(f"{source.stem:<45} {len(pages):>2} pages  {target.stat().st_size:>7} bytes")

    return written


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out",
        type=Path,
        default=PDF_DIR,
        help="directory to write PDFs into",
    )
    args = parser.parse_args()

    written = build(args.out)
    print(f"\n{len(written)} documents written to {args.out}")


if __name__ == "__main__":
    main()
