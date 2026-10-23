"""The closed quantity grammar that qualifies cited values (AGENTEE-348 third-HOLD redesign).

One implementation for every consumer that grants authority from a captured quote
(``source_authority.cite``, hence the design-rule import, native and generated custom
rules, and locality owner, manufacturer and datasheet bounds). It replaces shape-by-shape
token matching: a quote either parses COMPLETELY under the grammar below into exact typed
facts, or it FAILS CLOSED and states nothing, with the reason reported.

Lexical classes (every character of the quote belongs to exactly one token)::

    WS      space, tab, CR, LF, NBSP U+00A0, thin space U+2009, narrow NBSP U+202F
    NUMBER  [0-9]+ ( '.' [0-9]+ )? ( [eE] [+-U+2212]? [0-9]+ )?      unsigned
    WORD    a letter or '_' followed by letters, digits or '_'      (EE322, mm2, RF_50)
    PUNCT   any other single character; the signs '+', '-', U+2212 and U+00B1 (plus-minus)
            are explicit PUNCT tokens, never part of a NUMBER

Grammar of one quantity (only these shapes produce a fact)::

    quantity   := [sign] NUMBER unit-expr?
    sign       := '+' | '-' | U+2212        only after WS, the start, or one of ( [ " :
    unit-expr  := [WS?] term ( op term )*
                | [WS?] ('/' | 'per') term ( op term )*       a pure denominator
    op         := WS? ('/' | '*' | U+00B7 | U+00D7) WS?  |  WS 'per' WS
    term       := unit power? | 'thermal' WS count-noun | count-noun
    power      := '^' ['-' | U+2212] [0-9]+  (adjacent)  |  U+00B2 | U+00B3 (in the WORD)
    unit       := length: 'nm' 'um' 'µm' 'μm' 'mm' (SI prefixes n, micro, m on the metre;
                  no bare 'm', 'cm', 'km' or 'mils') | 'mil'
                | time: 's' 'ms' 'us' 'µs' 'μs' 'ns' | 'second' | 'seconds'
                  (time terms exist so a rate parses to its own dimension, never a length)
    count-noun := 'spoke' | 'spokes' | 'via' | 'vias'         dimension spoke or via
    head       := ('thermal' WS)? ('spoke' | 'via') WS 'count' WS? (':' | 'of' | 'is' | '=')? WS?
                  immediately before the NUMBER: multiplies the quantity by that count

A fact is (dimension vector, exact canonical value): metres, seconds and counts, each
with an integer exponent, and the value as an exact Fraction scaled to SI base units.
A count fact needs an unsigned integer literal. A head-form count must not repeat a
count noun.

Fail-closed rules: the whole quote states nothing, with reason, when it contains any of
the following.

- a plus-minus sign (a tolerance; no explicit tolerance fact exists);
- a range: a NUMBER or quantity followed by '-', U+2013, U+2014 or '~', a 'to' before a
  number, or 'between' before one;
- grouping or juxtaposition: a NUMBER adjacent to ',', '/', '.', an apostrophe or a
  quote mark that is followed by a digit, or a NUMBER followed by WS and another NUMBER;
- a sign that is not in the sign position above;
- a WORD directly after a numeral (it is read as its unit) that is not a grammar term,
  for example yd, cm2, mm2 or capacitors;
- a unit term juxtaposed after a complete quantity, or a unit term after a designator
  such as x-3 or D-1;
- any other PUNCT adjacent to a NUMBER or unit, for example '%', '^' without digits,
  or a degree sign;
- a non-integer, signed or exponent count, or a doubled count in head form.

A designator, WORD ('-' | '.') NUMBER, is an identifier (D-1, N-7, p.149) and states no
quantity.

Equality: a governed value qualifies only against a fact with an EXACTLY equal dimension
vector AND an exactly equal canonical value, compared as rationals, with no float
tolerance. A declared JSON number is read as its shortest round-trip decimal, str(float).
"""
from fractions import Fraction
import re

WS = ' \t\r\n   '
NUMBER = re.compile(r'[0-9]+(?:\.[0-9]+)?(?:[eE][+\-−]?[0-9]+)?')
WORD = re.compile(r'[^\W\d]\w*')
SIGNS = '+-−'
PLUS_MINUS = '±'
OPS = '/*·×'
RANGE = '-–—~'
OPEN = '([{"“:'
TERMINATORS = ',;:.)]}"\'’”'
PREFIX = {'n': Fraction(1, 10 ** 9), 'u': Fraction(1, 10 ** 6), '\u00b5': Fraction(1, 10 ** 6),
          '\u03bc': Fraction(1, 10 ** 6), 'm': Fraction(1, 1000)}
# The allow-list. Lengths are exactly the spellings the round-2 model accepted (nm um µm μm
# mm mil), so the redesign never newly accepts a length spelling; time terms exist only so a
# rate parses to its own dimension.
UNITS = {'mil': ('metre', Fraction(254, 10 ** 7)), 's': ('second', Fraction(1)),
         'second': ('second', Fraction(1)), 'seconds': ('second', Fraction(1))}
