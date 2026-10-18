"""Bounded structured-output resubmissions; refused calls remain separate evidence."""

import copy
import shutil
import time
from pathlib import Path

from .storage import digest, read_json, write_json

ERROR_PREFIX = "\nVALIDATION ERROR:\n"


def resubmission_limit(value=1):
    if isinstance(value, bool) or not isinstance(value, int) or value not in (0, 1):
        raise ValueError("output_resubmissions must be 0 or 1")
    return value


def _verify_inputs(run, packet_sha):
    from .baseline import verify_evidence
    from .candidate import verify_snapshot

    verify_snapshot(run)
    if digest(run / "packet.json") != packet_sha:
        raise ValueError("resubmission packet changed")
    verify_evidence(run, read_json(run / "packet.json")["checks"], required=True)


def structured_call(run, role, prompt, schema, call, validate, *, limit=1, deadline):
    """Repeat only malformed output, with a frozen prompt and the exact error.

    `call` must use the ordinary pinned preflight on every invocation. Archiving
    its scratch preserves raw output and frees the same sandbox path for a fresh
    context; no previous response is supplied to the new call.
    """
    from .findings import safe_id, OutputFormatError

    safe_id(role, "structured call role")
    if role == "check":
        raise ValueError("checks cannot be resubmitted")
    limit = resubmission_limit(limit)
    run = Path(run)
    calls = run / "review-executions" / (role + "-calls")
    calls.mkdir(parents=True, exist_ok=False)
    packet_sha = digest(run / "packet.json")
    records = []
    current_prompt = prompt
    interrupted = False
    for number in range(limit + 1):
        if number:
            # Recheck captured inputs and sealed artifacts before even attempting
            # another preflight. The call itself rechecks the pinned runtime.
            try:
                _verify_inputs(run, packet_sha)
            except KeyboardInterrupt:
                interrupted = True
                break
            except (ValueError, OSError, KeyError, TypeError) as exc:
                record["resubmission_refusal"] = str(exc)
                write_json(calls / f"{number - 1}.json", records[-1])
                break
            archive = run / "review-work/.refused" / role / str(number - 1)
            archive.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(run / "review-work" / role, archive)
            previous = records[-1]
            previous["execution_record"]["preflight"]["path"] = (
                (archive / "preflight/receipt.json").relative_to(run).as_posix())
            write_json(calls / f"{number - 1}.json", previous)
        prompt_path = calls / f"{number}-prompt.txt"
        prompt_path.write_text(current_prompt)
        write_json(calls / f"{number}-schema.json", schema)
        try:
            record, value = call(current_prompt)
        except KeyboardInterrupt:
            # reviewer_call retains this record before propagating cancellation.
            record = read_json(run / "review-work" / role / "launch-execution.json")
            value, interrupted = None, True
        record = copy.deepcopy(record)
        if record["execution"] == "malformed":
            record["refusal_class"] = "output_format"
        if value is not None and record["execution"] == "ok":
            try:
                validate(value)
            except (ValueError, KeyError, TypeError) as exc:
                record.update(execution="malformed", error=str(exc))
                record["refusal_class"] = ("output_format" if isinstance(exc, (OutputFormatError, KeyError, TypeError))
                                           else "judgment_or_evidence")
        retained = {"execution_record": record, "response": value,
                    "prompt": prompt_path.relative_to(run).as_posix(),
                    "schema": (calls / f"{number}-schema.json").relative_to(run).as_posix()}
        records.append(retained)
        write_json(calls / f"{number}.json", retained)
        if (record.get("refusal_class") != "output_format" or number == limit or
                time.monotonic() >= deadline):
            break
        if not isinstance(record.get("error"), str) or not record["error"]:
            break
        current_prompt = prompt + ERROR_PREFIX + record["error"]
    result = {**record, "resubmissions": {"limit": limit, "used": len(records) - 1,
        "calls": [(calls / f"{n}.json").relative_to(run).as_posix() for n in range(len(records))]}}
    if interrupted:
        write_json(run / (role + "-execution.json"), result)
        raise KeyboardInterrupt
    return result, value if record["execution"] == "ok" else None


def recorded_calls(run, record, role):
    """Verify the retained prompt/error chain and expose each preflight record."""
    detail = record.get("resubmissions")
    if detail is None:
        return [(record, role)]
    limit = resubmission_limit(detail["limit"])
    used = detail["used"]
    if isinstance(used, bool) or not isinstance(used, int) or not 0 <= used <= limit:
        raise ValueError("invalid resubmission count")
    paths = [f"review-executions/{role}-calls/{n}.json" for n in range(used + 1)]
    if detail["calls"] != paths:
        raise ValueError("resubmission call inventory changed")
    result = []
    base_prompt = None
    base_schema = None
    previous = None
    for n, path in enumerate(paths):
        saved = read_json(Path(run) / path)
        if saved["prompt"] != f"review-executions/{role}-calls/{n}-prompt.txt" or saved["schema"] != f"review-executions/{role}-calls/{n}-schema.json":
            raise ValueError("foreign resubmission prompt or schema")
        prompt = (Path(run) / saved["prompt"]).read_text()
        schema = read_json(Path(run) / saved["schema"])
        if n == 0:
            base_prompt, base_schema = prompt, schema
        elif (previous.get("execution") != "malformed" or previous.get("refusal_class") != "output_format" or not previous.get("error") or
                prompt != base_prompt + ERROR_PREFIX + previous["error"] or schema != base_schema):
            raise ValueError("resubmission changed prompt/schema or lacks a malformed refusal")
        previous = saved["execution_record"]
        result.append((previous, f".refused/{role}/{n}" if n < used else role))
    if previous != {k: v for k, v in record.items() if k != "resubmissions"}:
        raise ValueError("resubmission projection differs from final call")
    return result
