//! USB barcode scanner (and the RFID reader in keyboard mode). Port of
//! `find_scanner` / `read_scancode` from the Python version: the scanner
//! types the code as key presses followed by Enter.

use std::time::{Duration, Instant};

/// A half-read code is dropped after this long without a key. The scanner
/// pauses now and then inside a long QR code, so this is a real silence,
/// not a gap between two keys.
pub const SCAN_GAP: Duration = Duration::from_secs(1);

const KEY_ENTER: u16 = 28;
const KEY_KPENTER: u16 = 96;
const KEY_LEFTSHIFT: u16 = 42;
const KEY_RIGHTSHIFT: u16 = 54;

/// Scan codes (US QWERTY positions) -> characters. 12 is the minus key:
/// '-' in the IPNs on the TV's QR codes (FIL-PLA-DBLU), '_' with Shift.
fn char_for(code: u16, shift: bool) -> Option<char> {
    if shift {
        match code {
            12 => return Some('_'),
            13 => return Some('+'),
            _ => {}
        }
    }
    Some(match code {
        2 => '1', 3 => '2', 4 => '3', 5 => '4', 6 => '5',
        7 => '6', 8 => '7', 9 => '8', 10 => '9', 11 => '0',
        12 => '-', 13 => '=',
        16 => 'Q', 17 => 'W', 18 => 'E', 19 => 'R', 20 => 'T',
        21 => 'Y', 22 => 'U', 23 => 'I', 24 => 'O', 25 => 'P',
        30 => 'A', 31 => 'S', 32 => 'D', 33 => 'F', 34 => 'G',
        35 => 'H', 36 => 'J', 37 => 'K', 38 => 'L',
        44 => 'Z', 45 => 'X', 46 => 'C', 47 => 'V', 48 => 'B', 49 => 'N', 50 => 'M',
        51 => ',', 52 => '.', 53 => '/', 57 => ' ',
        _ => return None,
    })
}

/// Turns key events into complete codes.
#[derive(Default)]
pub struct Decoder {
    buf: String,
    shift: bool,
    last_key: Option<Instant>,
}

impl Decoder {
    /// Feed one key event (`value`: 1 down, 0 up, 2 repeat). Returns the
    /// code when Enter completes it.
    pub fn key(&mut self, code: u16, value: i32, now: Instant) -> Option<String> {
        if code == KEY_LEFTSHIFT || code == KEY_RIGHTSHIFT {
            self.shift = value != 0;
            return None;
        }
        if value != 1 {
            return None;
        }
        if self.last_key.is_some_and(|t| now - t > SCAN_GAP) && !self.buf.is_empty() {
            eprintln!("[scanner] dropped incomplete scan {:?}", self.buf);
            self.buf.clear();
        }
        self.last_key = Some(now);
        if code == KEY_ENTER || code == KEY_KPENTER {
            let code = self.buf.trim().to_string();
            self.buf.clear();
            return (!code.is_empty()).then_some(code);
        }
        if let Some(c) = char_for(code, self.shift) {
            self.buf.push(c);
        }
        None
    }
}

/// Name fragments of the scanners and readers in use. "keyboard" and "hid"
/// cover scanners that announce themselves as a generic keyboard. On the
/// kiosk there is nothing else to mistake for one, but on a PC they match
/// the real keyboard or a virtual HID device, so `allow_generic` is off in
/// demo mode.
fn looks_like_scanner(name: &str, allow_generic: bool) -> bool {
    let name = name.to_lowercase();
    if let Ok(want) = std::env::var("KIOSK_SCANNER") {
        return !want.is_empty() && name.contains(&want.to_lowercase());
    }
    ["usbscn", "scanner", "barcode"].iter().any(|k| name.contains(k))
        || (allow_generic && (name.contains("keyboard") || name.contains("hid")))
}

