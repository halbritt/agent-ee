"""Explicit host-side downloads and local CAD retention for design compilation."""

import datetime
import hashlib
import json
from pathlib import Path
import sys
import urllib.request

from .design import DesignError, footprint_path, validate, virtual_parts
from .storage import digest, write_json
from .symbol_capture import capture_libraries
from .symbol_library import children, parse_sexpr

MAX_SUPPLIER_BYTES = 8 * 1024 * 1024


def result_span(raw):
    """Retain the exact UTF-8 byte span of the top-level result object."""
    text = raw.decode("utf-8")
    decoder = json.JSONDecoder()
    pos = 0
    while pos < len(text) and text[pos].isspace():
        pos += 1
    if text[pos : pos + 1] != "{":
        raise ValueError("supplier response must be a JSON object")
    pos += 1
    found = None
    keys = set()
    while True:
        while pos < len(text) and text[pos].isspace():
            pos += 1
        key, pos = decoder.raw_decode(text, pos)
        if not isinstance(key, str) or key in keys:
            raise ValueError(
                "supplier response has invalid or duplicate top-level keys"
            )
        keys.add(key)
        while text[pos].isspace():
            pos += 1
        if text[pos] != ":":
            raise ValueError("supplier response lacks colon")
        pos += 1
        while text[pos].isspace():
            pos += 1
        start = pos
        value, pos = decoder.raw_decode(text, pos)
        if key == "result":
            if not isinstance(value, dict):
                raise ValueError("supplier result must be a component object")
            found = (len(text[:start].encode("utf-8")), len(text[:pos].encode("utf-8")))
        while text[pos].isspace():
            pos += 1
        if text[pos] == "}":
            if text[pos + 1 :].strip():
                raise ValueError("supplier response has trailing data")
            break
        if text[pos] != ",":
            raise ValueError("supplier response lacks comma")
        pos += 1
    if found is None:
        raise ValueError("supplier response lacks result")
    return raw[found[0] : found[1]], found


def capture_cad(design, out, kicad_root):
    """Retain selected local library bytes; no network or native process."""
    out.mkdir(parents=True)
    selected = sorted(
        {
            p["lib_id"]
            for p in [*design["parts"].values(), *virtual_parts(design).values()]
        }
    )
    sources = {
        lib: (kicad_root / "symbols" / (lib + ".kicad_sym")).read_bytes()
        for lib in {p.split(":")[0] for p in selected}
    }
    _, captured, receipts = capture_libraries(selected, sources)
    for rel, raw in captured.items():
        (out / rel).write_bytes(raw)
    for p in design["parts"].values():
        rel = footprint_path(p)
        target = out / rel
        target.parent.mkdir(exist_ok=True)
        target.write_bytes((kicad_root / "footprints" / rel).read_bytes())
    write_json(out / "symbol-receipts.json", receipts)


def model_references(cad):
    refs = set()
    from .models3d import stock_reference

    for path in sorted(cad.glob("*.pretty/*.kicad_mod")):
        for node in children(parse_sexpr(path.read_bytes()), "model"):
            stock = stock_reference(node[1])
            if stock is None:
                raise DesignError(
                    [
                        f"parts.footprint: {path.name} has unsupported model {node[1]}; capture a footprint using pinned stock models"
                    ]
                )
            refs.add(stock[1])
    return sorted(refs)


def capture_supplier(lcsc, out):
    from .datasheet_capture import run_owned_child

    url = f"https://easyeda.com/api/products/{lcsc}/components?version=6.4.19.5"
    raw = run_owned_child([sys.executable, "-m", __name__, "--fetch", url], 40)
    if raw is None:
        raise ValueError(
            f"parts.{lcsc}: supplier download exceeded 40 s; retry design capture"
        )
    # Preserve the complete exchange before parsing, including a failed response.
    out.mkdir(exist_ok=True)
    (out / (lcsc + ".full.json")).write_bytes(raw)
    span, (start, end) = result_span(raw)
    from .easyeda_footprint import parse_package

    parse_package(span, lcsc, 200)
    rel = f"supplier/{lcsc}.raweasy.json"
    (out / (lcsc + ".raweasy.json")).write_bytes(span)
    write_json(
        out / (lcsc + ".provenance.json"),
        dict(
            schema="agent-ee.supplier-footprint/1",
            format="easyeda-std-component/1",
            lcsc=lcsc,
            path=rel,
            sha256=hashlib.sha256(span).hexdigest(),
            size_bytes=len(span),
            retrieved_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),
            retrieval="HTTP GET; exact top-level result-object byte span",
            source_url=url,
        ),
    )
    write_json(
        out / (lcsc + ".capture.json"),
        dict(
            path=f"supplier/{lcsc}.full.json",
            sha256=hashlib.sha256(raw).hexdigest(),
            byte_start=start,
            byte_end=end,
        ),
    )


