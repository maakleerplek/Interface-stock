use crate::cart::{Cart, Part};
use std::collections::HashMap;
use std::path::{Path, PathBuf};
use std::sync::Mutex;

/// InvenTree could not be reached, so the stock level is unknown.
#[derive(Debug)]
pub struct StockLookupError;

/// One tile of the product grid.
#[derive(Clone, Debug)]
pub struct CatalogItem {
    pub part: Part,
    pub category: String,
    pub price: f64,
    pub stock: f64,
    /// Local thumbnail file, if there is one.
    pub image: Option<PathBuf>,
}

/// Everything the state machine needs from the outside world. The real
/// implementation talks to InvenTree; `Demo` runs on a PC without a server.
pub trait Backend: Send {
    /// Products for the touch grid.
    fn catalog(&self) -> Vec<CatalogItem>;
    /// The part behind a grid tile.
    fn part(&self, pk: i64) -> Option<Part>;
    fn lookup(&self, barcode: &str) -> Option<Part>;
    /// Units in stock over all of the part's stock items; 0.0 = out of stock.
    fn stock(&self, part_pk: i64) -> Result<f64, StockLookupError>;
    /// Selling price; 0.0 when InvenTree has no sale price for the part.
    fn price(&self, part: &Part) -> f64;
    fn category(&self, part: &Part) -> String;
    /// Remove the cart from stock. Err holds the message for the screen.
    fn checkout(&self, cart: &Cart) -> Result<(), String>;
}

/// (barcode, pk, name, price, category, stock)
type DemoRow = (&'static str, i64, &'static str, f64, &'static str, f64);

/// Built-in fake shop for `--demo` without a snapshot: limited stock, one
/// sold-out item and one very long name, so every tile variant shows up.
pub const DEMO_PARTS: &[DemoRow] = &[
    ("COLA", 1, "Coca-Cola 33cl", 1.20, "drinks", 12.0),
    ("MATE", 2, "Club-Mate 50cl", 2.00, "drinks", 3.0),
    ("ICETEA", 3, "Lipton Ice Tea Peach 33cl", 1.20, "drinks", 8.0),
    ("WATER", 4, "Spa Reine 50cl", 0.80, "drinks", 0.0),
    ("FANTA", 5, "Fanta Orange 33cl", 1.20, "drinks", 6.0),
    ("TWIX", 6, "Twix", 1.00, "snacks", 10.0),
    ("CHIPS", 7, "Lay's Paprika Chips 40g", 1.00, "snacks", 4.0),
    ("KOEK", 8, "Lotus Biscoff Speculoos Original Caramelised Biscuits Family Pack", 0.50, "snacks", 20.0),
    ("FIL-PLA-DBLU", 9, "PLA Filament Dark Blue 1kg", 18.50, "filament", 2.0),
    ("FIL-PETG-ZYEL", 10, "PETG Filament Zinc Yellow 1kg", 21.00, "filament", 1.0),
];

/// One entry of `demo-data/catalog.json` (written by tools/fetch_demo_data.py).
#[derive(Clone, Debug, serde::Deserialize)]
pub struct DemoItem {
    pub pk: i64,
    pub name: String,
    pub category: String,
    pub price: f64,
    pub stock: f64,
    #[serde(default)]
    pub barcodes: Vec<String>,
    #[serde(default)]
    pub image: Option<PathBuf>,
}

/// Shop without a server: checkout only lowers the stock in memory.
pub struct Demo {
    items: Vec<DemoItem>,
    stock: Mutex<HashMap<i64, f64>>,
}

impl Demo {
    /// The built-in fake shop.
    pub fn new() -> Self {
        Self::with_items(
            DEMO_PARTS
                .iter()
                .map(|r| DemoItem {
                    pk: r.1,
                    name: r.2.into(),
                    category: r.4.into(),
                    price: r.3,
                    stock: r.5,
                    barcodes: vec![r.0.into()],
                    image: None,
                })
                .collect(),
        )
    }

    /// Real names, prices, stock and photos from an InvenTree snapshot.
    pub fn from_snapshot(dir: &Path) -> std::io::Result<Self> {
        let text = std::fs::read_to_string(dir.join("catalog.json"))?;
        let mut items: Vec<DemoItem> = serde_json::from_str(&text)?;
        for it in &mut items {
            it.image = it.image.take().map(|p| dir.join(p));
        }
        Ok(Self::with_items(items))
    }

    fn with_items(items: Vec<DemoItem>) -> Self {
        let stock = items.iter().map(|i| (i.pk, i.stock)).collect();
        Demo { items, stock: Mutex::new(stock) }
    }

    fn item(&self, pk: i64) -> Option<&DemoItem> {
        self.items.iter().find(|i| i.pk == pk)
    }

    fn to_part(item: &DemoItem) -> Part {
        Part { pk: item.pk, name: item.name.clone(), ..Default::default() }
    }

    pub fn barcodes(&self) -> impl Iterator<Item = &str> {
        self.items.iter().flat_map(|i| i.barcodes.iter().map(String::as_str))
    }
}

impl Default for Demo {
    fn default() -> Self {
        Self::new()
    }
}

impl Backend for Demo {
    fn catalog(&self) -> Vec<CatalogItem> {
        let stock = self.stock.lock().unwrap();
        self.items
            .iter()
            .map(|i| CatalogItem {
                part: Self::to_part(i),
                category: i.category.clone(),
                price: i.price,
                stock: stock[&i.pk],
                image: i.image.clone(),
            })
            .collect()
    }

    fn part(&self, pk: i64) -> Option<Part> {
        self.item(pk).map(Self::to_part)
    }

    fn lookup(&self, barcode: &str) -> Option<Part> {
        self.items
            .iter()
            .find(|i| i.barcodes.iter().any(|b| b.eq_ignore_ascii_case(barcode)))
            .map(Self::to_part)
    }

    fn stock(&self, part_pk: i64) -> Result<f64, StockLookupError> {
        Ok(*self.stock.lock().unwrap().get(&part_pk).unwrap_or(&0.0))
    }

    fn price(&self, part: &Part) -> f64 {
        self.item(part.pk).map_or(0.0, |i| i.price)
    }

    fn category(&self, part: &Part) -> String {
        self.item(part.pk).map_or("uncategorized".into(), |i| i.category.clone())
    }

    fn checkout(&self, cart: &Cart) -> Result<(), String> {
        if !cfg!(test) {
            std::thread::sleep(std::time::Duration::from_millis(600));
        }
        let mut stock = self.stock.lock().unwrap();
        for (part, qty) in &cart.items {
            let have = *stock.get(&part.pk).unwrap_or(&0.0);
            if have < *qty as f64 {
                return Err(format!("Only {}x {} left", have as u32, part.name));
            }
        }
        for (part, qty) in &cart.items {
            *stock.get_mut(&part.pk).unwrap() -= *qty as f64;
        }
        Ok(())
    }
}