/// Read codes forever: find the scanner, grab it (so its keys do not also
/// reach the focused window, e.g. the TV browser), and reconnect when it
/// is unplugged.
pub fn run(allow_generic: bool, mut on_code: impl FnMut(String)) {
    let mut announced = false;
    loop {
        let found = evdev::enumerate().find(|(_, d)| {
            // Virtual devices (uinput, remote desktops) have no physical
            // path; a USB scanner always has one.
            d.physical_path().is_some_and(|p| !p.is_empty())
                && d.name().is_some_and(|n| looks_like_scanner(n, allow_generic))
                && d.supported_keys().is_some_and(|k| k.contains(evdev::KeyCode(KEY_ENTER)))
        });
        let Some((path, mut dev)) = found else {
            if !announced {
                eprintln!("[scanner] no scanner found; retrying every 2 s");
                announced = true;
            }
            std::thread::sleep(Duration::from_secs(2));
            continue;
        };
        announced = false;
        eprintln!("[scanner] using {} ({})", dev.name().unwrap_or("?"), path.display());
        if let Err(e) = dev.grab() {
            eprintln!("[scanner] could not grab the device: {e}");
        }
        let mut dec = Decoder::default();
        loop {
            let events = match dev.fetch_events() {
                Ok(ev) => ev,
                Err(e) => {
                    eprintln!("[scanner] lost the device: {e}");
                    break;
                }
            };
            for ev in events {
                if ev.event_type() == evdev::EventType::KEY {
                    if let Some(code) = dec.key(ev.code(), ev.value(), Instant::now()) {
                        on_code(code);
                    }
                }
            }
        }
        std::thread::sleep(Duration::from_secs(2));
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn type_keys(dec: &mut Decoder, keys: &[(u16, bool)], t: Instant) -> Option<String> {
        let mut out = None;
        for &(k, shift) in keys {
            if shift {
                dec.key(KEY_LEFTSHIFT, 1, t);
            }
            out = dec.key(k, 1, t).or(out);
            dec.key(k, 0, t);
            if shift {
                dec.key(KEY_LEFTSHIFT, 0, t);
            }
        }
        out
    }

    #[test]
    fn ipn_with_minus_and_enter() {
        // F I L - P L A - D B L U <Enter>
        let keys: Vec<(u16, bool)> = [33, 23, 38, 12, 25, 38, 30, 12, 32, 48, 38, 22, KEY_ENTER]
            .iter()
            .map(|&k| (k, false))
            .collect();
        let mut dec = Decoder::default();
        assert_eq!(type_keys(&mut dec, &keys, Instant::now()).as_deref(), Some("FIL-PLA-DBLU"));
    }

    #[test]
    fn ean_digits_and_shifted_underscore() {
        let mut dec = Decoder::default();
        let t = Instant::now();
        let keys = [(6, false), (12, true), (2, false), (KEY_ENTER, false)];
        assert_eq!(type_keys(&mut dec, &keys, t).as_deref(), Some("5_1"));
    }

    #[test]
    fn silence_drops_half_code_but_not_short_pauses() {
        let mut dec = Decoder::default();
        let t = Instant::now();
        dec.key(2, 1, t);
        dec.key(3, 1, t + Duration::from_millis(600));
        assert_eq!(dec.key(KEY_ENTER, 1, t + Duration::from_millis(900)).as_deref(), Some("12"));
        dec.key(2, 1, t + Duration::from_secs(5));
        dec.key(3, 1, t + Duration::from_secs(7));
        assert_eq!(dec.key(KEY_ENTER, 1, t + Duration::from_secs(7)).as_deref(), Some("2"));
    }

    #[test]
    fn empty_enter_gives_nothing() {
        let mut dec = Decoder::default();
        assert_eq!(dec.key(KEY_ENTER, 1, Instant::now()), None);
    }

    #[test]
    fn keyboard_only_matches_on_the_kiosk() {
        std::env::remove_var("KIOSK_SCANNER");
        assert!(looks_like_scanner("USBSCN Barcode Device", false));
        assert!(!looks_like_scanner("Keychron Keychron B6 Pro Keyboard", false));
        assert!(!looks_like_scanner("libvirtualhid Keyboard", false));
        assert!(looks_like_scanner("Generic USB Keyboard", true));
    }
}