for _p, _f in PREFIX.items():
    UNITS[_p + 'm'] = ('metre', _f)
    UNITS[_p + 's'] = ('second', _f)
COUNT_NOUNS = {'spoke': 'spoke', 'spokes': 'spoke', 'via': 'via', 'vias': 'via'}
COUNT_KINDS = {'thermal_spoke_count': 'spoke', 'via_count': 'via'}
SUPERSCRIPT = {'²': 2, '³': 3}


class OutsideGrammar(ValueError):
    """The quote contains a quantity expression the closed grammar does not define."""


def lex(quote):
    tokens, i = [], 0
    while i < len(quote):
        c = quote[i]
        if c in WS:
            j = i
            while j < len(quote) and quote[j] in WS:
                j += 1
            tokens.append(('ws', quote[i:j], i))
            i = j
        elif c in '0123456789':
            match = NUMBER.match(quote, i)
            tokens.append(('num', match[0], i))
            i = match.end()
        elif WORD.match(quote, i):
            match = WORD.match(quote, i)
            tokens.append(('word', match[0], i))
            i = match.end()
        else:
            tokens.append(('punct', c, i))
            i += 1
    return tokens


def _unit(word):
    """(dimension dict, scale) of one WORD term, or None if it is not a grammar term."""
    power = 1
    if word and word[-1] in SUPERSCRIPT:
        word, power = word[:-1], SUPERSCRIPT[word[-1]]
    if word in COUNT_NOUNS:
        return ({COUNT_NOUNS[word]: power}, Fraction(1)) if power == 1 else None
    if word in UNITS:
        dimension, scale = UNITS[word]
        return {dimension: power}, scale ** power
    return None


def _combine(left, right, sign):
    dims = dict(left)
    for key, exponent in right.items():
        dims[key] = dims.get(key, 0) + sign * exponent
    return {k: v for k, v in dims.items() if v}


