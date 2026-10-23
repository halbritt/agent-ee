//! Complete, exact citation quantity grammar, matching agent_ee.quantity.
use crate::exact::{Int, Rat};
use num_traits::{One, Signed, Zero};
use regex::Regex;
use serde_json::Value;
use std::{collections::BTreeMap, sync::OnceLock};

type Dimensions = BTreeMap<String, Int>;
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Governed {
    pub dimensions: Dimensions,
    pub value: Rat,
}
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Fact {
    pub governed: Governed,
    pub denominator: bool,
}

const WS: &str = " \t\r\n\u{a0}\u{2009}\u{202f}";
const SIGNS: &str = "+-−";
const OPS: &str = "/*·×";
const RANGE: &str = "-–—~";
const OPEN: &str = "([{\"“:";
const TERMINATORS: &str = ",;:.)]}\"'’”";
const PROSE_PUNCT: &str = ",;:.()[]{}\"'‘’“”?!&=";
const SUPERSCRIPTS: &str = "⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻";

/// Python repr's quotation and non-printable escaping, for identical diagnostics.
pub fn python_repr(text: &str) -> String {
    static NONPRINTABLE: OnceLock<Regex> = OnceLock::new();
    let nonprintable = NONPRINTABLE.get_or_init(|| Regex::new(r"[\pC\pZ]").unwrap());
    let quote = if text.contains('\'') && !text.contains('"') {
        '"'
    } else {
        '\''
    };
    let mut out = String::new();
    out.push(quote);
    for c in text.chars() {
        match c {
            '\\' => out.push_str("\\\\"),
            '\t' => out.push_str("\\t"),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            c if c == quote => {
                out.push('\\');
                out.push(c);
            }
            c if c != ' ' && nonprintable.is_match(&c.to_string()) => {
                let n = u32::from(c);
                if n <= 0xff {
                    out.push_str(&format!("\\x{n:02x}"));
                } else if n <= 0xffff {
                    out.push_str(&format!("\\u{n:04x}"));
                } else {
                    out.push_str(&format!("\\U{n:08x}"));
                }
            }
            c => out.push(c),
        }
    }
    out.push(quote);
    out
}

