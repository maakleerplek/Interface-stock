//! Headless UI check: taps through the real window like a finger would,
//! checks the result after every tap, checks every visible button for
//! size/bounds/overlap, and writes a screenshot of each step to
//! `target/screens/`.

use i_slint_backend_testing::{AccessibleRole, ElementHandle, ElementQuery};
use interface_stock::backend::Demo;
use interface_stock::state::{Event, State};
use interface_stock::{apply, wire, AppWindow, Config, Kiosk, Screen};
use slint::platform::software_renderer::{MinimalSoftwareWindow, RepaintBufferType};
use slint::platform::{Platform, PointerEventButton, WindowAdapter, WindowEvent};
use slint::{ComponentHandle, LogicalPosition, Model, PhysicalSize, Rgb8Pixel};
use std::cell::RefCell;
use std::path::PathBuf;
use std::rc::Rc;

const W: u32 = 1280;
const H: u32 = 720;
/// Smallest touch target a fingertip hits reliably.
const MIN_TOUCH: f32 = 56.0;

struct Headless(Rc<MinimalSoftwareWindow>);

impl Platform for Headless {
    fn create_window_adapter(&self) -> Result<Rc<dyn WindowAdapter>, slint::PlatformError> {
        Ok(self.0.clone())
    }
}

struct Rig {
    ui: AppWindow,
    win: Rc<MinimalSoftwareWindow>,
    kiosk: Kiosk,
    queue: Rc<RefCell<Vec<Event>>>,
    step: usize,
    out: PathBuf,
    problems: Vec<String>,
}

impl Rig {
    fn new() -> Self {
        let win = MinimalSoftwareWindow::new(RepaintBufferType::NewBuffer);
        slint::platform::set_platform(Box::new(Headless(win.clone()))).unwrap();
        let ui = AppWindow::new().unwrap();
        ui.set_htl_name("HTL MAKERSPACE".into());
        win.set_size(PhysicalSize::new(W, H));
        ui.show().unwrap();
        let queue = Rc::new(RefCell::new(Vec::new()));
        let q = queue.clone();
        wire(&ui, move |e| q.borrow_mut().push(e));
        let kiosk = Kiosk::new(
            Box::new(Demo::new()),
            Config { htl_name: "HTL Makerspace".into(), iban: "BE71096123456769".into(), hidden_categories: vec!["machinegebruik".into()] },
        );
        apply(&ui, kiosk.view(""));
        let out = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("target/screens");
        let _ = std::fs::remove_dir_all(&out);
        std::fs::create_dir_all(&out).unwrap();
        Rig { ui, win, kiosk, queue, step: 0, out, problems: vec![] }
    }

    /// Feed queued UI events to the kiosk, the way the worker thread does.
    fn pump(&mut self) {
        loop {
            let events: Vec<Event> = self.queue.borrow_mut().drain(..).collect();
            if events.is_empty() {
                break;
            }
            for e in events {
                let ui = &self.ui;
                self.kiosk.handle(e, |v| apply(ui, v));
            }
        }
        slint::platform::update_timers_and_animations();
    }

    fn scan(&mut self, code: &str) {
        let ui = &self.ui;
        self.kiosk.handle(Event::from_barcode(code), |v| apply(ui, v));
    }

    fn find(&self, label: &str) -> Option<ElementHandle> {
        ElementHandle::find_by_accessible_label(&self.ui, label).next()
    }

    /// Press and release at the element's centre, as a finger would. The
    /// event goes through normal hit testing, so anything on top gets it.
    fn tap(&mut self, label: &str) {
        let Some(el) = self.find(label) else {
            let all: Vec<_> = ElementQuery::from_root(&self.ui)
                .match_descendants()
                .match_accessible_role(AccessibleRole::Button)
                .find_all()
                .iter()
                .map(|b| b.accessible_label())
                .collect();
            panic!("step {}: no element labelled {label:?}; buttons: {all:?}", self.step);
        };
        let (p, s) = (el.absolute_position(), el.size());
        let c = LogicalPosition::new(p.x + s.width / 2.0, p.y + s.height / 2.0);
        assert!(
            c.x >= 0.0 && c.y >= 0.0 && c.x < W as f32 && c.y < H as f32,
            "step {}: {label:?} is off screen at {c:?}",
            self.step
        );
        let w = self.win.window();
        let button = PointerEventButton::Left;
        w.dispatch_event(WindowEvent::PointerMoved { position: c });
        w.dispatch_event(WindowEvent::PointerPressed { position: c, button });
        std::thread::sleep(std::time::Duration::from_millis(20));
        slint::platform::update_timers_and_animations();
        w.dispatch_event(WindowEvent::PointerReleased { position: c, button });
        w.dispatch_event(WindowEvent::PointerExited);
        self.pump();
    }