def capture_design(design, out, *, kicad_root=Path("/usr/share/kicad")):
    """Download only. Partial captures remain inspectable after failure."""
    validate(design)
    if out.exists() or out.is_symlink():
        raise FileExistsError(
            f"{out}: choose a new capture directory; captures are immutable"
        )
    out.parent.mkdir(parents=True, exist_ok=True)
    out.mkdir()
    capture_cad(design, out / "cad", kicad_root)
    validate(design, out, cad_only=True)
    from .jlc_stock_capture import capture_jlc_stock

    catalog = capture_jlc_stock(
        sorted({p["lcsc"] for p in design["parts"].values()}),
        out / "stock",
        allow_network=True,
    )
    failed = [
        r["lcsc"] + ": " + r["status"]
        for r in catalog["requests"]
        if r["status"] != "found"
    ]
    if failed:
        raise DesignError(
            [
                "parts: JLC stock capture incomplete; choose stocked parts or retry: "
                + "; ".join(failed)
            ]
        )
    bindings, official = [], {}
    for lcsc in sorted(catalog["parts"]):
        raw = json.loads((out / f"stock/responses/{lcsc}.json").read_bytes())
        row = next(
            r
            for r in raw["data"]["componentPageInfo"]["list"]
            if r["componentCode"] == lcsc
        )
        url = row.get("dataManualFileAccessIdUrl") or row.get("dataManualUrl")
        bindings.append(dict(id=lcsc, datasheet_url=url))
        official[lcsc] = row.get("dataManualOfficialLink")
    from .datasheet_capture import capture_datasheets

    attempts = out / "datasheet-attempts"
    attempts.mkdir()
    datasheets = capture_datasheets(bindings, attempts / "signed")
    failed_ids = {r["id"] for r in datasheets["parts"] if r["missing"]}
    if failed_ids and all(official.get(lcsc) for lcsc in failed_ids):
        fallback = [
            dict(
                id=b["id"],
                datasheet_url=official[b["id"]]
                if b["id"] in failed_ids
                else b["datasheet_url"],
            )
            for b in bindings
        ]
        datasheets = capture_datasheets(fallback, out / "datasheets")
    else:
        import shutil

        shutil.copytree(attempts / "signed", out / "datasheets")
    missing = [
        r["id"] + ": " + str(r["reason"]) for r in datasheets["parts"] if r["missing"]
    ]
    if missing:
        raise DesignError(
            [
                "parts: datasheet download failed; retry with fresh signed URLs: "
                + "; ".join(missing)
            ]
        )
    for lcsc in sorted(catalog["parts"]):
        capture_supplier(lcsc, out / "supplier")
    from .models3d import fetch_stock_references

    report = fetch_stock_references(model_references(out / "cad"), out / "lib3d")
    write_json(out / "models-report.json", report)
    if report["missing"]:
        raise DesignError(
            [
                "parts.footprint: some stock models were not found at pinned revisions; choose a supported footprint; see models-report.json"
            ]
        )
    validate(design, out)
    write_json(
        out / "capture.json",
        dict(
            schema="agent-ee.design-capture/1",
            files={
                str(p.relative_to(out)): digest(p)
                for p in sorted(out.rglob("*"))
                if p.is_file()
            },
        ),
    )


if __name__ == "__main__":
    if len(sys.argv) != 3 or sys.argv[1] != "--fetch":
        raise SystemExit("internal download worker requires --fetch URL")
    from .datasheet_capture import validate_url

    url = validate_url(sys.argv[2])
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(
        urllib.request.Request(url, headers={"User-Agent": "agent-ee-design/1"}),
        timeout=30,
    ) as response:
        raw = response.read(MAX_SUPPLIER_BYTES + 1)
        if len(raw) > MAX_SUPPLIER_BYTES:
            raise ValueError("supplier response exceeds 8 MiB")
        sys.stdout.buffer.write(raw)
