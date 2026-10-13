use serde::{Deserialize, Serialize};
use serde_json::Value;

/// One InvenTree part, in the same shape as an entry of `barcode_cache.json`.
#[derive(Clone, Debug, Default, PartialEq, Serialize, Deserialize)]
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

/// A 3D print paid at the till: the filament, the grams and its price per gram.
#[derive(Clone, Debug)]
pub struct Print {
    pub part: Part,
    pub grams: u32,
    pub per_gram: f64,
}

impl Print {
    pub fn price(&self) -> f64 {
        self.grams as f64 * self.per_gram
    }
}

#[derive(Clone, Debug, Default)]
pub struct Cart {
    pub items: Vec<(Part, u32)>,
    /// Shown after `items` in the cart; a cart row index past `items` is a print.
    pub prints: Vec<Print>,
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

    /// Take one unit off the line at `index`; drops the line at zero. A print
    /// row (past the items) goes as a whole.
    pub fn remove_one(&mut self, index: usize) -> Option<Part> {
        if let Some(i) = index.checked_sub(self.items.len()) {
            return (i < self.prints.len()).then(|| self.prints.remove(i).part);
        }
        let (part, qty) = self.items.get_mut(index)?;
        let part = part.clone();
        if *qty > 1 {
            *qty -= 1;
        } else {
            self.items.remove(index);
        }
        Some(part)
    }

    /// The last row of the cart: a print if there is one, else one unit of the last item.
    pub fn remove_last(&mut self) -> Option<Part> {
        if let Some(p) = self.prints.pop() {
            return Some(p.part);
        }
        self.remove_one(self.items.len().checked_sub(1)?)
    }

    /// Item units plus one per print.
    pub fn units(&self) -> u32 {
        self.items.iter().map(|(_, q)| q).sum::<u32>() + self.prints.len() as u32
    }

    pub fn total(&self, price: impl Fn(&Part) -> f64) -> f64 {
        self.items.iter().map(|(p, q)| price(p) * *q as f64).sum::<f64>() + self.prints.iter().map(Print::price).sum::<f64>()
    }

    pub fn clear(&mut self) {
        self.items.clear();
        self.prints.clear();
        self.volunteer = false;
    }

    pub fn is_empty(&self) -> bool {
        self.items.is_empty() && self.prints.is_empty()
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
    fn prints_add_to_the_total_and_are_removed_as_a_row() {
        let mut c = Cart::default();
        c.add(part(1));
        c.prints.push(Print { part: part(9), grams: 50, per_gram: 0.12 });
        assert!((c.total(|_| 2.0) - 8.0).abs() < 1e-9);   // 2.00 + 50 g × 0.12
        assert_eq!(c.units(), 2);
        assert_eq!(c.remove_one(1).map(|p| p.pk), Some(9));   // row 1 is the print
        assert!(c.prints.is_empty());
        assert!(c.remove_one(5).is_none());
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