#[derive(Clone, Copy, PartialEq, Eq)]
enum Kind {
    Ws,
    Num,
    Word,
    Punct,
    End,
}
#[derive(Clone)]
struct Token {
    kind: Kind,
    text: String,
    start: usize,
}
fn lex(quote: &str) -> Vec<Token> {
    static NUMBER: OnceLock<Regex> = OnceLock::new();
    static WORD: OnceLock<Regex> = OnceLock::new();
    let number =
        NUMBER.get_or_init(|| Regex::new(r"^[0-9]+(?:\.[0-9]+)?(?:[eE][+\-−]?[0-9]+)?").unwrap());
    // Python's Unicode \w is alphanumeric or underscore, excluding marks and
    // connector punctuation accepted by Rust regex's broader Unicode \w.
    let word = WORD.get_or_init(|| Regex::new(r"^[\pL\p{Nl}\p{No}_][\pL\pN_]*").unwrap());
    let mut tokens = Vec::new();
    let (mut byte, mut start) = (0, 0);
    while byte < quote.len() {
        let remaining = &quote[byte..];
        let c = remaining.chars().next().unwrap();
        let (kind, length) = if WS.contains(c) {
            (
                Kind::Ws,
                remaining
                    .chars()
                    .take_while(|c| WS.contains(*c))
                    .map(char::len_utf8)
                    .sum(),
            )
        } else if c.is_ascii_digit() {
            (Kind::Num, number.find(remaining).unwrap().end())
        } else if let Some(found) = word.find(remaining) {
            (Kind::Word, found.end())
        } else {
            (Kind::Punct, c.len_utf8())
        };
        let text = remaining[..length].to_owned();
        byte += length;
        let count = text.chars().count();
        tokens.push(Token { kind, text, start });
        start += count;
    }
    tokens
}
fn power(mut base: Rat, exponent: &Int) -> Rat {
    if base.is_one() {
        return base;
    }
    let mut exponent_abs = exponent.abs();
    let mut result = Rat::one();
    while !exponent_abs.is_zero() {
        if (&exponent_abs % 2_u8) == Int::one() {
            result *= &base;
        }
        exponent_abs >>= 1;
        if !exponent_abs.is_zero() {
            base = &base * &base;
        }
    }
    if exponent.is_negative() {
        result.recip()
    } else {
        result
    }
}
fn literal(text: &str) -> Rat {
    let text = text.replace('−', "-");
    let (mantissa, exponent) = text.split_once(['e', 'E']).unwrap_or((&text, "0"));
    let (whole, fractional) = mantissa.split_once('.').unwrap_or((mantissa, ""));
    let digits: Int = format!("{whole}{fractional}").parse().unwrap();
    let exponent: Int = exponent.parse::<Int>().unwrap() - Int::from(fractional.len());
    Rat::from_integer(digits) * power(Rat::from_integer(10.into()), &exponent)
}
fn dimension(name: &str, exponent: impl Into<Int>) -> Dimensions {
    BTreeMap::from([(name.to_owned(), exponent.into())])
}
fn unit(word: &str) -> Option<(Dimensions, Rat)> {
    let (word, exponent) = if let Some(word) = word.strip_suffix('²') {
        (word, 2)
    } else if let Some(word) = word.strip_suffix('³') {
        (word, 3)
    } else {
        (word, 1)
    };
    if let Some(noun) = match word {
        "spoke" | "spokes" => Some("spoke"),
        "via" | "vias" => Some("via"),
        _ => None,
    } {
        return (exponent == 1).then(|| (dimension(noun, 1), Rat::one()));
    }
    let (name, scale) = match word {
        "mil" => ("metre", Rat::new(254.into(), 10_000_000.into())),
        "s" | "second" | "seconds" => ("second", Rat::one()),
        _ => {
            let (prefix, suffix) = word.split_at(word.char_indices().last()?.0);
            let denom = match prefix {
                "n" => 1_000_000_000,
                "u" | "µ" | "μ" => 1_000_000,
                "m" => 1000,
                _ => return None,
            };
            (
                match suffix {
                    "m" => "metre",
                    "s" => "second",
                    _ => return None,
                },
                Rat::new(1.into(), denom.into()),
            )
        }
    };
    Some((dimension(name, exponent), power(scale, &exponent.into())))
}
fn combine(mut left: Dimensions, right: Dimensions, sign: i8) -> Dimensions {
    for (key, exponent) in right {
        *left.entry(key).or_default() += exponent * sign;
    }
    left.retain(|_, v| !v.is_zero());
    left
}
struct Parser<'a> {
    quote: &'a str,
    tokens: Vec<Token>,
    end: Token,
    facts: Vec<Fact>,
}
impl<'a> Parser<'a> {
    fn new(quote: &'a str) -> Self {
        Self {
            quote,
            tokens: lex(quote),
            end: Token {
                kind: Kind::End,
                text: String::new(),
                start: quote.chars().count(),
            },
            facts: Vec::new(),
        }
    }
    fn tok(&self, i: isize) -> &Token {
        usize::try_from(i)
            .ok()
            .and_then(|i| self.tokens.get(i))
            .unwrap_or(&self.end)
    }
    fn fail(&self, i: isize, why: impl AsRef<str>) -> String {
        let start = self.tok(i).start;
        let snippet: String = self
            .quote
            .chars()
            .skip(start.saturating_sub(8))
            .take(start + 12 - start.saturating_sub(8))
            .collect();
        format!("{} at {}", why.as_ref(), python_repr(&snippet))
    }
    fn parse(mut self) -> Result<Vec<Fact>, String> {
        if self.quote.contains('±') {
            return Err(
                "quote states a tolerance (±) and no explicit tolerance fact exists".into(),
            );
        }
        let mut i = 0;
        while i < self.tokens.len() as isize {
            let t = self.tok(i);
            if t.kind == Kind::Ws {
                i += 1;
            } else if t.kind == Kind::Word
                && ["-", "."].contains(&self.tok(i + 1).text.as_str())
                && self.tok(i + 2).kind == Kind::Num
            {
                i = self.designator(i)?;
            } else if t.kind == Kind::Num {
                i = self.quantity(i)?;
            } else if t.kind == Kind::Word {
                self.prose_word(i)?;
                i += 1;
            } else if SIGNS.contains(t.text.as_str()) && self.tok(i + 1).kind == Kind::Num {
                i += 1;
            } else if self.prose_punct(i) {
                i += 1;
            } else {
                return Err(self.fail(i, format!("{} outside the grammar", python_repr(&t.text))));
            }
        }
        Ok(self.facts)
    }
    fn prose_word(&self, i: isize) -> Result<(), String> {
        let text = &self.tok(i).text;
        if text.chars().any(|c| SUPERSCRIPTS.contains(c)) && unit(text).is_none() {
            return Err(self.fail(
                i,
                format!("superscript in {} outside the grammar", python_repr(text)),
            ));
        }
        Ok(())
    }
    fn prose_punct(&self, i: isize) -> bool {
        let text = self.tok(i).text.as_str();
        PROSE_PUNCT.contains(text)
            || ("-/".contains(text)
                && self.tok(i - 1).kind == Kind::Word
                && self.tok(i + 1).kind == Kind::Word)
    }
    fn designator(&self, mut i: isize) -> Result<isize, String> {
        self.prose_word(i)?;
        i += 1;
        while ["-", "."].contains(&self.tok(i).text.as_str())
            && matches!(self.tok(i + 1).kind, Kind::Num | Kind::Word)
        {
            if self.tok(i + 1).kind == Kind::Word {
                self.prose_word(i + 1)?;
            }
            i += 2;
        }
        if self.tok(i).kind == Kind::Ws
            && self.tok(i + 1).kind == Kind::Word
            && unit(&self.tok(i + 1).text).is_some()
        {
            return Err(self.fail(i + 1, "unit term after a designator"));
        }
        Ok(i)
    }
    fn quantity(&mut self, mut i: isize) -> Result<isize, String> {
        let start = i;
        let text = self.tok(i).text.clone();
        let (mut sign, mut signed) = (1, false);
        let before = self.tok(i - 1);
        if before.kind == Kind::Punct {
            if SIGNS.contains(before.text.as_str())
                && (matches!(self.tok(i - 2).kind, Kind::End | Kind::Ws)
                    || OPEN.contains(self.tok(i - 2).text.as_str()))
            {
                sign = if "-−".contains(before.text.as_str()) {
                    -1
                } else {
                    1
                };
                signed = true;
            } else if !OPEN.contains(before.text.as_str()) {
                return Err(self.fail(
                    i,
                    format!(
                        "numeral joined to a preceding {}",
                        python_repr(&before.text)
                    ),
                ));
            }
        } else if before.kind == Kind::Num {
            return Err(self.fail(i, "numeral joined to a preceding numeral"));
        }
        if before.kind == Kind::Ws
            && self.tok(i - 2).kind == Kind::Word
            && self.tok(i - 2).text.to_lowercase() == "between"
        {
            return Err(self.fail(i, "range (between)"));
        }
        let head = self.head(i);
        i += 1;
        let (mut dims, mut scale, mut count_term, mut denominator) =
            (Dimensions::new(), Rat::one(), false, false);
        let j = self.skip_ws(i);
        let first = self.tok(j);
        if first.kind == Kind::Word && first.text != "per" {
            (dims, scale, count_term, denominator, i) = self.unit_expr(j, false)?;
        } else if (first.kind == Kind::Punct && first.text == "/")
            || (first.kind == Kind::Word && first.text == "per")
        {
            if first.text == "per" && self.tok(j - 1).kind != Kind::Ws {
                return Err(self.fail(j, "per without spacing"));
            }
            (dims, scale, count_term, denominator, i) = self.unit_expr(j, true)?;
        }
        self.after(i)?;
        let value = literal(&text) * Rat::from_integer(sign.into()) * scale;
        let integer = !signed && text.bytes().all(|c| c.is_ascii_digit());
        if let Some(head) = head {
            if count_term {
                return Err(self.fail(start, "count noun repeated in a count-head form"));
            }
            if !integer {
                return Err(self.fail(start, "a count must be an unsigned integer literal"));
            }
            dims = combine(dimension(&head, 1), dims, 1);
        } else if count_term && !integer {
            return Err(self.fail(start, "a count must be an unsigned integer literal"));
        }
        self.facts.push(Fact {
            governed: Governed {
                dimensions: dims,
                value,
            },
            denominator,
        });
        Ok(i)
    }
    fn head(&self, i: isize) -> Option<String> {
        let mut k = i - 1;
        while self.tok(k).kind == Kind::Ws
            || [":", "="].contains(&self.tok(k).text.as_str())
            || (self.tok(k).kind == Kind::Word && ["of", "is"].contains(&self.tok(k).text.as_str()))
        {
            k -= 1;
        }
        if self.tok(k).text != "count" || self.tok(k - 1).kind != Kind::Ws {
            return None;
        }
        let noun = self.tok(k - 2);
        (noun.kind == Kind::Word && ["spoke", "via"].contains(&noun.text.as_str()))
            .then(|| noun.text.clone())
    }
    fn unit_expr(
        &self,
        mut j: isize,
        leading: bool,
    ) -> Result<(Dimensions, Rat, bool, bool, isize), String> {
        let (mut dims, mut scale, mut count_term, mut sign, mut denominator) =
            (Dimensions::new(), Rat::one(), false, 1, false);
        if leading {
            sign = -1;
            j = self.skip_ws(j + 1);
        }
        loop {
            if self.tok(j).kind != Kind::Word {
                return Err(self.fail(j, "operator without a unit term"));
            }
            if self.tok(j).text == "thermal"
                && self.tok(j + 1).kind == Kind::Ws
                && ["spoke", "spokes"].contains(&self.tok(j + 2).text.as_str())
            {
                j += 2;
            }
            let text = &self.tok(j).text;
            let Some((mut term_dims, mut term_scale)) = unit(text) else {
                return Err(self.fail(
                    j,
                    format!("unit term {} outside the grammar", python_repr(text)),
                ));
            };
            j += 1;
            let mut reciprocal = false;
            if self.tok(j).text == "^" {
                let (mut k, mut negative) = (j + 1, 1);
                if ["-", "−"].contains(&self.tok(k).text.as_str()) {
                    k += 1;
                    negative = -1;
                }
                if self.tok(k).kind != Kind::Num
                    || !self.tok(k).text.bytes().all(|c| c.is_ascii_digit())
                {
                    return Err(self.fail(j, "power without an integer exponent"));
                }
                let exponent = self.tok(k).text.parse::<Int>().unwrap() * negative;
                reciprocal = negative == -1;
                for value in term_dims.values_mut() {
                    *value *= &exponent;
                }
                term_scale = power(term_scale, &exponent);
                j = k + 1;
            }
            count_term |= term_dims.contains_key("spoke") || term_dims.contains_key("via");
            denominator |= sign == -1 || reciprocal;
            dims = combine(dims, term_dims, sign);
            if sign == 1 {
                scale *= term_scale;
            } else {
                scale /= term_scale;
            }
            let k = self.skip_ws(j);
            let next = self.tok(k);
            if next.kind == Kind::Punct && OPS.contains(next.text.as_str()) {
                sign = if next.text == "/" { -1 } else { 1 };
                j = self.skip_ws(k + 1);
                continue;
            }
            if next.kind == Kind::Word && next.text == "per" && k > j {
                sign = -1;
                j = self.skip_ws(k + 1);
                continue;
            }
            return Ok((dims, scale, count_term, denominator, j));
        }
    }
    fn skip_ws(&self, j: isize) -> isize {
        if self.tok(j).kind == Kind::Ws {
            j + 1
        } else {
            j
        }
    }
    fn after(&self, i: isize) -> Result<(), String> {
        let token = self.tok(i);
        if token.kind == Kind::End {
            return Ok(());
        }
        if token.kind == Kind::Ws {
            let next = self.tok(i + 1);
            if next.kind == Kind::Num {
                return Err(self.fail(i + 1, "juxtaposed numerals (grouping)"));
            }
            if next.kind == Kind::Punct
                && RANGE.contains(next.text.as_str())
                && matches!(self.tok(i + 2).kind, Kind::Ws | Kind::Num)
            {
                return Err(self.fail(i + 1, "range"));
            }
            if next.kind == Kind::Punct
                && !PROSE_PUNCT.contains(next.text.as_str())
                && !(SIGNS.contains(next.text.as_str()) && self.tok(i + 2).kind == Kind::Num)
            {
                return Err(self.fail(
                    i + 1,
                    format!(
                        "{} after a quantity outside the grammar",
                        python_repr(&next.text)
                    ),
                ));
            }
            if next.kind == Kind::Word {
                if unit(&next.text).is_some() || next.text == "thermal" {
                    return Err(self.fail(i + 1, "unit term juxtaposed after a quantity"));
                }
                if next.text == "to"
                    && self.tok(i + 2).kind == Kind::Ws
                    && (self.tok(i + 3).kind == Kind::Num
                        || SIGNS.contains(self.tok(i + 3).text.as_str()))
                {
                    return Err(self.fail(i + 1, "range (to)"));
                }
            }
            return Ok(());
        }
        if token.kind == Kind::Word {
            return Err(self.fail(
                i,
                format!("unit term {} outside the grammar", python_repr(&token.text)),
            ));
        }
        if token.kind == Kind::Num {
            return Err(self.fail(i, "numeral joined to a numeral"));
        }
        if TERMINATORS.contains(token.text.as_str()) && self.tok(i + 1).kind != Kind::Num {
            return Ok(());
        }
        Err(self.fail(
            i,
            format!("{} joined to a quantity", python_repr(&token.text)),
        ))
    }
}

