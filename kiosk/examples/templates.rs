//! Renders every design template in ui/templates/ through the same kiosk
//! states, to target/templates/<template>-<nn>-<screen>.png.
//!
//!     cargo run --example templates --features templates

use interface_stock::backend::Demo;
use interface_stock::state::{Action, Event};
use interface_stock::{Config, Kiosk};
use slint::platform::software_renderer::{MinimalSoftwareWindow, RepaintBufferType};
use slint::platform::{Platform, WindowAdapter};
use slint::{Model, PhysicalSize, Rgb8Pixel};
use std::path::{Path, PathBuf};
use std::rc::Rc;

const W: u32 = 1280;
const H: u32 = 720;

struct Headless(Rc<MinimalSoftwareWindow>);

impl Platform for Headless {
    fn create_window_adapter(&self) -> Result<Rc<dyn WindowAdapter>, slint::PlatformError> {
        Ok(self.0.clone())
    }
}

fn save(win: &MinimalSoftwareWindow, path: &Path) {
    slint::platform::update_timers_and_animations();
    win.request_redraw();
    let mut buf = vec![Rgb8Pixel { r: 0, g: 0, b: 0 }; (W * H) as usize];
    win.draw_if_needed(|r| {
        r.render(&mut buf, W as usize);
    });
    let file = std::fs::File::create(path).unwrap();
    let mut enc = png::Encoder::new(std::io::BufWriter::new(file), W, H);
    enc.set_color(png::ColorType::Rgb);
    enc.set_depth(png::BitDepth::Eight);
    let bytes: Vec<u8> = buf.iter().flat_map(|p| [p.r, p.g, p.b]).collect();
    enc.write_header().unwrap().write_image_data(&bytes).unwrap();
}

fn kiosk() -> Kiosk {
    let snap = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("demo-data");
    let demo = Demo::from_snapshot(&snap).unwrap_or_else(|_| Demo::new());
    Kiosk::new(
        Box::new(demo),
        Config {
            htl_name: "HTL Makerspace".into(),
            iban: "BE71096123456769".into(),
            hidden_categories: vec!["machinegebruik".into()],
        },
    )
}

/// Same story for every template: empty shop, a filled cart, checkout,
/// oath, QR, "did you pay?", cancel, volunteer done, error toast, lookup.
macro_rules! render {
    ($m:ident, $win:expr, $out:expr) => {{
        use $m::*;
        let name = stringify!($m);
        let ui = AppWindow::new().unwrap();
        ui.set_htl_name("HTL MAKERSPACE".into());
        $win.set_size(PhysicalSize::new(W, H));
        ui.show().unwrap();
        wire(&ui, |_| {});
        let mut k = kiosk();
        let mut n = 0;
        let mut shot = |ui: &AppWindow, k: &mut Kiosk, screen: &str, ev: Option<Event>| {
            if let Some(e) = ev {
                k.handle(e, |v| apply(ui, v));
            } else {
                apply(ui, k.view(""));
            }
            n += 1;
            save(&$win, &$out.join(format!("{name}-{n:02}-{screen}.png")));
        };
        shot(&ui, &mut k, "shop-empty", None);
        let pks: Vec<i64> = ui
            .get_all_products()
            .iter()
            .filter(|t| !t.sold_out)
            .map(|t| t.pk as i64)
            .take(4)
            .collect();
        for pk in &pks {
            k.handle(Event::Pick(*pk), |_| {});
        }
        k.handle(Event::Pick(pks[0]), |_| {});
        let fil = ui.get_all_products().iter().find(|t| t.category == "FILAMENT").map(|t| t.pk as i64);
        if let Some(pk) = fil {
            k.handle(Event::Pick(pk), |_| {});
        }
        shot(&ui, &mut k, "shop-cart", None);
        ui.invoke_select_category("DRINKS".into());
        shot(&ui, &mut k, "shop-drinks", None);
        shot(&ui, &mut k, "checkout", Some(Event::Button(Action::Confirm)));
        shot(&ui, &mut k, "oath", Some(Event::Button(Action::Volunteer)));
        k.handle(Event::Button(Action::Volunteer), |_| {});
        shot(&ui, &mut k, "qr", Some(Event::Button(Action::Confirm)));
        shot(&ui, &mut k, "did-you-pay", Some(Event::Button(Action::Confirm)));
        k.handle(Event::Button(Action::Confirm), |_| {});
        k.handle(Event::Pick(pks[1]), |_| {});
        shot(&ui, &mut k, "cancel", Some(Event::Button(Action::Cancel)));
        k.handle(Event::Button(Action::Back), |_| {});
        k.handle(Event::Button(Action::Volunteer), |_| {});
        k.handle(Event::Button(Action::Confirm), |_| {});
        shot(&ui, &mut k, "volunteer-done", Some(Event::Button(Action::Confirm)));
        shot(&ui, &mut k, "error-toast", Some(Event::Scan("NOPE-404".into())));
        apply(&ui, k.view("FIL-PLA-DBLU"));
        n += 1;
        save(&$win, &$out.join(format!("{name}-{n:02}-searching.png")));
        ui.hide().unwrap();
        println!("{name}: {n} screens");
    }};
}

#[allow(unused_macros)]
macro_rules! template {
    ($m:ident, $file:literal) => {
        mod $m {
            include!(concat!(env!("OUT_DIR"), $file));
            interface_stock::define_ui_glue!();
        }
    };
}

template!(beton, "/tpl_beton.rs");
template!(werkbank, "/tpl_werkbank.rs");

fn main() {
    let win = MinimalSoftwareWindow::new(RepaintBufferType::NewBuffer);
    slint::platform::set_platform(Box::new(Headless(win.clone()))).unwrap();
    let out = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("target/templates");
    let _ = std::fs::remove_dir_all(&out);
    std::fs::create_dir_all(&out).unwrap();
    render!(interface_stock, win, out);
    render!(beton, win, out);
    render!(werkbank, win, out);
}
