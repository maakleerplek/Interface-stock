//! The real backend: InvenTree's REST API. Port of the API part of
//! `barcode_inventree.py` (lookups, stock levels, sale prices, stock
//! removal, barcode cache) plus a cached product catalog for the grid.

use crate::backend::{Backend, CatalogItem, StockLookupError};
use crate::cart::{Cart, Part};
use serde_json::{json, Value};
use std::collections::HashMap;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex, RwLock};
use std::time::{Duration, Instant};

/// Catalog, prices and stock are reloaded this often.
const REFRESH: Duration = Duration::from_secs(300);
/// After a failed reload, try again this soon.
const RETRY: Duration = Duration::from_secs(30);
/// A barcode that found nothing is not looked up again for this long.
const MISS_TTL: Duration = Duration::from_secs(60);
/// The barcode cache is written to the SD card at most this often.
const CACHE_DEBOUNCE: Duration = Duration::from_secs(10);

pub struct InvenTreeConfig {
    pub url: String,
    pub token: String,
    /// Host header override, for reaching the server over Tailscale while
    /// its proxy only answers to the LAN address.
    pub host: Option<String>,
    pub cache_file: PathBuf,
    pub image_dir: PathBuf,
    pub htl_name: String,
    /// Tv-Presentation, for the "recent activity" changelog.
    pub tv_url: Option<String>,
    /// Categories that are never shown in the grid.
    pub hidden_categories: Vec<String>,
}

impl InvenTreeConfig {
    pub fn from_env(htl_name: &str, hidden_categories: Vec<String>) -> Self {
        let var = |k: &str| std::env::var(k).ok().filter(|v| !v.is_empty());
        InvenTreeConfig {
            url: var("INVENTREE_URL").unwrap_or_else(|| "http://10.72.1.246".into()).trim_end_matches('/').into(),
            token: var("INVENTREE_TOKEN").unwrap_or_default(),
            host: var("INVENTREE_HOST"),
            cache_file: var("BARCODE_CACHE").unwrap_or_else(|| "barcode_cache.json".into()).into(),
            image_dir: var("IMAGE_CACHE").unwrap_or_else(|| "image_cache".into()).into(),
            htl_name: htl_name.into(),
            tv_url: var("TV_PRESENTATION_URL").map(|u| u.trim_end_matches('/').to_string()),
            hidden_categories,
        }
    }
}

/// `barcode_cache.json`, same format as the Python version: barcode ->
/// the essential fields of the part.
struct BarcodeCache {
    path: PathBuf,
    map: HashMap<String, Value>,
    dirty: bool,
    saved: Instant,
}

impl BarcodeCache {
    fn load(path: PathBuf) -> Self {
        let map = std::fs::read_to_string(&path)
            .ok()
            .and_then(|t| serde_json::from_str(&t).ok())
            .unwrap_or_default();
        BarcodeCache { path, map, dirty: false, saved: Instant::now() - CACHE_DEBOUNCE }
    }

    fn get(&self, code: &str) -> Option<Part> {
        serde_json::from_value(self.map.get(code)?.clone()).ok()
    }

    fn set(&mut self, code: &str, part: &Part) {
        let v = serde_json::to_value(part).unwrap_or(Value::Null);
        if self.map.get(code) != Some(&v) {
            self.map.insert(code.to_string(), v);
            self.dirty = true;
        }
    }

    /// Write via a temp file, so a power cut never leaves half a file.
    fn flush(&mut self, force: bool) {
        if !self.dirty || (!force && self.saved.elapsed() < CACHE_DEBOUNCE) {
            return;
        }
        let tmp = self.path.with_extension("json.tmp");
        let write = || -> std::io::Result<()> {
            use std::io::Write;
            let mut f = std::fs::File::create(&tmp)?;
            f.write_all(serde_json::to_string(&self.map)?.as_bytes())?;
            f.sync_all()?;
            std::fs::rename(&tmp, &self.path)
        };
        match write() {
            Ok(()) => {
                self.dirty = false;
                self.saved = Instant::now();
            }
            Err(e) => eprintln!("[cache] could not write {}: {e}", self.path.display()),
        }
    }
}

#[derive(Default)]
struct Catalog {
    items: Vec<CatalogItem>,
    /// Every active part, also those without a sale price.
    parts: HashMap<i64, (Part, String)>,
    /// IPN / barcode (lowercase) -> part pk.
    by_code: HashMap<String, i64>,
    prices: HashMap<i64, f64>,
    loaded: bool,
}

