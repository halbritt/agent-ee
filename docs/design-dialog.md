# Capturing owner decisions before go

The offline standard-library entry point is
`PYTHONPATH=src python3 -m agent_ee.design_dialog --idea IDEA --questions QUESTIONS --answers ANSWERS --requirements ORIGINAL_REQUIREMENTS --out NEW_DIRECTORY`.
It makes no model calls, selects no parts, writes no designs and changes no
captured scope or baseline. The caller drafts the question batch and owns its
adequacy. This is a CLI/JSON interface with no viewer changes.

All four inputs are explicit. The idea is nonempty UTF-8 text. Original requirements
use `agent-ee.requirements/1` with a `requirements` array of objects containing
`id`, `text`, and `source_question_ids` (empty for original owner requirements).
An explicit empty array represents an idea with no existing requirements. The
[intent importer](design-intent.md) emits this original-requirement format.
Original requirement text and IDs survive derivation unchanged, and the original
JSON bytes are retained separately as `original-requirements.json`.
A [board envelope's](board-envelope.md#stage-one-requirements) obligations are
appended to these originals with `stage_one_requirements`. They then reach the
decision record with their source locators and the envelope hash.

Questions use `agent-ee.questions/1` with nonempty `context` and a `questions`
array. Each question has exactly these fields:

```json
{
  "id": "supply",
  "question": "Supply voltage?",
  "options": ["3.3V", "5V"],
  "default": "3.3V",
  "depends_on": [],
  "requirement_id": "R-supply"
}
```

IDs start with a letter and contain only ASCII letters, digits, underscores or
hyphens (maximum 128 characters). Question and requirement IDs are unique;
new requirement IDs cannot collide with originals. Options are unique nonempty
strings and the default must be among them. Dependencies name question IDs in
the same batch. Missing dependencies, self-dependencies and cycles are refused.
The report groups independent questions into deterministic topological frontiers.
`ready_question_ids` lists unanswered questions whose direct prerequisites have
valid answers along their complete ancestor chain. All questions still require explicit answers before derivation;
there are no inferred or conditionally skipped decisions.

Answers use `agent-ee.answers/1` with `questions_sha256`, `idea_sha256` and
`requirements_sha256` bound to exact input bytes and an `answers` object keyed by
question ID. Each value is either `{"value":"5V"}` or `{"use_default":true}`.
The former must select a listed option. Only the latter authorizes a default,
recorded as `defaulted: true` with its actual value in `dialog.json`. Omitted,
null, empty or malformed answers never authorize defaults. Extra question IDs,
stale hashes and duplicate JSON keys block capture. Free text must first be added
as a reviewed option in a new question batch, which changes the binding hash.

Outputs preserve raw idea, questions, original requirements and, when present,
answers. `dialog.json` records their hashes, normalized answer provenance,
dependency frontiers, findings and `acceptance_ready: false`. Missing or invalid
answers yield `outcome: unknown`, exit 1 and **no derived requirement files**.
Malformed question/original-requirement schemas and I/O failures exit 2. Existing
output directories are refused; an I/O failure can leave partial staging files.

On complete capture, exit 0 means only all decisions were explicitly recorded.
`requirements.json` preserves originals and appends one requirement per question,
with its declared stable ID, exact question and answer text, and transitive
`source_question_ids` in dependency order. `requirements.md` renders the same set.
The same bytes produce identical outputs; independent question ordering does not
change derived requirement bytes. The original inputs remain frozen artifacts;
outputs must be explicitly selected as scope inputs in a new baseline, for example
with `requirements.json` as each baseline source path and the requirement ID as its
locator; that file's hash then enters `baseline_id`. Scope/3 still refuses a
derived acceptance requirement mapped only to checks without an executable bound. Dialog completion
and an [intent importer](design-intent.md) lint pass confer no circuit acceptance.

This protocol is an independent implementation. It embeds no upstream code,
third-party design data or UI assets. Owner review remains separate from peer
review and tool verification.
