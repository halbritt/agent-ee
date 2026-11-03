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
    from .catalog import strict_json

    strict_json(raw)
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


def validate_supplier_capture(root, lcsc):
    """Replay a retained component and, when present, its exact response span."""
    from .design import load
    from .land_pattern import validate_provenance
    from .easyeda_footprint import parse_package

    root = Path(root)
    provenance = load(root / f"supplier/{lcsc}.provenance.json")
    validate_provenance(provenance, lcsc)
    raw = (root / provenance["path"]).read_bytes()
    if (
        hashlib.sha256(raw).hexdigest() != provenance["sha256"]
        or len(raw) != provenance["size_bytes"]
    ):
        raise ValueError("supplier footprint bytes differ from provenance")
    parse_package(raw, lcsc, 200)
    receipt_path = root / f"supplier/{lcsc}.capture.json"
    if not receipt_path.is_file():
        raise ValueError(
            "supplier response capture receipt is missing; recapture the part"
        )

    receipt = load(receipt_path)
    from .candidate import safe_relative

    full = (root / safe_relative(receipt["path"])).read_bytes()
    if hashlib.sha256(full).hexdigest() != receipt["sha256"]:
        raise ValueError("supplier full response differs from capture receipt")
    span, (start, end) = result_span(full)
    if (start, end) != (receipt["byte_start"], receipt["byte_end"]) or span != raw:
        raise ValueError(
            "supplier component is not the exact recorded result byte span"
        )
    return provenance


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


def missing_models(design, cad, report):
    """One refusal line per part whose footprint uses a stock 3D model that the fetch did not find,
    naming the part, its footprint, the model file and the repository revisions that were tried."""
    from .models3d import stock_reference

    missing = {r["path"]: r for r in report["files"] if r["status"] == "not-found"}
    problems = []
    for ref, part in sorted(design["parts"].items()):
        node = parse_sexpr((cad / footprint_path(part)).read_bytes())
        for model in children(node, "model"):
            stock = stock_reference(model[1])
            if stock is None or stock[1] not in missing:
                continue
            tried = ", ".join(a["commit"] for a in missing[stock[1]].get("attempts", []))
            problems.append(
                f"parts.{ref}.footprint: {part['footprint']} uses the 3D model {stock[1]}, which "
                f"{report['repository']} does not have at the revisions recorded for this capture "
                f"({tried}); choose a footprint whose model is there. models-report.json lists every model"
            )
    return problems or [
        "parts.footprint: a stock 3D model was not found; see models-report.json"
    ]


SUPPLIER_ENDPOINT = (
    "https://easyeda.com/api/products/{lcsc}/components?version=6.4.19.5"
)


def capture_supplier(lcsc, out, *, endpoint=SUPPLIER_ENDPOINT):
    from .datasheet_capture import run_owned_child

    url = endpoint.format(lcsc=lcsc)
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


def _capture_design(design, out, *, kicad_root, stock_endpoint, supplier_endpoint):
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
        **({"endpoint": stock_endpoint} if stock_endpoint is not None else {}),
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
    # Ordered sources per part, each recorded; see design_datasheets.
    from .design_datasheets import acquire

    acquire(design, out, catalog)
    for lcsc in sorted(catalog["parts"]):
        capture_supplier(lcsc, out / "supplier", endpoint=supplier_endpoint)
    from .models3d import fetch_stock_references

    report = fetch_stock_references(model_references(out / "cad"), out / "lib3d")
    write_json(out / "models-report.json", report)
    if report["missing"]:
        raise DesignError(missing_models(design, out / "cad", report))
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
    validate(design, out)


def capture_design(
    design,
    out,
    *,
    kicad_root=Path("/usr/share/kicad"),
    stock_endpoint=None,
    supplier_endpoint=SUPPLIER_ENDPOINT,
):
    try:
        return _capture_design(
            design,
            out,
            kicad_root=kicad_root,
            stock_endpoint=stock_endpoint,
            supplier_endpoint=supplier_endpoint,
        )
    except DesignError:
        raise
    except (OSError, ValueError, RuntimeError, KeyError, TypeError, IndexError) as exc:
        raise DesignError(
            [
                f"captures: acquisition failed: {exc}; inspect retained partial files and retry into a new directory"
            ]
        ) from exc


def validate_capture_manifest(root):
    """Detect missing, changed and added files, before interpreting captured inputs."""
    from .design import load
    from .requirement_map import input_inventory

    manifest = load(root / "capture.json")
    if (
        not isinstance(manifest, dict)
        or set(manifest) != {"schema", "files"}
        or manifest["schema"] != "agent-ee.design-capture/1"
        or not isinstance(manifest["files"], dict)
    ):
        raise ValueError("expected agent-ee.design-capture/1 with a files hash map")
    actual = input_inventory(root)
    actual.pop("capture.json", None)
    if actual != manifest["files"]:
        changed = sorted(
            k
            for k in set(actual) | set(manifest["files"])
            if actual.get(k) != manifest["files"].get(k)
        )
        raise ValueError("capture files missing or changed: " + ", ".join(changed))


def stock_freshness(root):
    """Warn at compilation without putting a wall-clock-dependent value in the bundle."""
    from .design import load
    import warnings

    catalog = load(root / "stock/catalog.json")
    now = datetime.datetime.now(datetime.timezone.utc)
    stamps = [catalog["as_of"], catalog["completed_at"]]
    stamps += [r["fetched_at"] for r in catalog["requests"]]
    stamps += [p["last_seen"] for p in catalog["parts"].values()]
    times = [datetime.datetime.fromisoformat(t.replace("Z", "+00:00")) for t in stamps]
    if any(t.tzinfo is None for t in times):
        raise ValueError("stock times need a UTC offset; recapture stock")
    if any(t > now for t in times):
        raise ValueError(
            "stock capture time is in the future; recapture stock with a correct clock"
        )
    left = 24 - (now - min(times)).total_seconds() / 3600
    warnings.warn(
        f"stock evidence has {left:.2f} hours left under the 24-hour package policy; recapture before building if stale",
        UserWarning,
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