struct Shared {
    cfg: InvenTreeConfig,
    agent: ureq::Agent,
    catalog: RwLock<Catalog>,
    cache: Mutex<BarcodeCache>,
    misses: Mutex<HashMap<String, Instant>>,
    category_names: Mutex<HashMap<i64, String>>,
    refresh_now: AtomicBool,
}

pub struct InvenTree {
    sh: Arc<Shared>,
}

/// JSON list endpoints answer either a bare list or `{results: [...]}`.
fn results(v: Value) -> Vec<Value> {
    match v {
        Value::Array(a) => a,
        Value::Object(mut o) => match o.remove("results") {
            Some(Value::Array(a)) => a,
            _ => vec![],
        },
        _ => vec![],
    }
}

fn num(v: &Value) -> Option<f64> {
    v.as_f64().or_else(|| v.as_str()?.parse().ok())
}

/// The `/api/stock/remove/` payload. Pure, so it can be tested.
pub fn removal_payload(lines: &[(i64, f64)], volunteer: bool, htl_name: &str) -> Value {
    let notes = if volunteer {
        format!("Volunteer drink via Interface-stock ({htl_name})")
    } else {
        format!("Purchased via Interface-stock ({htl_name})")
    };
    json!({
        "items": lines.iter().map(|(pk, q)| json!({"pk": pk, "quantity": q})).collect::<Vec<_>>(),
        "notes": notes,
    })
}

/// Spread `qty` over a part's stock items, fullest first. None = not enough stock.
pub fn spread(mut stock: Vec<(i64, f64)>, qty: u32) -> Option<Vec<(i64, f64)>> {
    stock.sort_by(|a, b| b.1.total_cmp(&a.1));
    let mut left = qty as f64;
    let mut out = vec![];
    for (pk, have) in stock {
        if left <= 0.0 {
            break;
        }
        let take = left.min(have);
        out.push((pk, take));
        left -= take;
    }
    (left <= 0.0).then_some(out)
}

impl Shared {
    fn get(&self, path_or_url: &str) -> Result<Value, String> {
        let url = if path_or_url.starts_with("http") {
            path_or_url.to_string()
        } else {
            format!("{}{}", self.cfg.url, path_or_url)
        };
        let mut req = self.agent.get(&url).header("Authorization", &format!("Token {}", self.cfg.token));
        if let Some(h) = &self.cfg.host {
            req = req.header("Host", h);
        }
        let mut resp = req.call().map_err(|e| e.to_string())?;
        if resp.status() != 200 {
            return Err(format!("HTTP {}", resp.status()));
        }
        resp.body_mut().read_json().map_err(|e| e.to_string())
    }

    fn post(&self, path: &str, body: &Value, timeout: Duration) -> Result<(u16, Value), String> {
        let mut req = self
            .agent
            .post(format!("{}{}", self.cfg.url, path))
            .header("Authorization", &format!("Token {}", self.cfg.token))
            .config()
            .timeout_global(Some(timeout))
            .build();
        if let Some(h) = &self.cfg.host {
            req = req.header("Host", h);
        }
        let mut resp = req.send_json(body).map_err(|e| e.to_string())?;
        let status = resp.status().as_u16();
        Ok((status, resp.body_mut().read_json().unwrap_or(Value::Null)))
    }

    /// Follow `next` links: past one page the rest used to be missing.
    fn get_all(&self, path: &str) -> Result<Vec<Value>, String> {
        let mut out = vec![];
        let mut next = Some(path.to_string());
        while let Some(p) = next.take() {
            let v = self.get(&p)?;
            next = v.get("next").and_then(Value::as_str).map(|n| {
                // InvenTree builds `next` from its own Host; keep our base URL.
                n.find("/api/").map_or(n.to_string(), |i| n[i..].to_string())
            });
            out.extend(results(v));
        }
        Ok(out)
    }

    fn part_from(&self, v: &Value) -> Option<Part> {
        let mut p: Part = serde_json::from_value(v.clone()).ok()?;
        if p.category_detail.is_none() {
            if let Some(name) = v.get("category_name").and_then(Value::as_str) {
                p.category_detail = Some(json!({ "name": name }));
            }
        }
        Some(p)
    }

    fn fetch_part(&self, pk: i64) -> Option<Part> {
        self.part_from(&self.get(&format!("/api/part/{pk}/?category_detail=true")).ok()?)
    }

