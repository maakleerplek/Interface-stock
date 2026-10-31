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

/// A 3D print paid at the till. `material` is booked as the line's description, for the
/// analytics per filament: the filament ("PLA White (1 kg)"), only its type ("PLA"), or "".
#[derive(Clone, Debug)]
pub struct Print {
    pub material: String,
    pub grams: u32,
    pub per_gram: f64,
    pub minimum: f64,
}

/// Type and colour from a filament's InvenTree name, "[Refill] TYPE [hardness] Colour (weight)":
/// "Refill PLA Grey Beige (750 g)" is ("PLA", "Grey Beige (refill)"), "rTPU 95A Traffic White (1 kg)"
/// is ("rTPU 95A", "Traffic White"). A brand in front is dropped: "EasyFil ePLA Traffic Red (1 kg)"
/// is ("PLA", "Traffic Red"), ePLA prints as PLA. A new spool or material shows up by itself.
pub fn filament_type(name: &str) -> (String, String) {
    let bare = name.split(" (").next().unwrap_or(name);
    let mut words: Vec<&str> = bare.split_whitespace().collect();
    let refill = words.first().is_some_and(|w| w.eq_ignore_ascii_case("refill"));
    if refill {
        words.remove(0);
    }
    if words.first().is_some_and(|w| w.eq_ignore_ascii_case("easyfil")) {
        words.remove(0);
    }
    let mut kind = words.first().map(|w| if w.eq_ignore_ascii_case("epla") { "PLA".to_string() } else { w.to_string() }).unwrap_or_default();
    let mut rest = words.get(1..).unwrap_or_default().to_vec();
    // TPU hardness ("90A") is part of the type: 90A and 95A print differently.
    if rest.first().is_some_and(|w| w.len() > 1 && w.ends_with(['A', 'a']) && w[..w.len() - 1].chars().all(|c| c.is_ascii_digit())) {
        kind = format!("{kind} {}", rest.remove(0));
    }
    let colour = rest.join(" ");
    (kind, if refill { format!("{colour} (refill)") } else { colour })
}

impl Print {
    pub fn price(&self) -> f64 {
        self.grams as f64 * self.per_gram
    }
}

/// A machine paid per minute at the till.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Tool {
    Laser,
    Cnc,
}

impl Tool {
    pub const ALL: [Tool; 2] = [Tool::Laser, Tool::Cnc];

    /// Its name in touch actions: "time:laser".
    pub fn id(self) -> &'static str {
        match self {
            Tool::Laser => "laser",
            Tool::Cnc => "cnc",
        }
    }

    pub fn from_id(id: &str) -> Option<Tool> {
        Tool::ALL.into_iter().find(|t| t.id() == id)
    }

    pub fn label(self) -> &'static str {
        match self {
            Tool::Laser => "Lasercut",
            Tool::Cnc => "CNC",
        }
    }

    /// Sales-order line reference, the same as the stock app books: the analytics group on it.
    pub fn reference(self) -> &'static str {
        match self {
            Tool::Laser => "Lasertime",
            Tool::Cnc => "CNC time",
        }
    }

    /// Its virtual part in InvenTree's machine category, which also holds the TV's price list.
    pub fn part_name(self) -> &'static str {
        match self {
            Tool::Laser => "Lasercutter",
            Tool::Cnc => "CNC",
        }
    }
}

/// Machine time paid at the till: minutes at a price per minute. The minimum counts per payment, see `Cart::top_up`.
#[derive(Clone, Debug)]
pub struct ToolTime {
    pub tool: Tool,
    pub minutes: u32,
    pub per_minute: f64,
    pub minimum: f64,
    /// The laser timer's cuts this time comes from: (id, name). Marked paid after the sale.
    pub sessions: Vec<(String, String)>,
    /// Their laser seconds together; `minutes` is this rounded up once. 0 for typed-in time.
    pub seconds: f64,
}

impl ToolTime {
    pub fn price(&self) -> f64 {
        self.minutes as f64 * self.per_minute
    }
}

fn print_name(p: &Print) -> String {
    if p.material.is_empty() { "3D print".into() } else { p.material.clone() }
}

#[derive(Clone, Debug, Default)]
pub struct Cart {
    pub items: Vec<(Part, u32)>,
    /// Shown after `items` in the cart; a cart row index past `items` is a print.
    pub prints: Vec<Print>,
    /// Machine time, shown after the prints.
    pub times: Vec<ToolTime>,
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
    /// or machine-time row (past the items) goes as a whole. Returns what left.
    pub fn remove_one(&mut self, index: usize) -> Option<String> {
        if let Some(i) = index.checked_sub(self.items.len()) {
            if i < self.prints.len() {
                return Some(print_name(&self.prints.remove(i)));
            }
            let i = i - self.prints.len();
            return (i < self.times.len()).then(|| self.times.remove(i).tool.label().to_string());
        }
        let (part, qty) = self.items.get_mut(index)?;
        let name = part.name.clone();
        if *qty > 1 {
            *qty -= 1;
        } else {
            self.items.remove(index);
        }
        Some(name)
    }

    /// The last row of the cart: machine time, else a print, else one unit of the last item.
    pub fn remove_last(&mut self) -> Option<String> {
        if let Some(t) = self.times.pop() {
            return Some(t.tool.label().into());
        }
        if let Some(p) = self.prints.pop() {
            return Some(print_name(&p));
        }
        self.remove_one(self.items.len().checked_sub(1)?)
    }

    /// Item units plus one per print and per machine job.
    pub fn units(&self) -> u32 {
        self.items.iter().map(|(_, q)| q).sum::<u32>() + (self.prints.len() + self.times.len()) as u32
    }