class _Parser:
    def __init__(self, quote):
        self.quote, self.tokens, self.facts = quote, lex(quote), []

    def fail(self, index, why):
        start = self.tokens[index][2] if index < len(self.tokens) else len(self.quote)
        raise OutsideGrammar(f'{why} at {self.quote[max(0, start - 8):start + 12]!r}')

    def tok(self, i):
        return self.tokens[i] if 0 <= i < len(self.tokens) else (None, '', len(self.quote))

    def parse(self):
        if PLUS_MINUS in self.quote:
            raise OutsideGrammar('quote states a tolerance (±) and no explicit tolerance fact exists')
        i = 0
        while i < len(self.tokens):
            kind, text, _ = self.tokens[i]
            if kind == 'word' and self.tok(i + 1)[1] in ('-', '.') and self.tok(i + 2)[0] == 'num':
                i = self.designator(i)
            elif kind == 'num':
                i = self.quantity(i)
            else:
                i += 1
        return self.facts

    def designator(self, i):
        """WORD ('-'|'.') NUMBER ... is an identifier; a unit term right after it is ambiguous."""
        i += 1
        while self.tok(i)[1] in ('-', '.') and self.tok(i + 1)[0] in ('num', 'word'):
            i += 2
        if self.tok(i)[0] == 'ws' and self.tok(i + 1)[0] == 'word' and _unit(self.tok(i + 1)[1]):
            self.fail(i + 1, 'unit term after a designator')
        return i

    def quantity(self, i):
        start = i
        literal = self.tokens[i][1]
        sign, signed = 1, False
        before = self.tok(i - 1)
        if before[0] == 'punct':
            if before[1] in SIGNS and (self.tok(i - 2)[0] in (None, 'ws') or self.tok(i - 2)[1] in OPEN):
                sign, signed = (-1 if before[1] in '-−' else 1), True
            elif before[1] not in OPEN:
                self.fail(i, 'numeral joined to a preceding ' + repr(before[1]))
        elif before[0] == 'num':
            self.fail(i, 'numeral joined to a preceding numeral')
        previous_word = self.tok(i - 2) if before[0] == 'ws' else (None, '', 0)
        if previous_word[0] == 'word' and previous_word[1].lower() == 'between':
            self.fail(i, 'range (between)')
        head = self.head(i)
        i += 1
        dims, scale, count_term = {}, Fraction(1), False
        # A unit expression starts adjacent, after one WS, or with a leading denominator.
        j = i + 1 if self.tok(i)[0] == 'ws' else i
        first = self.tok(j)
        if first[0] == 'word' and first[1] not in ('per',):
            dims, scale, count_term, i = self.unit_expr(j, start)
        elif (first[0] == 'punct' and first[1] == '/') or (first[0] == 'word' and first[1] == 'per'):
            if first[1] == 'per' and self.tok(j - 1)[0] != 'ws':
                self.fail(j, 'per without spacing')
            dims, scale, count_term, i = self.unit_expr(j, start, leading=True)
        self.after(i)
        value = sign * Fraction(literal.replace('−', '-')) * scale
        integer = not signed and literal.isdigit()
        if head is not None:
            if count_term:
                self.fail(start, 'count noun repeated in a count-head form')
            if not integer:
                self.fail(start, 'a count must be an unsigned integer literal')
            dims = _combine({head: 1}, dims, 1)
        elif count_term and not integer:
            self.fail(start, 'a count must be an unsigned integer literal')
        self.facts.append((tuple(sorted(dims.items())), value))
        return i

    def head(self, i):
        """The count noun of a head form ('spoke count: 3') immediately before the numeral."""
        k = i - 1
        while self.tok(k)[0] == 'ws' or self.tok(k)[1] in (':', '=') or (
                self.tok(k)[0] == 'word' and self.tok(k)[1] in ('of', 'is')):
            k -= 1
        if self.tok(k)[1] != 'count' or self.tok(k - 1)[0] != 'ws':
            return None
        noun = self.tok(k - 2)
        return COUNT_NOUNS.get(noun[1]) if noun[0] == 'word' and noun[1] in ('spoke', 'via') else None

    def unit_expr(self, j, start, leading=False):
        dims, scale, count_term, sign = {}, Fraction(1), False, 1
        if leading:
            sign = -1
            j = self.skip_ws(j + 1)
        while True:
            word = self.tok(j)
            if word[0] != 'word':
                self.fail(j, 'operator without a unit term')
            text = word[1]
            if text == 'thermal' and self.tok(j + 1)[0] == 'ws' and self.tok(j + 2)[1] in ('spoke', 'spokes'):
                j += 2
                text = self.tok(j)[1]
            term = _unit(text)
            if term is None:
                self.fail(j, f'unit term {text!r} outside the grammar')
            term_dims, term_scale = term
            j += 1
            if self.tok(j)[1] == '^':
                k, negative = j + 1, 1
                if self.tok(k)[1] in ('-', '−'):
                    k, negative = k + 1, -1
                if self.tok(k)[0] != 'num' or not self.tok(k)[1].isdigit():
                    self.fail(j, 'power without an integer exponent')
                power = negative * int(self.tok(k)[1])
                term_dims = {key: value * power for key, value in term_dims.items()}
                term_scale = term_scale ** power
                j = k + 1
            count_term = count_term or any(key in ('spoke', 'via') for key in term_dims)
            dims = _combine(dims, term_dims, sign)
            scale = scale * term_scale if sign == 1 else scale / term_scale
            # One operator, then the next term.
            k = self.skip_ws(j)
            nxt = self.tok(k)
            if nxt[0] == 'punct' and nxt[1] in OPS:
                sign = -1 if nxt[1] == '/' else 1
                j = self.skip_ws(k + 1)
                continue
            if nxt[0] == 'word' and nxt[1] == 'per' and k > j:
                sign, j = -1, self.skip_ws(k + 1)
                continue
            return dims, scale, count_term, j

    def skip_ws(self, j):
        return j + 1 if self.tok(j)[0] == 'ws' else j

    def after(self, i):
        """What may follow a complete quantity; anything else fails closed."""
        kind, text, _ = self.tok(i)
        if kind is None:
            return
        if kind == 'ws':
            nxt = self.tok(i + 1)
            if nxt[0] == 'num':
                self.fail(i + 1, 'juxtaposed numerals (grouping)')
            if nxt[0] == 'punct' and nxt[1] in RANGE and self.tok(i + 2)[0] in ('ws', 'num'):
                self.fail(i + 1, 'range')
            if nxt[0] == 'word':
                if _unit(nxt[1]) or nxt[1] == 'thermal':
                    self.fail(i + 1, 'unit term juxtaposed after a quantity')
                if nxt[1] == 'to' and self.tok(i + 2)[0] == 'ws' and (
                        self.tok(i + 3)[0] == 'num' or self.tok(i + 3)[1] in SIGNS):
                    self.fail(i + 1, 'range (to)')
            return
        if kind == 'word':
            self.fail(i, f'unit term {text!r} outside the grammar')
        if kind == 'num':
            self.fail(i, 'numeral joined to a numeral')
        if text in TERMINATORS and self.tok(i + 1)[0] != 'num':
            return
        self.fail(i, f'{text!r} joined to a quantity')


def facts(quote):
    """Exact typed facts of a quote, or OutsideGrammar when any part fails the grammar."""
    return _Parser(quote).parse()


def length_mm(value):
    """The governed fact of a declared millimetre value (JSON number: shortest repr decimal)."""
    value = value if isinstance(value, Fraction) else Fraction(str(value))
    return ((('metre', 1),), value / 1000)


def count(kind, n):
    return (((COUNT_KINDS[kind], 1),), Fraction(n)) if kind in COUNT_KINDS and type(n) is int else None


def unqualified(quote, governed):
    """None when every governed fact is exactly stated by ``quote``; otherwise the reason."""
    try:
        stated = set(facts(quote))
    except OutsideGrammar as exc:
        return 'quote has a quantity outside the closed grammar: ' + str(exc)
    if any(fact is None or fact not in stated for fact in governed):
        return 'cited quote does not state the declared value'
    return None