    /// Every button on screen must be big enough, fully inside the window
    /// and not overlap another one. Scrolling lists are only checked
    /// against their own row, since rows below the fold are legitimately
    /// outside the window.
    fn check_buttons(&mut self, name: &str) {
        let buttons: Vec<_> = ElementQuery::from_root(&self.ui)
            .match_descendants()
            .match_accessible_role(AccessibleRole::Button)
            .find_all()
            .into_iter()
            .filter(|b| b.computed_opacity() > 0.0)
            .map(|b| {
                let (p, s) = (b.absolute_position(), b.size());
                (b.accessible_label().unwrap_or_default().to_string(), p.x, p.y, s.width, s.height)
            })
            .collect();
        // The query reports a component and its root element separately.
        let mut buttons = buttons;
        buttons.dedup_by(|a, b| a == b);
        buttons.sort_by(|a, b| a.partial_cmp(b).unwrap());
        buttons.dedup_by(|a, b| a == b);
        for (label, x, y, w, h) in &buttons {
            if *w < MIN_TOUCH || *h < MIN_TOUCH {
                self.problems.push(format!("{name}: {label:?} is {w}×{h}, below {MIN_TOUCH}"));
            }
            let visible_top = *y < H as f32;
            if visible_top && (*x < 0.0 || x + w > W as f32 + 0.5) {
                self.problems.push(format!("{name}: {label:?} sticks out sideways ({x}+{w})"));
            }
        }
        for (i, a) in buttons.iter().enumerate() {
            for b in &buttons[i + 1..] {
                let overlap = a.1 < b.1 + b.3 - 0.5
                    && b.1 < a.1 + a.3 - 0.5
                    && a.2 < b.2 + b.4 - 0.5
                    && b.2 < a.2 + a.4 - 0.5;
                if overlap && a.2 < H as f32 && b.2 < H as f32 {
                    self.problems.push(format!("{name}: {:?} overlaps {:?}", a.0, b.0));
                }
            }
        }
    }

    fn shot(&mut self, name: &str) {
        self.step += 1;
        let name = format!("{:02}-{name}", self.step);
        self.check_buttons(&name);
        slint::platform::update_timers_and_animations();
        self.win.request_redraw();
        let mut buf = vec![Rgb8Pixel { r: 0, g: 0, b: 0 }; (W * H) as usize];
        self.win.draw_if_needed(|r| {
            r.render(&mut buf, W as usize);
        });
        let file = std::fs::File::create(self.out.join(format!("{name}.png"))).unwrap();
        let mut enc = png::Encoder::new(std::io::BufWriter::new(file), W, H);
        enc.set_color(png::ColorType::Rgb);
        enc.set_depth(png::BitDepth::Eight);
        let bytes: Vec<u8> = buf.iter().flat_map(|p| [p.r, p.g, p.b]).collect();
        enc.write_header().unwrap().write_image_data(&bytes).unwrap();
    }

    fn units(&self) -> i32 {
        self.ui.get_units()
    }
}