    /// Download a thumbnail once into the image cache.
    fn thumbnail(&self, pk: i64, path: &str) -> Option<PathBuf> {
        // Named after InvenTree's file, so a new photo (a new file name) is downloaded again.
        let base = Path::new(path).file_name().and_then(|e| e.to_str()).unwrap_or("image.png");
        let file = self.cfg.image_dir.join(format!("part_{pk}_{base}"));
        if file.exists() {
            return Some(file);
        }
        let url = if path.starts_with('/') { format!("{}{}", self.cfg.url, path) } else { path.to_string() };
        let mut req = self.agent.get(&url).header("Authorization", &format!("Token {}", self.cfg.token));
        if let Some(h) = &self.cfg.host {
            req = req.header("Host", h);
        }
        let bytes = req.call().ok().filter(|r| r.status() == 200)?.body_mut().read_to_vec().ok()?;
        if bytes.is_empty() {
            return None;
        }
        std::fs::create_dir_all(&self.cfg.image_dir).ok()?;
        let tmp = file.with_extension("tmp");
        std::fs::write(&tmp, bytes).ok()?;
        std::fs::rename(&tmp, &file).ok()?;
        Some(file)
    }

    /// Reload sale prices and the part list. Keeps the old data on failure.
    fn refresh(&self) -> Result<(), String> {
        // Cheapest-quantity sale price break per part, as refresh_sale_prices did.
        let mut best: HashMap<i64, (f64, f64)> = HashMap::new();
        for b in self.get_all("/api/part/sale-price/?limit=500")? {
            let (Some(part), Some(qty), Some(price)) =
                (b.get("part").and_then(Value::as_i64), b.get("quantity").and_then(num), b.get("price").and_then(num))
            else {
                continue;
            };
            if best.get(&part).is_none_or(|(q, _)| qty < *q) {
                best.insert(part, (qty, price));
            }
        }
        let prices: HashMap<i64, f64> = best.into_iter().map(|(k, (_, p))| (k, p)).collect();

        let mut cat = Catalog { prices, loaded: true, ..Default::default() };
        for v in self.get_all("/api/part/?active=true&limit=500")? {
            let Some(part) = self.part_from(&v) else { continue };
            let category = v
                .get("category_name")
                .and_then(Value::as_str)
                .unwrap_or("uncategorized")
                .to_lowercase();
            for code in [v.get("IPN"), v.get("barcode")].into_iter().flatten().filter_map(Value::as_str) {
                if !code.is_empty() {
                    cat.by_code.insert(code.to_lowercase(), part.pk);
                }
            }
            if let Some(&price) = cat.prices.get(&part.pk) {
                if !self.cfg.hidden_categories.contains(&category) {
                    let image = part
                        .thumbnail
                        .as_deref()
                        .or(part.image.as_deref())
                        .and_then(|p| self.thumbnail(part.pk, p));
                    cat.items.push(CatalogItem {
                        part: part.clone(),
                        category: category.clone(),
                        price,
                        stock: v.get("in_stock").and_then(num).unwrap_or(0.0),
                        image,
                    });
                }
            }
            cat.parts.insert(part.pk, (part, category));
        }
        cat.items.sort_by(|a, b| (&a.category, &a.part.name).cmp(&(&b.category, &b.part.name)));
        eprintln!("[inventree] {} parts, {} for sale", cat.parts.len(), cat.items.len());
        *self.catalog.write().unwrap() = cat;
        Ok(())
    }

    /// (stock item pk, quantity) of every in-stock item of a part.
    fn stock_items(&self, part_pk: i64) -> Result<Vec<(i64, f64)>, StockLookupError> {
        let v = self
            .get(&format!("/api/stock/?part={part_pk}&in_stock=true"))
            .map_err(|_| StockLookupError)?;
        Ok(results(v)
            .into_iter()
            .filter_map(|s| Some((s.get("pk")?.as_i64()?, s.get("quantity").and_then(num).unwrap_or(0.0))))
            .filter(|s| s.1 > 0.0)
            .collect())
    }

