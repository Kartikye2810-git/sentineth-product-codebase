"""Minimal valid PDF generation for tests.

Building a real PDF (rather than monkeypatching text extraction) means
the tests exercise `extraction_service.extract_pages` and pypdf for real,
which is where format bugs actually live.
"""


def _escape(line: str) -> bytes:
    escaped = (
        line.replace("\\", r"\\")
        .replace("(", r"\(")
        .replace(")", r"\)")
    )

    return escaped.encode("latin-1")


def build_pdf_pages(pages: list[list[str]]) -> bytes:
    """Return a PDF of `len(pages)` pages, each holding its own lines.

    Multi-page matters for anything asserting on page numbers: a
    single-page document cannot tell a correct page number from a
    hardcoded 1.
    """
    page_count = len(pages)

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

    for lines in pages:
        operators = [b"BT", b"/F1 12 Tf", b"72 720 Td", b"14 TL"]

        for line in lines:
            # A blank line is drawn as a single space: pypdf emits nothing
            # for an empty show operator, which would collapse the
            # paragraph breaks the chunker splits on.
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

    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")

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


def build_pdf(lines: list[str]) -> bytes:
    """Return a single-page PDF containing `lines` as extractable text."""
    return build_pdf_pages([lines])
