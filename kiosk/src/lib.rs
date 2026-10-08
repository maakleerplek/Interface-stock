pub mod backend;
pub mod cart;
pub mod inventree;
pub mod payment;
pub mod scanner;
pub mod state;
pub mod tv;

pub use slint;

use backend::Backend;
use slint::{Color, Rgb8Pixel, SharedPixelBuffer};
use state::{Action, Event, Machine, State, TvPage};
use std::path::{Path, PathBuf};
use std::time::{Duration, Instant};

slint::include_modules!();

/// Two scans of the same label within this window count as one.
const DEBOUNCE: Duration = Duration::from_millis(150);
pub const ALL: &str = "ALL";

pub struct Config {
    pub htl_name: String,
    pub iban: String,
    /// Categories left out of the touch grid (lowercase). Scanning their
    /// barcodes still works.
    pub hidden_categories: Vec<String>,
}

impl Config {
    pub fn from_env() -> Self {
        let var = |keys: &[&str], default: &str| {
            keys.iter()
                .find_map(|k| std::env::var(k).ok().filter(|v| !v.is_empty()))
                .unwrap_or_else(|| default.to_string())
        };
        Config {
            htl_name: var(&["VITE_PAYMENT_NAME", "HTL_NAME"], "HTL Makerspace"),
            iban: var(&["VITE_PAYMENT_IBAN", "HTL_IBAN"], ""),
            hidden_categories: var(&["KIOSK_HIDDEN_CATEGORIES"], "machinegebruik")
                .split(',')
                .map(|c| c.trim().to_lowercase())
                .filter(|c| !c.is_empty())
                .collect(),
        }
    }
}

pub fn format_price(p: f64) -> String {
    if p == 0.0 { "-".into() } else { format!("€{p:.2}") }
}

/// First letters of the first two words that start with a letter, so
/// "Coca-Cola 33cl" gives "CC", not "C3".
fn initials(name: &str) -> String {
    name.split(|c: char| c.is_whitespace() || c == '-')
        .filter_map(|w| w.chars().next())
        .filter(|c| c.is_alphabetic())
        .take(2)
        .collect::<String>()
        .to_uppercase()
}

fn rgb(hex: u32) -> Color {
    Color::from_argb_encoded(0xff00_0000 | hex)
}

/// (light, deep) colour of a category, from the maakleerplek.be agenda:
/// workshop blue, openlab green, herstel amber, jongeren purple.
pub fn category_colors(category: &str) -> (Color, Color) {
    match category.to_lowercase().as_str() {
        "all" => (rgb(0xE3E1DB), rgb(0x5B5C55)),
        "drinks" | "dranken" => (rgb(0xCCDCF4), rgb(0x2F4F85)),
        "filament" => (rgb(0xCCDC9C), rgb(0x4A5C22)),
        "wood" | "hout" => (rgb(0xECB47C), rgb(0x7F4A12)),
        _ => (rgb(0xC4B4E4), rgb(0x4B3A7A)),
    }
}

/// One grid tile as plain data. The Slint structs hold a `slint::Image`,
/// which is not `Send`, so the thumbnail is decoded on the UI thread.
pub struct TileData {
    pub pk: i32,
    pub name: String,
    pub price: String,
    pub initials: String,
    pub light: Color,
    pub deep: Color,
    pub sold_out: bool,
    pub category: String,
    pub in_cart: i32,
    pub image: Option<PathBuf>,
}

pub struct RowData {
    pub name: String,
    pub qty: i32,
    pub price: String,
    pub deep: Color,
}

pub struct CategoryData {
    pub name: String,
    pub light: Color,
    pub deep: Color,
}

/// Everything the window shows, built on the worker thread. Slint models
/// are not `Send`, so this plain struct crosses to the UI thread instead.
pub struct View {
    pub state: State,
    pub message: String,
    pub message_error: bool,
    pub searching: String,
    pub rows: Vec<RowData>,
    pub units: i32,
    pub total: String,
    pub volunteer: bool,
    pub qr: Option<SharedPixelBuffer<Rgb8Pixel>>,
    pub categories: Vec<CategoryData>,
    pub catalog: Vec<TileData>,
    pub cart_categories: String,
    pub done_summary: String,
}

