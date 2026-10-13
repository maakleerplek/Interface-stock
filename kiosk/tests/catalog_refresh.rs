//! The InvenTree catalog loads in the background after the window is up:
//! the grid must be redrawn when it arrives, not only after a tap.

use interface_stock::backend::{Backend, CatalogItem, StockLookupError};
use interface_stock::cart::{Cart, Part};
use interface_stock::{Config, Kiosk};
use std::sync::{Arc, Mutex};

struct Slow(Arc<Mutex<Vec<CatalogItem>>>);

impl Backend for Slow {
    fn catalog(&self) -> Vec<CatalogItem> {
        self.0.lock().unwrap().clone()
    }
    fn part(&self, _: i64) -> Option<Part> {
        None
    }
    fn lookup(&self, _: &str) -> Option<Part> {
        None
    }
    fn stock(&self, _: i64) -> Result<f64, StockLookupError> {
        Ok(1.0)
    }
    fn price(&self, _: &Part) -> f64 {
        1.0
    }
    fn category(&self, _: &Part) -> String {
        "drinks".into()
    }
    fn print_price(&self, _: &Part) -> f64 {
        0.1
    }
    fn checkout(&self, _: &Cart) -> Result<(), String> {
        Ok(())
    }
}

#[test]
fn grid_redraws_when_the_catalog_arrives() {
    let shared = Arc::new(Mutex::new(Vec::new()));
    let cfg = Config { htl_name: "HTL".into(), iban: String::new(), hidden_categories: vec![] };
    let mut kiosk = Kiosk::new(Box::new(Slow(shared.clone())), cfg);
    assert!(kiosk.tick().is_none(), "nothing changed yet");

    let part = Part { pk: 1, name: "Cola".into(), ..Default::default() };
    shared.lock().unwrap().push(CatalogItem { part, category: "drinks".into(), price: 1.2, stock: 5.0, image: None });
    assert!(kiosk.tick().is_some(), "catalog loaded: redraw");
    assert!(kiosk.tick().is_none(), "same catalog: no redraw");

    shared.lock().unwrap()[0].stock = 4.0;
    assert!(kiosk.tick().is_some(), "catalog reloaded with new stock: redraw");
}
