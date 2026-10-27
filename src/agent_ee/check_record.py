"""Stable check-record mapping, with in-memory scope for composed limitations."""


class CheckRecord(dict):
    """JSON stays a plain check record; limitation scopes live only during composition."""

    def __init__(self, cid, kind, candidate_id, **fields):
        super().__init__(id=cid, kind=kind, candidate_id=candidate_id,
                         execution='not_run', outcome='unknown', summary='',
                         evidence=[], details={}, limitations=[])
        self.update(fields)
        self._limitation_scopes = {}

    def add_limitation(self, text, *, scope):
        self._limitation_scopes[len(self['limitations'])] = scope
        self['limitations'].append(text)

    def limitations_except(self, *scopes):
        return [text for index, text in enumerate(self['limitations'])
                if self._limitation_scopes.get(index) not in scopes]
