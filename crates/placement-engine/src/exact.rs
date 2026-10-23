use anyhow::{Context, Result, bail};
use num_bigint::BigInt;
use num_integer::Integer;
use num_rational::BigRational;
use num_traits::{One, Signed, ToPrimitive, Zero};
use serde_json::{Value, json};

pub type Int = BigInt;
pub type Rat = BigRational;

#[derive(Clone, Debug, Eq, PartialEq, Ord, PartialOrd, Hash)]
pub struct Point {
    pub x: Int,
    pub y: Int,
}

impl Point {
    pub fn new(x: i64, y: i64) -> Self {
        Self {
            x: x.into(),
            y: y.into(),
        }
    }
    pub fn zero() -> Self {
        Self::new(0, 0)
    }
    pub fn plus(&self, other: &Self) -> Self {
        Self {
            x: &self.x + &other.x,
            y: &self.y + &other.y,
        }
    }
    pub fn minus(&self, other: &Self) -> Self {
        Self {
            x: &self.x - &other.x,
            y: &self.y - &other.y,
        }
    }
}

pub fn integer(value: &Value) -> Result<Int> {
    let number = value
        .as_number()
        .context("coordinate must be integer nanometres")?;
    number
        .to_string()
        .parse()
        .context("coordinate must be integer nanometres")
}

pub fn integer_value(value: &Int) -> Value {
    serde_json::from_str(&value.to_string()).expect("BigInt is a JSON integer")
}

pub fn point(value: &Value) -> Result<Point> {
    let pair = value
        .as_array()
        .context("point must contain two integer coordinates")?;
    if pair.len() != 2 {
        bail!("point must contain two integer coordinates");
    }
    Ok(Point {
        x: integer(&pair[0])?,
        y: integer(&pair[1])?,
    })
}

pub fn point_value(p: &Point) -> Value {
    Value::Array(vec![integer_value(&p.x), integer_value(&p.y)])
}

pub fn rational(value: &Value) -> Result<Rat> {
    let pair = value
        .as_array()
        .context("rational must be numerator and denominator")?;
    if pair.len() != 2 {
        bail!("rational must be numerator and denominator");
    }
    let numerator = integer(&pair[0])?;
    let denominator = integer(&pair[1])?;
    if denominator <= Int::zero() {
        bail!("rational denominator must be positive");
    }
    Ok(Rat::new(numerator, denominator))
}

pub fn rational_value(value: &Rat) -> Value {
    Value::Array(vec![
        integer_value(value.numer()),
        integer_value(value.denom()),
    ])
}

pub fn fraction_record(value: &Rat) -> Value {
    json!({"numerator": integer_value(value.numer()), "denominator": integer_value(value.denom())})
}

pub fn floor(value: &Rat) -> Int {
    value.numer().div_floor(value.denom())
}
pub fn ceil(value: &Rat) -> Int {
    value.numer().div_ceil(value.denom())
}
pub fn round_even(value: &Rat) -> Int {
    let lower = floor(value);
    let remainder = value.numer() - &lower * value.denom();
    match (&remainder * 2_u8).cmp(value.denom()) {
        std::cmp::Ordering::Less => lower,
        std::cmp::Ordering::Greater => lower + 1_u8,
        std::cmp::Ordering::Equal => {
            if lower.is_even() {
                lower
            } else {
                lower + 1_u8
            }
        }
    }
}

pub fn isqrt(value: &Int) -> Int {
    assert!(!value.is_negative(), "square root of a negative integer");
    value.sqrt()
}

pub fn rotate(p: &Point, turns: i64) -> Point {
    match turns.rem_euclid(4) {
        0 => p.clone(),
        1 => Point {
            x: p.y.clone(),
            y: -&p.x,
        },
        2 => Point { x: -&p.x, y: -&p.y },
        _ => Point {
            x: -&p.y,
            y: p.x.clone(),
        },
    }
}

pub fn rotation(value: &Value) -> Result<i64> {
    if let Ok(n) = integer(value) {
        if !(&n % 90_u8).is_zero() {
            bail!("rotation must be a quarter turn");
        }
        return n.to_i64().context("rotation outside native representation");
    }
    let n = value.as_f64().context("rotation must be a quarter turn")?;
    if !n.is_finite() || n % 90.0 != 0.0 || n < i64::MIN as f64 || n >= i64::MAX as f64 {
        bail!("rotation must be a quarter turn");
    }
    Ok(n as i64)
}

pub fn squared_distance(a: &Point, b: &Point) -> Int {
    let x = &a.x - &b.x;
    let y = &a.y - &b.y;
    &x * &x + &y * &y
}

pub fn decimal(value: &Value) -> Result<Rat> {
    let text = if let Some(s) = value.as_str() {
        s.to_owned()
    } else {
        value
            .as_number()
            .context("expected decimal number")?
            .to_string()
    };
    let (mantissa, exponent) = match text.find(['e', 'E']) {
        Some(i) => (
            &text[..i],
            text[i + 1..]
                .parse::<i64>()
                .context("invalid decimal exponent")?,
        ),
        None => (text.as_str(), 0),
    };
    let digits = mantissa.replace('.', "");
    let numerator: Int = digits.parse().context("invalid decimal number")?;
    let places = mantissa
        .split_once('.')
        .map_or(0, |(_, tail)| tail.len() as i64)
        - exponent;
    if places.unsigned_abs() > 100_000 {
        bail!("decimal exponent exceeds work budget");
    }
    let scale = Int::from(10_u8).pow(places.unsigned_abs() as u32);
    Ok(if places >= 0 {
        Rat::new(numerator, scale)
    } else {
        Rat::from_integer(numerator * scale)
    })
}

pub fn nanometres(value: &Value) -> Result<Int> {
    let n = decimal(value)? * Rat::from_integer(1_000_000.into());
    if n.denom() != &Int::one() {
        bail!("millimetres must be exact to 1 nm");
    }
    Ok(n.to_integer())
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn rounding_is_python_ties_even_including_negative_values() {
        for (n, expected) in [(-5, -2), (-3, -2), (-1, 0), (1, 0), (3, 2), (5, 2)] {
            assert_eq!(
                round_even(&Rat::new(n.into(), 2.into())),
                Int::from(expected)
            );
        }
        assert_eq!(integer(&json!("12")).is_err(), true);
        assert_eq!(integer(&json!(true)).is_err(), true);
        let large: Value = serde_json::from_str("184467440737095516160000").unwrap();
        assert_eq!(integer_value(&integer(&large).unwrap()), large);
    }
}