    /// `/api/barcode/`, then the fallbacks of the Python version in
    /// parallel: part barcode / IPN in three letter cases, stock barcode.
    fn lookup_remote(&self, code: &str) -> Option<Part> {
        if let Ok((200, res)) = self.post("/api/barcode/", &json!({ "barcode": code }), Duration::from_secs(5)) {
            let from = |obj: &Value| -> Option<Part> {
                let inst = obj.get("instance").filter(|i| i.is_object()).unwrap_or(obj);
                inst.get("part_detail")
                    .and_then(|d| self.part_from(d))
                    .or_else(|| inst.get("part").and_then(Value::as_i64).and_then(|pk| self.fetch_part(pk)))
                    .or_else(|| inst.get("name").and_then(|_| self.part_from(inst)))
            };
            if let Some(s) = res.get("stockitem") {
                if let Some(p) = from(s) {
                    return Some(p);
                }
            } else if let Some(p) = res.get("part") {
                if let Some(p) = from(p).or_else(|| p.as_i64().and_then(|pk| self.fetch_part(pk))) {
                    return Some(p);
                }
            }
        }

        let mut variants = vec![code.to_string(), code.to_lowercase(), code.to_uppercase()];
        variants.dedup();
        std::thread::scope(|s| {
            let mut tasks = vec![];
            for v in &variants {
                tasks.push(s.spawn(move || {
                    let enc = urlencode(v);
                    let found = self.get(&format!("/api/part/?barcode={enc}&category_detail=true&limit=5")).ok()?;
                    results(found).into_iter().find(|p| {
                        [p.get("barcode"), p.get("IPN")]
                            .into_iter()
                            .flatten()
                            .filter_map(Value::as_str)
                            .any(|c| c.eq_ignore_ascii_case(v))
                    })
                }));
                tasks.push(s.spawn(move || {
                    let enc = urlencode(v);
                    let found = self.get(&format!("/api/part/?IPN={enc}&category_detail=true&limit=5")).ok()?;
                    results(found)
                        .into_iter()
                        .find(|p| p.get("IPN").and_then(Value::as_str).is_some_and(|c| c.eq_ignore_ascii_case(v)))
                }));
            }
            let stock_task = s.spawn(move || -> Option<Part> {
                let enc = urlencode(code);
                let found = self.get(&format!("/api/stock/?barcode={enc}&part_detail=true&limit=5")).ok()?;
                let item = results(found)
                    .into_iter()
                    .find(|i| i.get("barcode").and_then(Value::as_str) == Some(code))?;
                item.get("part_detail")
                    .and_then(|d| self.part_from(d))
                    .or_else(|| self.fetch_part(item.get("part")?.as_i64()?))
            });
            tasks
                .into_iter()
                .filter_map(|t| t.join().ok().flatten())
                .find_map(|v| self.part_from(&v))
                .or_else(|| stock_task.join().ok().flatten())
        })
    }

    fn changelog(&self, action: &str, name: &str, quantity: u32, price: Option<f64>) {
        let Some(tv) = self.cfg.tv_url.clone() else { return };
        let mut body = json!({"action": action, "source": "interface-stock", "item_name": name, "quantity": quantity});
        if let Some(p) = price {
            body["price"] = json!((p * 100.0).round() / 100.0);
        }
        let agent = self.agent.clone();
        std::thread::spawn(move || {
            let _ = agent
                .post(format!("{tv}/api/changelog"))
                .config()
                .timeout_global(Some(Duration::from_secs(3)))
                .build()
                .send_json(&body);
        });
    }
}

fn urlencode(s: &str) -> String {
    s.bytes()
        .map(|b| match b {
            b'A'..=b'Z' | b'a'..=b'z' | b'0'..=b'9' | b'-' | b'_' | b'.' | b'~' => (b as char).to_string(),
            _ => format!("%{b:02X}"),
        })
        .collect()
}

impl InvenTree {
    /// Connects lazily: the catalog loads in the background, so the UI
    /// comes up even while InvenTree is unreachable.
    pub fn start(cfg: InvenTreeConfig) -> Self {
        let tls = ureq::tls::TlsConfig::builder().disable_verification(true).build();
        let agent: ureq::Agent = ureq::Agent::config_builder()
            .timeout_global(Some(Duration::from_secs(5)))
            .http_status_as_error(false)
            .tls_config(tls)
            .build()
            .into();
        let cache = Mutex::new(BarcodeCache::load(cfg.cache_file.clone()));
        let sh = Arc::new(Shared {
            cfg,
            agent,
            catalog: Default::default(),
            cache,
            misses: Default::default(),
            category_names: Default::default(),
            refresh_now: AtomicBool::new(true),
        });
        let bg = sh.clone();
        std::thread::spawn(move || {
            let mut next = Instant::now();
            loop {
                if bg.refresh_now.swap(false, Ordering::Relaxed) || Instant::now() >= next {
                    next = match bg.refresh() {
                        Ok(()) => Instant::now() + REFRESH,
                        Err(e) => {
                            eprintln!("[inventree] refresh failed: {e}");
                            Instant::now() + RETRY
                        }
                    };
                }
                bg.cache.lock().unwrap().flush(false);
                std::thread::sleep(Duration::from_secs(1));
            }
        });
        InvenTree { sh }
    }

