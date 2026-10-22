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
        "lasercut" => (rgb(0xF6C9B4), rgb(0xA8401A)),
        "cnc" => (rgb(0xC9D3D8), rgb(0x34505E)),
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
    /// A 3D print or machine job: no + on its row.
    pub whole: bool,
    /// The top-up to the minimum per payment: no − or + on its row.
    pub fixed: bool,
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
    /// Filaments for the 3D-print screen: name = colour, category = type, price per gram.
    pub filaments: Vec<TileData>,
    /// Filament types for the 3D-print screen: (a filament's pk, type, "from" price per gram).
    pub filament_types: Vec<(i32, String, String)>,
    /// Machines for the minutes screen: (id, name, price per minute, minimum per payment).
    pub tools: Vec<(String, String, f32, f32)>,
    /// Laser timer sessions to pay: (name, minutes, price).
    pub laser_sessions: Vec<(String, i32, String)>,
    pub cart_categories: String,
    pub done_summary: String,
}

fn build_view(m: &Machine, be: &dyn Backend, cfg: &Config, searching: &str) -> View {
    let price = |p: &cart::Part| be.price(p);
    // + 0.0: an empty f64 sum is -0.0, which prints as "€-0.00".
    let total = m.cart.total(price) + 0.0;
    let mut cats: Vec<String> = m.cart.items.iter().map(|(p, _)| be.category(p)).collect();
    if !m.cart.prints.is_empty() {
        cats.push("3d print".into());
    }
    cats.extend(m.cart.times.iter().map(|t| t.tool.label().to_lowercase()));
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
    let filaments = catalog
        .iter()
        .filter(|c| c.category.eq_ignore_ascii_case("filament"))
        .map(|c| {
            let (light, deep) = category_colors(&c.category);
            // The print screen shows types first, then the colours of the chosen type.
            let (kind, colour) = cart::filament_type(&c.part.name);
            TileData {
                pk: c.part.pk as i32,
                initials: initials(&colour),
                name: colour,
                price: format!("€{:.2}/g", be.print_price(&c.part)),
                light,
                deep,
                sold_out: false,
                category: kind,
                in_cart: 0,
                image: c.image.clone(),
            }
        })
        .collect::<Vec<_>>();
    // One section per type, in name order: a filament of it (for "print-type:<pk>") and its lowest price.
    let mut filament_types: Vec<(i32, String, f64)> = vec![];
    for c in catalog.iter().filter(|c| c.category.eq_ignore_ascii_case("filament")) {
        let (kind, price) = (cart::filament_type(&c.part.name).0, be.print_price(&c.part));
        match filament_types.iter_mut().find(|t| t.1 == kind) {
            Some(t) => t.2 = t.2.min(price),
            None => filament_types.push((c.part.pk as i32, kind, price)),
        }
    }
    filament_types.sort_by(|a, b| a.1.to_lowercase().cmp(&b.1.to_lowercase()));
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
                whole: false,
                fixed: false,
            })
            .chain(m.cart.prints.iter().map(|p| RowData {
                name: if p.material.is_empty() { format!("3D print · {} g", p.grams) } else { format!("3D print · {} · {} g", p.material, p.grams) },
                qty: 1,
                price: format_price(p.price()),
                deep: category_colors("filament").1,
                whole: true,
                fixed: false,
            }))
            .chain(m.cart.times.iter().map(|t| RowData {
                name: format!("{} · {} min", t.tool.label(), t.minutes),
                qty: 1,
                price: format_price(t.price()),
                deep: category_colors(t.tool.label()).1,
                whole: true,
                fixed: false,
            }))
            .chain((m.cart.top_up() > 0.0).then(|| RowData {
                // Topped up, the machine lines add up to exactly the minimum.
                name: format!("Minimum {}", format_price({ let (p, t) = m.cart.machine_prices(); p.iter().chain(&t).sum() })),
                qty: 1,
                price: format_price(m.cart.top_up()),
                deep: category_colors("all").1,
                whole: true,
                fixed: true,
            }))
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
        filaments,
        filament_types: filament_types
            .into_iter()
            .map(|(pk, kind, price)| (pk, kind, format!("from €{price:.2}/g")))
            .collect(),
        tools: cart::Tool::ALL
            .into_iter()
            .map(|t| {
                let (per_minute, minimum) = be.tool_rate(t);
                (t.id().into(), t.label().to_uppercase(), per_minute as f32, minimum as f32)
            })
            .collect(),
        laser_sessions: {
            let per_minute = be.tool_rate(cart::Tool::Laser).0;
            m.open_sessions(be)
                .into_iter()
                .map(|s| (s.name, s.minutes as i32, format_price(s.minutes as f64 * per_minute)))
                .collect()
        },
        cart_categories: cats.join(" / ").to_uppercase(),
        done_summary: format!(
            "{done_units} item{} · {}",
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
    // "print:<filament pk>" with the grams as n, from the 3D-print screen.
    let grams = n.max(0) as u32;
    if let Some(pk) = name.strip_prefix("print-type:") {
        return Some(Action::Print(state::PrintPick::TypeOf(pk.parse().ok()?), grams));
    }
    if name == "print:none" {
        return Some(Action::Print(state::PrintPick::Unknown, grams));
    }
    if let Some(pk) = name.strip_prefix("print:") {
        return Some(Action::Print(state::PrintPick::Filament(pk.parse().ok()?), grams));
    }
    // "session:<n>", a laser timer session from the minutes screen.
    if let Some(n) = name.strip_prefix("session:") {
        return Some(Action::Session(n.parse().ok()?));
    }
    // "time:<machine>" with the minutes as n, from the minutes screen.
    if let Some(id) = name.strip_prefix("time:") {
        return Some(Action::Time(cart::Tool::from_id(id)?, n.max(0) as u32));
    }
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
                .map(|r| CartRow { name: r.name.into(), qty: r.qty, price: r.price.into(), deep: r.deep, whole: r.whole, fixed: r.fixed })
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
            let filaments: Vec<ProductTile> = v
                .filaments
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
                        sold_out: false,
                        category: t.category.into(),
                        in_cart: 0,
                        has_image: image.is_some(),
                        image: image.unwrap_or_default(),
                    }
                })
                .collect();
            ui.set_filaments(ModelRc::new(VecModel::from(filaments)));
            let types: Vec<ProductTile> = v
                .filament_types
                .into_iter()
                .map(|(pk, name, price)| ProductTile {
                    pk,
                    name: name.clone().into(),
                    price: price.into(),
                    initials: Default::default(),
                    light: Default::default(),
                    deep: Default::default(),
                    sold_out: false,
                    category: name.into(),
                    in_cart: 0,
                    has_image: false,
                    image: Default::default(),
                })
                .collect();
            ui.set_filament_types(ModelRc::new(VecModel::from(types)));
            let tools: Vec<ToolRate> = v
                .tools
                .into_iter()
                .map(|(id, name, per_minute, minimum)| ToolRate { id: id.into(), name: name.into(), per_minute, minimum })
                .collect();
            ui.set_tools(ModelRc::new(VecModel::from(tools)));
            let sessions: Vec<LaserSessionRow> = v
                .laser_sessions
                .into_iter()
                .map(|(name, minutes, price)| LaserSessionRow { name: name.into(), minutes, price: price.into() })
                .collect();
            ui.set_laser_sessions(ModelRc::new(VecModel::from(sessions)));
            ui.set_cart_categories(v.cart_categories.into());
            ui.set_done_summary(v.done_summary.into());
            refilter(ui);
            refilter_colours(ui);
        }

        /// The 3D-print screen: only the colours of the chosen filament type.
        pub fn refilter_colours(ui: &AppWindow) {
            use $crate::slint::{Model, ModelRc, VecModel};
            let kind = ui.get_print_type();
            let tiles: Vec<ProductTile> = ui.get_filaments().iter().filter(|t| t.category == kind).collect();
            ui.set_print_colours(ModelRc::new(VecModel::from(tiles)));
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
            let weak = ui.as_weak();
            ui.on_select_print_type(move |kind| {
                if let Some(ui) = weak.upgrade() {
                    ui.set_print_type(kind);
                    refilter_colours(&ui);
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
    /// The catalog as last drawn; it loads (and reloads) in the background.
    catalog: Vec<backend::CatalogItem>,
    /// The laser timer sessions as last drawn; polled in the background.
    laser: Vec<backend::LaserSession>,
}

impl Kiosk {
    pub fn new(be: Box<dyn Backend>, cfg: Config) -> Self {
        let catalog = be.catalog();
        let laser = be.laser_sessions();
        Kiosk { m: Machine::new(Instant::now()), be, cfg, last_scan: None, tv: None, catalog, laser }
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
        // Redraw when the catalog loaded or reloaded, else the grid stays as it was at startup.
        let catalog = self.be.catalog();
        let reloaded = catalog != self.catalog;
        self.catalog = catalog;
        // Same for laser sessions: one paid or deleted elsewhere must leave the screen without a tap.
        let laser = self.be.laser_sessions();
        let laser_changed = laser != self.laser;
        self.laser = laser;
        (changed || reloaded || laser_changed).then(|| self.view(""))
    }
}

define_ui_glue!();