    pub fn total(&self, price: impl Fn(&Part) -> f64) -> f64 {
        self.items.iter().map(|(p, q)| price(p) * *q as f64).sum::<f64>()
            + self.prints.iter().map(Print::price).sum::<f64>()
            + self.times.iter().map(ToolTime::price).sum::<f64>()
            + self.top_up()
    }

    /// What prints and machine time together miss to reach the minimum per payment
    /// (bank costs): the highest minimum among them. Drinks and products do not count.
    pub fn top_up(&self) -> f64 {
        let minimum = self.prints.iter().map(|p| p.minimum).chain(self.times.iter().map(|t| t.minimum)).fold(0.0, f64::max);
        let raw = self.prints.iter().map(Print::price).sum::<f64>() + self.times.iter().map(ToolTime::price).sum::<f64>();
        (minimum - raw).max(0.0)
    }

    /// Price of each print and each machine job with the top-up spread over them in
    /// proportion to their price, so the analytics give each machine its share.
    pub fn machine_prices(&self) -> (Vec<f64>, Vec<f64>) {
        let raw: Vec<f64> = self.prints.iter().map(Print::price).chain(self.times.iter().map(ToolTime::price)).collect();
        let (sum, top, n) = (raw.iter().sum::<f64>(), self.top_up(), raw.len() as f64);
        let mut all = raw.iter().map(|r| r + if sum > 0.0 { top * r / sum } else { top / n });
        let prints = all.by_ref().take(self.prints.len()).collect();
        (prints, all.collect())
    }

    pub fn clear(&mut self) {
        self.items.clear();
        self.prints.clear();
        self.times.clear();
        self.volunteer = false;
    }

    pub fn is_empty(&self) -> bool {
        self.items.is_empty() && !self.always_paid()
    }

    /// 3D prints and machine time are never a free volunteer drink.
    pub fn always_paid(&self) -> bool {
        !self.prints.is_empty() || !self.times.is_empty()
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
        c.prints.push(Print { material: "P9".into(), grams: 50, per_gram: 0.12, minimum: 5.0 });
        assert!((c.total(|_| 2.0) - 8.0).abs() < 1e-9);   // 2.00 + 50 g × 0.12
        assert_eq!(c.units(), 2);
        assert_eq!(c.remove_one(1).as_deref(), Some("P9"));   // row 1 is the print
        assert!(c.prints.is_empty());
        assert!(c.remove_one(5).is_none());
    }

    fn job(minutes: u32) -> ToolTime {
        ToolTime { tool: Tool::Laser, minutes, per_minute: 0.5, minimum: 5.0, sessions: vec![], seconds: 0.0 }
    }

    fn print(grams: u32) -> Print {
        Print { material: "P9".into(), grams, per_gram: 0.1, minimum: 5.0 }
    }

    #[test]
    fn machine_time_is_raised_to_the_minimum_per_payment() {
        let mut c = Cart::default();
        c.add(part(1));
        c.prints.push(print(10));
        c.times.push(job(4));
        assert_eq!(c.units(), 3);
        assert!((c.total(|_| 2.0) - 7.0).abs() < 1e-9);   // drink 2 + €1 print + €2 laser raised to €5 together
        assert_eq!(c.remove_one(2).as_deref(), Some("Lasercut"));   // row 2 is the job
        assert!(c.times.is_empty() && c.always_paid());
    }

    #[test]
    fn the_minimum_counts_once_per_payment() {
        let total = |prints: Vec<Print>, times: Vec<ToolTime>| Cart { prints, times, ..Default::default() }.total(|_| 0.0);
        assert!((total(vec![print(30)], vec![job(8)]) - 7.0).abs() < 1e-9);   // €3 print + €4 laser
        assert!((total(vec![], vec![job(4), job(4)]) - 5.0).abs() < 1e-9);    // two €2 jobs: €5, not €10
        assert!((total(vec![], vec![job(12)]) - 6.0).abs() < 1e-9);           // above the minimum: as is
        assert!((total(vec![print(30)], vec![]) - 5.0).abs() < 1e-9);         // a print alone too
    }

    #[test]
    fn the_top_up_is_spread_in_proportion() {
        let c = Cart { prints: vec![print(10)], times: vec![job(4)], ..Default::default() };   // €1 + €2
        let (p, t) = c.machine_prices();
        assert!((p[0] - 5.0 / 3.0).abs() < 1e-9 && (t[0] - 10.0 / 3.0).abs() < 1e-9);
    }

    #[test]
    fn filament_type_and_colour_from_the_name() {
        let t = |n: &str| filament_type(n);
        assert_eq!(t("PLA Matt Black (1 kg)"), ("PLA".into(), "Matt Black".into()));
        assert_eq!(t("Refill PLA Grey Beige (750 g)"), ("PLA".into(), "Grey Beige (refill)".into()));
        assert_eq!(t("Refill PETG Zinc Yellow (750 g)"), ("PETG".into(), "Zinc Yellow (refill)".into()));
        assert_eq!(t("rTPU 95A Traffic White (1 kg)"), ("rTPU 95A".into(), "Traffic White".into()));
        assert_eq!(t("PLA White"), ("PLA".into(), "White".into()));
        assert_eq!(t("EasyFil ePLA Traffic Red (1 kg)"), ("PLA".into(), "Traffic Red".into()));
    }

    #[test]
    fn tool_ids_round_trip() {
        for t in Tool::ALL {
            assert_eq!(Tool::from_id(t.id()), Some(t));
        }
        assert_eq!(Tool::from_id("drill"), None);
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