    /// Wait until the first catalog load finished (or failed), for --check.
    pub fn wait_loaded(&self, max: Duration) -> bool {
        let t = Instant::now();
        while t.elapsed() < max {
            if self.sh.catalog.read().unwrap().loaded {
                return true;
            }
            std::thread::sleep(Duration::from_millis(200));
        }
        false
    }

    pub fn flush_cache(&self) {
        self.sh.cache.lock().unwrap().flush(true);
    }
}

impl Backend for InvenTree {
    fn catalog(&self) -> Vec<CatalogItem> {
        self.sh.catalog.read().unwrap().items.clone()
    }

    fn part(&self, pk: i64) -> Option<Part> {
        let known = self.sh.catalog.read().unwrap().parts.get(&pk).map(|(p, _)| p.clone());
        known.or_else(|| self.sh.fetch_part(pk))
    }

    fn lookup(&self, barcode: &str) -> Option<Part> {
        let sh = &self.sh;
        // The catalog (reloaded every 5 min) wins over the cache: a code that
        // moved to another part stayed on the old one forever otherwise.
        let in_catalog = {
            let cat = sh.catalog.read().unwrap();
            cat.by_code
                .get(&barcode.to_lowercase())
                .and_then(|pk| cat.parts.get(pk))
                .map(|(p, _)| p.clone())
        };
        if let Some(p) = in_catalog {
            sh.cache.lock().unwrap().set(barcode, &p);
            return Some(p);
        }
        // Not in the catalog: the cache only when the catalog never loaded
        // (InvenTree unreachable since start), else ask InvenTree.
        if !sh.catalog.read().unwrap().loaded {
            if let Some(p) = sh.cache.lock().unwrap().get(barcode) {
                return Some(p);
            }
        }
        if sh.cfg.token.is_empty() {
            return None;
        }
        if sh.misses.lock().unwrap().get(barcode).is_some_and(|t| t.elapsed() < MISS_TTL) {
            return None;
        }
        match sh.lookup_remote(barcode) {
            Some(p) => {
                sh.cache.lock().unwrap().set(barcode, &p);
                Some(p)
            }
            None => {
                let mut m = sh.misses.lock().unwrap();
                if m.len() > 200 {
                    m.clear();
                }
                m.insert(barcode.to_string(), Instant::now());
                None
            }
        }
    }

    fn stock(&self, part_pk: i64) -> Result<f64, StockLookupError> {
        Ok(self.sh.stock_items(part_pk)?.iter().map(|s| s.1).sum())
    }

    /// No fallback to pricing_min/max on purpose: those are cost figures,
    /// and quietly charging the supplier cost is worse than an obvious 0.
    fn price(&self, part: &Part) -> f64 {
        match self.sh.catalog.read().unwrap().prices.get(&part.pk) {
            Some(p) => *p,
            None => {
                eprintln!("[inventree] no sale price for part {} ({})", part.pk, part.name);
                0.0
            }
        }
    }

    fn category(&self, part: &Part) -> String {
        if let Some((_, c)) = self.sh.catalog.read().unwrap().parts.get(&part.pk) {
            return c.clone();
        }
        if let Some(n) = part.category_name() {
            return n;
        }
        let Some(cpk) = part.category else { return "uncategorized".into() };
        if let Some(n) = self.sh.category_names.lock().unwrap().get(&cpk) {
            return n.clone();
        }
        match self.sh.get(&format!("/api/part/category/{cpk}/")).ok().and_then(|v| Some(v.get("name")?.as_str()?.to_lowercase())) {
            Some(n) => {
                self.sh.category_names.lock().unwrap().insert(cpk, n.clone());
                n
            }
            None => "uncategorized".into(),
        }
    }

