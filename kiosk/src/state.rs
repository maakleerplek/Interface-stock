//! The kiosk state machine. A port of `handle_barcode` from the Python
//! version: touch buttons and command barcodes become the same `Action`, so
//! both inputs behave identically. No I/O here except through `Backend`.

use crate::backend::{Backend, LaserSession};
use crate::cart::{filament_type, Cart, Part, Print, Tool, ToolTime};
use std::time::{Duration, Instant};

/// Inactive carts are dropped after this long.
pub const TIMEOUT: Duration = Duration::from_secs(300);
/// How long the "enjoy your drink" screen stays up after a volunteer checkout.
pub const VOLUNTEER_DONE_FOR: Duration = Duration::from_secs(4);
/// An unanswered "Did you pay?" goes back to the QR after this long.
pub const PAY_CONFIRM_FOR: Duration = Duration::from_secs(30);
/// Banner messages disappear after this long.
pub const MESSAGE_FOR: Duration = Duration::from_secs(6);

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum State {
    Idle,
    Shopping,
    CancelConfirm,
    CheckoutConfirm,
    Processing,
    QrDisplay,
    /// "Did you pay?" after DONE on the payment QR.
    PayConfirm,
    VolunteerDone,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Action {
    Confirm,
    Cancel,
    Remove,
    Volunteer,
    /// Leave a confirm screen without changing the cart.
    Back,
    /// One more unit of cart line `n`.
    Increment(usize),
    /// A 3D print and its grams, from the print screen.
    Print(PrintPick, u32),
    /// Machine time: the machine and its minutes, from the minutes screen.
    Time(Tool, u32),
    /// Pay laser timer session `n` of `open_sessions`.
    Session(usize),
    /// One unit less of cart line `n`.
    Decrement(usize),
    PagePrev,
    PageNext,
}

/// What the visitor picked on the 3D-print screen; the colour and the type are optional.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum PrintPick {
    /// A filament (type and colour), by part pk.
    Filament(i64),
    /// Only the type of this filament part, no colour.
    TypeOf(i64),
    /// Only grams.
    Unknown,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub enum Event {
    Scan(String),
    /// A product tile on the touch grid, by part pk.
    Pick(i64),
    Button(Action),
}

impl Event {
    /// Command barcodes become the same actions as the touch buttons.
    pub fn from_barcode(code: &str) -> Event {
        let action = match code.to_ascii_uppercase().as_str() {
            "CONFIRM" => Action::Confirm,
            "CANCEL" => Action::Cancel,
            "REMOVE" => Action::Remove,
            "VOLUNTEER" => Action::Volunteer,
            "PAGE-PREV" => Action::PagePrev,
            "PAGE-NEXT" => Action::PageNext,
            _ => return Event::Scan(code.to_string()),
        };
        Event::Button(action)
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum TvPage {
    Prev,
    Next,
}

#[derive(Clone, Debug, PartialEq)]
pub struct Message {
    pub text: String,
    pub error: bool,
}

pub struct Machine {
    pub state: State,
    pub cart: Cart,
    pub message: Option<Message>,
    /// Last part that was added, shown large on the shopping screen.
    pub last_part: Option<Part>,
    /// Units and total of the volunteer checkout that just finished.
    pub done_summary: (u32, f64),
    last_interaction: Instant,
    entered: Instant,
    message_at: Instant,
}

impl Machine {
    pub fn new(now: Instant) -> Self {
        Machine {
            state: State::Idle,
            cart: Cart::default(),
            message: None,
            last_part: None,
            done_summary: (0, 0.0),
            last_interaction: now,
            entered: now,
            message_at: now,
        }
    }

    fn go(&mut self, state: State, now: Instant) {
        if state != self.state {
            self.entered = now;
        }
        self.state = state;
    }

    fn info(&mut self, text: impl Into<String>) {
        self.message = Some(Message { text: text.into(), error: false });
        self.message_at = self.last_interaction;
    }

    fn error(&mut self, text: impl Into<String>) {
        self.message = Some(Message { text: text.into(), error: true });
        self.message_at = self.last_interaction;
    }

    fn reset(&mut self, now: Instant) {
        self.cart.clear();
        self.last_part = None;
        self.go(State::Idle, now);
    }

    /// The TV pauses its page cycle while someone is shopping. The payment
    /// QR counts as idle: the sale is booked by then.
    pub fn busy(&self) -> bool {
        !matches!(
            self.state,
            State::Idle | State::QrDisplay | State::PayConfirm | State::VolunteerDone
        )
    }

    /// Time-based transitions: inactivity timeout and the end of the
    /// volunteer screen. Returns true when the screen must be redrawn.
    pub fn tick(&mut self, now: Instant) -> bool {
        if self.state == State::VolunteerDone && now - self.entered >= VOLUNTEER_DONE_FOR {
            self.go(State::Idle, now);
            return true;
        }
        if self.state == State::PayConfirm && now - self.entered >= PAY_CONFIRM_FOR {
            self.go(State::QrDisplay, now);
            return true;
        }
        if self.busy() && self.state != State::Processing && now - self.last_interaction > TIMEOUT {
            self.last_interaction = now;
            self.reset(now);
            self.info("Timeout: cart cleared");
            return true;
        }
        if self.message.is_some() && now.saturating_duration_since(self.message_at) >= MESSAGE_FOR {
            self.message = None;
            return true;
        }
        false
    }

    /// Add one unit of `part`, but only if InvenTree actually has it.
    fn try_add(&mut self, be: &dyn Backend, part: Part) -> Result<(), String> {
        let available = be
            .stock(part.pk)
            .map_err(|_| "Cannot reach InvenTree. Try again.".to_string())?;
        let name = part.name.clone();
        if available <= 0.0 {
            return Err(format!("{name} is out of stock."));
        }
        if (self.cart.quantity_of(&part) + 1) as f64 > available {
            return Err(format!("Only {}x {name} in stock.", available as u32));
        }
        self.cart.add(part.clone());
        self.last_part = Some(part);
        Ok(())
    }

    /// Add a scanned or tapped product. `Err((message, found))`: `found` is
    /// false when the barcode or tile matched no part at all.
    fn add_product(&mut self, be: &dyn Backend, event: &Event) -> Result<(), (String, bool)> {
        let part = match event {
            Event::Scan(code) => be.lookup(code).ok_or((format!("Unknown barcode: {code}"), false))?,
            Event::Pick(pk) => be.part(*pk).ok_or(("Product not found".to_string(), false))?,
            Event::Button(_) => unreachable!(),
        };
        self.try_add(be, part).map_err(|e| (e, true))
    }

    /// A 3D print goes in the cart at the filament's price per gram; only a type is its
    /// cheapest filament's price, only grams the default price.
    fn add_print(&mut self, be: &dyn Backend, pick: PrintPick, grams: u32) -> Result<(), String> {
        if grams == 0 {
            return Err("Enter the grams of your print.".into());
        }
        let (material, per_gram) = match pick {
            PrintPick::Filament(pk) => {
                let part = be.part(pk).ok_or("Filament not found")?;
                (part.name.clone(), be.print_price(&part))
            }
            PrintPick::TypeOf(pk) => {
                let kind = filament_type(&be.part(pk).ok_or("Filament not found")?.name).0;
                let cheapest = be
                    .catalog()
                    .iter()
                    .filter(|c| c.category.eq_ignore_ascii_case("filament") && filament_type(&c.part.name).0 == kind)
                    .map(|c| be.print_price(&c.part))
                    .fold(f64::INFINITY, f64::min);
                (kind, if cheapest.is_finite() { cheapest } else { be.print_price(&Part::default()) })
            }
            PrintPick::Unknown => (String::new(), be.print_price(&Part::default())),
        };
        self.info(format!("3D print added: {grams} g {material}"));
        self.cart.prints.push(Print { material, grams, per_gram });
        Ok(())
    }

    /// Machine time goes in the cart at the machine's price, at least its minimum.
    fn add_time(&mut self, be: &dyn Backend, tool: Tool, minutes: u32) -> Result<(), String> {
        if minutes == 0 {
            return Err("Enter the minutes the machine ran.".into());
        }
        let (per_minute, minimum) = be.tool_rate(tool);
        self.info(format!("{} added: {minutes} min", tool.label()));
        self.cart.times.push(ToolTime { tool, minutes, per_minute, minimum, session: None });
        Ok(())
    }

    /// Laser timer sessions to pay, without the ones already in this cart.
    pub fn open_sessions(&self, be: &dyn Backend) -> Vec<LaserSession> {
        let in_cart = |s: &LaserSession| self.cart.times.iter().any(|t| t.session.as_ref().is_some_and(|(id, _)| *id == s.id));
        be.laser_sessions().into_iter().filter(|s| !in_cart(s)).collect()
    }

    /// A laser timer session goes in the cart with its minutes, at the laser's price.
    fn add_session(&mut self, be: &dyn Backend, n: usize) -> Result<(), String> {
        let s = self.open_sessions(be).into_iter().nth(n).ok_or("That laser session is gone. Try again.")?;
        let (per_minute, minimum) = be.tool_rate(Tool::Laser);
        self.info(format!("Laser session {} added: {} min", s.name, s.minutes));
        self.cart.times.push(ToolTime { tool: Tool::Laser, minutes: s.minutes, per_minute, minimum, session: Some((s.id, s.name)) });
        Ok(())
    }

    /// Volunteer use is free and booked as such (machine time and prints at €0), for the analytics.
    fn toggle_volunteer(&mut self) {
        self.cart.volunteer = !self.cart.volunteer;
        if self.cart.volunteer {
            self.info("Volunteer drink: cart is FREE");
        } else {
            self.info("Volunteer off: cart is paid again");
        }
    }

    pub fn handle(&mut self, be: &dyn Backend, event: Event, now: Instant) -> Option<TvPage> {
        // Page buttons only talk to the TV and never touch the cart.
        match event {
            Event::Button(Action::PagePrev) => return Some(TvPage::Prev),
            Event::Button(Action::PageNext) => return Some(TvPage::Next),
            _ => {}
        }
        if self.state == State::Processing {
            return None;
        }
        self.last_interaction = now;
        self.message = None;
        if self.state == State::VolunteerDone {
            self.go(State::Idle, now);
        }

        use Action::*;
        match (self.state, event) {
            (State::Idle | State::Shopping, Event::Button(Print(pk, grams))) => match self.add_print(be, pk, grams) {
                Ok(()) => self.go(State::Shopping, now),
                Err(e) => self.error(e),
            },
            (State::Idle | State::Shopping, Event::Button(Time(tool, minutes))) => match self.add_time(be, tool, minutes) {
                Ok(()) => self.go(State::Shopping, now),
                Err(e) => self.error(e),
            },
            (State::Idle | State::Shopping, Event::Button(Session(n))) => match self.add_session(be, n) {
                Ok(()) => self.go(State::Shopping, now),
                Err(e) => self.error(e),
            },
            (State::Idle, Event::Button(Volunteer)) => {
                self.info("Add a drink first, then tap VOLUNTEER.")
            }
            (State::Idle, Event::Button(_)) => self.info("Cart is empty."),
            (State::Idle, ev) => match self.add_product(be, &ev) {
                Ok(()) => self.go(State::Shopping, now),
                Err((e, _)) => self.error(e),
            },

            (State::QrDisplay, Event::Button(Confirm)) => self.go(State::PayConfirm, now),
            (State::QrDisplay, _) => {
                self.error("Please finish payment first, then tap DONE.")
            }

            (State::PayConfirm, Event::Button(Confirm)) => {
                self.reset(now);
                self.info("Thank you!");
            }
            (State::PayConfirm, Event::Button(Back)) => self.go(State::QrDisplay, now),
            (State::PayConfirm, _) => {}

            (State::Shopping, Event::Button(Cancel)) => self.go(State::CancelConfirm, now),
            (State::Shopping, Event::Button(Confirm)) => self.go(State::CheckoutConfirm, now),
            (State::Shopping, Event::Button(Volunteer)) => self.toggle_volunteer(),
            (State::Shopping, Event::Button(Remove)) => {
                let removed = self.cart.remove_last();
                self.after_removal(removed, now);
            }
            (State::Shopping, Event::Button(Decrement(i))) => {
                let removed = self.cart.remove_one(i);
                self.after_removal(removed, now);
            }
            (State::Shopping, Event::Button(Increment(i))) => {
                if let Some((part, _)) = self.cart.items.get(i).cloned() {
                    if let Err(e) = self.try_add(be, part) {
                        self.error(e);
                    }
                }
            }
            (State::Shopping, Event::Button(_)) => {}
            (State::Shopping, ev) => {
                if let Err((e, _)) = self.add_product(be, &ev) {
                    self.error(e);
                }
            }

            (State::CancelConfirm, Event::Button(Cancel)) => {
                self.reset(now);
                self.info("Transaction cancelled.");
            }
            (State::CancelConfirm, Event::Button(_)) => {
                self.go(State::Shopping, now);
                self.info("Cancellation aborted. Cart unchanged.");
            }
            (State::CancelConfirm, ev) => {
                self.go(State::Shopping, now);
                match self.add_product(be, &ev) {
                    Ok(()) => self.info("Cancellation aborted."),
                    Err((e, true)) => self.error(e),
                    Err((_, false)) => self.info("Cancellation aborted. Cart unchanged."),
                }
            }

            (State::CheckoutConfirm, Event::Button(Confirm)) => self.go(State::Processing, now),
            (State::CheckoutConfirm, Event::Button(Cancel)) => self.go(State::CancelConfirm, now),
            (State::CheckoutConfirm, Event::Button(Volunteer)) => {
                // Stay: the confirm screen redraws with the new header and total.
                self.cart.volunteer = !self.cart.volunteer;
            }
            (State::CheckoutConfirm, Event::Button(_)) => {
                self.go(State::Shopping, now);
                self.info("Checkout aborted. Continue shopping.");
            }
            (State::CheckoutConfirm, ev) => match self.add_product(be, &ev) {
                Ok(()) => {
                    self.go(State::Shopping, now);
                    self.info("Item added. Tap CHECKOUT when ready.");
                }
                Err((e, true)) => {
                    self.go(State::Shopping, now);
                    self.error(e);
                }
                Err((e, false)) => self.error(e),
            },

            (State::Processing | State::VolunteerDone, _) => unreachable!(),
        }
        None
    }

    fn after_removal(&mut self, removed: Option<String>, now: Instant) {
        if self.cart.is_empty() {
            self.reset(now);
            self.info("Cart is now empty.");
        } else if let Some(name) = removed {
            self.info(format!("Removed {name}"));
        }
    }

    /// Run the checkout after `handle` moved to `Processing`.
    pub fn checkout(&mut self, be: &dyn Backend, now: Instant) {
        if self.state != State::Processing {
            return;
        }
        self.last_interaction = now;
        match be.checkout(&self.cart) {
            Ok(()) if self.cart.volunteer => {
                self.done_summary = (self.cart.units(), self.cart.total(|p| be.price(p)));
                self.reset(now);
                self.go(State::VolunteerDone, now);
            }
            Ok(()) => {
                self.go(State::QrDisplay, now);
                self.info("Stock removed!");
            }
            Err(e) => {
                self.go(State::Shopping, now);
                self.error(format!("Checkout failed: {e}"));
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::backend::Demo;

    fn run(m: &mut Machine, be: &Demo, events: &[&str]) {
        for e in events {
            m.handle(be, Event::from_barcode(e), Instant::now());
            m.checkout(be, Instant::now());
        }
    }

    #[test]
    fn scan_confirm_confirm_shows_qr() {
        let (be, mut m) = (Demo::new(), Machine::new(Instant::now()));
        run(&mut m, &be, &["COLA", "COLA", "CONFIRM"]);
        assert_eq!(m.state, State::CheckoutConfirm);
        run(&mut m, &be, &["CONFIRM"]);
        assert_eq!(m.state, State::QrDisplay);
        assert_eq!(m.cart.units(), 2);
        run(&mut m, &be, &["CONFIRM"]);
        assert_eq!(m.state, State::PayConfirm);
        run(&mut m, &be, &["CONFIRM"]);
        assert_eq!(m.state, State::Idle);
        assert!(m.cart.is_empty());
    }

    #[test]
    fn a_3d_print_can_be_volunteer_use() {
        let (be, mut m) = (Demo::new(), Machine::new(Instant::now()));
        let print = |m: &mut Machine, grams| m.handle(&be, Event::Button(Action::Print(PrintPick::Filament(9), grams)), Instant::now());
        print(&mut m, 0);
        assert_eq!(m.state, State::Idle);                           // no grams: nothing added
        print(&mut m, 50);
        assert_eq!((m.state, m.cart.prints.len()), (State::Shopping, 1));
        assert!((m.cart.total(|p| be.price(p)) - 5.0).abs() < 1e-9);   // 50 g × €0.10
        run(&mut m, &be, &["VOLUNTEER"]);
        assert!(m.cart.volunteer, "a volunteer's print is free, booked at €0");
        run(&mut m, &be, &["CONFIRM", "CONFIRM"]);
        assert_eq!(m.state, State::VolunteerDone);
    }

    #[test]
    fn machine_time_is_paid_with_the_minimum() {
        let (be, mut m) = (Demo::new(), Machine::new(Instant::now()));
        let time = |m: &mut Machine, minutes| m.handle(&be, Event::Button(Action::Time(Tool::Laser, minutes)), Instant::now());
        time(&mut m, 0);
        assert_eq!(m.state, State::Idle);                           // no minutes: nothing added
        time(&mut m, 4);
        assert_eq!((m.state, m.cart.times.len()), (State::Shopping, 1));
        assert!((m.cart.total(|p| be.price(p)) - 5.0).abs() < 1e-9);   // 4 min, at least the €5 minimum
        run(&mut m, &be, &["CONFIRM", "CONFIRM"]);
        assert_eq!(m.state, State::QrDisplay, "without VOLUNTEER it is paid");
    }

    #[test]
    fn a_laser_session_goes_in_once_with_its_minutes() {
        let (be, mut m) = (Demo::new(), Machine::new(Instant::now()));
        m.handle(&be, Event::Button(Action::Session(0)), Instant::now());
        assert_eq!(m.cart.times.len(), 1);
        assert_eq!((m.cart.times[0].minutes, m.cart.times[0].session.as_ref().map(|s| s.1.as_str())), (12, Some("Ruben")));
        assert!(m.open_sessions(&be).is_empty(), "a session in the cart is not offered again");
        m.handle(&be, Event::Button(Action::Session(0)), Instant::now());
        assert_eq!(m.cart.times.len(), 1);
        assert!(m.message.as_ref().unwrap().error);
        assert_eq!(crate::action_from("session:0", 0), Some(Action::Session(0)));
    }

    #[test]
    fn time_action_from_the_touch_screen() {
        assert_eq!(crate::action_from("time:cnc", 30), Some(Action::Time(Tool::Cnc, 30)));
        assert_eq!(crate::action_from("time:drill", 30), None);
    }

    #[test]
    fn a_print_without_colour_or_type_is_booked_without_one() {
        let (be, mut m) = (Demo::new(), Machine::new(Instant::now()));
        m.handle(&be, Event::Button(Action::Print(PrintPick::Unknown, 20)), Instant::now());
        m.handle(&be, Event::Button(Action::Print(PrintPick::TypeOf(9), 20)), Instant::now());
        let booked: Vec<&str> = m.cart.prints.iter().map(|p| p.material.as_str()).collect();
        assert_eq!(booked, ["", "PLA"]);
        assert!((m.cart.total(|p| be.price(p)) - 4.0).abs() < 1e-9);   // 2 × 20 g × €0.10
    }

    #[test]
    fn print_action_from_the_touch_screen() {
        assert_eq!(crate::action_from("print:9", 42), Some(Action::Print(PrintPick::Filament(9), 42)));
        assert_eq!(crate::action_from("print-type:9", 42), Some(Action::Print(PrintPick::TypeOf(9), 42)));
        assert_eq!(crate::action_from("print:none", 42), Some(Action::Print(PrintPick::Unknown, 42)));
        assert_eq!(crate::action_from("print:x", 42), None);
    }

    #[test]
    fn cancel_needs_confirmation() {
        let (be, mut m) = (Demo::new(), Machine::new(Instant::now()));
        run(&mut m, &be, &["COLA", "CANCEL"]);
        assert_eq!(m.state, State::CancelConfirm);
        run(&mut m, &be, &["REMOVE"]);
        assert_eq!((m.state, m.cart.units()), (State::Shopping, 1));
        run(&mut m, &be, &["CANCEL", "CANCEL"]);
        assert_eq!(m.state, State::Idle);
        assert!(m.cart.is_empty());
    }

    #[test]
    fn remove_last_unit_returns_to_idle() {
        let (be, mut m) = (Demo::new(), Machine::new(Instant::now()));
        run(&mut m, &be, &["COLA", "REMOVE"]);
        assert_eq!(m.state, State::Idle);
    }

    #[test]
    fn stock_limit_and_out_of_stock() {
        let (be, mut m) = (Demo::new(), Machine::new(Instant::now()));
        run(&mut m, &be, &["WATER"]);
        assert_eq!(m.state, State::Idle);
        assert!(m.message.as_ref().unwrap().error);
        run(&mut m, &be, &["MATE", "MATE", "MATE", "MATE"]);
        assert_eq!(m.cart.units(), 3);
        assert!(m.message.as_ref().unwrap().text.contains("Only 3x"));
    }

    #[test]
    fn touch_increment_and_decrement() {
        let (be, mut m) = (Demo::new(), Machine::new(Instant::now()));
        run(&mut m, &be, &["COLA"]);
        m.handle(&be, Event::Button(Action::Increment(0)), Instant::now());
        assert_eq!(m.cart.units(), 2);
        m.handle(&be, Event::Button(Action::Decrement(0)), Instant::now());
        m.handle(&be, Event::Button(Action::Decrement(0)), Instant::now());
        assert_eq!(m.state, State::Idle);
    }

    #[test]
    fn volunteer_checkout_skips_payment() {
        let (be, mut m) = (Demo::new(), Machine::new(Instant::now()));
        run(&mut m, &be, &["VOLUNTEER"]);
        assert!(!m.cart.volunteer);
        run(&mut m, &be, &["COLA", "VOLUNTEER", "CONFIRM", "CONFIRM"]);
        assert_eq!(m.state, State::VolunteerDone);
        assert_eq!(m.done_summary.0, 1);
        assert!(m.cart.is_empty());
        assert!(m.tick(Instant::now() + VOLUNTEER_DONE_FOR));
        assert_eq!(m.state, State::Idle);
    }

    #[test]
    fn unknown_barcode_keeps_state() {
        let (be, mut m) = (Demo::new(), Machine::new(Instant::now()));
        run(&mut m, &be, &["COLA", "CONFIRM", "NOPE"]);
        assert_eq!(m.state, State::CheckoutConfirm);
    }

    #[test]
    fn timeout_clears_cart() {
        let t0 = Instant::now();
        let (be, mut m) = (Demo::new(), Machine::new(t0));
        m.handle(&be, Event::from_barcode("COLA"), t0);
        assert!(!m.tick(t0 + Duration::from_secs(10)));
        assert!(m.tick(t0 + TIMEOUT + Duration::from_secs(1)));
        assert_eq!(m.state, State::Idle);
        assert!(m.cart.is_empty());
    }

    #[test]
    fn touch_only_flow() {
        let (be, mut m) = (Demo::new(), Machine::new(Instant::now()));
        let tap = |m: &mut Machine, e: Event| {
            m.handle(&be, e, Instant::now());
            m.checkout(&be, Instant::now());
        };
        tap(&mut m, Event::Pick(1));
        assert_eq!(m.state, State::Shopping);
        tap(&mut m, Event::Pick(6));
        tap(&mut m, Event::Button(Action::Confirm));
        assert_eq!(m.state, State::CheckoutConfirm);
        tap(&mut m, Event::Button(Action::Back));
        assert_eq!(m.state, State::Shopping);
        tap(&mut m, Event::Button(Action::Cancel));
        tap(&mut m, Event::Button(Action::Back));
        assert_eq!((m.state, m.cart.units()), (State::Shopping, 2));
        tap(&mut m, Event::Button(Action::Confirm));
        tap(&mut m, Event::Button(Action::Confirm));
        assert_eq!(m.state, State::QrDisplay);
        tap(&mut m, Event::Pick(1));
        assert_eq!(m.state, State::QrDisplay);
        assert!(m.message.as_ref().unwrap().error);
        tap(&mut m, Event::Button(Action::Confirm));
        assert_eq!(m.state, State::PayConfirm);
        tap(&mut m, Event::Button(Action::Confirm));
        assert_eq!(m.state, State::Idle);
        assert!(m.cart.is_empty());
    }

    fn at_qr(be: &Demo) -> Machine {
        let mut m = Machine::new(Instant::now());
        run(&mut m, be, &["COLA", "CONFIRM", "CONFIRM"]);
        assert_eq!(m.state, State::QrDisplay);
        m
    }

    #[test]
    fn pay_confirm_not_yet_returns_to_qr() {
        let be = Demo::new();
        let mut m = at_qr(&be);
        m.handle(&be, Event::Button(Action::Confirm), Instant::now());
        assert_eq!(m.state, State::PayConfirm);
        m.handle(&be, Event::Pick(2), Instant::now());
        assert_eq!(m.state, State::PayConfirm, "products are ignored here");
        m.handle(&be, Event::Button(Action::Back), Instant::now());
        assert_eq!(m.state, State::QrDisplay);
        assert_eq!(m.cart.units(), 1);
    }

    #[test]
    fn pay_confirm_times_out_to_qr() {
        let be = Demo::new();
        let mut m = at_qr(&be);
        let t = Instant::now();
        m.handle(&be, Event::Button(Action::Confirm), t);
        assert!(!m.tick(t + Duration::from_secs(5)) || m.state == State::PayConfirm);
        assert!(m.tick(t + PAY_CONFIRM_FOR + Duration::from_secs(1)));
        assert_eq!(m.state, State::QrDisplay);
    }

    #[test]
    fn sold_out_tile_is_refused() {
        let (be, mut m) = (Demo::new(), Machine::new(Instant::now()));
        m.handle(&be, Event::Pick(4), Instant::now());
        assert_eq!(m.state, State::Idle);
        assert!(m.message.as_ref().unwrap().text.contains("out of stock"));
    }

    #[test]
    fn page_barcodes_leave_cart_alone() {
        let (be, mut m) = (Demo::new(), Machine::new(Instant::now()));
        run(&mut m, &be, &["COLA"]);
        let tv = m.handle(&be, Event::from_barcode("page-next"), Instant::now());
        assert_eq!(tv, Some(TvPage::Next));
        assert_eq!((m.state, m.cart.units()), (State::Shopping, 1));
    }
}
