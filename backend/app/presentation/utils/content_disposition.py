from urllib.parse import quote


def build_attachment_content_disposition(filename: str) -> str:
    """Build Content-Disposition header safe for latin-1 transport (RFC 6266 / RFC 5987)."""
    ascii_fallback = "".join(character if character.isascii() else "_" for character in filename)
    ascii_fallback = ascii_fallback.strip() or "download"
    ascii_fallback = ascii_fallback.replace('"', "'")
    encoded_filename = quote(filename, safe="")
    return f'attachment; filename="{ascii_fallback}"; filename*=UTF-8\'\'{encoded_filename}'
