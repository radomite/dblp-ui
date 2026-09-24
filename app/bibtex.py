"""Create copyable BibTeX from locally indexed dblp XML fields."""

import re

FIELD_ORDER = (
    "author", "editor", "title", "journal", "booktitle", "series", "volume",
    "number", "pages", "year", "month", "publisher", "school", "address",
    "isbn", "doi", "url",
)
ESCAPES = {
    "\\": r"\textbackslash{}", "&": r"\&", "%": r"\%", "$": r"\$",
    "#": r"\#", "_": r"\_", "{": r"\{", "}": r"\}",
    "~": r"\textasciitilde{}", "^": r"\textasciicircum{}",
}
SPECIAL = re.compile(r"[\\&%$#_{}~^]")


def escape(value):
    return SPECIAL.sub(lambda match: ESCAPES[match.group()], value)


def make_bibtex(key, record_type, values):
    kind = "misc" if record_type in ("www", "data") else record_type
    fields = dict(values)
    if fields.get("ee"):
        fields["url"] = fields["ee"]
        doi_match = re.match(r"https?://(?:dx\.)?doi\.org/(.+)", fields["ee"], re.I)
        if doi_match:
            fields["doi"] = doi_match.group(1)
    else:
        fields["url"] = "https://dblp.org/rec/" + key
    fields.pop("ee", None)
    lines = [f"@{kind}{{DBLP:{key},"]
    for field in FIELD_ORDER:
        value = fields.get(field)
        if value:
            lines.append(f"  {field} = {{{escape(value)}}},")
    lines.append("}")
    return "\n".join(lines) + "\n"