#[test]
fn tap_through_every_screen() {
    let mut r = Rig::new();

    // Empty shop: cart buttons disabled, so tapping them does nothing.
    r.shot("shop-empty");
    r.tap("CHECKOUT  →");
    r.tap("CANCEL");
    r.tap("VOLUNTEER DRINK");
    assert_eq!(r.ui.get_screen(), Screen::Shop);
    assert_eq!(r.kiosk.m.state, State::Idle);

    // Products by touch.
    r.tap("Coca-Cola 33cl");
    assert_eq!(r.units(), 1);
    assert_eq!(r.kiosk.m.state, State::Shopping);
    r.shot("one-item");
    for _ in 0..4 {
        r.tap("Club-Mate 50cl");
    }
    assert_eq!(r.units(), 4, "Club-Mate has 3 in stock");
    assert!(r.ui.get_message_error());
    r.shot("stock-limit-toast");

    // Sold-out tile ignores taps.
    r.tap("Spa Reine 50cl");
    assert_eq!(r.units(), 4);

    // Category tabs filter the grid.
    r.tap("SNACKS");
    assert_eq!(r.ui.get_products().row_count(), 3);
    r.tap("Lotus Biscoff Speculoos Original Caramelised Biscuits Family Pack");
    r.tap("Twix");
    r.tap("Lay's Paprika Chips 40g");
    r.shot("snacks-long-name");
    r.tap("FILAMENT");
    r.tap("PLA Filament Dark Blue 1kg");
    r.tap("PETG Filament Zinc Yellow 1kg");
    r.tap("DRINKS");
    r.tap("Lipton Ice Tea Peach 33cl");
    r.shot("full-cart");
    assert_eq!(r.ui.get_rows().row_count(), 8);

    // + and − on a cart line.
    let before = r.units();
    r.tap("plus Coca-Cola 33cl");
    assert_eq!(r.units(), before + 1);
    r.tap("minus Coca-Cola 33cl");
    r.tap("minus Coca-Cola 33cl");
    assert_eq!(r.units(), before - 1, "last − removes the line");
    r.tap("ALL");

    // TV page buttons leave the cart alone.
    r.tap("TV next page");
    r.tap("TV previous page");
    assert_eq!(r.units(), before - 1);

    // Cancel needs confirmation.
    r.tap("CANCEL");
    assert_eq!(r.ui.get_screen(), Screen::CancelConfirm);
    r.shot("cancel-confirm");
    r.tap("← KEEP SHOPPING");
    assert_eq!(r.ui.get_screen(), Screen::Shop);
    assert_eq!(r.units(), before - 1);

    // Checkout, back, volunteer toggle on the confirm screen, then pay.
    r.tap("CHECKOUT  →");
    assert_eq!(r.ui.get_screen(), Screen::CheckoutConfirm);
    r.shot("checkout-confirm");
    r.tap("← BACK");
    assert_eq!(r.ui.get_screen(), Screen::Shop);
    r.tap("CHECKOUT  →");
    r.tap("VOLUNTEER");
    assert!(r.ui.get_volunteer());
    r.shot("volunteer-oath");
    r.tap("✓ VOLUNTEER");
    assert!(!r.ui.get_volunteer());
    r.tap("CONFIRM & PAY →");
    assert_eq!(r.ui.get_screen(), Screen::Qr);
    r.shot("payment-qr");
    r.tap("DONE");
    assert_eq!(r.ui.get_screen(), Screen::PayConfirm);
    r.shot("did-you-pay");
    r.tap("← NOT YET");
    assert_eq!(r.ui.get_screen(), Screen::Qr);
    r.tap("DONE");
    r.tap("YES, I PAID");
    assert_eq!(r.ui.get_screen(), Screen::Shop);
    assert_eq!(r.units(), 0);
    r.shot("after-payment");

    // Volunteer drink start to finish.
    r.tap("Coca-Cola 33cl");
    r.tap("VOLUNTEER DRINK");
    assert!(r.ui.get_volunteer());
    r.shot("volunteer-on");
    r.tap("CHECKOUT  →");
    r.tap("I SWEAR IT");
    assert_eq!(r.ui.get_screen(), Screen::VolunteerDone);
    r.shot("volunteer-done");

    // The scanner keeps working: products and command barcodes.
    r.scan("TWIX");
    assert_eq!(r.ui.get_screen(), Screen::Shop);
    assert_eq!(r.units(), 1);
    r.scan("NOPE-404");
    assert!(r.ui.get_message_error());
    r.shot("unknown-barcode");
    r.scan("CANCEL");
    assert_eq!(r.ui.get_screen(), Screen::CancelConfirm);
    r.scan("CANCEL");
    assert_eq!(r.units(), 0);

    // Frames that only exist for a moment.
    apply(&r.ui, r.kiosk.view("FIL-PLA-DBLU"));
    r.shot("searching");
    r.ui.set_screen(Screen::Processing);
    r.ui.set_searching("".into());
    r.shot("processing");

    // Real products and photos, when a snapshot was fetched.
    let snap = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("demo-data");
    if let Ok(demo) = Demo::from_snapshot(&snap) {
        let codes: Vec<String> = demo.barcodes().map(String::from).take(3).collect();
        r.kiosk = Kiosk::new(Box::new(demo), Config { htl_name: "HTL Makerspace".into(), iban: "BE71096123456769".into(), hidden_categories: vec!["machinegebruik".into()] });
        r.ui.set_searching("".into());
        apply(&r.ui, r.kiosk.view(""));
        r.shot("real-all");
        // Three full rows of products fit without scrolling.
        let names: Vec<String> = r.ui.get_products().iter().take(9).map(|t| t.name.to_string()).collect();
        for n in &names {
            let el = r.find(n).unwrap_or_else(|| panic!("tile {n:?} missing"));
            let bottom = el.absolute_position().y + el.size().height;
            assert!(bottom <= H as f32, "tile {n:?} ends at y={bottom}, below the screen");
        }
        for code in &codes {
            r.scan(code);
        }
        let cats: Vec<String> = r.ui.get_categories().iter().map(|c| c.name.to_string()).collect();
        assert!(!cats.iter().any(|c| c == "MACHINEGEBRUIK"), "hidden category shows a tab");
        for cat in cats.iter().filter(|c| *c != "ALL") {
            r.tap(cat);
            r.shot(&format!("real-{}", cat.to_lowercase()));
        }
    }

    assert!(r.problems.is_empty(), "layout problems:\n{}", r.problems.join("\n"));
}
