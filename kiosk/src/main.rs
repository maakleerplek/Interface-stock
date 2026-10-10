use interface_stock::state::{Event, State};
use interface_stock::inventree::{InvenTree, InvenTreeConfig};
use interface_stock::{apply, backend::Demo, tv, wire, AppWindow, Config, Kiosk};
use slint::ComponentHandle;
use std::io::BufRead;
use std::sync::mpsc;
use std::time::Duration;

/// Owns the kiosk logic, so network calls never block the UI thread.
/// Every frame is pushed to the window as a `View`.
fn worker(rx: mpsc::Receiver<Event>, ui: slint::Weak<AppWindow>, mut kiosk: Kiosk) {
    let push = |v| {
        let _ = ui.upgrade_in_event_loop(move |ui| apply(&ui, v));
    };
    push(kiosk.view(""));
    loop {
        match rx.recv_timeout(Duration::from_millis(500)) {
            Ok(event) => {
                kiosk.handle(event, push);
                // Drop whatever was tapped or scanned during checkout: a second
                // CONFIRM would clear the payment QR before anyone paid.
                if matches!(kiosk.m.state, State::QrDisplay | State::VolunteerDone) {
                    while rx.try_recv().is_ok() {}
                }
            }
            Err(mpsc::RecvTimeoutError::Timeout) => {
                if let Some(v) = kiosk.tick() {
                    push(v);
                }
            }
            Err(mpsc::RecvTimeoutError::Disconnected) => return,
        }
    }
}

/// Read-only check of the InvenTree connection: catalog, prices, a few
/// lookups and stock levels. Changes nothing in InvenTree.
fn check(it: &InvenTree) -> bool {
    use interface_stock::backend::Backend;
    if !it.wait_loaded(Duration::from_secs(60)) {
        eprintln!("FAIL: catalog did not load");
        return false;
    }
    let cat = it.catalog();
    println!("catalog: {} products for sale", cat.len());
    let mut ok = !cat.is_empty();
    for c in cat.iter().take(3) {
        let stock = it.stock(c.part.pk);
        println!(
            "  {:<32} {:>8} {:<10} stock {:?} image {}",
            c.part.name,
            interface_stock::format_price(c.price),
            c.category,
            stock.as_ref().map_err(|_| "unreachable"),
            if c.image.is_some() { "yes" } else { "no" }
        );
        ok &= stock.is_ok();
    }
    for code in std::env::args().skip_while(|a| a != "--check").skip(1) {
        let t = std::time::Instant::now();
        match it.lookup(&code) {
            Some(p) => println!("lookup {code}: {} (pk {}) in {:?}", p.name, p.pk, t.elapsed()),
            None => println!("lookup {code}: not found ({:?})", t.elapsed()),
        }
    }
    it.flush_cache();
    ok
}

fn main() -> Result<(), slint::PlatformError> {
    let args: Vec<String> = std::env::args().collect();
    let has = |flag: &str| args.iter().any(|a| a == flag);
    let demo = has("--demo");
    // .env next to the program (same file the Python version used).
    let _ = dotenvy::dotenv();
    let cfg = Config::from_env();

    let backend: Box<dyn interface_stock::backend::Backend> = if demo {
        // A snapshot from tools/fetch_demo_data.py gives real products and
        // photos; without one the built-in fake shop is used.
        let snapshot = std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("demo-data");
        Box::new(match Demo::from_snapshot(&snapshot) {
            Ok(d) => {
                eprintln!("[demo] InvenTree snapshot from {}", snapshot.display());
                d
            }
            Err(_) => {
                eprintln!("[demo] built-in products (no snapshot in {})", snapshot.display());
                Demo::new()
            }
        })
    } else {
        let itc = InvenTreeConfig::from_env(&cfg.htl_name, cfg.hidden_categories.clone());
        if itc.token.is_empty() {
            eprintln!("[inventree] INVENTREE_TOKEN is not set: lookups and checkout will fail");
        }
        eprintln!("[inventree] {}", itc.url);
        let it = InvenTree::start(itc);
        if has("--check") {
            std::process::exit(if check(&it) { 0 } else { 1 });
        }
        Box::new(it)
    };

    let ui = AppWindow::new()?;
    ui.set_htl_name(cfg.htl_name.to_uppercase().into());

    let (tx, rx) = mpsc::channel::<Event>();
    let t = tx.clone();
    wire(&ui, move |e| {
        let _ = t.send(e);
    });

    // The USB barcode scanner / RFID reader. In demo mode on a PC generic
    // "keyboard"/"hid" devices are never taken, so the real keyboard stays free.
    if !has("--no-scanner") {
        let tx = tx.clone();
        std::thread::spawn(move || {
            interface_stock::scanner::run(!demo, move |code| {
                let _ = tx.send(Event::from_barcode(&code));
            })
        });
    }

    // Stand-in for the USB scanner on a PC: one barcode per line on stdin.
    if has("--fake-scanner") {
        std::thread::spawn(move || {
            for line in std::io::stdin().lock().lines().map_while(Result::ok) {
                let code = line.trim();
                if !code.is_empty() && tx.send(Event::from_barcode(code)).is_err() {
                    return;
                }
            }
        });
    }

    let mut kiosk = Kiosk::new(backend, cfg);
    if !has("--no-tv") {
        let addr = std::env::var("KIOSK_TV_WS").unwrap_or_else(|_| tv::DEFAULT_ADDR.into());
        kiosk = kiosk.with_tv(tv::Tv::start(&addr));
    }
    let weak = ui.as_weak();
    std::thread::spawn(move || worker(rx, weak, kiosk));
    ui.run()
}
