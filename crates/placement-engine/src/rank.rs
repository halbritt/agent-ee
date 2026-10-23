//! Compact queue keys. Common board coordinates stay inline; larger values and
//! fractional costs retain the same exact ordering without range restrictions.
use std::cmp::Ordering;

use num_traits::ToPrimitive;

use crate::exact::{Int, Rat};

#[derive(Clone, Debug, Eq, PartialEq)]
pub enum Integer {
    Small(i64),
    Large(Box<Int>),
}

impl From<Int> for Integer {
    fn from(value: Int) -> Self {
        match value.to_i64() {
            Some(n) => Self::Small(n),
            None => Self::Large(Box::new(value)),
        }
    }
}

impl From<&Int> for Integer {
    fn from(value: &Int) -> Self {
        match value.to_i64() {
            Some(n) => Self::Small(n),
            None => Self::Large(Box::new(value.clone())),
        }
    }
}

impl Integer {
    pub fn sum(a: &Int, b: &Int) -> Self {
        if let (Some(a), Some(b)) = (a.to_i64(), b.to_i64()) {
            if let Some(sum) = a.checked_add(b) {
                return Self::Small(sum);
            }
        }
        (a + b).into()
    }

    pub fn exact(&self) -> Int {
        match self {
            Self::Small(n) => (*n).into(),
            Self::Large(n) => (**n).clone(),
        }
    }
}

impl Ord for Integer {
    fn cmp(&self, other: &Self) -> Ordering {
        match (self, other) {
            (Self::Small(a), Self::Small(b)) => a.cmp(b),
            (Self::Large(a), Self::Large(b)) => a.cmp(b),
            _ => self.exact().cmp(&other.exact()),
        }
    }
}

impl PartialOrd for Integer {
    fn partial_cmp(&self, other: &Self) -> Option<Ordering> {
        Some(self.cmp(other))
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub enum Fraction {
    Integer(i64),
    Other(Box<Rat>),
}

impl From<Rat> for Fraction {
    fn from(value: Rat) -> Self {
        if value.is_integer() {
            if let Some(n) = value.numer().to_i64() {
                return Self::Integer(n);
            }
        }
        Self::Other(Box::new(value))
    }
}

impl Ord for Fraction {
    fn cmp(&self, other: &Self) -> Ordering {
        match (self, other) {
            (Self::Integer(a), Self::Integer(b)) => a.cmp(b),
            (Self::Other(a), Self::Other(b)) => a.cmp(b),
            (Self::Integer(a), Self::Other(b)) => Rat::from_integer((*a).into()).cmp(b),
            (Self::Other(a), Self::Integer(b)) => (**a).cmp(&Rat::from_integer((*b).into())),
        }
    }
}

impl PartialOrd for Fraction {
    fn partial_cmp(&self, other: &Self) -> Option<Ordering> {
        Some(self.cmp(other))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn compact_keys_preserve_exact_order_across_inline_boundaries() {
        let integers = [
            -(Int::from(1) << 140usize),
            Int::from(i64::MIN) - 1,
            i64::MIN.into(),
            (-1).into(),
            0.into(),
            i64::MAX.into(),
            Int::from(i64::MAX) + 1,
            Int::from(1) << 140usize,
        ];
        for a in &integers {
            for b in &integers {
                assert_eq!(Integer::sum(a, b).exact(), a + b);
                assert_eq!(
                    Integer::from(a.clone()).cmp(&Integer::from(b.clone())),
                    a.cmp(b)
                );
                for denominator in [1, 2, 3] {
                    let x = Rat::new(a.clone(), denominator.into());
                    let y = Rat::new(b.clone(), 2.into());
                    assert_eq!(
                        Fraction::from(x.clone()).cmp(&Fraction::from(y.clone())),
                        x.cmp(&y)
                    );
                }
            }
        }
    }
}