pub fn facts(quote: &str) -> Result<Vec<Fact>, String> {
    Parser::new(quote).parse()
}
pub fn length_mm(value: &Rat) -> Governed {
    Governed {
        dimensions: dimension("metre", 1),
        value: value / Rat::from_integer(1000.into()),
    }
}
pub fn count(kind: &str, value: &Value) -> Option<Governed> {
    let name = match kind {
        "thermal_spoke_count" => "spoke",
        "via_count" => "via",
        _ => return None,
    };
    Some(Governed {
        dimensions: dimension(name, 1),
        value: Rat::from_integer(crate::exact::integer(value).ok()?),
    })
}
pub fn unqualified(quote: &str, governed: &[Option<Governed>]) -> Option<String> {
    let stated = match facts(quote) {
        Ok(facts) => facts,
        Err(reason) => {
            return Some(format!(
                "quote has a quantity outside the closed grammar: {reason}"
            ));
        }
    };
    for fact in governed {
        let Some(fact) = fact else {
            return Some("cited quote does not state the declared value".into());
        };
        let equal: Vec<_> = stated.iter().filter(|s| &s.governed == fact).collect();
        let is_count = fact.dimensions.contains_key("spoke") || fact.dimensions.contains_key("via");
        if is_count && !equal.is_empty() && equal.iter().all(|s| s.denominator) {
            return Some("count fact has a denominator".into());
        }
        if equal.is_empty() {
            return Some("cited quote does not state the declared value".into());
        }
    }
    None
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    // Retained independent expected outcomes from the Python consumer matrix,
    // not generated by the implementation under test. All 60 length and 21
    // count cases include exact advisory diagnostics.
    // tests/test_quantity_matrix.py SHA256 8bbf30381095e7cfb1754bca7963b49bbbe278a888bb2ade32255abe80dba6d5
    const MATRIX: &str = r###"[
  ["L01", "length", "3 mm", "3", "1", null],
  ["L02", "length", "3.0 mm", "3", "1", null],
  ["L03", "length", "+3 mm", "3", "1", null],
  ["L04", "length", "3e0 mm", "3", "1", null],
  ["L05", "length", "30e-1 mm", "3", "1", null],
  ["L06", "length", "3mm", "3", "1", null],
  ["L07", "length", "1e-3 mm", "3", "1", "cited quote does not state the declared value"],
  ["L08", "length", "-3 mm", "3", "1", "cited quote does not state the declared value"],
  ["L09", "length", "−3 mm", "3", "1", "cited quote does not state the declared value"],
  ["L10", "length", "±3 mm", "3", "1", "quote has a quantity outside the closed grammar: quote states a tolerance (±) and no explicit tolerance fact exists"],
  ["L11", "length", "3 ±0.1 mm", "3", "1", "quote has a quantity outside the closed grammar: quote states a tolerance (±) and no explicit tolerance fact exists"],
  ["L12", "length", "3,000 um", "3", "1", "quote has a quantity outside the closed grammar: ',' joined to a quantity at '3,000 um'"],
  ["L13", "length", "3 000 um", "3", "1", "quote has a quantity outside the closed grammar: juxtaposed numerals (grouping) at '3 000 um'"],
  ["L14", "length", "0.3 cm", "3", "1", "quote has a quantity outside the closed grammar: unit term 'cm' outside the grammar at '0.3 cm'"],
  ["L15", "length", "3 mils", "3", "1", "quote has a quantity outside the closed grammar: unit term 'mils' outside the grammar at '3 mils'"],
  ["L16", "length", "3 mm2", "3", "1", "quote has a quantity outside the closed grammar: unit term 'mm2' outside the grammar at '3 mm2'"],
  ["L17", "length", "3-5 mm", "3", "1", "quote has a quantity outside the closed grammar: '-' joined to a quantity at '3-5 mm'"],
  ["L18", "length", "Captured feed speed or area: 3 mm/s", "3", "1", "cited quote does not state the declared value"],
  ["L19", "length", "Captured feed speed or area: 3 mm^2", "3", "1", "cited quote does not state the declared value"],
  ["L20", "length", "Captured feed speed or area: 3 mm / s", "3", "1", "cited quote does not state the declared value"],
  ["L21", "length", "Captured feed speed or area: 3 mm/second", "3", "1", "cited quote does not state the declared value"],
  ["L22", "length", "3 mm²", "3", "1", "cited quote does not state the declared value"],
  ["L23", "length", "Captured length: 13 mm", "3", "1", "cited quote does not state the declared value"],
  ["L24", "length", "Captured length: 3.3 mm", "3", "1", "cited quote does not state the declared value"],
  ["L25", "length", "Captured length: x-3 mm", "3", "1", "quote has a quantity outside the closed grammar: unit term after a designator at 'th: x-3 mm'"],
  ["L26", "length", "Captured length: 1-3 mm", "3", "1", "quote has a quantity outside the closed grammar: '-' joined to a quantity at 'ength: 1-3 mm'"],
  ["L27", "length", "Captured length: 1/3 mm", "3", "1", "quote has a quantity outside the closed grammar: operator without a unit term at 'ngth: 1/3 mm'"],
  ["L28", "length", "Captured length: 1’3 mm", "3", "1", "quote has a quantity outside the closed grammar: '’' joined to a quantity at 'ength: 1’3 mm'"],
  ["L29", "length", "Captured length: 1'3 mm", "3", "1", "quote has a quantity outside the closed grammar: \"'\" joined to a quantity at \"ength: 1'3 mm\""],
  ["L30", "length", "Captured length: 1 3 mm", "3", "1", "quote has a quantity outside the closed grammar: juxtaposed numerals (grouping) at 'ngth: 1 3 mm'"],
  ["L31", "length", "Captured length: 1 3 mm", "3", "1", "quote has a quantity outside the closed grammar: juxtaposed numerals (grouping) at 'ngth: 1\\u20093 mm'"],
  ["L32", "length", "Captured length: 1,3 mm", "3", "1", "quote has a quantity outside the closed grammar: ',' joined to a quantity at 'ength: 1,3 mm'"],
  ["L33", "length", "Captured length: 3 to 5 mm", "3", "1", "quote has a quantity outside the closed grammar: unit term 'to' outside the grammar at 'ngth: 3 to 5 mm'"],
  ["L34", "length", "Captured length: between 3 mm and 5 mm", "3", "1", "quote has a quantity outside the closed grammar: range (between) at 'between 3 mm and 5 m'"],
  ["L35", "length", "Captured maximum separation: 1000 um", "1", "1", null],
  ["L36", "length", "Captured: 1000 µm", "1", "1", null],
  ["L37", "length", "Captured: 1000000 nm", "1", "1", null],
  ["L38", "length", "Captured: 10 mil", "127", "500", null],
  ["L39", "length", "Captured: 1e3 um", "1", "1", null],
  ["L40", "length", "Captured: 1000 um", "1000001", "1000000", "cited quote does not state the declared value"],
  ["L41", "length", "Review synthetic numeric requirement: 0.15 mm", "999", "1", "cited quote does not state the declared value"],
  ["L42", "length", "Review synthetic numeric requirement: 0.15 mm", "3", "20", null],
  ["L43", "length", "Review synthetic source: there is no numeric design requirement here.", "3", "1", "cited quote does not state the declared value"],
  ["L44", "length", "within 3 mm and 999 yd", "3", "1", "quote has a quantity outside the closed grammar: unit term 'yd' outside the grammar at 'and 999 yd'"],
  ["L45", "length", "within 3 mm and 4 mm", "3", "1", null],
  ["L46", "length", "Captured offset: - 3 mm", "3", "1", "quote has a quantity outside the closed grammar: '-' outside the grammar at 'offset: - 3 mm'"],
  ["L47", "length", "Captured offset: − 3 mm", "3", "1", "quote has a quantity outside the closed grammar: '−' outside the grammar at 'offset: − 3 mm'"],
  ["L48", "length", "Captured inverse length: 3 mm ⁻¹", "3", "1", "quote has a quantity outside the closed grammar: '⁻' after a quantity outside the grammar at 'h: 3 mm ⁻¹'"],
  ["L49", "length", "Captured: D-1.¹, 3 mm", "3", "1", "quote has a quantity outside the closed grammar: superscript in '¹' outside the grammar at 'ed: D-1.¹, 3 mm'"],
  ["L50", "length", "Captured: D-1.foo¹, 3 mm", "3", "1", "quote has a quantity outside the closed grammar: superscript in 'foo¹' outside the grammar at 'ed: D-1.foo¹, 3 mm'"],
  ["L51", "length", "Captured: 3 mm ; D-1.¹", "3", "1", "quote has a quantity outside the closed grammar: superscript in '¹' outside the grammar at 'm ; D-1.¹'"],
  ["L52", "length", "Captured: D¹-1, 3 mm", "3", "1", "quote has a quantity outside the closed grammar: superscript in 'D¹' outside the grammar at 'ptured: D¹-1, 3 mm'"],
  ["L53", "length", "Captured: D-1, 3 mm", "3", "1", null],
  ["L54", "length", "Captured: R12.3, 3 mm", "3", "1", null],
  ["L55", "length", "Captured: U5-A, 3 mm", "3", "1", null],
  ["L56", "length", "Captured: 3 mm ; D-1", "3", "1", null],
  ["L57", "length", "Captured: 3 mm ; U5-A.2", "3", "1", null],
  ["L58", "length", "Captured: 3 mm^1", "3", "1", null],
  ["L59", "length", "Captured: 3 mm*mm^-1*mm", "3", "1", null],
  ["L60", "length", "Captured: 3 mm*s^−1*s", "3", "1", null],
  ["C01", "count", "Captured: 3 thermal spokes", "3", "1", null],
  ["C02", "count", "Captured: spoke count: 3", "3", "1", null],
  ["C03", "count", "Captured: 3 spokes", "3", "1", null],
  ["C04", "count", "Captured board thickness: 3 mm. This record states no thermal spoke count.", "3", "1", "cited quote does not state the declared value"],
  ["C05", "count", "Captured board thickness: 3mm.", "3", "1", "cited quote does not state the declared value"],
  ["C06", "count", "Captured value: 3", "3", "1", "cited quote does not state the declared value"],
  ["C07", "count", "Captured: 3 vias", "3", "1", "cited quote does not state the declared value"],
  ["C08", "count", "Captured: 4 thermal spokes", "3", "1", "cited quote does not state the declared value"],
  ["C09", "count", "Captured: 3.0 thermal spokes", "3", "1", "quote has a quantity outside the closed grammar: a count must be an unsigned integer literal at 'ptured: 3.0 thermal '"],
  ["C10", "count", "Captured spoke density: 3 thermal spokes/mm", "3", "1", "cited quote does not state the declared value"],
  ["C11", "count", "Captured spoke density: 3 spokes per mm", "3", "1", "cited quote does not state the declared value"],
  ["C12", "count", "Captured spoke density: spoke count: 3 / mm", "3", "1", "cited quote does not state the declared value"],
  ["C13", "count", "Captured: +3 thermal spokes", "3", "1", "quote has a quantity outside the closed grammar: a count must be an unsigned integer literal at 'tured: +3 thermal sp'"],
  ["C14", "count", "3 spokes/mm*mm", "3", "1", "count fact has a denominator"],
  ["C15", "count", "spoke count: 3 /mm*mm", "3", "1", "count fact has a denominator"],
  ["C16", "count", "3 spokes /s*s", "3", "1", "count fact has a denominator"],
  ["C17", "count", "Captured: 3 spokes*mm^-1*mm", "3", "1", "count fact has a denominator"],
  ["C18", "count", "Captured: 3 spokes*mm^−1*mm", "3", "1", "count fact has a denominator"],
  ["C19", "count", "Captured: 3 spokes*s^-1*s", "3", "1", "count fact has a denominator"],
  ["C20", "count", "Captured: 3 spokes*s^−1*s", "3", "1", "count fact has a denominator"],
  ["C21", "count", "Captured: 3 spokes^1", "3", "1", null]
]"###;

    #[test]
    fn closed_grammar_matches_all_retained_consumer_matrix_rows() {
        let rows: Vec<Value> = serde_json::from_str(MATRIX).unwrap();
        assert_eq!(rows.len(), 81);
        for row in rows {
            let n: Int = row[3].as_str().unwrap().parse().unwrap();
            let d: Int = row[4].as_str().unwrap().parse().unwrap();
            let governed = if row[1] == "length" {
                Some(length_mm(&Rat::new(n, d)))
            } else {
                count("thermal_spoke_count", &json!(3))
            };
            let expected = row[5].as_str().map(str::to_owned);
            assert_eq!(
                unqualified(row[2].as_str().unwrap(), &[governed]),
                expected,
                "{}: {}",
                row[0],
                row[2]
            );
        }
    }

    #[test]
    fn complete_quote_and_exact_dimensions_are_required() {
        let stated = facts("Captured: 3 mm*mm^-1*mm").unwrap();
        assert_eq!(stated[0].governed, length_mm(&Rat::from_integer(3.into())));
        assert!(stated[0].denominator);
        assert_eq!(
            facts("3 mm/s").unwrap()[0].governed.dimensions,
            BTreeMap::from([("metre".into(), 1.into()), ("second".into(), (-1).into())])
        );
        assert_eq!(
            facts("10 mil").unwrap()[0].governed.value,
            Rat::new(127.into(), 500_000.into())
        );
        assert_eq!(
            facts("1000 μm").unwrap()[0].governed.value,
            Rat::new(1.into(), 1000.into())
        );
        assert!(
            unqualified(
                "3 mm and 999 yd",
                &[Some(length_mm(&Rat::from_integer(3.into())))]
            )
            .unwrap()
            .contains("'yd' outside the grammar")
        );
        assert_eq!(
            unqualified(
                "3 spokes/mm*mm; 3 spokes",
                &[count("thermal_spoke_count", &json!(3))]
            ),
            None
        );
        assert_eq!(
            unqualified("3 spokes^0", &[count("thermal_spoke_count", &json!(3))]),
            Some("cited quote does not state the declared value".into())
        );
        assert!(count("via_count", &json!(true)).is_none());
        assert!(count("via_count", &json!(3.0)).is_none());
    }

    #[test]
    fn arbitrary_exponents_and_unicode_diagnostics_are_not_narrowed() {
        let stated = facts("3 s^18446744073709551616").unwrap();
        assert_eq!(
            stated[0].governed.dimensions["second"],
            "18446744073709551616".parse::<Int>().unwrap()
        );
        assert_eq!(
            facts("1e−1001 mm").unwrap()[0].governed.value,
            Rat::new(1.into(), Int::from(10).pow(1004))
        );
        assert_eq!(
            python_repr("a\u{2009}b\u{a0}c\u{200b}"),
            "'a\\u2009b\\xa0c\\u200b'"
        );
        assert_eq!(python_repr("a'b"), "\"a'b\"");
        assert_eq!(python_repr("a'b\""), "'a\\'b\"'");
    }
}
