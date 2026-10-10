use serde::{Deserialize, Serialize};
use serde_json::Value;

/// One InvenTree part, in the same shape as an entry of `barcode_cache.json`.
#[derive(Clone, Debug, Default, Serialize, Deserialize)]
pub struct Part {
    pub pk: i64,
    #[serde(default)]
    pub name: String,
    #[serde(default)]
    pub thumbnail: Option<String>,
    #[serde(default)]
    pub image: Option<String>,
    #[serde(default)]
    pub category: Option<i64>,
    #[serde(default)]
    pub category_detail: Option<Value>,
}

impl Part {
    /// Category name from `category_detail`, if the lookup returned it.
    pub fn category_name(&self) -> Option<String> {
        self.category_detail
            .as_ref()?
            .get("name")?
            .as_str()
            .map(str::to_lowercase)
    }
}

#[derive(Clone, Debug, Default)]
pub struct Cart {
    pub items: Vec<(Part, u32)>,
    /// The whole cart is a free volunteer drink.
    pub volunteer: bool,
}

impl Cart {
    /// One line per part: checkout spreads it over the part's stock items.
    pub fn add(&mut self, part: Part) {
        match self.items.iter_mut().find(|(p, _)| p.pk == part.pk) {
            Some((_, qty)) => *qty += 1,
            None => self.items.push((part, 1)),
        }
    }

    /// Units of this part already in the cart.
    pub fn quantity_of(&self, part: &Part) -> u32 {
        self.items
            .iter()
            .find(|(p, _)| p.pk == part.pk)
            .map_or(0, |(_, q)| *q)
    }

    /// Take one unit off the line at `index`; drops the line at zero.
    pub fn remove_one(&mut self, index: usize) -> Option<Part> {
        let (part, qty) = self.items.get_mut(index)?;
        let part = part.clone();
        if *qty > 1 {
            *qty -= 1;
        } else {
            self.items.remove(index);
        }
        Some(part)
    }

    pub fn remove_last(&mut self) -> Option<Part> {
        self.remove_one(self.items.len().checked_sub(1)?)
    }

    pub fn units(&self) -> u32 {
        self.items.iter().map(|(_, q)| q).sum()
    }

    pub fn total(&self, price: impl Fn(&Part) -> f64) -> f64 {
        self.items.iter().map(|(p, q)| price(p) * *q as f64).sum()
    }

    pub fn clear(&mut self) {
        self.items.clear();
        self.volunteer = false;
    }

    pub fn is_empty(&self) -> bool {
        self.items.is_empty()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn part(pk: i64) -> Part {
        Part { pk, name: format!("P{pk}"), ..Default::default() }
    }

    #[test]
    fn same_part_stacks() {
        let mut c = Cart::default();
        c.add(part(1));
        c.add(part(1));
        c.add(part(2));
        assert_eq!(c.items.len(), 2);
        assert_eq!(c.quantity_of(&part(1)), 2);
    }

    #[test]
    fn remove_last_takes_one_unit() {
        let mut c = Cart::default();
        c.add(part(1));
        c.add(part(1));
        c.remove_last();
        assert_eq!(c.units(), 1);
        c.remove_last();
        assert!(c.is_empty());
        assert!(c.remove_last().is_none());
    }
}