    fn checkout(&self, cart: &Cart) -> Result<(), String> {
        let sh = &self.sh;
        if sh.cfg.token.is_empty() {
            return Err("INVENTREE_TOKEN not configured".into());
        }
        // A paid line without a sale price would go out for free: price()
        // shows 0 for it on purpose, so refuse it here instead of booking it.
        if !cart.volunteer {
            let prices = &sh.catalog.read().unwrap().prices;
            if let Some((p, _)) = cart.items.iter().find(|(p, _)| !prices.contains_key(&p.pk)) {
                return Err(format!("No price for {}. Ask a volunteer.", p.name));
            }
        }
        // Re-check every line against live stock. InvenTree clamps a removal
        // at zero instead of refusing it, so without this a cart could be
        // charged for stock that is no longer there.
        let mut lines = vec![];
        for (part, qty) in &cart.items {
            let stock = sh.stock_items(part.pk).map_err(|_| "Cannot reach InvenTree to verify stock".to_string())?;
            let available: f64 = stock.iter().map(|s| s.1).sum();
            if available <= 0.0 {
                return Err(format!("{} is out of stock", part.name));
            }
            let spread = spread(stock, *qty).ok_or_else(|| format!("Only {}x {} left", available as u32, part.name))?;
            lines.extend(spread);
        }
        if lines.is_empty() {
            return Err("No stock items found to remove".into());
        }
        let payload = removal_payload(&lines, cart.volunteer, &sh.cfg.htl_name);
        match sh.post("/api/stock/remove/", &payload, Duration::from_secs(10)) {
            Ok((200 | 201, _)) => {}
            Ok((code, _)) => return Err(format!("API error {code}")),
            Err(e) => return Err(e),
        }
        for (part, qty) in &cart.items {
            if cart.volunteer {
                sh.changelog("volunteer", &part.name, *qty, None);
            } else {
                let unit = self.price(part);
                sh.changelog("checkout", &part.name, *qty, (unit > 0.0).then_some(unit * *qty as f64));
            }
        }
        // Show the new stock levels in the grid soon.
        sh.refresh_now.store(true, Ordering::Relaxed);
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn payload_matches_python_version() {
        let p = removal_payload(&[(101, 2.0), (205, 1.0)], false, "HTL Makerspace");
        assert_eq!(
            p,
            json!({"items": [{"pk": 101, "quantity": 2.0}, {"pk": 205, "quantity": 1.0}],
                   "notes": "Purchased via Interface-stock (HTL Makerspace)"})
        );
        let v = removal_payload(&[(7, 1.0)], true, "HTL");
        assert_eq!(v["notes"], "Volunteer drink via Interface-stock (HTL)");
    }

    #[test]
    fn spread_takes_fullest_first_across_items() {
        assert_eq!(spread(vec![(1, 2.0), (2, 3.0)], 4), Some(vec![(2, 3.0), (1, 1.0)]));
        assert_eq!(spread(vec![(1, 2.0), (2, 3.0)], 5), Some(vec![(2, 3.0), (1, 2.0)]));
        assert_eq!(spread(vec![(1, 2.0), (2, 3.0)], 6), None);
        assert_eq!(spread(vec![(1, 5.0)], 1), Some(vec![(1, 1.0)]));
    }

    #[test]
    fn list_or_paged_results() {
        assert_eq!(results(json!([1, 2])).len(), 2);
        assert_eq!(results(json!({"count": 1, "results": [1]})).len(), 1);
        assert!(results(json!({"detail": "nope"})).is_empty());
    }

    #[test]
    fn reads_python_cache_entries() {
        let dir = std::env::temp_dir().join(format!("kiosk-cache-{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        let path = dir.join("barcode_cache.json");
        std::fs::write(
            &path,
            r#"{"5000112545326": {"pk": 8, "name": "Coca Cola", "pricing_min": "1.0", "pricing_max": null,
                "sell_price": null, "thumbnail": "/media/x.png", "image": null,
                "category_detail": null, "category": 2, "_stock_item_pk": 41}}"#,
        )
        .unwrap();
        let mut c = BarcodeCache::load(path.clone());
        let p = c.get("5000112545326").unwrap();
        assert_eq!((p.pk, p.name.as_str()), (8, "Coca Cola"));
        c.set("NEW", &p);
        c.flush(true);
        let again = BarcodeCache::load(path);
        assert!(again.get("NEW").is_some());
        let _ = std::fs::remove_dir_all(dir);
    }

    #[test]
    fn urlencode_keeps_ipn_chars() {
        assert_eq!(urlencode("FIL-PLA_DBLU.1"), "FIL-PLA_DBLU.1");
        assert_eq!(urlencode("a b&c"), "a%20b%26c");
    }
}
