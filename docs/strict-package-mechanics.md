# Mechanics continuity through strict packaging

When a strict block build declares `placement.mechanics`, packaging authenticates
that recipe and its four source hashes against the sealed schematic candidate.
It reconstructs original native references from the block binding's explicit UUID
aliases, checks the original capture identity, and requires exact equality with a
fresh source-model mechanics binding. The retained problem must have its correct
self-identity and matching source body, access, native owner, pose and pad data.

The package proof pins the capture, problem, configuration, completed job result
and actual accepted final-placement board. Mechanics must have passed on that
board with the original binding. Artwork must then preserve that geometry:

- `no_movable_fields` requires actual board byte equality with accepted placement.
- `repaired` revalidates the retained receipt and current producer snapshot, replays
  `routed_artwork.qualify_receipt`, and verifies the exact input tree, allowed
  output tree, board bytes and adoption. Only the existing authorized silk-field
  scope is available; a report claiming preservation is insufficient.

This transfers existing passing mechanics evidence; it adds no second geometry
predicate, native invocation or source baseline. The existing fresh final block
capture/report replay and package marker/review identities remain required.
Undeclared mechanics cannot appear in config, capture or problem. A build with no
mechanics declaration retains its existing behavior. Source/fixture success is not
native qualification, physical fit, manufacturing acceptance or G4.