fn build_view(m: &Machine, be: &dyn Backend, cfg: &Config, searching: &str) -> View {
    let price = |p: &cart::Part| be.price(p);
    // + 0.0: an empty f64 sum is -0.0, which prints as "€-0.00".
    let total = m.cart.total(price) + 0.0;
    let mut cats: Vec<String> = m.cart.items.iter().map(|(p, _)| be.category(p)).collect();
    cats.sort();
    cats.dedup();
    let qr = (m.state == State::QrDisplay)
        .then(|| {
            let desc = if cats.is_empty() {
                format!("{} - Purchase", cfg.htl_name)
            } else {
                format!("{}: {}", cfg.htl_name, cats.join(", "))
            };
            payment::qr_pixels(&payment::epc_payload(&cfg.htl_name, &cfg.iban, total, &desc))
        })
        .flatten();

    let mut catalog = be.catalog();
    catalog.retain(|c| !cfg.hidden_categories.contains(&c.category.to_lowercase()));
    let mut categories: Vec<String> = catalog.iter().map(|c| c.category.to_uppercase()).collect();
    categories.sort();
    categories.dedup();
    let tiles = catalog
        .iter()
        .map(|c| {
            let (light, deep) = category_colors(&c.category);
            TileData {
                pk: c.part.pk as i32,
                name: c.part.name.clone(),
                price: format_price(c.price),
                initials: initials(&c.part.name),
                light,
                deep,
                sold_out: c.stock <= 0.0,
                category: c.category.to_uppercase(),
                in_cart: m
                    .cart
                    .items
                    .iter()
                    .filter(|(p, _)| p.pk == c.part.pk)
                    .map(|(_, q)| *q as i32)
                    .sum(),
                image: c.image.clone(),
            }
        })
        .collect();

    let (done_units, done_total) = m.done_summary;
    View {
        state: m.state,
        message: m.message.as_ref().map(|x| x.text.clone()).unwrap_or_default(),
        message_error: m.message.as_ref().is_some_and(|x| x.error),
        searching: searching.into(),
        rows: m
            .cart
            .items
            .iter()
            .map(|(p, q)| RowData {
                name: p.name.clone(),
                qty: *q as i32,
                price: format_price(price(p) * *q as f64),
                deep: category_colors(&be.category(p)).1,
            })
            .collect(),
        units: m.cart.units() as i32,
        total: format!("€{total:.2}"),
        volunteer: m.cart.volunteer,
        qr,
        categories: std::iter::once(ALL.to_string())
            .chain(categories)
            .map(|name| {
                let (light, deep) = category_colors(&name);
                CategoryData { name, light, deep }
            })
            .collect(),
        catalog: tiles,
        cart_categories: cats.join(" / ").to_uppercase(),
        done_summary: format!(
            "{done_units} drink{} · {}",
            if done_units == 1 { "" } else { "s" },
            format_price(done_total)
        ),
    }
}

thread_local! {
    /// Decoded thumbnails. `slint::Image` must stay on the UI thread, and
    /// decoding a file once per tile is enough.
    static THUMBS: std::cell::RefCell<std::collections::HashMap<PathBuf, Option<slint::Image>>> =
        Default::default();
}

#[doc(hidden)]
pub fn thumbnail(path: &Path) -> Option<slint::Image> {
    THUMBS.with(|t| {
        t.borrow_mut()
            .entry(path.to_path_buf())
            .or_insert_with(|| slint::Image::load_from_path(path).ok())
            .clone()
    })
}

pub fn action_from(name: &str, n: i32) -> Option<Action> {
    let n = n.max(0) as usize;
    Some(match name {
        "confirm" => Action::Confirm,
        "cancel" => Action::Cancel,
        "remove" => Action::Remove,
        "volunteer" => Action::Volunteer,
        "back" => Action::Back,
        "inc" => Action::Increment(n),
        "dec" => Action::Decrement(n),
        "page-prev" => Action::PagePrev,
        "page-next" => Action::PageNext,
        _ => return None,
    })
}

