//! The kiosk state machine. A port of `handle_barcode` from the Python
//! version: touch buttons and command barcodes become the same `Action`, so
//! both inputs behave identically. No I/O here except through `Backend`.

use crate::backend::Backend;
use crate::cart::{Cart, Part};
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
    /// One unit less of cart line `n`.
    Decrement(usize),
    PagePrev,
    PageNext,
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
        self.message_at = Instant::now();
    }

    fn error(&mut self, text: impl Into<String>) {
        self.message = Some(Message { text: text.into(), error: true });
        self.message_at = Instant::now();
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
            self.reset(now);
            self.info("Timeout: cart cleared");
            self.last_interaction = now;
            return true;
        }
        if self.message.is_some() && now.saturating_duration_since(self.message_at) >= MESSAGE_FOR {
            self.message = None;
            return true;
        }
        false
    }

    /// Add one unit of `part`, but only if InvenTree actually has it.
    fn try_add(&mut self, be: &dyn Backend, mut part: Part) -> Result<(), String> {
        let (stock_pk, available) = be
            .stock(part.pk)
            .map_err(|_| "Cannot reach InvenTree. Try again.".to_string())?;
        let name = part.name.clone();
        let Some(stock_pk) = stock_pk.filter(|_| available > 0.0) else {
            return Err(format!("{name} is out of stock."));
        };
        // Pin the stock item we just counted, so checkout removes from that one.
        part.stock_item_pk = Some(stock_pk);
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

    fn after_removal(&mut self, removed: Option<Part>, now: Instant) {
        if self.cart.is_empty() {
            self.reset(now);
            self.info("Cart is now empty.");
        } else if let Some(p) = removed {
            self.info(format!("Removed {}", p.name));
        }
    }

    /// Run the checkout after `handle` moved to `Processing`.
    pub fn checkout(&mut self, be: &dyn Backend, now: Instant) {
        if self.state != State::Processing {
            return;
        }
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
        self.last_interaction = now;
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