/// Defines `apply`, `refilter` and `wire` for the `AppWindow` in scope.
/// Every `.slint` design compiles to its own Rust types, so the app and
/// each design template instantiate this once.
#[macro_export]
macro_rules! define_ui_glue {
    () => {
        pub fn apply(ui: &AppWindow, v: $crate::View) {
            use $crate::slint::{ModelRc, VecModel};
            use $crate::state::State;
            ui.set_screen(match v.state {
                State::Idle | State::Shopping => Screen::Shop,
                State::CancelConfirm => Screen::CancelConfirm,
                State::CheckoutConfirm => Screen::CheckoutConfirm,
                State::Processing => Screen::Processing,
                State::QrDisplay => Screen::Qr,
                State::PayConfirm => Screen::PayConfirm,
                State::VolunteerDone => Screen::VolunteerDone,
            });
            ui.set_message(v.message.into());
            ui.set_message_error(v.message_error);
            ui.set_searching(v.searching.into());
            let rows: Vec<CartRow> = v
                .rows
                .into_iter()
                .map(|r| CartRow { name: r.name.into(), qty: r.qty, price: r.price.into(), deep: r.deep })
                .collect();
            ui.set_rows(ModelRc::new(VecModel::from(rows)));
            ui.set_units(v.units);
            ui.set_total(v.total.into());
            ui.set_volunteer(v.volunteer);
            if let Some(px) = v.qr {
                ui.set_qr($crate::slint::Image::from_rgb8(px));
            }
            if !v.categories.iter().any(|c| c.name == ui.get_category().as_str()) {
                ui.set_category($crate::ALL.into());
            }
            let cats: Vec<Category> = v
                .categories
                .into_iter()
                .map(|c| Category { name: c.name.into(), light: c.light, deep: c.deep })
                .collect();
            ui.set_categories(ModelRc::new(VecModel::from(cats)));
            let tiles: Vec<ProductTile> = v
                .catalog
                .into_iter()
                .map(|t| {
                    let image = t.image.and_then(|p| $crate::thumbnail(&p));
                    ProductTile {
                        pk: t.pk,
                        name: t.name.into(),
                        price: t.price.into(),
                        initials: t.initials.into(),
                        light: t.light,
                        deep: t.deep,
                        sold_out: t.sold_out,
                        category: t.category.into(),
                        in_cart: t.in_cart,
                        has_image: image.is_some(),
                        image: image.unwrap_or_default(),
                    }
                })
                .collect();
            ui.set_all_products(ModelRc::new(VecModel::from(tiles)));
            ui.set_cart_categories(v.cart_categories.into());
            ui.set_done_summary(v.done_summary.into());
            refilter(ui);
        }

        /// Show only the tiles of the selected category.
        pub fn refilter(ui: &AppWindow) {
            use $crate::slint::{Model, ModelRc, VecModel};
            let cat = ui.get_category();
            let tiles: Vec<ProductTile> = ui
                .get_all_products()
                .iter()
                .filter(|t| cat == $crate::ALL || t.category == cat)
                .collect();
            ui.set_products(ModelRc::new(VecModel::from(tiles)));
        }

        /// Connect the window's callbacks to an event sink and the category tabs.
        pub fn wire(ui: &AppWindow, send: impl Fn($crate::state::Event) + Clone + 'static) {
            use $crate::slint::ComponentHandle;
            use $crate::state::Event;
            let s = send.clone();
            ui.on_action(move |name, n| {
                if let Some(a) = $crate::action_from(&name, n) {
                    s(Event::Button(a));
                }
            });
            ui.on_pick(move |pk| send(Event::Pick(pk as i64)));
            let weak = ui.as_weak();
            ui.on_select_category(move |cat| {
                if let Some(ui) = weak.upgrade() {
                    ui.set_category(cat);
                    refilter(&ui);
                }
            });
        }
    };
}

/// State machine plus backend. Runs on the worker thread in the app and
/// on the test thread in the UI checks.
pub struct Kiosk {
    pub m: Machine,
    be: Box<dyn Backend>,
    cfg: Config,
    last_scan: Option<(String, Instant)>,
    tv: Option<tv::Tv>,
}

impl Kiosk {
    pub fn new(be: Box<dyn Backend>, cfg: Config) -> Self {
        Kiosk { m: Machine::new(Instant::now()), be, cfg, last_scan: None, tv: None }
    }

    /// Tell the TV page when someone is shopping, and page it on request.
    pub fn with_tv(mut self, tv: tv::Tv) -> Self {
        tv.set_busy(self.m.busy());
        self.tv = Some(tv);
        self
    }

    fn report_busy(&self) {
        if let Some(tv) = &self.tv {
            tv.set_busy(self.m.busy());
        }
    }

    pub fn view(&self, searching: &str) -> View {
        build_view(&self.m, self.be.as_ref(), &self.cfg, searching)
    }

    /// Handle one event. `show` gets every frame in order: a "searching"
    /// frame before a scan lookup, a "processing" frame before checkout.
    pub fn handle(&mut self, event: Event, mut show: impl FnMut(View)) {
        let now = Instant::now();
        if let Event::Scan(code) = &event {
            if self.last_scan.as_ref().is_some_and(|(c, t)| c == code && now - *t < DEBOUNCE) {
                return;
            }
            self.last_scan = Some((code.clone(), now));
            if !matches!(self.m.state, State::QrDisplay | State::PayConfirm) {
                show(self.view(code));
            }
        }
        if let Some(page) = self.m.handle(self.be.as_ref(), event, now) {
            match &self.tv {
                Some(tv) => tv.page(page),
                None => eprintln!("[tv] page {}", if page == TvPage::Prev { "prev" } else { "next" }),
            }
        }
        self.report_busy();
        if self.m.state == State::Processing {
            show(self.view(""));
            self.m.checkout(self.be.as_ref(), Instant::now());
            self.report_busy();
        }
        show(self.view(""));
    }

    pub fn tick(&mut self) -> Option<View> {
        let changed = self.m.tick(Instant::now());
        if changed {
            self.report_busy();
        }
        changed.then(|| self.view(""))
    }
}

define_ui_glue!();
